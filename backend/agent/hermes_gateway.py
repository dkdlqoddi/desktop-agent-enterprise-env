"""The messenger gateway: the accounts the user listed write to their bot in Telegram, Discord or Slack, and the
chat's agent answers there.

It is Hermes' own gateway, in a second process of the bundled worker (runtime/hermes/worker.py, run_gateway), kept
running for as long as the user has it turned on. Like the chat's worker it holds no model key: each model request
comes back here as an "inference" frame and goes out on the app's LLM connection, and each of the app's actions comes
back as an "app_tool" frame, checked and run exactly as the chat's are. The bot tokens reach it through its
environment alone, and nothing of it listens on a port: the messengers are connected outwards. A command Hermes finds
dangerous is asked in the messenger it came from and waits there for one of the listed accounts.

The process is started again when a setting it was started with changes, and, a few times and further apart each
time, when it ends by itself. A change is applied behind the request that brought it (ending a gateway lets it finish
what it has in hand first), so the settings screen is answered at once and reads the outcome from the status.
"""

import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import diagnostics
from .hermes import MAX_FRAME, child_environment, chromium_path, suppress_os
from .hermes_llm import SelectedLLM
from .llm import UserError

# The messengers, and the keys each needs in the gateway's environment before it is connected.
TOKENS = {
    "telegram": ("TELEGRAM_BOT_TOKEN",),
    "discord": ("DISCORD_BOT_TOKEN",),
    "slack": ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN"),
}
NAMES = {"telegram": "텔레그램", "discord": "디스코드", "slack": "슬랙"}
# Seconds before a gateway that ended by itself is started again: the first few times, then it is left stopped until
# a setting changes or the app starts again.
RESTART_DELAYS = (2, 10, 30, 120, 300)
# A gateway that ran this long had started well: the next time it ends, the delays begin again.
SETTLED_SECONDS = 600
# What a stop waits for the gateway to end the turns it has in hand before the process is ended.
STOP_SECONDS = 12
# Model requests of the gateway's conversations sent at once; more wait their turn.
MODEL_REQUESTS = 6
GENERIC = "모델 요청을 처리하지 못했습니다."
NOT_CONNECTED = "LLM이 연결되지 않았습니다. 앱의 관리 → LLM 연결에서 먼저 연결해 주세요."
INCOMPLETE = "봇 토큰과 허용할 계정을 모두 넣은 메신저가 없습니다. 메신저 하나에 둘 다 넣어 주세요."
# A stand-in for Telegram's Bot API that a test runs on this PC; any other address is not taken.
LOCAL_API = re.compile(r"http://127\.0\.0\.1:\d{1,5}/[A-Za-z0-9/_-]{0,64}")


def message(state, error):
    """A messenger's connection in words, from Hermes' state and error code (never its error text, which may quote the
    bot token that was refused)."""
    if state == "connected":
        return "연결됨"
    if state in ("", "connecting"):
        return "연결하는 중"
    if "intents" in error:
        return "디스코드 개발자 포털에서 봇의 Message Content Intent를 켜 주세요."
    if "auth" in error or error.startswith("missing_"):
        return "봇 토큰이 맞지 않습니다. 토큰을 다시 넣어 주세요."
    if "conflict" in error or error.endswith("_lock"):
        return "같은 봇을 다른 프로그램이 쓰고 있습니다. 그쪽을 끄거나 다른 봇을 연결해 주세요."
    if error == "missing_dependency":
        return "이 메신저에 필요한 구성 요소가 배포 파일에 없습니다."
    if state == "retrying":
        return "메신저 서버에 연결하지 못해 다시 시도하고 있습니다."
    if state == "fatal":
        return "연결하지 못했습니다. 봇 설정과 네트워크를 확인해 주세요."
    return "연결이 끊겼습니다."


class Gateway:
    def __init__(self, chat):
        # The chat (agent_chat.py): its tools, toolsets, settings and work folder are the gateway's too.
        self.chat = chat
        self.hermes = chat.hermes
        self.lock = threading.RLock()
        self.write_lock = threading.Lock()
        self.child = None
        # What the user set, and what the process is (to be) started with; None while the gateway is off.
        self.enabled, self.launch = False, None
        self.state, self.reason, self.platforms = "off", "", {}
        self.failures, self.started_at, self.timer = 0, 0.0, None
        self.closing = False
        # A change of settings waiting to be applied (`changed`, to `target`), one at a time and the latest only.
        self.applying = threading.Lock()
        self.changed, self.target = False, None
        # Set when the process a request belongs to is being ended, so a model request in flight is given up.
        self.stopped = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=MODEL_REQUESTS, thread_name_prefix="gateway")
        # A test's stand-in for Telegram's Bot API on this PC; '' for Telegram's own.
        stand_in = os.environ.get("PRIVATE_AGENT_TELEGRAM_API", "")
        self.telegram_api = stand_in if LOCAL_API.fullmatch(stand_in) else ""

    # -- what the settings screen shows ------------------------------------------

    def status(self, _params=None):
        """{"enabled", "state": off | starting | running | stopping | failed, "message", "platforms": [{"name", "label",
        "state": connected | connecting | failed, "message"}]}: whether the gateway runs and each messenger's
        connection. It is off only once its process has gone: until then a bot may still answer."""
        with self.lock:
            platforms = []
            for name, entry in self.platforms.items():
                state, error = entry["state"], entry["error"]
                if self.state == "starting":
                    state, error = "", ""
                elif self.state != "running":
                    state, error = "disconnected", ""
                shown = {"connected": "connected", "": "connecting", "connecting": "connecting"}.get(state, "failed")
                platforms.append(
                    {"name": name, "label": NAMES.get(name, name), "state": shown, "message": message(state, error)}
                )
            return {"enabled": self.enabled, "state": self.state, "message": self.reason, "platforms": platforms}

    # -- settings ----------------------------------------------------------------

    def configure(self, options, env):
        """Apply what the main process set: {"enabled", "users": {messenger: "id,id"}, "env": {the bot tokens}} and
        the chat's own keys (env). The gateway runs while it is on and at least one messenger has its token and an
        account to answer; a change of anything it was started with starts it again."""
        options = options if isinstance(options, dict) else {}
        users = options.get("users") if isinstance(options.get("users"), dict) else {}
        tokens = options.get("env") if isinstance(options.get("env"), dict) else {}
        tokens = {name: value for name, value in tokens.items() if isinstance(name, str) and isinstance(value, str)}
        accounts = {
            name: users[name].strip()
            for name, keys in TOKENS.items()
            if isinstance(users.get(name), str) and users[name].strip() and all(tokens.get(key) for key in keys)
        }
        enabled = options.get("enabled") is True
        launch = None
        if enabled and accounts:
            launch = {
                "frame": {
                    "type": "gateway",
                    "system": self.chat.gateway_system(),
                    "toolsets": self.chat.toolsets(),
                    "app_tools": self.chat.tool_schemas(),
                    "settings": self.chat.settings,
                    "users": accounts,
                    "workspace": self.chat.folder(),
                    "telegram_api": self.telegram_api,
                    # What runs unasked and what may not be touched, as in the chat; a messenger's own "always" counts
                    # for its conversation only (runtime/hermes/worker.py, tell_grants).
                    **self.chat.limits(conversation=False),
                },
                "env": {**(env or {}), **tokens},
            }
        with self.lock:
            self.enabled = enabled
            wanted = self.target if self.changed else self.launch
            if launch is None and wanted is None:
                self.reason = INCOMPLETE if enabled else ""
                return
            if launch == wanted and (self.changed or self.child is not None or self.timer is not None):
                return
            self.changed, self.target = True, launch
            if launch is not None:
                self.state, self.reason = "starting", ""
                self.platforms = {name: {"state": "", "error": ""} for name in accounts}
        threading.Thread(target=self.apply, daemon=True).start()

    def apply(self):
        """End the gateway that runs and start the one last asked for, if any."""
        with self.applying:
            with self.lock:
                if not self.changed or self.closing:
                    return
            self.stop()
            with self.lock:
                launch, self.changed, self.target = self.target, False, None
                self.launch, self.failures = launch, 0
                if launch is None:
                    self.reason = INCOMPLETE if self.enabled else ""
                    return
                self.state = "starting"
                self.platforms = {name: {"state": "", "error": ""} for name in launch["frame"]["users"]}
            self.start()

    # -- the process -------------------------------------------------------------

    def start(self):
        with self.lock:
            launch = self.launch
            if launch is None or self.closing or self.child is not None:
                return
            self.timer = None
            try:
                executable, worker, _root = self.hermes.launch_paths()
                home = self.hermes.prepared_home()
                frame = {**launch["frame"], "model": self.model(), "chromium": chromium_path()}
                child = subprocess.Popen(
                    [str(executable), "-I", "-B", str(worker)],
                    cwd=home,
                    env=child_environment(home, launch["env"]),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    encoding="utf-8",
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except UserError as error:
                self.state, self.reason = "failed", str(error)
                return
            except OSError:
                self.state = "failed"
                self.reason = "내장 Hermes 실행 파일을 시작하지 못했습니다. 배포 파일과 백신 격리 기록을 확인해 주세요."
                return
            self.child, self.stopped = child, threading.Event()
            self.state, self.reason, self.started_at = "starting", "", time.monotonic()
            self.platforms = {name: {"state": "", "error": ""} for name in frame["users"]}
            stopped = self.stopped
        threading.Thread(target=self.read, args=(child, stopped), daemon=True).start()
        self.send(child, frame)

    def model(self):
        """The model's name as the gateway shows it (the request itself always goes to the connection selected when it
        is made); a word in its place while no LLM is connected."""
        try:
            return SelectedLLM(self.chat.service.llm, threading.Event()).target["model_name"]
        except Exception:
            return "private-agent"

    def send(self, child, frame):
        line = json.dumps(frame, ensure_ascii=False) + "\n"
        if len(line) > MAX_FRAME:
            if frame.get("type") != "completion" or "error" in frame:
                return
            line = json.dumps({"type": "completion", "id": frame.get("id"), "error": GENERIC}) + "\n"
        with self.write_lock, suppress_os():
            if child.poll() is None:
                child.stdin.write(line)
                child.stdin.flush()

    def read(self, child, stopped):
        try:
            while line := child.stdout.readline(MAX_FRAME + 1):
                if len(line) > MAX_FRAME:
                    break
                try:
                    frame = json.loads(line)
                except (ValueError, RecursionError):
                    continue
                if not isinstance(frame, dict):
                    continue
                kind = frame.get("type")
                if kind == "inference":
                    self.pool.submit(self.infer, child, stopped, frame)
                elif kind == "app_tool":
                    # Its own thread: an action that opens the visible browser takes a while, and the model requests
                    # of other conversations go on meanwhile.
                    threading.Thread(target=self.act, args=(child, frame), daemon=True).start()
                elif kind == "gateway":
                    self.told(child, frame)
        except (OSError, RuntimeError):
            pass
        finally:
            self.ended(child, stopped)

    def told(self, child, frame):
        state = frame.get("state")
        platforms = frame.get("platforms") if isinstance(frame.get("platforms"), dict) else {}
        with self.lock:
            if self.child is not child:
                return
            if state in ("starting", "running", "failed"):
                self.state = state
            for name in self.platforms:
                entry = platforms.get(name)
                if isinstance(entry, dict):
                    self.platforms[name] = {"state": str(entry.get("state", "")), "error": str(entry.get("error", ""))}
            if state == "failed":
                self.reason = "게이트웨이를 시작하지 못했습니다. 봇 설정을 확인해 주세요."

    def infer(self, child, stopped, frame):
        """One model request of a conversation in a messenger, sent on the app's LLM connection as it is now. A
        failure is told to the worker in words, which Hermes passes on to the one who asked."""
        reply = {"type": "completion", "id": frame.get("id")}
        try:
            body, llm = frame.get("body"), self.chat.service.llm
            if stopped.is_set() or not isinstance(body, dict):
                raise UserError(GENERIC)
            if not (llm and llm.public().get("configured")):
                raise UserError(NOT_CONNECTED)
            reply["body"] = SelectedLLM(llm, stopped).complete(body)
        except UserError as error:
            reply["error"] = str(error)
        except Exception as error:
            reply["error"] = diagnostics.report(error, GENERIC, "메신저 게이트웨이")
        self.send(child, reply)

    def act(self, child, frame):
        try:
            card = self.chat.call_tool(frame.get("tool"), frame.get("args") or {})
        except Exception as error:
            words = diagnostics.report(error, "작업 중 문제가 생겼습니다.", "메신저 게이트웨이")
            card = {"status": "failed", "result": {"text": words}}
        self.send(
            child,
            {
                "type": "app_tool_reply",
                "id": frame.get("id"),
                "status": card.get("status", "failed"),
                "result": card.get("result") or {},
            },
        )

    def ended(self, child, stopped):
        """The process has gone. One that was not asked to stop is started again after a while, a few times."""
        stopped.set()
        with suppress_os():
            child.stdin.close()
            child.stdout.close()
        with self.lock:
            if self.child is not child:
                return
            self.child = None
            if self.launch is None or self.closing:
                return
            if time.monotonic() - self.started_at > SETTLED_SECONDS:
                self.failures = 0
            self.state = "failed"
            if self.failures >= len(RESTART_DELAYS):
                self.reason = "게이트웨이가 거듭 종료되어 멈췄습니다. 설정을 확인한 뒤 껐다 켜 주세요."
                return
            self.reason = "게이트웨이가 종료되어 다시 시작합니다."
            self.timer = threading.Timer(RESTART_DELAYS[self.failures], self.start)
            self.timer.daemon = True
            self.failures += 1
            self.timer.start()

    def stop(self, patience=STOP_SECONDS):
        """End the process, letting it finish what it can first, and forget what it was started with."""
        with self.lock:
            child, self.child, self.launch = self.child, None, None
            if self.timer is not None:
                self.timer.cancel()
                self.timer = None
            self.state, self.reason, self.platforms = "stopping" if child else "off", "", {}
            self.stopped.set()
        if child is None:
            return
        self.send(child, {"type": "shutdown"})
        try:
            child.wait(timeout=patience)
        except subprocess.TimeoutExpired:
            with suppress_os():
                child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        with self.lock:
            if self.state == "stopping":
                self.state = "off"

    def shutdown(self):
        """The backend is closing: the gateway goes with it, without the wait a change of settings gives it."""
        with self.lock:
            self.closing = True
        self.stop(patience=3)
        self.pool.shutdown(wait=False, cancel_futures=True)
