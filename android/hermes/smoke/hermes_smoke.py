"""Smoke test of the Android Hermes runtime, run by the app's HermesRuntimeTest. Not part of the app.

    libhermes_python.so -I -B hermes_smoke.py <runtime root>

It does what runtime/hermes/worker.py does, without the worker's protocol: it imports Hermes, gives AIAgent an
OpenAI client whose httpx transport answers from canned chat completions, and runs one conversation turn in which the
model calls one tool. Nothing leaves the process: name resolution and socket connections fail.

JSON lines on stdin and stdout:
    -> {"type": "ready", ...}              at start, before Hermes is imported
    <- {"type": "run"}                     -> {"type": "result", ...}
    <- {"type": "echo", "text": "..."}     -> {"type": "echo", "text": "..."}
    <- {"type": "hang"}                    blocks until the process is killed
    <- {"type": "shutdown"}                exits with status 0
"""

import contextlib
import importlib.util
import json
import logging
import os
import socket
import sys
import time
import uuid
from pathlib import Path

STARTED = time.monotonic()
ROOT = Path(sys.argv[1]).resolve()
HOME = Path(os.environ["HERMES_HOME"]).resolve()
sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")
INPUT, OUTPUT = sys.stdin, sys.stdout
TEXT = "안녕하세요, Hermes"


def emit(frame):
    OUTPUT.write(json.dumps(frame, ensure_ascii=False) + "\n")
    OUTPUT.flush()


def receive():
    line = INPUT.readline()
    if not line:
        sys.exit(3)
    return json.loads(line)


def offline():
    def refuse(*args, **kwargs):
        raise OSError("The smoke test has no network")

    socket.getaddrinfo = refuse

    def audit(event, args):
        if event in {"socket.connect", "socket.bind", "socket.sendto"}:
            raise PermissionError("The smoke test has no network")

    sys.addaudithook(audit)


def memory():
    """VmRSS and VmHWM (peak) of this process, in kilobytes."""
    values = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, value = line.partition(":")
        if key in {"VmRSS", "VmHWM"}:
            values[key] = int(value.split()[0])
    return values


def natives():
    """Imports and exercises every extension module of the runtime that Hermes and its dependencies use."""
    results = {}

    def check(name, probe):
        started = time.monotonic()
        try:
            results[name] = {"ok": True, "value": str(probe())[:200]}
        except Exception as error:  # noqa: BLE001 - the test reports which module failed and why
            results[name] = {"ok": False, "value": f"{type(error).__name__}: {error}"[:300]}
        results[name]["ms"] = round((time.monotonic() - started) * 1000)

    def ssl_probe():
        import ssl

        return ssl.OPENSSL_VERSION

    def sqlite_probe():
        import sqlite3

        with contextlib.closing(sqlite3.connect(":memory:")) as db:
            return db.execute("select sqlite_version(), json('[1,2]')").fetchone()

    def pydantic_probe():
        import pydantic
        import pydantic_core

        class Item(pydantic.BaseModel):
            name: str
            count: int

        return pydantic_core.__version__, Item.model_validate_json('{"name": "a", "count": 2}').count

    def jiter_probe():
        import jiter

        return jiter.from_json(b'{"a": [1, 2.5, "\\u00e9"]}')

    def cryptography_probe():
        from cryptography.fernet import Fernet
        from cryptography.hazmat.primitives.asymmetric import ec

        key = Fernet(Fernet.generate_key())
        ec.generate_private_key(ec.SECP256R1())
        return key.decrypt(key.encrypt(b"ok"))

    def cffi_probe():
        import cffi

        ffi = cffi.FFI()
        return cffi.__version__, ffi.new("int *", 7)[0]

    def pillow_probe():
        import io

        from PIL import Image, features

        image = Image.new("RGB", (64, 48), (200, 30, 90))
        sizes = {}
        for kind in ("JPEG", "PNG"):
            data = io.BytesIO()
            image.save(data, kind)
            sizes[kind] = Image.open(io.BytesIO(data.getvalue())).size
        return sizes, features.check("jpg"), features.check("zlib")

    def psutil_probe():
        import psutil

        process = psutil.Process()
        return process.memory_info().rss > 0, psutil.cpu_count(), len(psutil.disk_partitions())

    def yaml_probe():
        import _ruamel_yaml  # noqa: F401 - the C extension itself
        from ruamel.yaml import YAML

        return YAML(typ="safe").load("a: [1, 2]")

    def charset_probe():
        import charset_normalizer
        import charset_normalizer.md

        return charset_normalizer.from_bytes(TEXT.encode("utf-8")).best().encoding, charset_normalizer.md.__file__

    def markupsafe_probe():
        import markupsafe

        return markupsafe.escape("<a>"), importlib.util.find_spec("markupsafe._speedups").origin

    def web_probe():
        # Hermes' keyless web search: ddgs on primp (rustls, aws-lc) and lxml (libxml2, libxslt).
        import ddgs
        import primp
        from lxml import etree, html

        page = html.fromstring("<ul><li>하나</li><li>둘</li></ul>")
        etree.XSLT(etree.XML(
            '<xsl:stylesheet version="1.0" xmlns:xsl="http://www.w3.org/1999/XSL/Transform"/>'))
        return ddgs.__version__, primp.__name__, [item.text_content() for item in page.iter("li")]

    def zoneinfo_probe():
        import datetime
        import zoneinfo

        return datetime.datetime(2026, 1, 1, tzinfo=zoneinfo.ZoneInfo("Asia/Seoul")).utcoffset()

    for name, probe in [("ssl", ssl_probe), ("sqlite3", sqlite_probe), ("pydantic_core", pydantic_probe),
                        ("jiter", jiter_probe), ("cryptography", cryptography_probe), ("cffi", cffi_probe),
                        ("pillow", pillow_probe), ("psutil", psutil_probe), ("ruamel_yaml_clib", yaml_probe),
                        ("charset_normalizer", charset_probe), ("markupsafe", markupsafe_probe),
                        ("web", web_probe), ("zoneinfo", zoneinfo_probe)]:
        check(name, probe)
    return results


def prepare_home(model):
    """The worker's app-owned Hermes configuration (runtime/hermes/worker.py prepare_home)."""
    for name in ("memories", "skills", "sessions", "logs"):
        (HOME / name).mkdir(exist_ok=True, mode=0o700)
    config = {"model": {"default": model, "provider": "custom", "base_url": "http://127.0.0.1/v1",
                        "context_length": 65536},
              "compression": {"enabled": False}, "fallback_model": {}, "fallback_models": [],
              "memory": {"memory_enabled": False, "user_profile_enabled": False, "nudge_interval": 0},
              "agent": {"max_iterations": 3, "api_max_retries": 1, "auto_recovery_cycles": 0},
              "security": {"allow_lazy_installs": False}, "tools": {"tool_search": {"enabled": "off"}},
              "plugins": {"enabled": []}, "mcp_servers": {},
              "auxiliary": {"title_generation": {"enabled": False, "model_upgrade_enabled": False}},
              "checkpoints": {"enabled": False}}
    (HOME / "config.yaml").write_text(json.dumps(config, indent=2), encoding="utf-8")


def imported():
    """How much of the runtime's bytecode this process loaded: modules, and their .pyc bytes."""
    count = size = 0
    for module in list(sys.modules.values()):
        cached = getattr(module, "__cached__", None)
        if cached and str(cached).startswith(str(ROOT)) and os.path.exists(cached):
            count += 1
            size += os.path.getsize(cached)
    return {"modules": count, "pyc_bytes": size}


def completion(number, message, finish):
    return {"id": f"chatcmpl-smoke-{number}", "object": "chat.completion", "created": 1767225600,
            "model": "smoke-model", "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 32, "completion_tokens": 8, "total_tokens": 40}}


def run():
    timings = {}
    sys.path[0:0] = [str(ROOT / "source"), str(ROOT / "dependencies")]
    started = time.monotonic()
    import httpx
    from hermes_state import SessionDB
    from openai import OpenAI
    from run_agent import AIAgent
    from tools.registry import registry

    timings["import_ms"] = round((time.monotonic() - started) * 1000)

    calls, echoed = [], []
    replies = [
        completion(1, {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_smoke_1", "type": "function",
             "function": {"name": "smoke_echo", "arguments": json.dumps({"text": TEXT}, ensure_ascii=False)}}]},
            "tool_calls"),
        completion(2, {"role": "assistant", "content": f"smoke ok: {TEXT}"}, "stop"),
    ]

    class CannedTransport(httpx.BaseTransport):
        """worker.py's ParentTransport, answering from replies instead of asking the parent process."""

        def handle_request(self, request):
            if request.method != "POST" or request.url.path != "/v1/chat/completions":
                return httpx.Response(400, json={"error": {"message": "Unsupported Hermes API"}})
            body = json.loads(request.content)
            calls.append({"messages": len(body.get("messages", [])), "stream": bool(body.get("stream")),
                          "tools": sorted(tool["function"]["name"] for tool in body.get("tools") or [])})
            reply = replies[min(len(calls), len(replies)) - 1]
            if body.get("stream"):
                choice = reply["choices"][0]
                delta = dict(choice["message"])
                if delta.get("tool_calls"):
                    delta["tool_calls"] = [{"index": i, **call} for i, call in enumerate(delta["tool_calls"])]
                chunk = {**reply, "object": "chat.completion.chunk",
                         "choices": [{"index": 0, "delta": delta, "finish_reason": choice["finish_reason"]}]}
                return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                      content=("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode())
            return httpx.Response(200, json=reply)

    class SmokeClient(OpenAI):
        def platform_headers(self):
            return {}

    class SmokeAgent(AIAgent):
        def _create_openai_client(self, kwargs, **unused):
            return SmokeClient(api_key="smoke", base_url="http://127.0.0.1/v1", max_retries=0,
                               http_client=httpx.Client(transport=CannedTransport()))

    def echo(args, **unused):
        echoed.append(args.get("text"))
        return json.dumps({"echo": args.get("text")}, ensure_ascii=False)

    schema = {"name": "smoke_echo", "description": "Echoes the text back.",
              "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                             "additionalProperties": False}}
    registry.register(name="smoke_echo", toolset="smoke", schema=schema, handler=echo)
    prepare_home("smoke-model")

    started = time.monotonic()
    database = SessionDB(HOME / "state.db")
    agent = SmokeAgent(model="smoke-model", provider="custom", api_mode="chat_completions", api_key="smoke",
                       base_url="http://127.0.0.1/v1", enabled_toolsets=["smoke"], max_iterations=3, max_tokens=1024,
                       quiet_mode=True, save_trajectories=False, verbose_logging=False, skip_context_files=True,
                       load_soul_identity=False, skip_memory=True, skip_background_review=True, fallback_model={},
                       session_db=database, session_id="smoke_" + uuid.uuid4().hex, cwd=str(HOME),
                       platform="private_agent", ephemeral_system_prompt="You are a smoke test. Call smoke_echo once.",
                       request_overrides={"stream": False}, run_budget_seconds=120)
    tools = sorted(tool["function"]["name"] for tool in agent.tools)
    timings["agent_ms"] = round((time.monotonic() - started) * 1000)
    started = time.monotonic()
    try:
        result = agent.run_conversation(f"Echo this with smoke_echo: {TEXT}")
    finally:
        agent.close()
        database.close()
    timings["turn_ms"] = round((time.monotonic() - started) * 1000)
    started = time.monotonic()
    modules = natives()
    timings["natives_ms"] = round((time.monotonic() - started) * 1000)
    final = result.get("final_response")
    return {"type": "result", "final": final if isinstance(final, str) else None,
            "completed": bool(result.get("completed")), "error": str(result.get("error") or "")[:300],
            "tools": tools, "calls": calls, "echoed": echoed, "natives": modules, "timings": timings,
            "memory_kb": memory(), "imported": imported()}


def main():
    offline()
    emit({"type": "ready", "python": sys.version.split()[0], "platform": sys.platform,
          "machine": os.uname().machine, "executable": sys.executable, "prefix": sys.prefix,
          "flags": {"isolated": sys.flags.isolated, "dont_write_bytecode": sys.flags.dont_write_bytecode},
          "magic": importlib.util.MAGIC_NUMBER.hex(), "startup_ms": round((time.monotonic() - STARTED) * 1000),
          "memory_kb": memory()})
    while True:
        request = receive()
        kind = request.get("type")
        if kind == "run":
            # Upstream banners and warnings must not corrupt the frames: they go to stderr, like worker.py's quiet.
            with contextlib.redirect_stdout(sys.stderr):
                logging.disable(logging.CRITICAL)
                frame = run()
            emit(frame)
        elif kind == "echo":
            emit({"type": "echo", "text": request.get("text")})
        elif kind == "hang":
            time.sleep(3600)
        elif kind == "shutdown":
            return


if __name__ == "__main__":
    main()
