"""Private Agent's stdio host for the pinned Hermes AIAgent (never the user's CLI).

No provider credentials enter this process. An OpenAI SDK transport asks the parent
to perform inference using its immutable snapshot of the selected connection.
"""
import contextlib
import json
import logging
import os
from pathlib import Path
import socket
import sys


ROOT = Path(__file__).resolve().parent
HOME = Path(os.environ["HERMES_HOME"]).resolve()
# -I deliberately ignores PYTHONIOENCODING. Windows pipes otherwise use the
# user's code page, which cannot represent every prompt or tool argument.
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
INPUT, OUTPUT = sys.stdin, sys.stdout
MAX_FRAME = 2_000_000


@contextlib.contextmanager
def home_lock():
    with (HOME / ".private-agent.lock").open("a+b") as lock:
        if os.name == "nt":
            import msvcrt
            if not lock.tell():
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise BlockingIOError("home in use") from None
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def prepare_home(model):
    for name in ("memories", "skills", "sessions", "logs"):
        (HOME / name).mkdir(exist_ok=True, mode=0o700)
    for name, content in {"SOUL.md": "You assist the user through Private Agent's reviewed actions.\n",
                          "memories/MEMORY.md": "", "memories/USER.md": ""}.items():
        try:
            with (HOME / name).open("x", encoding="utf-8") as stream:
                stream.write(content)
        except FileExistsError:
            pass
    # JSON is valid YAML. This is app-owned, non-secret configuration, regenerated
    # for each selected connection; personalization and sessions are preserved.
    config = {"model": {"default": model, "provider": "custom", "base_url": "http://127.0.0.1/v1", "context_length": 65536},
              "compression": {"enabled": False}, "fallback_model": {}, "fallback_models": [],
              "memory": {"memory_enabled": True, "user_profile_enabled": True, "nudge_interval": 0},
              "agent": {"max_iterations": 3, "api_max_retries": 1, "auto_recovery_cycles": 0,
                        "coding_context": "off", "task_completion_guidance": False,
                        "parallel_tool_call_guidance": False, "tool_use_enforcement": False,
                        "execution_guidance": False},
              "security": {"allow_lazy_installs": False},
              "tools": {"tool_search": {"enabled": "off"}},
              "plugins": {"enabled": []}, "mcp_servers": {},
              "auxiliary": {"title_generation": {"enabled": False, "model_upgrade_enabled": False}},
              "checkpoints": {"enabled": False}}
    (HOME / "config.yaml").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def emit(frame):
    OUTPUT.write(json.dumps(frame, ensure_ascii=False) + "\n")
    OUTPUT.flush()


def receive():
    line = INPUT.readline(MAX_FRAME + 1)
    if not line or len(line) > MAX_FRAME:
        raise RuntimeError("invalid parent frame")
    return json.loads(line)


def isolate():
    """Constrain incidental upstream I/O as well as its configured home.

    This is a compatibility boundary, not an OS sandbox for untrusted Python.
    Only the bundled app tool is exposed; no terminal, plugins, or MCP discovery.
    """
    read_roots = (ROOT, HOME, Path(sys.base_prefix).resolve())
    null_path = Path(os.devnull).resolve()

    def allowed(path, roots):
        if isinstance(path, int) or path is None:
            return True
        resolved = Path(os.fsdecode(path)).resolve()
        return resolved == null_path or any(resolved.is_relative_to(root) for root in roots)

    def audit(event, args):
        if event == "open":
            path, mode, flags = args
            writing = flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            if not writing and isinstance(path, (str, bytes)) and os.fsdecode(path) in {"/dev/urandom", "/dev/random", "/etc/localtime"}:
                return
            if not allowed(path, (HOME,) if writing else read_roots):
                raise PermissionError("Hermes file access outside its private runtime/home")
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.chmod", "os.utime"}:
            if not allowed(args[0], (HOME,)):
                raise PermissionError("Hermes write outside its private home")
        elif event in {"os.rename", "os.link", "os.symlink"}:
            if not all(allowed(path, (HOME,)) for path in args[:2]):
                raise PermissionError("Hermes move outside its private home")
        elif event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "socket.connect", "socket.bind"}:
            raise PermissionError("Hermes uses only the Private Agent stdio bridge")

    sys.addaudithook(audit)
    # Resolve no external hosts, even for optional model metadata discovery.
    def offline(*args, **kwargs):
        raise OSError("Hermes network is owned by Private Agent")
    socket.getaddrinfo = offline


def run(request):
    sys.path.insert(0, str(ROOT / "dependencies"))
    sys.path.insert(0, str(ROOT / "source"))
    prepare_home(request["model"])
    import httpx
    from openai import OpenAI
    from run_agent import AIAgent
    from hermes_state import SessionDB
    from tools.registry import registry

    class ParentTransport(httpx.BaseTransport):
        def handle_request(self, req):
            if req.method != "POST" or req.url.path != "/v1/chat/completions":
                return httpx.Response(400, json={"error": {"message": "Unsupported Hermes API"}})
            body = json.loads(req.content)
            # The app streams the selected API to its UI; Hermes receives the complete
            # lossless message, including all tool calls. It never opens an HTTP port.
            emit({"type": "inference", "body": body})
            reply = receive()
            if reply.get("type") != "completion":
                raise RuntimeError("invalid inference response")
            if body.get("stream"):
                complete = reply["body"]
                choice = complete["choices"][0]
                delta = dict(choice["message"])
                if delta.get("tool_calls"):
                    delta["tool_calls"] = [{"index": i, **call} for i, call in enumerate(delta["tool_calls"])]
                chunk = {**complete, "object": "chat.completion.chunk", "choices": [
                    {"index": 0, "delta": delta, "finish_reason": choice["finish_reason"]}]}
                return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                      content=("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
            return httpx.Response(200, json=reply["body"])

    class BridgeClient(OpenAI):
        def platform_headers(self):
            # Transport is stdio; no provider needs SDK OS discovery (which can read
            # global files or launch system utilities outside the private runtime).
            return {}

    class DesktopAgent(AIAgent):
        def _create_openai_client(self, kwargs, **unused):
            return BridgeClient(api_key="private-agent", base_url="http://127.0.0.1/v1",
                          max_retries=0, http_client=httpx.Client(transport=ParentTransport()))

    prepared = []
    schema = {"name": "prepare_actions", "description": "Prepare Private Agent actions for review. Does not execute them.",
              "parameters": {"type": "object", "properties": {
                  "actions": {"type": "array", "maxItems": 4, "items": {
                      "type": "object", "properties": {"tool": {"type": "string", "enum": request["tools"]},
                                                          "args": {"type": "object"}},
                      "required": ["tool", "args"], "additionalProperties": False}}},
                  "required": ["actions"], "additionalProperties": False}}

    def prepare(args, **unused):
        if prepared:
            return json.dumps({"error": "A plan is already prepared; finish this turn."})
        actions = args.get("actions")
        if not isinstance(actions, list) or len(actions) > 4:
            return json.dumps({"error": "Use zero to four actions."})
        prepared.append(actions)
        return json.dumps({"status": "prepared_for_app_review", "executed": False})

    registry.register(name="prepare_actions", toolset="private_agent", schema=schema, handler=prepare)
    database = SessionDB(HOME / "state.db")
    agent = DesktopAgent(model=request["model"], provider="custom", api_mode="chat_completions",
                         api_key="private-agent", base_url="http://127.0.0.1/v1",
                         enabled_toolsets=["private_agent"], max_iterations=3, max_tokens=4096,
                         quiet_mode=True, save_trajectories=False, verbose_logging=False,
                         skip_context_files=True, load_soul_identity=True, skip_memory=False,
                         skip_background_review=True, fallback_model={}, session_db=database,
                         session_id=request["session_id"], cwd=str(HOME), platform="private_agent",
                         ephemeral_system_prompt=request["system"],
                         request_overrides={"stream": False}, run_budget_seconds=120)
    # Fail closed if an upstream release changes toolset selection.
    if {tool["function"]["name"] for tool in agent.tools} != {"prepare_actions"}:
        raise RuntimeError("Unexpected Hermes toolset")
    try:
        result = agent.run_conversation(request["context"])
        final = result.get("final_response", "")
        if result.get("error") or not result.get("completed", False):
            raise RuntimeError("Hermes did not complete the turn")
        if prepared:
            # Hermes' words after staging the plan go with the cards; the app checks both.
            plan = {"message": final if isinstance(final, str) else "", "actions": prepared[0]}
        else:
            # Some gateways answer with the JSON plan the system prompt describes instead
            # of a native tool call. The same app validator checks that final response.
            try:
                plan = json.loads(final)
            except (ValueError, TypeError):
                plan = {"message": final, "actions": []}
    finally:
        agent.close()
        database.close()
    emit({"type": "result", "plan": plan})


if __name__ == "__main__":
    try:
        request = receive()
        isolate()
        # Upstream banners and warnings must never corrupt the framing or expose raw
        # model errors. Persistent logs have no credentials: only the parent has them.
        with open(os.devnull, "w", encoding="utf-8") as quiet, contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
            logging.disable(logging.CRITICAL)
            with home_lock():
                while request.get("type") != "shutdown":
                    run(request)
                    request = receive()
    except Exception as error:
        emit({"type": "error", "kind": type(error).__name__})
        sys.exit(1)
