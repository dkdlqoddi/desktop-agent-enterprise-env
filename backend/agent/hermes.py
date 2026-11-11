"""Lifetime and protocol of the bundled Hermes worker (runtime/hermes), the planner of the agent chat.

The backend starts the worker with the bundled CPython and talks to it in JSON lines over its standard input and output:
no port opens, and no Hermes installed on the PC is looked for or used. The worker's model requests come back here
(hermes_llm.py), so the LLM settings and their secrets never reach it. Hermes gets one tool, prepare_actions, which
only proposes app actions: agent_chat.py validates them and runs them after the user's approval. Its home is the Wiki
folder's hermes folder (memories, sessions); one worker per Wiki is kept for the next request.
"""

import atexit
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


def shutdown_all():
    for runtime in list(RUNTIMES):
        runtime.shutdown()


atexit.register(shutdown_all)


def child_environment(home):
    # Allow only OS bootstrap values. Inherited provider keys, proxies, Hermes profiles,
    # Python paths and user-wide configuration cannot replace the selected connection.
    env = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "SYSTEMDRIVE", "NUMBER_OF_PROCESSORS", "LANG"}
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
    return env


class Hermes:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.child = self.watch = self.frames = self.inference = None
        self.cancelled = threading.Event()
        self.request_id, self.preview, self.phase = "", "", "idle"
        self.closing = False
        self.home = Path(service.db.path).resolve().parent / "hermes"
        RUNTIMES.add(self)

    def status(self, params):
        with self.lock:
            if params.get("request_id") != self.request_id:
                return {"phase": "idle", "text": "", "draft": "", "can_stop": False}
            draft = answer(self.preview)
            if draft.startswith(("{", "```")):
                draft = ""  # Legacy JSON plans are control data, not user-facing text.
            return {
                "phase": self.phase,
                "text": "응답을 작성하고 있습니다." if self.preview else "작업을 준비하고 있습니다.",
                "draft": draft,
                "can_stop": self.phase in ("starting", "thinking"),
            }

    def stop(self, params):
        with self.lock:
            if params.get("request_id") != self.request_id or self.phase not in ("starting", "thinking"):
                return {"stopped": False}
            self.cancelled.set()
            self.phase = "stopping"
            if self.watch:
                self.watch.cut()
            if self.child and self.child.poll() is None:
                self.child.terminate()
            return {"stopped": True}

    def shutdown(self):
        with self.lock:
            self.closing = True
            self.stop({"request_id": self.request_id})
            child = self.child
            if child and child.poll() is None and not self.cancelled.is_set():
                try:
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
            # Active plan cleanup owns its pipes; idle shutdown owns them here.
            with self.lock:
                if self.phase == "idle":
                    child.stdin.close()
                    child.stdout.close()
                    self.child = self.frames = None

    def on_watch(self, watch, cancelled):
        with self.lock:
            if self.cancelled is cancelled:
                self.watch = watch
            if cancelled.is_set():
                watch.cut()

    def on_text(self, value, cancelled):
        with self.lock:
            if self.cancelled is cancelled and not cancelled.is_set():
                # Keep opening JSON/reasoning markers so a long control response
                # cannot turn into visible text when its beginning is truncated.
                self.preview = (self.preview + value)[:12000]

    def complete(self, selected, body):
        # Windows' synchronous socket read can remain blocked after shutdown().
        # Keep UI/worker cancellation responsive while the bounded network request
        # unwinds. Only one such reader is allowed, including between chat turns.
        replies = queue.Queue(maxsize=1)

        def infer():
            try:
                replies.put((True, selected.complete(body)))
            except Exception as error:
                replies.put((False, error))

        with self.lock:
            self.inference = threading.Thread(target=infer, daemon=True)
            self.inference.start()
        while time.monotonic() < selected.end:
            if selected.cancelled.is_set():
                raise UserError("채팅 요청을 중지했습니다.")
            try:
                ok, value = replies.get(timeout=0.1)
            except queue.Empty:
                continue
            self.inference.join(timeout=0.1)
            if selected.cancelled.is_set():
                raise UserError("채팅 요청을 중지했습니다.")
            if ok:
                return value
            raise value
        raise UserError("Hermes 모델 응답 제한 시간을 초과했습니다.")

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
            return executable, worker
        except (OSError, ValueError, KeyError):
            raise UserError(
                "내장 Hermes 실행 환경이 없거나 손상되었습니다. 앱 배포 파일을 다시 준비해 주세요."
                " 개발 환경에서는 npm run build:hermes가 필요합니다."
            ) from None

    def plan(self, system, context, *, request_id, tools):
        with self.lock:
            if self.closing:
                raise UserError("앱이 종료 중입니다.")
            if self.phase in ("starting", "thinking", "stopping", "finishing"):
                raise UserError("Hermes가 이전 요청을 처리하고 있습니다.")
            if self.inference and self.inference.is_alive():
                raise UserError("이전 모델 연결을 정리하고 있습니다. 잠시 후 다시 보내 주세요.")
            self.cancelled = threading.Event()
            cancelled = self.cancelled
            self.request_id, self.preview, self.phase = request_id, "", "starting"
        child, finished = None, False
        try:
            selected = SelectedLLM(
                self.service.llm,
                cancelled,
                lambda value: self.on_text(value, cancelled),
                lambda watch: self.on_watch(watch, cancelled),
            )
            executable, worker = self.launch_paths()
            self.home.mkdir(exist_ok=True, mode=0o700)
            if self.home.is_symlink() or self.home.resolve() != self.home:
                raise UserError("Hermes 저장 폴더는 Wiki 안의 실제 폴더여야 합니다.")
            for name in ("tmp", "cache", "config", "data"):
                folder = self.home / name
                folder.mkdir(exist_ok=True, mode=0o700)
                if folder.is_symlink():
                    raise UserError("Hermes 저장 폴더 안의 외부 경로 링크는 사용할 수 없습니다.")
            with self.lock:
                if self.cancelled.is_set():
                    raise UserError("채팅 요청을 중지했습니다.")
                child = self.child
                fresh = child is None or child.poll() is not None
                if fresh:
                    if child:
                        child.stdin.close()
                        child.stdout.close()
                    try:
                        child = subprocess.Popen(
                            [str(executable), "-I", "-B", str(worker)],
                            cwd=self.home,
                            env=child_environment(self.home),
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
                    self.frames = queue.Queue(maxsize=8)
                self.child, self.phase = child, "thinking"
                frames = self.frames

            def read():
                try:
                    while line := child.stdout.readline(MAX_FRAME + 1):
                        if len(line) > MAX_FRAME:
                            break
                        frame = json.loads(line)
                        frames.put(frame, timeout=1)
                except (OSError, ValueError, queue.Full):
                    pass
                finally:
                    try:
                        frames.put(None, timeout=1)
                    except queue.Full:
                        pass

            if fresh:
                reader = threading.Thread(target=read, daemon=True)
                reader.start()

            def write(frame):
                line = json.dumps(frame, ensure_ascii=False) + "\n"
                if len(line) > MAX_FRAME:
                    raise UserError("Hermes에 전달할 대화가 너무 큽니다. 대화를 비운 뒤 다시 보내 주세요.")
                child.stdin.write(line)
                child.stdin.flush()

            write(
                {
                    "system": system
                    + "\nUse prepare_actions to submit the action plan when native tools are supported."
                    " The tool only stages actions. Never claim execution. Finish after preparing one plan.",
                    "context": context,
                    "tools": list(tools),
                    "model": selected.target["model_name"],
                    "session_id": "pa_" + uuid.uuid4().hex,
                }
            )
            while time.monotonic() < selected.end:
                if self.cancelled.is_set():
                    raise UserError("채팅 요청을 중지했습니다.")
                try:
                    frame = frames.get(timeout=min(0.2, max(0.001, selected.end - time.monotonic())))
                except queue.Empty:
                    continue
                if frame is None:
                    raise UserError("내장 Hermes가 응답을 마치기 전에 종료되었습니다.")
                if frame.get("type") == "inference":
                    with self.lock:
                        self.preview = ""
                    write({"type": "completion", "body": self.complete(selected, frame["body"])})
                elif frame.get("type") == "result":
                    with self.lock:
                        if self.cancelled.is_set():
                            raise UserError("채팅 요청을 중지했습니다.")
                        # Once accepted, stop must not report a cancelled plan
                        # that is already being returned to the app validator.
                        self.phase, finished = "finishing", True
                    return frame["plan"]
                elif frame.get("type") == "error":
                    if frame.get("kind") == "BlockingIOError":
                        raise UserError(
                            "같은 Wiki의 Hermes가 다른 앱에서 실행 중입니다. 해당 앱을 종료한 뒤 다시 보내 주세요."
                        )
                    raise UserError(
                        "내장 Hermes 실행에 실패했습니다. 배포 환경을 확인해 주세요 ("
                        + str(frame.get("kind", "unknown"))[:60]
                        + ")."
                    )
                else:
                    raise UserError("내장 Hermes의 응답 형식을 확인할 수 없습니다.")
            raise UserError("Hermes 채팅 처리 제한 시간을 초과했습니다.")
        except (BrokenPipeError, OSError):
            raise UserError(
                "채팅 요청을 중지했습니다." if self.cancelled.is_set() else "내장 Hermes와의 연결이 종료되었습니다."
            ) from None
        finally:
            if child and not finished:
                try:
                    child.stdin.close()
                except OSError:
                    pass
                try:
                    child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
                child.stdout.close()
            with self.lock:
                if child and not finished:
                    self.child = self.frames = None
                self.watch = None
                self.phase = "cancelled" if self.cancelled.is_set() else "idle"
