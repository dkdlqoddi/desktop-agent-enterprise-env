import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.db import Database
from agent.hermes import Hermes, child_environment
from agent.hermes_llm import SelectedLLM, anthropic_messages, completion
from agent.llm import LLM, UserError
from agent.service import Service

ROOT = Path(__file__).resolve().parents[2]
HAS_RUNTIME = (ROOT / "dist/hermes/manifest.json").is_file()
bundled = pytest.mark.skipif(not HAS_RUNTIME, reason="npm run build:hermes builds the bundled Hermes these tests run")
SECRETS = ("test-secret", "header-secret")


@pytest.fixture
def model_server():
    state = SimpleNamespace(requests=[], headers=[], reply=None, delay=0.0)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.requests.append(request)
            state.headers.append(dict(self.headers))
            time.sleep(state.delay)
            status, value, streaming, *extra = state.reply(request)
            body = value.encode() if isinstance(value, str) else json.dumps(value).encode()
            self.send_response(status)
            for name, header in (extra[0] if extra else {}).items():
                self.send_header(name, header)
            self.send_header("Content-Type", "text/event-stream" if streaming else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.url = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def connection(server, api_format="openai"):
    llm = LLM()
    llm.update(
        {
            "api_format": api_format,
            "base_url": server.url,
            "model_name": "test-model",
            "api_key": "test-secret",
            "headers": [{"name": "X-Custom", "value": "header-secret"}],
        }
    )
    return llm


def sse(*values):
    return "".join("data: " + (v if isinstance(v, str) else json.dumps(v)) + "\n\n" for v in values)


PLAN = {"actions": [{"tool": "web_capture", "args": {"site": "coupang", "query": "노트북"}}]}


def openai_tool_reply(request):
    if any(m["role"] == "tool" for m in request["messages"]):
        return 200, completion({"content": "쿠팡 검색을 준비했습니다."}, request["model"]), False
    call = {"id": "call_1", "type": "function", "function": {"name": "prepare_actions", "arguments": json.dumps(PLAN)}}
    return 200, completion({"content": None, "tool_calls": [call]}, request["model"]), False


def anthropic_tool_reply(request):
    if any(
        block.get("type") == "tool_result"
        for m in request["messages"]
        if isinstance(m["content"], list)
        for block in m["content"]
    ):
        return 200, {"content": [{"type": "text", "text": "준비했습니다."}], "usage": {}}, False
    return 200, {"content": [{"type": "tool_use", "id": "toolu_1", "name": "prepare_actions", "input": PLAN}]}, False


def test_environment_drops_user_hermes_provider_python_and_proxy_settings(tmp_path, monkeypatch):
    for key in ("HERMES_HOME", "HERMES_PROFILE", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "PYTHONPATH", "HTTP_PROXY"):
        monkeypatch.setenv(key, "personal-secret")
    env = child_environment(tmp_path)
    assert "personal-secret" not in str(env)
    assert env["HERMES_HOME"] == str(tmp_path)
    assert "PATH" not in env


@pytest.mark.parametrize("prefix", ['{"actions":', "<think>"])
def test_long_control_text_never_becomes_a_visible_draft(tmp_path, prefix):
    hermes = Hermes(SimpleNamespace(db=SimpleNamespace(path=str(tmp_path / "wiki.db"))))
    hermes.on_text(prefix + "private-control" * 1200, hermes.cancelled)
    hermes.on_text("tail", hermes.cancelled)
    assert hermes.status({"request_id": ""})["draft"] == ""


def test_tool_messages_convert_to_anthropic_without_losing_ids():
    system, messages = anthropic_messages(
        [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "x", "function": {"name": "prepare_actions", "arguments": '{"actions":[]}'}}],
            },
            {"role": "tool", "tool_call_id": "x", "content": "prepared"},
        ]
    )
    assert system == "rules"
    assert messages[1]["content"][0] == {
        "type": "tool_use",
        "id": "x",
        "name": "prepare_actions",
        "input": {"actions": []},
    }
    assert messages[2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "x", "content": "prepared"}],
    }


def test_a_run_keeps_the_settings_it_started_with_and_sends_tools(model_server):
    llm = connection(model_server)
    selected = SelectedLLM(llm, threading.Event())
    llm.update({"model_name": "other-model"})
    model_server.reply = lambda request: (200, completion({"content": "ok"}, request["model"]), False)
    tool = {"type": "function", "function": {"name": "prepare_actions", "parameters": {"type": "object"}}}
    reply = selected.complete({"model": "ignored", "messages": [{"role": "user", "content": "hi"}], "tools": [tool]})
    assert reply["choices"][0]["message"]["content"] == "ok"
    sent, headers = model_server.requests[0], model_server.headers[0]
    assert sent["model"] == "test-model" and sent["tools"] == [tool]
    assert headers["Authorization"] == "Bearer test-secret" and headers["X-Custom"] == "header-secret"


def test_a_redirect_is_not_followed_with_the_key(model_server):
    llm = connection(model_server)
    model_server.reply = lambda request: (302, "", False, {"Location": "https://elsewhere.example.test/"})
    with pytest.raises(UserError, match="HTTP 302"):
        SelectedLLM(llm, threading.Event()).complete({"messages": [{"role": "user", "content": "hi"}]})
    assert len(model_server.requests) == 1


def test_a_streamed_openai_tool_call_is_put_back_together(model_server):
    llm = connection(model_server)
    arguments = json.dumps(PLAN)
    chunks = [
        {
            "choices": [
                {"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "prepare_"}}]}}
            ]
        },
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [{"index": 0, "function": {"name": "actions", "arguments": arguments[:10]}}]
                    },
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": arguments[10:]}}]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
        "[DONE]",
    ]
    model_server.reply = lambda request: (200, sse(*chunks), True)
    reply = SelectedLLM(llm, threading.Event()).complete({"messages": [{"role": "user", "content": "hi"}]})
    call = reply["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["name"] == "prepare_actions" and json.loads(call["function"]["arguments"]) == PLAN


def chat(tmp_path, llm):
    return Service(Database(tmp_path / "wiki.db"), llm)


@bundled
@pytest.mark.parametrize(("api_format", "reply"), [("openai", openai_tool_reply), ("anthropic", anthropic_tool_reply)])
def test_bundled_hermes_plans_and_nothing_runs_before_approval(tmp_path, model_server, api_format, reply):
    model_server.reply = reply
    service = chat(tmp_path, connection(model_server, api_format))
    assert service.agent.engine() == {"engine": "hermes", "ready": True}
    calls = []
    service.web.capture = lambda *args, **kwargs: calls.append(args)
    try:
        sent = service.dispatch("agent.send", {"text": "쿠팡에서 노트북 광고 기록해 줘", "request_id": "r1"})
    finally:
        service.agent.shutdown()
    assert sent["reply"]["engine"] == "hermes"
    # What Hermes said once the plan was staged goes with the cards.
    assert sent["reply"]["content"] in ("쿠팡 검색을 준비했습니다.", "준비했습니다.")
    card = sent["reply"]["actions"][0]
    assert card["tool"] == "web_capture" and card["status"] == "pending" and card["args"]["site"] == "coupang"
    assert calls == []
    # The model saw the tool and the app's rules; the secrets stayed in the backend.
    first = model_server.requests[0]
    names = [tool["function"]["name"] if "function" in tool else tool["name"] for tool in first["tools"]]
    assert names == ["prepare_actions"]
    home = tmp_path / "hermes"
    written = "".join(path.read_text(encoding="utf-8", errors="ignore") for path in home.rglob("*") if path.is_file())
    assert not any(secret in written for secret in SECRETS)


@bundled
def test_bundled_hermes_stops_when_asked(tmp_path, model_server):
    model_server.delay = 30
    model_server.reply = openai_tool_reply
    service = chat(tmp_path, connection(model_server))
    outcome = {}

    def send():
        outcome["sent"] = service.dispatch("agent.send", {"text": "구글 laptop", "request_id": "r-stop"})

    thread = threading.Thread(target=send)
    thread.start()
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and service.agent.status({"request_id": "r-stop"})["phase"] != "thinking":
        time.sleep(0.1)
    while time.monotonic() < deadline and not model_server.requests:
        time.sleep(0.1)
    assert service.agent.stop({"request_id": "r-stop"}) == {"stopped": True}
    thread.join(30)
    try:
        assert not thread.is_alive()
        assert "중지" in outcome["sent"]["reply"]["content"] and outcome["sent"]["reply"]["actions"] == []
    finally:
        service.agent.shutdown()
