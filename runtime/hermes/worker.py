"""Private Agent's stdio host for the pinned Hermes AIAgent (never the user's CLI).

The backend starts this with the bundled CPython and talks to it in JSON lines over stdin/stdout. No provider
credential enters this process: every model request the agent makes — the main turn, and the auxiliary tasks
(smart approvals, vision, compression, titles) and any subagent — is routed back to the parent as an "inference"
frame, which performs it with the app's own LLM connection. The agent's tools run here (terminal, files, code,
browser, MCP, memory, skills, computer use, web search); the app's own actions (document search, web records,
tasks, schedules) run in the parent, reached as "app_tool" frames.

A process serves one of two things, by the first frame it gets. A "turn" runs one message of the chat: dangerous
commands ask the user through an "approval" frame (what a "this conversation" answer covers follows as a "grant"
frame), clarify questions through a "clarify" frame, and progress and draft text stream up as "progress" and "draft"
frames; the process is kept for the next turn. A "gateway" runs
Hermes' messenger gateway until it is shut down (run_gateway): the accounts the user listed write to their bot in
Telegram, Discord or Slack, and the agent answers there, asking them there before a dangerous command. Either way its
home is the Wiki's hermes folder.

Frames to the parent: inference, approval, grant, app_tool, clarify, progress, draft, result, error, gateway.
Frames from the parent: turn, gateway, completion, approval_reply, app_tool_reply, clarify_reply, cancel, shutdown.
"""
import contextlib
import json
import logging
import os
import queue
import re
import threading
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ["HERMES_HOME"]).resolve()
# -I deliberately ignores PYTHONIOENCODING. Windows pipes otherwise use the user's code page, which cannot
# represent every prompt or tool argument.
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
INPUT, OUTPUT = sys.stdin, sys.stdout
MAX_FRAME = 2_000_000
OUT_LOCK = threading.Lock()
# The messengers the gateway may connect. Whatever else Hermes could serve (a webhook, an API server, another chat
# service whose key happens to be among the user's) is never started.
GATEWAY_PLATFORMS = ("telegram", "discord", "slack")
# What an account the user listed may type as a command in a messenger: the commands that steer its own
# conversation. Hermes' other commands change what the agent may do (yolo, approvals, model), reach outside the PC
# (update, debug, login) or set the gateway up (restart, platform, sethome); the app's settings screen does those.
GATEWAY_COMMANDS = ("start", "new", "retry", "undo", "title", "compress", "stop", "approve", "deny", "status",
                    "queue", "steer", "btw", "agents", "context", "commands", "usage", "version", "resume",
                    "sessions", "discard")
# Environment names that let more people in than the accounts the user listed (everyone, other bots, whole group
# chats, roles), or that start a listener (an API server, webhooks). None is part of the gateway's environment,
# whatever was stored under such a name (electron/hermes-settings.cjs refuses to store one: GATEWAY_STEERING).
GATEWAY_OPENINGS = re.compile(
    r"_ALLOW_ALL_USERS$|_ALLOW_BOTS$|_ALLOWED_(USERS|CHATS|CHANNELS|ROLES|GROUPS)$|_HOME_CHANNEL"
    r"|^GATEWAY_|^API_SERVER_|^WEBHOOK_|^TELEGRAM_WEBHOOK_")
# An ID no messenger gives an account: with it as the only "admin", every listed account is an ordinary user of
# Hermes' command tiers and runs GATEWAY_COMMANDS alone.
NO_ADMIN = "private-agent-no-admin"
# The protected paths of the turn (or gateway) this process serves, as real paths: what Hermes' file tools refuse
# to read or write (guard_files). Replaced whole each time (protect).
PROTECTED = []
# The command a person is being asked about on this thread, as Hermes would run it (remember_asked).
ASKED = threading.local()
# The longest command "always" keeps to the letter, and how many commands and deny patterns a config takes.
EXACT_COMMAND = 1000
MAX_ALLOWED, MAX_DENIED = 200, 600
# A turn's iterations and the seconds a command's approval waits for its answer, by default and at most, when a turn
# (or the gateway) is not given its own (max_iterations, answer_seconds); a question waits twice that.
ITERATIONS, MAX_ITERATIONS = 24, 200
ANSWER_SECONDS, MAX_ANSWER_SECONDS = 300, 1800


def emit(frame):
    line = json.dumps(frame, ensure_ascii=False) + "\n"
    with OUT_LOCK:
        OUTPUT.write(line)
        OUTPUT.flush()


class Cancelled(Exception):
    """The parent asked to stop this turn."""


class Channel:
    """Request/reply multiplexing over the one stdio pipe. A tool thread emits a frame with a fresh id and waits
    for the matching reply the reader routes back; delegation runs several at once, so replies are keyed by id.
    Waits poll a cancel event, so a stop never leaves a thread blocked on a read that will not come."""

    def __init__(self):
        self.lock = threading.Lock()
        self.waiters = {}
        self.cancelled = threading.Event()
        self.shutdown = False

    def request(self, kind, body):
        if self.cancelled.is_set():
            raise Cancelled()
        rid = uuid.uuid4().hex
        reply = queue.Queue(maxsize=1)
        with self.lock:
            self.waiters[rid] = reply
        try:
            emit({"type": kind, "id": rid, **body})
            while True:
                if self.cancelled.is_set():
                    raise Cancelled()
                try:
                    return reply.get(timeout=0.1)
                except queue.Empty:
                    continue
        finally:
            with self.lock:
                self.waiters.pop(rid, None)

    def deliver(self, rid, value):
        with self.lock:
            reply = self.waiters.get(rid)
        if reply is not None:
            with contextlib.suppress(queue.Full):
                reply.put_nowait(value)

    def cancel(self):
        self.cancelled.set()

    def reset(self):
        self.cancelled = threading.Event()


CHANNEL = Channel()


def mcp_servers(servers):
    """The MCP servers as config.yaml takes them, without the values that may be secrets: a server's env and headers,
    its arguments and its URL (a token is often part of one). config.yaml is a file in the Wiki folder, which may be
    copied, synced or backed up, while the app keeps these settings encrypted. So each such value goes into this
    process's environment under a name of its own and config.yaml holds a ${NAME} reference, which Hermes resolves
    when it reads the servers. A value that is itself a reference (${GITHUB_TOKEN}, a key the user stored) stays as
    written: it names a secret and holds none.

    Hermes refuses a server whose command, arguments or environment are shaped like an attack
    (hermes_cli.mcp_security), judging the entry as config.yaml has it. It could not see that behind the references,
    so each entry is checked here first, as it was written, and one it refuses is left out; were that check missing
    from the bundle, the arguments and the environment stay as written for Hermes' own check."""
    count = 0
    # The values of the turn before (this process is kept for the next turn) go first.
    for name in [name for name in os.environ if name.startswith("PRIVATE_AGENT_MCP_")]:
        del os.environ[name]
    try:
        from hermes_cli.mcp_security import validate_mcp_server_entry as refused
    except Exception:
        refused = None

    def reference(value):
        nonlocal count
        if not isinstance(value, str) or not value or "${" in value:
            return value
        count += 1
        name = f"PRIVATE_AGENT_MCP_{count}"
        os.environ[name] = value
        return "${" + name + "}"

    kept = {}
    for name, server in (servers if isinstance(servers, dict) else {}).items():
        if not isinstance(server, dict) or (refused is not None and refused(name, server)):
            continue
        entry = dict(server)
        if isinstance(entry.get("headers"), dict):
            entry["headers"] = {key: reference(value) for key, value in entry["headers"].items()}
        if "url" in entry:
            entry["url"] = reference(entry["url"])
        if refused is not None:
            if isinstance(entry.get("env"), dict):
                entry["env"] = {key: reference(value) for key, value in entry["env"].items()}
            if isinstance(entry.get("args"), list):
                entry["args"] = [reference(value) for value in entry["args"]]
        kept[name] = entry
    return kept


def write_config(path, config):
    """One of Hermes' config files, replaced whole: the chat's worker and the gateway's read the same home, and
    Hermes reads a file it finds half written as no settings at all. JSON is valid YAML."""
    content = json.dumps(config, ensure_ascii=False, indent=2)
    with contextlib.suppress(OSError):
        if path.read_text(encoding="utf-8") == content:
            return
    draft = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    draft.write_text(content, encoding="utf-8")
    os.replace(draft, path)


def own_settings_only(managed=None):
    """Keep Hermes to the settings this process was started with. Two things would otherwise change them from
    outside the app. A .env file in its home is read over the environment it was started with, at the start and
    again for every message of a gateway: the keys, the accounts a messenger answers, even whether approvals are
    asked. The app hands every such value over through the environment, so a file of that name in the Wiki's hermes
    folder is not the app's (a restored or shared folder may bring one): it is removed and its loader does nothing.
    And a Hermes installed on this PC by its administrator may pin settings in /etc/hermes; `managed` names the one
    folder Hermes may read such settings from instead, or no folder at all."""
    for name in (".env", ".op.env"):
        with contextlib.suppress(OSError):
            (HOME / name).unlink()
    os.environ["HERMES_MANAGED_DIR"] = str(managed) if managed else os.devnull
    from hermes_cli import config as hermes_config, env_loader

    env_loader.load_hermes_dotenv = lambda *args, **kwargs: []
    hermes_config.reload_env = lambda *args, **kwargs: 0


def protect(request):
    """The paths the user (and the app, for its own files) put out of the agent's reach, as this turn has them: each
    as it was written and as it resolves, without the root of a drive. Kept for the file tools (guard_files) and
    returned for the commands (denied_commands)."""
    paths = []
    for value in request.get("protected") or []:
        if not isinstance(value, str) or not value.strip() or not os.path.isabs(value):
            continue
        for path in (os.path.normpath(value), os.path.realpath(value)):
            if os.path.dirname(path) != path and path not in paths:
                paths.append(path)
    PROTECTED[:] = [os.path.normcase(path) for path in paths]
    return paths


def denied_commands(request):
    """Hermes' approvals.deny: a command matching one of these patterns is refused before any approval, whatever
    was allowed earlier. One for each way a protected path is spelled in a command, and the patterns the user wrote
    themselves. A pattern is matched against the command's text, so this stops a command that names the path, not
    one that reaches it another way (a script, a link, a path put together at run time)."""
    blocked = request.get("blocked") or []
    patterns = [value.strip() for value in blocked if isinstance(value, str) and value.strip()]
    for path in protect(request):
        # A command may spell a Windows path with either slash, and Hermes also reads it with its backslashes taken
        # for escapes (dropped).
        for spelled in dict.fromkeys((path, path.replace("\\", "/"), path.replace("\\", ""))):
            patterns.append("*" + re.sub(r"([*?\[])", r"[\1]", spelled) + "*")
    return patterns[:MAX_DENIED]


def allowed_commands(request):
    """Hermes' command_allowlist: the commands the user chose to always allow, each to the letter (exact_command).
    The app keeps them with its settings on this PC and hands them over with every turn, so config.yaml, a file in
    the Wiki folder that may be copied or restored, never decides what runs unasked."""
    allowed = request.get("allowed") or []
    commands = {value.strip() for value in allowed if isinstance(value, str) and exact_text(value.strip())}
    return sorted(commands)[:MAX_ALLOWED]


def within(value, default, highest, lowest=1):
    """A whole number the request gave, if it lies within lowest..highest, else `default`."""
    return value if type(value) is int and lowest <= value <= highest else default


def prepare_home(request):
    """The agent's home and app-owned config for this turn. It carries no secret (the API key never reaches here,
    and the MCP servers' own values stay in the environment: mcp_servers) and is regenerated for each selected
    connection, while memories, skills and sessions stay. How far the turn goes and how long a command or a question
    waits for its answer are the user's (max_iterations, answer_seconds); a question waits longer than the app does,
    so that in the chat the app's own wait decides."""
    for name in ("memories", "skills", "sessions", "logs", "cron", "tmp", "cache", "config", "data"):
        (HOME / name).mkdir(exist_ok=True, mode=0o700)
    for name, content in {
        "SOUL.md": "You assist the user through Private Agent, a desktop app on their own PC.\n",
        "memories/MEMORY.md": "",
        "memories/USER.md": "",
    }.items():
        with contextlib.suppress(FileExistsError):
            with (HOME / name).open("x", encoding="utf-8") as stream:
                stream.write(content)

    settings = request["settings"]
    model = request["model"]
    base = "http://127.0.0.1/v1"
    # Every auxiliary task (smart approvals, vision, compression, titles, web-extract summaries) pinned to the same
    # custom endpoint as the main model, with no fallback provider, so no task ever leaves the app's connection.
    aux_tasks = ["approval", "vision", "compression", "title", "summarize", "web_extract", "reasoning",
                 "classification", "routing", "memory", "skill_review", "background_review"]
    auxiliary = {task: {"provider": "custom", "model": model, "base_url": base} for task in aux_tasks}
    auxiliary["title_generation"] = {"enabled": False}
    chromium = request.get("chromium") or ""
    answer = within(request.get("answer_seconds"), ANSWER_SECONDS, MAX_ANSWER_SECONDS, 60)
    config = {
        "model": {"default": model, "provider": "custom", "base_url": base, "context_length": 65536},
        "compression": {"enabled": True},
        "fallback_model": {}, "fallback_models": [], "fallback_providers": [],
        "memory": {"memory_enabled": True, "user_profile_enabled": True, "nudge_interval": 0},
        "agent": {"max_iterations": within(request.get("max_iterations"), ITERATIONS, MAX_ITERATIONS),
                  "api_max_retries": 1, "auto_recovery_cycles": 0, "clarify_timeout": answer * 2},
        "approvals": {"mode": "smart", "timeout": answer, "cron_mode": "deny", "unattended_mode": "deny",
                      "deny": denied_commands(request)},
        "command_allowlist": allowed_commands(request),
        "security": {"allow_lazy_installs": False},
        "tools": {"tool_search": {"enabled": "off"}},
        "auxiliary": auxiliary,
        # The services the app names by the keys the user stored (ddgs, which needs none, without one). Hermes' free
        # tiers of services the user never chose stay off: a page nothing can read is told as that.
        "web": {
            "search_backend": settings.get("search_backend") or "ddgs",
            "extract_backend": settings.get("extract_backend") or "ddgs",
            "keyless_fallback": False,
        },
        "browser": {"executable_path": chromium} if chromium else {},
        "computer_use": {"enabled": bool(settings.get("computer_use", True))},
        "mcp_servers": mcp_servers(settings.get("mcp_servers")),
        "plugins": {"enabled": []},
        "checkpoints": {"enabled": False},
    }
    write_config(HOME / "config.yaml", config)
    return config


def patch_model_calls():
    """Route every OpenAI-SDK client Hermes builds — the main agent, every auxiliary task and every subagent — to
    the parent over stdio, so no provider key, header or CA is ever needed here. Both the agent method and the
    auxiliary clients build their httpx client through this one function, so patching it covers them all."""
    import httpx
    from agent import process_bootstrap

    class ParentTransport(httpx.BaseTransport):
        def handle_request(self, req):
            return parent_completion(req)

    class AsyncParentTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, req):
            import anyio

            await req.aread()
            return await anyio.to_thread.run_sync(lambda: parent_completion(req))

    def build(base_url="", *, async_mode=False, verify=True):
        if async_mode:
            return httpx.AsyncClient(transport=AsyncParentTransport(), timeout=None)
        return httpx.Client(transport=ParentTransport(), timeout=None)

    process_bootstrap.build_keepalive_http_client = build


def parent_completion(req):
    import httpx

    if req.method != "POST" or not req.url.path.endswith("/chat/completions"):
        return httpx.Response(404, json={"error": {"message": "Only chat completions are bridged"}})
    body = json.loads(req.content)
    # The app streams the selected API to its UI; Hermes receives the complete lossless message, tool calls and
    # all. The transport presents it as one SSE chunk when streaming is asked, so the agent's own code is unchanged.
    reply = CHANNEL.request("inference", {"body": body})
    if not isinstance(reply, dict) or reply.get("type") != "completion":
        raise RuntimeError("invalid inference reply")
    if "error" in reply:
        # The app could not get an answer (the LLM is not connected, refused the request or timed out). A gateway
        # goes on serving: Hermes tells the one who asked, in the messenger, that the model failed.
        return httpx.Response(502, json={"error": {"message": str(reply["error"])[:500], "type": "private_agent"}})
    complete = reply["body"]
    if not body.get("stream"):
        return httpx.Response(200, json=complete)
    choice = complete["choices"][0]
    delta = dict(choice["message"])
    if delta.get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **call} for i, call in enumerate(delta["tool_calls"])]
    chunk = {**complete, "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": delta, "finish_reason": choice["finish_reason"]}]}
    content = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content=content)


def register_app_tools(registry, tools):
    """The app's own actions as Hermes tools: each call goes to the parent, which checks every argument and runs it
    (agent_chat.py) and hands back a result Hermes reads. Nothing here touches the Wiki or the visible browser."""
    def make(name):
        def handler(args, **unused):
            card = CHANNEL.request("app_tool", {"tool": name, "args": args or {}})
            result = (card or {}).get("result") or {}
            payload = {"status": (card or {}).get("status", "failed"), "text": result.get("text", "")}
            for key in ("documents", "records", "ads", "products", "capture_id", "task_id", "routine_id", "url",
                        "tasks", "routines", "document", "overview"):
                if key in result:
                    payload[key] = result[key]
            return json.dumps(payload, ensure_ascii=False)

        return handler

    for tool in tools:
        registry.register(name=tool["name"], toolset="private_agent",
                          schema={"name": tool["name"], "description": tool["description"],
                                  "parameters": tool["parameters"]},
                          handler=make(tool["name"]))


def exact_text(command):
    """Whether Hermes' allowlist would read this text as one command and nothing else: a single line without shell
    operators, and without a wildcard (an entry holding one is a pattern of many commands)."""
    if not command or len(command) > EXACT_COMMAND or re.search(r"[\r\n*?\[]", command):
        return False
    try:
        from tools.approval_floors import _has_allowlist_shell_operator

        return not _has_allowlist_shell_operator(command)
    except Exception:
        return False


def exact_command(shown):
    """Whether "always" may be offered for the command a person is shown: it is then kept to the letter. It must be
    the very text Hermes runs (what is shown has secrets masked, and a masked command is neither kept nor would it
    match), a terminal command Hermes' detector flagged (not a script or a tool's name), and a text its allowlist
    takes as one command (exact_text)."""
    raw = getattr(ASKED, "command", None)
    if not isinstance(raw, str) or raw.strip() != shown.strip() or not exact_text(raw.strip()):
        return False
    try:
        from tools.approval import _command_findings

        return bool(_command_findings(raw))
    except Exception:
        return False


def remember_asked():
    """Keep, for the thread's approval callback, the command Hermes asks a person about as it would run it
    (exact_command): the callback itself is handed the text with secrets masked."""
    from tools import approval

    ask = approval.prompt_dangerous_approval
    if getattr(ask, "private_agent", False):
        return

    def asking(command, *args, **kwargs):
        ASKED.command = command
        try:
            return ask(command, *args, **kwargs)
        finally:
            ASKED.command = None

    asking.private_agent = True
    approval.prompt_dangerous_approval = asking


def tell_grants(messenger=False):
    """What a "this conversation" answer covers. Hermes remembers the kinds of command it was allowed (its
    detector's keys) for the session they were asked in, and a turn of the chat is a session of its own: so each
    grant is told to the parent, which hands the keys back with every later turn of the conversation (run_turn)
    until the user clears it. Hermes' own "always" (a kind of command, for good, written to config.yaml) is never
    used: the chat keeps "always" as one exact command with the app's settings (approval_callback), and in a
    messenger it counts for the conversation only, as what runs unasked for good is decided at the PC."""
    from tools import approval

    persist = approval._persist_choice
    if getattr(persist, "private_agent", False):
        return

    def persisted(session_key, choice, keys):
        if choice == "always":
            choice = "session"
        persist(session_key, choice, keys)
        if choice == "session" and not messenger:
            emit({"type": "grant", "keys": [str(key)[:200] for key in keys][:20]})

    persisted.private_agent = True
    approval._persist_choice = persisted


def guard_files():
    """Hermes' file tools refuse the protected paths (protect) as they refuse its own credential files: its rules
    for reads and for writes, deletes and moves (agent.file_safety) are asked first, and here they also say no to
    a path inside a protected one. Like those rules it is a second line, not a wall: a script or a command that
    does not name the path is not stopped by it."""
    from agent import file_safety

    read, write, denied = (file_safety.get_read_block_error, file_safety.get_write_denied_error,
                           file_safety.is_write_denied)
    if getattr(read, "private_agent", False):
        return
    told = ("is in a folder the user protected in Private Agent's settings. Do not read, change or copy it by "
            "another way; tell the user it is protected.")

    def inside(path):
        try:
            resolved = os.path.normcase(os.path.realpath(os.path.expanduser(str(path))))
        except (OSError, ValueError):
            return False
        return any(resolved == zone or resolved.startswith(zone.rstrip("/\\") + os.sep) for zone in PROTECTED)

    def read_error(path, *args, **kwargs):
        return f"Access denied: {path} {told}" if inside(path) else read(path, *args, **kwargs)

    def write_error(path, *args, **kwargs):
        return f"Access denied: {path} {told}" if inside(path) else write(path, *args, **kwargs)

    def write_denied(path, *args, **kwargs):
        return inside(path) or denied(path, *args, **kwargs)

    read_error.private_agent = True
    swaps = (("get_read_block_error", read, read_error), ("get_write_denied_error", write, write_error),
             ("is_write_denied", denied, write_denied))
    # The module itself, and any module that took the functions by name before this ran.
    for module in [file_safety, *list(sys.modules.values())]:
        for name, old, new in swaps:
            if getattr(module, name, None) is old:
                with contextlib.suppress(Exception):
                    setattr(module, name, new)


def approval_callback(command, description="", *, allow_permanent=True, allow_session=True, smart_denied=False, **kw):
    """A dangerous command the smart guardian escalated: ask the user and wait. The frame says which answers the
    chat may offer beside "once" and "deny": "session" (this kind of command, for the conversation: tell_grants)
    and "always" (this very command, for good: exact_command). An override of the guardian's own refusal is one
    run only. deny, a timeout or a stop block the command; "always" runs it once here and puts the command on
    this process's list for the rest of the turn, while the app keeps it for the turns after."""
    command, description = str(command), str(description)
    scoped = bool(allow_session and not smart_denied)
    exact = bool(allow_permanent and not smart_denied and exact_command(command))
    try:
        reply = CHANNEL.request("approval", {"command": command[:4000], "description": description[:2000],
                                             "smart_denied": bool(smart_denied), "allow_session": scoped,
                                             "allow_permanent": exact})
    except Cancelled:
        return "deny"
    decision = (reply or {}).get("decision", "deny")
    if decision == "always" and exact:
        with contextlib.suppress(Exception):
            from tools.approval import approve_permanent

            approve_permanent(command.strip())
        return "once"
    if decision == "session" and scoped:
        return "session"
    return "once" if decision in ("once", "session", "always") else "deny"


def clarify_callback(questions, **kw):
    """Up to five multiple-choice or open questions for the user; the app shows them and returns the answers."""
    try:
        reply = CHANNEL.request("clarify", {"questions": questions})
    except Cancelled:
        return {"answers": {}, "outcome": "cancelled"}
    answers = (reply or {}).get("answers") or {}
    outcome = (reply or {}).get("outcome") or "submitted"
    return {"answers": answers, "outcome": outcome}


def on_progress(event, tool_name=None, preview=None, args=None, **kw):
    if event in ("tool.started", "tool.completed"):
        emit({"type": "progress", "event": event, "tool": tool_name or "", "preview": str(preview or "")[:200]})


def on_delta(text):
    if text:
        emit({"type": "draft", "text": str(text)[:2000]})


def joined_delegation():
    """Inside it, a subagent the agent starts (delegate_task) is waited for in its tool call, and its result is what
    the call returns. Hermes otherwise lets a subagent run on after the turn and hands its result to a gateway as a
    new message: a chat turn has nothing to take that message, so the answer never came, and the subagent's model
    requests and app actions arrived in whatever turn came next."""
    from tools.delegate_tool_config import gateway_delegation_inline_scope

    return gateway_delegation_inline_scope(True)


def build_agent(request, config):
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from tools.registry import registry
    from tools.terminal_tool import set_approval_callback
    from tools.approval_context import set_hermes_interactive_context, set_current_session_key

    register_app_tools(registry, request["app_tools"])
    # A user is at the screen to answer, so dangerous commands prompt instead of failing closed.
    set_hermes_interactive_context(True)
    set_current_session_key(request["session_id"])
    set_approval_callback(approval_callback)
    remember_asked()
    tell_grants()
    # What runs unasked is what this turn was handed, no more: the commands allowed for good are read again from
    # the config just written (a list a turn before left in this process goes), and the kinds of command allowed
    # for this conversation are given to the turn's own session.
    from tools.approval import approve_session, load_permanent_allowlist

    load_permanent_allowlist()
    for key in request.get("session_allowed") or []:
        if isinstance(key, str) and key:
            approve_session(request["session_id"], key)

    database = SessionDB(HOME / "state.db")
    # The tools the agent is built with say how a delegation ends (joined_delegation).
    with joined_delegation():
        agent = AIAgent(
            model=request["model"], provider="custom", api_mode="chat_completions",
            api_key="private-agent", base_url="http://127.0.0.1/v1",
            enabled_toolsets=request["toolsets"], max_iterations=config["agent"]["max_iterations"],
            max_tokens=request.get("max_tokens", 8192), quiet_mode=True, save_trajectories=False,
            verbose_logging=False, skip_context_files=True, load_soul_identity=True, skip_memory=False,
            # Memory is read and written by the agent during the turn; the post-turn review fork is off so a turn never
            # makes model calls after its result frame (which the parent has stopped reading).
            skip_background_review=True, fallback_model={}, session_db=database,
            session_id=request["session_id"], cwd=str(request.get("workspace") or HOME), platform="private_agent",
            ephemeral_system_prompt=request["system"], clarify_callback=clarify_callback,
            tool_progress_callback=on_progress, stream_delta_callback=on_delta,
            request_overrides={"stream": False}, run_budget_seconds=request.get("budget", 300),
        )
    return agent, database


def bundled_paths():
    sys.path.insert(0, str(ROOT / "dependencies"))
    sys.path.insert(0, str(ROOT / "source"))
    os.environ["PYTHONPATH"] = str(ROOT / "dependencies")


def prepare_tools(request):
    """What a turn and a gateway both need before an agent is built: the bundled skills, and the MCP servers'
    tools (which may take a while to answer)."""
    with contextlib.suppress(Exception):
        from tools.skills_sync import sync_skills

        sync_skills(quiet=True)
    if request["settings"].get("mcp_servers"):
        with contextlib.suppress(Exception):
            from tools.mcp_tool_discovery import discover_mcp_tools

            discover_mcp_tools(list(request["settings"]["mcp_servers"]))


def run_turn(request):
    bundled_paths()
    own_settings_only()
    guard_files()
    config = prepare_home(request)
    patch_model_calls()
    prepare_tools(request)

    agent, database = build_agent(request, config)
    CURRENT_AGENT["agent"] = agent
    try:
        with joined_delegation():
            result = agent.run_conversation(request["context"])
        if CHANNEL.cancelled.is_set():
            raise Cancelled()
        final = result.get("final_response", "")
        if result.get("error") and not result.get("completed"):
            raise RuntimeError("Hermes did not complete the turn")
        emit({"type": "result", "message": final if isinstance(final, str) else "", "actions": []})
    finally:
        with contextlib.suppress(Exception):
            agent.close()
        with contextlib.suppress(Exception):
            database.close()


# -- the messenger gateway -----------------------------------------------------------------------------------------


def gateway_accounts(request):
    """{messenger: the account IDs it answers}, of the messengers the gateway may connect that have a bot token in
    the environment and at least one account: a bot nobody is listed for answers nobody, so it is not connected."""
    token = {"telegram": ("TELEGRAM_BOT_TOKEN",), "discord": ("DISCORD_BOT_TOKEN",),
             "slack": ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN")}
    listed = request.get("users") if isinstance(request.get("users"), dict) else {}
    accounts = {}
    for name in GATEWAY_PLATFORMS:
        ids = [part for part in re.split(r"[\s,]+", str(listed.get(name) or "")) if part and part != "*"]
        if ids and all(os.environ.get(key) for key in token[name]):
            accounts[name] = ids
    return accounts


def gateway_policy(request, config, accounts):
    """The settings the gateway is held to, over the home's config.yaml (Hermes' managed layer, which its own
    commands and settings cannot change): the same model route, tools and approvals as the chat, and for each
    messenger who may write (nobody unlisted is answered, not even with a pairing code) and what they may type."""
    # In a chat room of several people the bot answers a listed account only when it is addressed.
    extra = {"allow_admin_from": [NO_ADMIN], "user_allowed_commands": list(GATEWAY_COMMANDS),
             "group_allow_admin_from": [NO_ADMIN], "group_user_allowed_commands": list(GATEWAY_COMMANDS),
             "unauthorized_dm_behavior": "ignore", "require_mention": True}
    toolsets = [str(name) for name in request["toolsets"]]
    platforms = {name: {"enabled": True, "extra": dict(extra)} for name in accounts}
    # Another address of the Bot API than Telegram's own, which only a test's stand-in server is given.
    if request.get("telegram_api") and "telegram" in platforms:
        platforms["telegram"]["extra"]["base_url"] = str(request["telegram_api"])
    return {
        **config,
        "agent": {**config["agent"], "max_turns": config["agent"]["max_iterations"],
                  "system_prompt": request["system"]},
        "terminal": {"cwd": str(request.get("workspace") or HOME)},
        "unauthorized_dm_behavior": "ignore",
        "allow_all_users": False,
        "gateway": {"allow_all_users": False, "multiplex_profiles": False, "loop_watchdog": False},
        # Hermes' own task board and schedules are not the app's: its routines are (schedule_capture).
        "kanban": {"dispatch_in_gateway": False},
        "platform_toolsets": {name: toolsets for name in accounts},
        "platforms": platforms,
    }


def only_listed_accounts(runner_class, accounts):
    """Hermes lets a sender in by its allowlists, and also by a pairing code approved earlier, a chat or a role
    that was allowed, or a switch that allows everyone, each of which a file or a variable can turn on. Here the
    accounts the user listed in the app are the whole list: a sender Hermes would let in is still refused unless it
    is one of them."""
    from gateway.authz_mixin import _principal_matches_allowlist

    judged = runner_class._principal_authorized

    def listed(self, source, **kwargs):
        if not judged(self, source, **kwargs):
            return False
        platform = getattr(getattr(source, "platform", None), "value", "")
        user = getattr(source, "user_id", None)
        ids = set(accounts.get(platform) or ())
        if not user or not ids:
            return False
        with contextlib.suppress(Exception):
            ids |= set(self._adapter_resolved_allowlist_ids(source))
        return bool(_principal_matches_allowlist(source, str(user), ids))

    runner_class._principal_authorized = listed


def offer_only(commands):
    """The messengers' command menus and /help without the commands no listed account may run. (Typed all the same,
    one of those is refused by Hermes' command tiers: gateway_policy.)"""
    from hermes_cli.commands import COMMAND_REGISTRY

    for command in COMMAND_REGISTRY:
        if command.name not in commands:
            object.__setattr__(command, "cli_only", True)
            object.__setattr__(command, "gateway_config_gate", None)


def gateway_states(names):
    """Each messenger's connection as the settings screen tells it: Hermes' word for the state and its error code.
    Its error text stays here: an adapter may quote the bot token it was refused with."""
    record = {}
    with contextlib.suppress(Exception):
        from gateway.status import read_runtime_status

        record = (read_runtime_status() or {}).get("platforms") or {}
    states = {}
    for name in names:
        entry = record.get(name) if isinstance(record.get(name), dict) else {}
        states[name] = {key: re.sub(r"[^a-z0-9_]", "", str(entry.get(field) or "").lower())[:60]
                        for key, field in (("state", "state"), ("error", "error_code"))}
    return states


def run_gateway(request, requests):
    """Hermes' messenger gateway, until the parent shuts it down. Only its adapters run, inside this process:
    Telegram by long polling, Discord and Slack over a socket they open themselves, so nothing listens on a port.
    Hermes' own service around a gateway (its control socket, local API, dashboard, scheduler and task board, and
    the liveness socket other programs would probe) is not started."""
    import asyncio

    bundled_paths()
    for name in [name for name in os.environ if GATEWAY_OPENINGS.search(name)]:
        del os.environ[name]
    accounts = gateway_accounts(request)
    for name, ids in accounts.items():
        os.environ[f"{name.upper()}_ALLOWED_USERS"] = ",".join(ids)
    # A dangerous command asks in the messenger it came from and waits for the answer there.
    os.environ["HERMES_EXEC_ASK"] = "1"
    # Without it the Telegram adapter looks api.telegram.org up at two public DNS services of its own choosing.
    os.environ["HERMES_TELEGRAM_DISABLE_FALLBACK_IPS"] = "1"
    # The gateway's own words (a command to approve, a command refused) in the app's language.
    os.environ["HERMES_LANGUAGE"] = "ko"
    # The folder the agent's terminal and file tools start in, as in the chat.
    os.environ["TERMINAL_CWD"] = str(request.get("workspace") or HOME)
    policy = HOME / "gateway"
    policy.mkdir(exist_ok=True, mode=0o700)
    own_settings_only(policy)
    guard_files()
    config = prepare_home(request)
    write_config(policy / "config.yaml", gateway_policy(request, config, accounts))
    patch_model_calls()
    prepare_tools(request)
    tell_grants(messenger=True)

    from gateway.config import Platform
    from gateway.run import GatewayRunner, load_gateway_config_for_runner
    from tools.registry import registry

    register_app_tools(registry, request["app_tools"])
    loaded = load_gateway_config_for_runner()
    wanted = {Platform(name) for name in accounts}
    for platform in [platform for platform in loaded.platforms if platform not in wanted]:
        del loaded.platforms[platform]
    only_listed_accounts(GatewayRunner, accounts)
    offer_only({*GATEWAY_COMMANDS, "help", "whoami"})
    # The heartbeat serves programs that watch a Hermes service from outside, through a socket it would open.
    GatewayRunner._start_loop_heartbeat_task = lambda self: None

    async def no_setup_notes(self, *args, **kwargs):
        """Hermes answers a first message with its own setup: a profile to build, and a home channel to set with a
        command that is not among GATEWAY_COMMANDS. The app's settings screen is the setup."""

    GatewayRunner._hmwa_first_contact_notes = no_setup_notes

    async def serve():
        unknown = {name: {"state": "", "error": ""} for name in accounts}
        emit({"type": "gateway", "state": "starting", "platforms": unknown})
        if not accounts or set(loaded.platforms) != wanted:
            emit({"type": "gateway", "state": "failed", "platforms": unknown})
            return
        runner = GatewayRunner(loaded)
        ended = asyncio.ensure_future(runner.wait_for_shutdown())
        try:
            if not await runner.start() or not runner._running:
                emit({"type": "gateway", "state": "failed", "platforms": gateway_states(accounts)})
                return
            told = None
            while not ended.done():
                with contextlib.suppress(queue.Empty):
                    if requests.get_nowait().get("type") == "shutdown":
                        break
                for name in (".env", ".op.env"):
                    with contextlib.suppress(OSError):
                        (HOME / name).unlink()
                states = gateway_states(accounts)
                if states != told:
                    told = states
                    emit({"type": "gateway", "state": "running", "platforms": states})
                await asyncio.sleep(0.5)
        finally:
            ended.cancel()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(runner.stop(), 30)
            emit({"type": "gateway", "state": "stopped", "platforms": gateway_states(accounts)})

    asyncio.run(serve())


def reader_loop(requests):
    """The one stdin reader. Replies route to their waiting tool thread by id; a cancel stops the turn; a turn, a
    gateway or a shutdown goes to the main thread."""
    while True:
        line = INPUT.readline(MAX_FRAME + 1)
        if not line or len(line) > MAX_FRAME:
            CHANNEL.cancel()
            requests.put({"type": "shutdown"})
            return
        try:
            frame = json.loads(line)
        except ValueError:
            continue
        kind = frame.get("type")
        if kind in ("completion", "approval_reply", "app_tool_reply", "clarify_reply"):
            CHANNEL.deliver(frame.get("id", ""), frame)
        elif kind == "cancel":
            CHANNEL.cancel()
            agent = CURRENT_AGENT.get("agent")
            if agent is not None:
                with contextlib.suppress(Exception):
                    agent.interrupt(hard_cancel=True)
        elif kind in ("turn", "gateway", "shutdown"):
            requests.put(frame)


CURRENT_AGENT = {}


def main():
    requests = queue.Queue()
    reader = threading.Thread(target=reader_loop, args=(requests,), daemon=True)
    reader.start()
    # Upstream banners and warnings must never corrupt the framing. Persistent logs hold no credential.
    with open(os.devnull, "w", encoding="utf-8") as quiet, contextlib.redirect_stdout(quiet), \
            contextlib.redirect_stderr(quiet):
        logging.disable(logging.CRITICAL)
        while True:
            request = requests.get()
            if request.get("type") == "shutdown":
                return
            if request.get("type") == "gateway":
                # A gateway is this process's whole life: it ends with it, whatever is still running in its threads.
                try:
                    run_gateway(request, requests)
                except Exception as error:
                    emit({"type": "error", "kind": type(error).__name__})
                    emit({"type": "gateway", "state": "failed", "platforms": {}})
                OUTPUT.flush()
                os._exit(0)
            CHANNEL.reset()
            try:
                run_turn(request)
            except Cancelled:
                emit({"type": "result", "message": "", "actions": [], "cancelled": True})
            except Exception as error:
                emit({"type": "error", "kind": type(error).__name__})
            finally:
                CURRENT_AGENT.clear()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        emit({"type": "error", "kind": type(error).__name__})
        sys.exit(1)
