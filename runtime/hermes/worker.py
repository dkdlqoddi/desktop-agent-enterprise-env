"""Private Agent's stdio host for the pinned Hermes AIAgent (never the user's CLI).

The backend starts this with the bundled CPython and talks to it in JSON lines over stdin/stdout. No provider
credential enters this process: every model request the agent makes — the main turn, and the auxiliary tasks
(smart approvals, vision, compression, titles) and any subagent — is routed back to the parent as an "inference"
frame, which performs it with the app's own LLM connection. The agent's tools run here (terminal, files, code,
browser, MCP, memory, skills, computer use, web search); the app's own actions (document search, web records,
tasks, schedules) run in the parent, reached as "app_tool" frames. Dangerous commands ask the user through an
"approval" frame; clarify questions through a "clarify" frame; progress and draft text stream up as "progress"
and "draft" frames. One worker per Wiki, reused for the next turn; its home is the Wiki's hermes folder.

Frames to the parent: inference, approval, app_tool, clarify, progress, draft, result, error.
Frames from the parent: turn, completion, approval_reply, app_tool_reply, clarify_reply, cancel, shutdown.
"""
import contextlib
import json
import logging
import os
import queue
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


def prepare_home(request):
    """The agent's home and app-owned config for this turn. JSON is valid YAML; it carries no secret (the API key
    never reaches here) and is regenerated for each selected connection, while memories, skills and sessions stay."""
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
    config = {
        "model": {"default": model, "provider": "custom", "base_url": base, "context_length": 65536},
        "compression": {"enabled": True},
        "fallback_model": {}, "fallback_models": [], "fallback_providers": [],
        "memory": {"memory_enabled": True, "user_profile_enabled": True, "nudge_interval": 0},
        "agent": {"max_iterations": request.get("max_iterations", 24), "api_max_retries": 1,
                  "auto_recovery_cycles": 0, "clarify_timeout": 600},
        "approvals": {"mode": "smart", "timeout": 300, "cron_mode": "deny", "unattended_mode": "deny"},
        "security": {"allow_lazy_installs": False},
        "tools": {"tool_search": {"enabled": "off"}},
        "auxiliary": auxiliary,
        "web": {"search_backend": "ddgs", "extract_backend": settings.get("extract_backend", "ddgs")},
        "browser": {"executable_path": chromium} if chromium else {},
        "computer_use": {"enabled": bool(settings.get("computer_use", True))},
        "mcp_servers": settings.get("mcp_servers", {}),
        "plugins": {"enabled": []},
        "checkpoints": {"enabled": False},
    }
    (HOME / "config.yaml").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
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
            for key in ("documents", "records", "ads", "products", "capture_id", "task_id", "routine_id", "url"):
                if key in result:
                    payload[key] = result[key]
            return json.dumps(payload, ensure_ascii=False)

        return handler

    for tool in tools:
        registry.register(name=tool["name"], toolset="private_agent",
                          schema={"name": tool["name"], "description": tool["description"],
                                  "parameters": tool["parameters"]},
                          handler=make(tool["name"]))


def approval_callback(command, description="", *, allow_permanent=True, allow_session=True, smart_denied=False, **kw):
    """A dangerous command the smart guardian escalated: ask the user and wait. deny/timeout/raise block; any other
    value runs it (once)."""
    try:
        reply = CHANNEL.request("approval", {"command": str(command)[:4000], "description": str(description)[:2000],
                                             "smart_denied": bool(smart_denied)})
    except Cancelled:
        return "deny"
    decision = (reply or {}).get("decision", "deny")
    return decision if decision in ("once", "session", "always", "deny") else "deny"


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

    database = SessionDB(HOME / "state.db")
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


def run_turn(request):
    sys.path.insert(0, str(ROOT / "dependencies"))
    sys.path.insert(0, str(ROOT / "source"))
    os.environ["PYTHONPATH"] = str(ROOT / "dependencies")

    config = prepare_home(request)
    patch_model_calls()
    with contextlib.suppress(Exception):
        from tools.skills_sync import sync_skills

        sync_skills(quiet=True)
    if request["settings"].get("mcp_servers"):
        with contextlib.suppress(Exception):
            from tools.mcp_tool_discovery import discover_mcp_tools

            discover_mcp_tools(list(request["settings"]["mcp_servers"]))

    agent, database = build_agent(request, config)
    CURRENT_AGENT["agent"] = agent
    try:
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


def reader_loop(requests):
    """The one stdin reader. Replies route to their waiting tool thread by id; a cancel stops the turn, a turn or a
    shutdown goes to the main thread."""
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
        elif kind in ("turn", "shutdown"):
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
