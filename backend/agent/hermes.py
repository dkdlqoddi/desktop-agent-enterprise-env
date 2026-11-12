"""Lifetime and protocol of the bundled Hermes worker (runtime/hermes), the agent behind the chat.

The backend starts the worker with the bundled CPython and talks to it in JSON lines over its standard input and
output: no port opens, and no Hermes installed on the PC is looked for or used. The worker runs Hermes' own tools
(terminal, files, code, browser, MCP, memory, skills, computer use, web search); the app's own actions (document
search, web records, tasks, schedules) run here, reached as "app_tool" frames; dangerous commands ask the user
through an "approval" frame and clarify questions through a "clarify" frame. What a turn may run without asking and
what it may not touch are handed to it with the turn (plan: limits). Every model request comes back here
(hermes_llm.py), so the LLM settings and their secrets never reach the worker. Its home is the Wiki folder's hermes
folder; one worker per Wiki is kept for the next request.
"""

import atexit
import contextlib
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
import weakref
from pathlib import Path

from .hermes_llm import SelectedLLM
from .llm import UserError, answer

COMMIT = "2bd378ed1b8d6bf2192cb26e0a55ff3091e6bd19"
MAX_FRAME = 2_000_000
RUNTIMES = weakref.WeakSet()
# A paused turn waits this long for the user to answer one approval or clarify before it fails closed (deny).
INTERACTION_SECONDS = 300
# The whole turn's compute budget (not counting time the user spends answering), bounded well under the RPC limit.
TURN_SECONDS = 900
# The phases of a turn 중지 ends: while it starts, while the agent works, and while it waits for the user's answer.
STOPPABLE = ("starting", "thinking", "waiting")


def shutdown_all():
    for runtime in list(RUNTIMES):
        runtime.shutdown()


atexit.register(shutdown_all)


@contextlib.contextmanager
def suppress_os():
    try:
        yield
    except OSError:
        pass


@contextlib.contextmanager
def suppress_full():
    try:
        yield
    except queue.Full:
        pass


_CHROMIUM = None


def chromium_path():
    """The app's bundled Chromium (the same Playwright build the web records use), for Hermes' browser tool, found
    once by its known layout under PLAYWRIGHT_BROWSERS_PATH. '' when there is none, so the browser tool says so."""
    global _CHROMIUM
    if _CHROMIUM is not None:
        return _CHROMIUM
    _CHROMIUM = ""
    root = os.environ.get("PRIVATE_AGENT_HERMES_CHROMIUM") or os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if root:
        direct = Path(os.environ.get("PRIVATE_AGENT_HERMES_CHROMIUM") or "")
        if direct.is_file():
            _CHROMIUM = str(direct)
        else:
            relatives = (
                "chrome-linux/chrome",
                "chrome-win/chrome.exe",
                "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
            )
            for folder in sorted(Path(root).glob("chromium-*"), reverse=True):
                for relative in relatives:
                    found = folder / relative
                    if found.is_file():
                        _CHROMIUM = str(found)
                        break
                if _CHROMIUM:
                    break
    return _CHROMIUM


def child_environment(home, extra=None):
    # Allow only OS bootstrap values. Inherited provider keys, proxies, Hermes profiles, Python paths and
    # user-wide configuration cannot replace the selected connection.
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS", "LANG", "PATH"}
    }
    env.update(
        {
            "HERMES_HOME": str(home),
            "HERMES_DISABLE_LAZY_INSTALLS": "1",
            "HERMES_ENABLE_PROJECT_PLUGINS": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(home / "config"),
            "LOCALAPPDATA": str(home / "cache"),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "TEMP": str(home / "tmp"),
            "TMP": str(home / "tmp"),
            "TMPDIR": str(home / "tmp"),
        }
    )
    # The service keys and messenger tokens the user set (hermes.json), which Hermes reads from its environment:
    # web-search and media providers, and the gateway's bot tokens and allowed accounts. They reach the backend only
    # over its private pipe from the main process (agent.configure), never the renderer; no model-provider key is ever
    # among them (hermes-settings.cjs refuses those names). A source run or test may pass them through the environment.
    if extra is None:
        raw = os.environ.get("PRIVATE_AGENT_HERMES_ENV")
        extra = json.loads(raw) if raw else {}
    for name, value in (extra or {}).items():
        if isinstance(name, str) and isinstance(value, str):
            env[name] = value
    return env


class Interaction:
    """One approval or clarify the worker is waiting on, held for the user to answer through the chat."""

    def __init__(self, kind, payload):
        self.kind = kind
        self.payload = payload
        self.answer = None
        self.event = threading.Event()

    def resolve(self, answer):
        self.answer = answer
        self.event.set()

    def wait(self, deadline):
        remaining = deadline - time.monotonic()
        return self.answer if self.event.wait(max(0.0, remaining)) else None


class Hermes:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.child = self.frames = None
        self.cancelled = threading.Event()
        self.request_id, self.preview, self.phase = "", "", "idle"
        self.watch = None  # The model response being read now, which a stop closes so the read ends at once.
        self.inference = None  # The thread running the current model request, polled for a stop.
        self.progress = []  # Recent tool activity lines for the chat's live view.
        self.interaction = None  # The approval/clarify the user is being asked, or None.
        self.closing = False
        self.home = Path(service.db.path).resolve().parent / "hermes"
        self.write_lock = threading.Lock()
        # The agent's service keys and messenger tokens (name -> value), set by the main process over the backend's
        # private pipe (agent.configure); put into the worker's environment, never sent to the renderer or the model.
        self.extra_env = None
        RUNTIMES.add(self)

    # -- what the screen polls while a turn runs ---------------------------------

    def status(self, params):
        with self.lock:
            if params.get("request_id") != self.request_id:
                return {
                    "phase": "idle",
                    "text": "",
                    "draft": "",
                    "progress": [],
                    "interaction": None,
                    "can_stop": False,
                }
            draft = answer(self.preview)
            if draft.startswith(("{", "```")):
                draft = ""  # A control response is not user-facing text.
            interaction = None
            if self.interaction is not None:
                interaction = {"kind": self.interaction.kind, **self.interaction.payload}
            return {
                "phase": self.phase,
                "text": "응답을 작성하고 있습니다." if self.preview else "작업을 진행하고 있습니다.",
                "draft": draft,
                "progress": list(self.progress),
                "interaction": interaction,
                "can_stop": self.phase in STOPPABLE,
            }

    def resolve(self, params, kind, settle=None):
        """The user's answer to the approval or clarify the worker is waiting on, given once: a second answer to the
        same question (a second click, a second window) finds it gone. `settle(asked)` makes the answer the turn gets
        out of the request and of what was asked (the question's payload); without it the request is the answer.
        Returns that answer."""
        with self.lock:
            interaction = self.interaction
            if (
                params.get("request_id") != self.request_id
                or interaction is None
                or interaction.kind != kind
                or params.get("interaction_id") != interaction.payload.get("interaction_id")
                or interaction.event.is_set()
            ):
                raise UserError("응답할 작업을 찾을 수 없습니다. 화면을 새로고침해 주세요.")
            answer = settle(dict(interaction.payload)) if settle else params
            interaction.resolve(answer)
        return answer

    def stop(self, params):
        with self.lock:
            if params.get("request_id") != self.request_id or self.phase not in STOPPABLE:
                return {"stopped": False}
            self.cancelled.set()
            self.phase = "stopping"
            # A turn paused on an approval or a clarify stops too: its question is taken back (answered as refused)
            # rather than left to run out its time.
            if self.interaction is not None:
                self.interaction.resolve(None)
            if self.watch is not None:
                self.watch.cut()  # Unblock an in-flight model read so the turn stops without waiting it out.
            child = self.child
        self.send({"type": "cancel"})
        with self.lock:
            if child and child.poll() is None:
                child.terminate()
        return {"stopped": True}

    def recycle(self, extra_env):
        """Apply new service keys or MCP settings: the idle worker is dropped so the next turn starts fresh with
        them. A turn in progress keeps the worker it started with."""
        with self.lock:
            self.extra_env = extra_env
            child = self.child if self.phase in ("idle", "cancelled") else None
            if child is None:
                return
            self.child = self.frames = None
        with suppress_os():
            if child.poll() is None:
                child.stdin.write('{"type":"shutdown"}\n')
                child.stdin.flush()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
        with suppress_os():
            child.stdin.close()
            child.stdout.close()

    def shutdown(self):
        with self.lock:
            self.closing = True
            self.stop({"request_id": self.request_id})
            child = self.child
            if child and child.poll() is None and not self.cancelled.is_set():
                try:
                    with self.write_lock:
                        child.stdin.write('{"type":"shutdown"}\n')
                        child.stdin.flush()
                except OSError:
                    pass
        if child:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
            with self.lock:
                if self.phase in ("idle", "cancelled"):
                    with suppress_os():
                        child.stdin.close()
                        child.stdout.close()
                    self.child = self.frames = None

    # -- launching the worker ----------------------------------------------------

    def send(self, frame):
        with self.lock:
            child = self.child
        if child is None or child.poll() is not None:
            return
        line = json.dumps(frame, ensure_ascii=False) + "\n"
        if len(line) > MAX_FRAME:
            raise UserError("Hermes에 전달할 내용이 너무 큽니다.")
        with self.write_lock:
            try:
                child.stdin.write(line)
                child.stdin.flush()
            except OSError:
                pass

    def launch_paths(self):
        default = Path(__file__).resolve().parents[2] / "dist" / "hermes"
        root = Path(os.environ.get("PRIVATE_AGENT_HERMES_DIR") or default).resolve()
        try:
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            expected = "python/python.exe" if os.name == "nt" else "python/bin/python3.14"
            if (
                manifest["commit"] != COMMIT
                or manifest["executable"] != expected
                or manifest["platform"] != sys.platform
            ):
                raise ValueError()
            worker, executable = root / "worker.py", root / expected
            if not executable.is_file() or hashlib.sha256(worker.read_bytes()).hexdigest() != manifest["worker_sha256"]:
                raise ValueError()
            return executable, worker, root
        except (OSError, ValueError, KeyError):
            raise UserError(
                "내장 Hermes 실행 환경이 없거나 손상되었습니다. 앱 배포 파일을 다시 준비해 주세요."
                " 개발 환경에서는 npm run build:hermes가 필요합니다."
            ) from None

    def prepared_home(self):
        """The agent's home, made if it is missing: a real folder of the Wiki's, as are the folders its temporary files,
        caches and settings go to (a link would lead them out of the Wiki folder)."""
        self.home.mkdir(exist_ok=True, mode=0o700)
        if self.home.is_symlink() or self.home.resolve() != self.home:
            raise UserError("Hermes 저장 폴더는 Wiki 안의 실제 폴더여야 합니다.")
        for name in ("tmp", "cache", "config", "data"):
            folder = self.home / name
            folder.mkdir(exist_ok=True, mode=0o700)
            if folder.is_symlink():
                raise UserError("Hermes 저장 폴더 안의 외부 경로 링크는 사용할 수 없습니다.")
        return self.home

    def ensure_child(self, executable, worker):
        """The worker process, started if there is none and the old one has exited; its reader thread fills the
        frame queue. Reused between turns so memories and sessions stay warm."""
        self.prepared_home()
        with self.lock:
            child = self.child
            if child is not None and child.poll() is None:
                return
            if child is not None:
                with suppress_os():
                    child.stdin.close()
                    child.stdout.close()
            try:
                child = subprocess.Popen(
                    [str(executable), "-I", "-B", str(worker)],
                    cwd=self.home,
                    env=child_environment(self.home, self.extra_env),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except OSError:
                raise UserError(
                    "내장 Hermes 실행 파일을 시작하지 못했습니다. 배포 파일과 백신 격리 기록을 확인해 주세요."
                ) from None
            frames = queue.Queue(maxsize=64)
            self.child, self.frames = child, frames

        def read():
            try:
                while line := child.stdout.readline(MAX_FRAME + 1):
                    if len(line) > MAX_FRAME:
                        break
                    try:
                        frame = json.loads(line)
                    except (ValueError, RecursionError):
                        continue
                    # Only a frame is passed on: a line that reads as other JSON (a number or a word that a tool's
                    # subprocess printed to the pipe it inherited) is not the worker's.
                    if not isinstance(frame, dict):
                        continue
                    # A full queue waits for the turn's loop, which may be answering a model request or holding a
                    # question for the user for minutes: a frame dropped here would leave the worker waiting for a
                    # reply that never comes. The wait ends once the worker has gone or the turn has let go of it.
                    while True:
                        try:
                            frames.put(frame, timeout=1)
                            break
                        except queue.Full:
                            if child.poll() is not None or self.frames is not frames:
                                return
            except OSError:
                pass
            finally:
                with suppress_full():
                    frames.put(None, timeout=1)

        threading.Thread(target=read, daemon=True).start()

    # -- running one turn --------------------------------------------------------

    def plan(
        self,
        system,
        context,
        *,
        request_id,
        toolsets,
        app_tools,
        settings,
        on_app_tool,
        on_interaction,
        workspace="",
        limits=None,
        on_event=None,
    ):
        """Run one chat turn on the worker. Model requests are answered with the app's LLM connection; app_tool
        frames call on_app_tool(tool, args) -> card; approval/clarify frames call on_interaction with the payload
        and the live Interaction. `workspace`: the folder the agent's terminal and file tools start in ('' for its own
        home in the Wiki folder). `limits`: what the turn runs without asking and what it may not touch, as the
        worker takes them: {"allowed": commands, "session_allowed": kinds of command, "protected": paths, "blocked":
        command patterns}. `on_event(kind, data)` hears what the turn did, for the activity kept with its answer:
        "tool" {tool, preview}, "approval" {command, description, decision}, "clarify" {questions, outcome} and
        "grant" {keys} (the kinds of command a "this conversation" answer covers). A question nobody answered has
        the decision or outcome "timeout", or "stopped" when the turn was stopped meanwhile. Returns {"message",
        "cancelled"}; the executed cards are gathered by the caller through on_app_tool."""
        limits = limits or {}
        told = on_event or (lambda kind, data: None)
        with self.lock:
            if self.closing:
                raise UserError("앱이 종료 중입니다.")
            if self.phase in ("starting", "thinking", "stopping", "finishing"):
                raise UserError("Hermes가 이전 요청을 처리하고 있습니다.")
            self.cancelled = threading.Event()
            cancelled = self.cancelled
            self.request_id, self.preview, self.phase = request_id, "", "starting"
            self.progress, self.interaction = [], None

        selected = SelectedLLM(
            self.service.llm,
            cancelled,
            lambda value: self.on_text(value, cancelled),
            lambda watch: self.on_watch(watch, cancelled),
        )
        executable, worker, root = self.launch_paths()
        self.ensure_child(executable, worker)
        chromium = chromium_path()
        with self.lock:
            if self.cancelled.is_set():
                raise UserError("채팅 요청을 중지했습니다.")
            frames = self.frames
            self.phase = "thinking"

        budget = min(TURN_SECONDS, max(120, int(float(selected.config["timeout"]) * 4)))
        self.send(
            {
                "type": "turn",
                "system": system,
                "context": context,
                "model": selected.target["model_name"],
                "session_id": "pa_" + uuid.uuid4().hex,
                "toolsets": list(toolsets),
                "app_tools": list(app_tools),
                "settings": settings,
                "chromium": chromium,
                "budget": budget,
                "workspace": workspace,
                **{
                    name: [value for value in limits.get(name) or () if isinstance(value, str)]
                    for name in ("allowed", "session_allowed", "protected", "blocked")
                },
            }
        )
        message, finished = "", False
        # The whole turn's wall-clock bound; time the user spends answering an approval or clarify does not count.
        deadline = time.monotonic() + budget + 30
        try:
            while True:
                if self.cancelled.is_set():
                    raise UserError("채팅 요청을 중지했습니다.")
                if time.monotonic() > deadline:
                    raise UserError("Hermes 채팅 처리 제한 시간을 초과했습니다.")
                try:
                    frame = frames.get(timeout=0.2)
                except queue.Empty:
                    continue
                if frame is None:
                    raise UserError("내장 Hermes가 응답을 마치기 전에 종료되었습니다.")
                kind = frame.get("type")
                if kind == "inference":
                    body = frame.get("body")
                    if not isinstance(body, dict):
                        raise UserError("내장 Hermes의 모델 요청 형식이 올바르지 않습니다.")
                    with self.lock:
                        self.preview = ""
                    selected.renew()
                    self.send({"type": "completion", "id": frame.get("id"), "body": self.complete(selected, body)})
                elif kind == "app_tool":
                    card = on_app_tool(frame.get("tool"), frame.get("args") or {})
                    self.record_progress(card.get("title", ""), card.get("status", ""))
                    self.send(
                        {
                            "type": "app_tool_reply",
                            "id": frame.get("id"),
                            "status": card.get("status", "failed"),
                            "result": card.get("result") or {},
                        }
                    )
                elif kind in ("approval", "clarify"):
                    waited = time.monotonic()
                    answered = self.ask(kind, frame, on_interaction)
                    deadline += time.monotonic() - waited  # The user's thinking time is not the turn's compute time.
                    unanswered = "stopped" if self.cancelled.is_set() else "timeout"
                    if kind == "approval":
                        decision = (answered or {}).get("decision", "deny")
                        decision = decision if decision in ("once", "session", "always", "deny") else "deny"
                        self.send({"type": "approval_reply", "id": frame.get("id"), "decision": decision})
                        told(
                            "approval",
                            {
                                "command": frame.get("command", ""),
                                "description": frame.get("description", ""),
                                "decision": decision if answered else unanswered,
                            },
                        )
                    else:
                        outcome = (answered or {}).get("outcome", "cancelled")
                        self.send(
                            {
                                "type": "clarify_reply",
                                "id": frame.get("id"),
                                "answers": (answered or {}).get("answers") or {},
                                "outcome": outcome,
                            }
                        )
                        told(
                            "clarify",
                            {"questions": frame.get("questions"), "outcome": outcome if answered else unanswered},
                        )
                elif kind == "grant":
                    told("grant", {"keys": [key for key in frame.get("keys") or () if isinstance(key, str)]})
                elif kind == "progress":
                    self.record_progress(
                        frame.get("tool", ""), frame.get("event", ""), preview=frame.get("preview", "")
                    )
                    if frame.get("event") == "tool.started":
                        told("tool", {"tool": frame.get("tool", ""), "preview": frame.get("preview", "")})
                elif kind == "draft":
                    self.on_text(frame.get("text", ""), cancelled)
                elif kind == "result":
                    with self.lock:
                        if self.cancelled.is_set():
                            raise UserError("채팅 요청을 중지했습니다.")
                        self.phase, finished = "finishing", True
                    if frame.get("cancelled"):
                        raise UserError("채팅 요청을 중지했습니다.")
                    message = frame.get("message", "")
                    return {"message": message if isinstance(message, str) else "", "cancelled": False}
                elif kind == "error":
                    raise UserError(
                        "내장 Hermes 실행에 실패했습니다. 배포 환경을 확인해 주세요 ("
                        + str(frame.get("kind", "unknown"))[:60]
                        + ")."
                    )
        except (BrokenPipeError, OSError):
            raise UserError(
                "채팅 요청을 중지했습니다." if self.cancelled.is_set() else "내장 Hermes와의 연결이 종료되었습니다."
            ) from None
        finally:
            with self.lock:
                self.interaction = None
                self.watch = None
                self.phase = "cancelled" if self.cancelled.is_set() else "idle"
                # A turn that did not reach its result (an LLM error, a timeout, a stop) may leave the worker blocked
                # waiting for a reply to a frame it already sent, so it cannot be reused: it is ended here and the
                # next turn starts a fresh one. A clean result keeps the worker warm for the next turn.
                stale = None if finished else self.child
                if not finished:
                    self.child = self.frames = None
            if stale is not None:
                with suppress_os():
                    if stale.poll() is None:
                        stale.terminate()
                try:
                    stale.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    stale.kill()
                    stale.wait(timeout=5)
                with suppress_os():
                    stale.stdin.close()
                    stale.stdout.close()

    def ask(self, kind, frame, on_interaction):
        """Hold an approval or clarify for the user, and wait (bounded) for the answer the chat sends back."""
        payload = {key: value for key, value in frame.items() if key not in ("type", "id")}
        payload["interaction_id"] = uuid.uuid4().hex
        interaction = Interaction(kind, payload)
        with self.lock:
            if self.cancelled.is_set():
                return None
            self.interaction = interaction
            self.phase = "waiting"
            on_interaction(kind, payload)
        answer = interaction.wait(time.monotonic() + INTERACTION_SECONDS)
        with self.lock:
            self.interaction = None
            if not self.cancelled.is_set():
                self.phase = "thinking"
        return answer

    def record_progress(self, name, event, preview=""):
        line = name if not preview else f"{name}: {preview}"
        if not line:
            return
        with self.lock:
            self.progress = (self.progress + [{"event": event, "text": line[:200]}])[-20:]

    def complete(self, selected, body):
        """One model request, run in a daemon thread so a stop is answered at once even while the request is blocked
        before the server has sent any byte (a slow gateway, or one that delays its headers)."""
        replies = queue.Queue(maxsize=1)

        def infer():
            try:
                replies.put((True, selected.complete(body)))
            except Exception as error:  # Carried back to the plan loop, which raises it.
                replies.put((False, error))

        with self.lock:
            self.inference = threading.Thread(target=infer, daemon=True)
            self.inference.start()
        while time.monotonic() < selected.end:
            if self.cancelled.is_set():
                raise UserError("채팅 요청을 중지했습니다.")
            try:
                ok, value = replies.get(timeout=0.1)
            except queue.Empty:
                continue
            if self.cancelled.is_set():
                raise UserError("채팅 요청을 중지했습니다.")
            if ok:
                return value
            raise value
        raise UserError("Hermes 모델 응답 제한 시간을 초과했습니다.")

    def on_watch(self, watch, cancelled):
        with self.lock:
            if self.cancelled is cancelled:
                self.watch = watch
            if cancelled.is_set():
                watch.cut()

    def on_text(self, value, cancelled):
        with self.lock:
            if self.cancelled is cancelled and not cancelled.is_set():
                self.preview = (self.preview + value)[:12000]
