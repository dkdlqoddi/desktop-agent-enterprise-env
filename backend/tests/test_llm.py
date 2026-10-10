import contextlib
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests
from requests.structures import CaseInsensitiveDict

from agent import diagnostics, llm as module
from agent.llm import (
    BATCH,
    ENVIRONMENT,
    INTERACTIVE,
    LLM,
    Cancel,
    Cancelled,
    LLMFormatError,
    LLMRequestError,
    LLMTruncatedError,
    Slots,
    UserError,
    json_object,
    priority,
    shown,
)

OPENAI_REPLY = {"choices": [{"message": {"content": "연결 성공"}}]}
HELLO = [{"role": "user", "content": "hi"}]


class Response:
    def __init__(self, status=200, body=None, headers=None, chunks=()):
        self.status_code, self.body, self.chunks = status, body, list(chunks)
        self.headers = CaseInsensitiveDict(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def json(self):
        if self.status_code != 200:
            raise AssertionError("Error body must not be read or disclosed")
        return self.body

    def iter_content(self, chunk_size=None):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


def events(*items, split=7):
    """A streamed answer: each item a server-sent event's data, cut into chunks of `split` bytes, as a network may."""
    data = b"".join(
        b"data: " + (item if isinstance(item, str) else json.dumps(item)).encode() + b"\n\n" for item in items
    )
    return Response(
        headers={"Content-Type": "text/event-stream"},
        chunks=[data[start : start + split] for start in range(0, len(data), split)],
    )


def openai_delta(text=None, finish=None):
    return {"choices": [{"delta": {} if text is None else {"content": text}, "finish_reason": finish}]}


def answer(monkeypatch, *replies):
    """Answers the LLM requests with the replies in turn, the last one from then on, and keeps the requests. A reply
    that is an exception is raised instead, as a connection that fails."""
    calls = []

    def post(session, url, **kwargs):
        calls.append({"url": url, **kwargs})
        reply = replies[min(len(calls), len(replies)) - 1]
        if isinstance(reply, BaseException):
            raise reply
        return reply

    monkeypatch.setattr("agent.llm.requests.Session.post", post)
    return calls


@pytest.fixture(autouse=True)
def no_llm_environment(monkeypatch):
    """A developer's own LLM settings in the environment change nothing here."""
    for name in ENVIRONMENT.values():
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("PRIVATE_AGENT_" + name, raising=False)


@pytest.fixture(autouse=True)
def waits(monkeypatch):
    """The waits between attempts, kept instead of slept."""
    waited = []
    monkeypatch.setattr(LLM, "pause", staticmethod(lambda seconds, cancel=None: waited.append(seconds)))
    return waited


@pytest.fixture
def log(tmp_path):
    """The backend's log, as Electron has the backend write it. pytest's own file handler, which it adds to every logger
    that does not propagate, is set aside meanwhile: diagnostics.start() adds none beside another."""
    others = [handler for handler in diagnostics.LOG.handlers if isinstance(handler, logging.FileHandler)]
    for handler in others:
        diagnostics.LOG.removeHandler(handler)
    folder = tmp_path / "logs"
    diagnostics.start(folder)
    yield folder / "backend.log"
    for handler in list(diagnostics.LOG.handlers):
        if isinstance(handler, logging.FileHandler):
            diagnostics.LOG.removeHandler(handler)
            handler.close()
    for handler in others:
        diagnostics.LOG.addHandler(handler)


def general(**values):
    llm = LLM()
    llm.update(
        {
            "provider": "general",
            "base_url": "https://llm.example/v1/chat/completions",
            "api_key": "sk-test",
            "model_name": "test-model",
            **values,
        }
    )
    return llm


def test_the_key_goes_in_its_format_s_header_or_where_the_headers_put_it(monkeypatch):
    calls = answer(monkeypatch, Response(body=OPENAI_REPLY))
    llm = general(headers=[{"name": "OpenAI-Organization", "value": "org-1"}, {"name": " ", "value": ""}])
    # The screens and AI tools read the headers' names and whether each has a value, never the values, which the
    # main process alone reads to keep them encrypted.
    assert llm.public()["headers"] == [{"name": "OpenAI-Organization", "has_value": True, "uses_api_key": False}]
    assert llm.secrets() == {"api_key": "sk-test", "headers": [{"name": "OpenAI-Organization", "value": "org-1"}]}
    llm.complete(HELLO)
    assert calls[-1]["headers"] == {"Authorization": "Bearer sk-test", "OpenAI-Organization": "org-1"}
    assert calls[-1]["json"] == {"model": "test-model", "messages": HELLO, "temperature": 0.5, "stream": False}
    # A header with {api_key} takes the key instead of Authorization; every {uuid} is a new ID.
    llm.update({"headers": {"api-key": "{api_key}", "X-Request-Id": "req-{uuid}"}})
    llm.complete(HELLO)
    llm.complete(HELLO)
    first, second = calls[-2]["headers"], calls[-1]["headers"]
    assert first["api-key"] == "sk-test" and "Authorization" not in first
    assert first["X-Request-Id"].startswith("req-") and len(first["X-Request-Id"]) == 40
    assert first["X-Request-Id"] != second["X-Request-Id"]
    # A header named like a default one, in any case, is sent instead of it.
    llm.update({"headers": [{"name": "authorization", "value": "Basic abc"}]})
    llm.complete(HELLO)
    assert calls[-1]["headers"] == {"authorization": "Basic abc"}
    # Without a key, a server that needs none gets no authentication header.
    llm.update({"api_key": "", "headers": []})
    llm.complete(HELLO)
    assert calls[-1]["headers"] == {}
    assert "sk-test" not in json.dumps(llm.public())
    llm.update({"headers": [{"name": "X-Team", "value": "team-secret-123"}, {"name": "api-key", "value": "{api_key}"}]})
    assert "team-secret-123" not in json.dumps(llm.public())
    assert llm.public()["headers"] == [
        {"name": "X-Team", "has_value": True, "uses_api_key": False},
        {"name": "api-key", "has_value": True, "uses_api_key": True},
    ]


def test_a_header_sent_without_its_value_keeps_the_saved_one(monkeypatch):
    # An AI tool reads the headers' names only: it changes the model and sends the settings back as it read them.
    calls = answer(monkeypatch, Response(body=OPENAI_REPLY))
    llm = general(headers=[{"name": "X-Team", "value": "team-secret-123"}, {"name": "api-key", "value": "{api_key}"}])
    read = {**llm.public(), "headers": [{"name": header["name"]} for header in llm.public()["headers"]]}
    llm.update({**read, "model_name": "other-model"})
    assert llm.secrets()["headers"] == [
        {"name": "X-Team", "value": "team-secret-123"},
        {"name": "api-key", "value": "{api_key}"},
    ]
    llm.complete(HELLO)
    assert (calls[-1]["headers"]["X-Team"], calls[-1]["json"]["model"]) == ("team-secret-123", "other-model")
    # By its name in any case; a new header without a value is empty, and the screen's values are taken as sent.
    llm.update({"headers": [{"name": "x-team"}, {"name": "X-New"}]})
    assert llm.secrets()["headers"] == [{"name": "x-team", "value": "team-secret-123"}, {"name": "X-New", "value": ""}]
    llm.update({"headers": [{"name": "X-Team", "value": ""}]})
    assert llm.secrets()["headers"] == [{"name": "X-Team", "value": ""}]


def test_the_key_and_the_header_values_stay_with_the_server_they_were_saved_for(monkeypatch):
    calls = answer(monkeypatch, Response(body=OPENAI_REPLY))
    headers = [{"name": "X-Team", "value": "team-secret-123"}, {"name": "api-key", "value": "{api_key}"}]
    names = [{"name": header["name"]} for header in headers]
    llm = general(headers=headers)
    # Another path of the same server keeps them.
    settings = llm.update({"base_url": "https://LLM.example:443/v2/chat/completions", "headers": names})
    assert (settings["has_api_key"], llm.secrets()["headers"]) == (True, headers)
    # Another server, e.g. a mistyped one: the saved key is not sent there, nor are header values not sent again.
    for change in (
        {"base_url": "https://other.example/v1/chat/completions"},
        {"base_url": "https://other.example/v1/chat/completions", "headers": [{"name": "X-Team"}]},
    ):
        with pytest.raises(UserError, match="custom header 값을 모두 함께"):
            llm.update(change)
    assert llm.public()["base_url"] == "https://LLM.example:443/v2/chat/completions"
    settings = llm.update({"base_url": "http://llm.example/v2/chat/completions", "headers": headers})
    assert (settings["has_api_key"], settings["configured"]) == (False, False)
    with pytest.raises(UserError, match="API Key"):
        llm.complete(HELLO)
    assert calls == []
    # A key typed with the change goes to the new server.
    llm.update({"base_url": "https://other.example/v1/chat/completions", "api_key": "sk-other", "headers": headers})
    llm.complete(HELLO)
    assert (calls[-1]["url"], calls[-1]["headers"]["api-key"]) == (
        "https://other.example/v1/chat/completions",
        "sk-other",
    )
    # Saved settings, read back at the start, are taken as they are.
    assert (
        LLM().load(
            {"base_url": "https://llm.example/v1", "api_key": "sk-test", "model_name": "m", "provider": "general"}
        )["has_api_key"]
        is True
    )


def test_a_header_that_needs_the_key_waits_for_it(monkeypatch):
    calls = answer(monkeypatch, Response(body=OPENAI_REPLY))
    llm = general(api_key="", headers=[{"name": "api-key", "value": "{api_key}"}])
    assert not llm.public()["configured"]
    with pytest.raises(UserError, match="API Key"):
        llm.complete(HELLO)
    assert calls == []


def test_anthropic_messages_api(monkeypatch):
    calls = answer(
        monkeypatch,
        Response(
            body={
                "content": [
                    {"type": "thinking", "thinking": ""},
                    {"type": "text", "text": "연결 "},
                    {"type": "text", "text": "성공"},
                ],
                "stop_reason": "end_turn",
            }
        ),
    )
    llm = general(
        api_format="anthropic",
        base_url="https://api.anthropic.com/v1/messages",
        api_key="sk-ant",
        model_name="claude-opus-5-5",
    )
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert llm.complete(messages, temperature=0.1) == "연결 성공"
    assert calls[-1]["headers"] == {"anthropic-version": "2023-06-01", "x-api-key": "sk-ant"}
    # The system prompt is a field of its own, and current Claude models take no temperature.
    assert calls[-1]["json"] == {
        "model": "claude-opus-5-5",
        "max_tokens": 16000,
        "system": "sys",
        "messages": [{"role": "user", "content": "hi"}],
    }
    answer(monkeypatch, Response(body={"content": [], "stop_reason": "refusal"}))
    with pytest.raises(UserError, match="답하지 않았습니다"):
        llm.complete(messages)
    # An answer in the other format names where the answer was expected.
    answer(monkeypatch, Response(body=OPENAI_REPLY))
    with pytest.raises(UserError, match="content의 text"):
        llm.complete(messages)


def test_a_model_that_refuses_a_temperature_is_asked_without_one(monkeypatch):
    calls = answer(monkeypatch, Response(400), Response(body=OPENAI_REPLY))
    llm = general()
    assert llm.complete(HELLO) == "연결 성공"
    assert ["temperature" in call["json"] for call in calls] == [True, False]
    # From then on that model is asked without one at once.
    llm.complete(HELLO)
    assert len(calls) == 3 and "temperature" not in calls[-1]["json"]


def test_a_request_refused_for_another_reason_is_reported_once_retried(monkeypatch):
    calls = answer(monkeypatch, Response(400))
    llm = general()
    with pytest.raises(UserError, match="HTTP 400"):
        llm.complete(HELLO)
    assert len(calls) == 2
    with pytest.raises(UserError, match="HTTP 400"):
        llm.complete(HELLO)
    assert "temperature" in calls[2]["json"]


def test_a_rejected_key_says_so(monkeypatch):
    answer(monkeypatch, Response(401))
    with pytest.raises(UserError, match=r"인증을 거부했습니다\(HTTP 401\)") as error:
        general().complete(HELLO)
    assert "sk-test" not in str(error.value)


def test_an_answer_that_is_not_json_is_a_format_error_not_a_connection_error(monkeypatch):
    class NotJson(Response):
        def json(self):
            raise requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)

    answer(monkeypatch, NotJson())
    with pytest.raises(UserError, match="응답 형식"):
        general().complete(HELLO)


def reply(text):
    return Response(body={"choices": [{"message": {"content": text}}]})


@pytest.mark.parametrize(
    "content, expected",
    [
        ("<think>사용자가 무엇을 원하는지…</think>\n\n회의를 정리했습니다.", "회의를 정리했습니다."),
        # Some gateways leave out the opening tag: the thinking is all before the first </think>.
        ("요청을 다시 읽어 보면…\n</think>\n\n회의를 정리했습니다.", "회의를 정리했습니다."),
        ("<THINK>a</THINK>회의를 정리했습니다. <think>잘린 생각", "회의를 정리했습니다."),
    ],
)
def test_a_reasoning_model_s_thinking_is_never_part_of_its_answer(monkeypatch, tmp_path, content, expected):
    """Prompt runs, schedules, briefings and the routines' documents keep the answer only, whatever they ask."""
    from agent.db import Database
    from agent.service import Service

    answer(monkeypatch, reply(content))
    assert general().complete(HELLO) == expected
    service = Service(Database(tmp_path / "wiki.db"), general())
    run = service.prompts_run({"prompt_id": "starter-0", "variables": {"회의 내용": "주간 회의"}})
    assert service.runs_get({"id": run["id"]})["output"] == expected


def test_an_answer_that_is_all_thinking_is_a_format_error_not_an_object_inside_it(monkeypatch):
    # A reply cut short, or one whose opening tag is missing, never hands over a draft from inside the thinking.
    for content in (
        '<think>초안 {"tasks": [{"title": "가짜"}]} 을 검토',
        '초안 {"tasks": [{"title": "가짜"}]} 을 검토…</think>',
    ):
        answer(monkeypatch, reply(content))
        with pytest.raises(LLMFormatError, match="추론"):
            general().complete(HELLO)
        with pytest.raises(LLMFormatError):
            general().structured("system", "text")
        with pytest.raises(LLMFormatError, match="JSON"):
            json_object(content)


@pytest.mark.parametrize(
    "content, summary",
    [
        # A raw line break inside the outer object's string: the outer object, not the relation inside it.
        (
            '{"summary": "첫 줄\n둘째 줄", "keywords": ["a"], "relations": [{"source": "a", "target": "b", "label": "x"}]}',
            "첫 줄\n둘째 줄",
        ),
        # A Markdown escape copied from a document is no JSON escape; it stays as written.
        (
            '결과입니다.\n```json\n{"summary": "API\\_SPEC 검토\\n끝", "keywords": ["a\\\\b"]}\n```',
            "API\\_SPEC 검토\n끝",
        ),
        ('{"summary": "목록,]", "relations": [{"source": "a"},],}', "목록,]"),
    ],
)
def test_json_a_model_writes_almost_right_is_read_as_its_outer_object(monkeypatch, content, summary):
    answer(monkeypatch, reply(content))
    result = general().structured("system", "text")
    assert result["summary"] == summary and "source" not in result


@pytest.mark.parametrize(
    "headers, message",
    [
        ([{"name": "X-Author", "value": "홍길동"}], "영문"),
        ([{"name": "X-Note", "value": "a\r\nInjected: 1"}], "영문"),
        ([{"name": "Bad Name", "value": "x"}], "헤더 이름"),
        ([{"name": "", "value": "x"}], "헤더 이름"),
        ([{"name": "Host", "value": "evil.example"}], "앱이"),
        ([{"name": "X-A", "value": "1"}, {"name": "x-a", "value": "2"}], "두 번"),
        ([{"name": "X-A", "value": 1}], "글자"),
        ("X-A: 1", "목록"),
        ([{"name": f"X-{i}", "value": "1"} for i in range(31)], "30개"),
    ],
)
def test_custom_headers_are_checked_when_saved(headers, message):
    llm = LLM()
    with pytest.raises(UserError, match=message):
        llm.update({"headers": headers})
    assert llm.public()["headers"] == []


@pytest.mark.parametrize(
    "values", [{"api_key": "키"}, {"provider": "openai"}, {"api_format": "gemini"}, {"base_url": "ftp://llm"}]
)
def test_other_settings_are_checked_when_saved(values):
    with pytest.raises(UserError):
        LLM().update(values)


def test_a_saved_value_no_longer_valid_keeps_its_default():
    settings = LLM().load(
        {
            "provider": "openai",
            "base_url": "ftp://llm",
            "model_name": "m",
            "timeout": 999,
            "headers": [{"name": "X-Author", "value": "홍길동"}],
            "api_format": "anthropic",
        }
    )
    assert (
        settings["provider"],
        settings["base_url"],
        settings["model_name"],
        settings["timeout"],
        settings["headers"],
        settings["api_format"],
    ) == ("general", "", "m", 300.0, [], "anthropic")


def test_llm_defaults_from_the_environment_never_stop_the_backend(monkeypatch):
    import sys

    # Values that are not valid keep their defaults instead of failing the start.
    monkeypatch.setenv("LLM_TIMEOUT", "600")
    monkeypatch.setenv("TEMPERATURE", "warm")
    monkeypatch.setenv("BASE_URL", "localhost:8080")
    monkeypatch.setenv("MODEL_NAME", "dev-model")
    monkeypatch.setenv("LLM_HEADERS", "X-Team: ai")
    settings = LLM().public()
    assert (
        settings["timeout"],
        settings["temperature"],
        settings["base_url"],
        settings["model_name"],
        settings["headers"],
    ) == (300.0, 0.5, "", "dev-model", [])
    # The PRIVATE_AGENT_ names come first; the headers are JSON.
    monkeypatch.setenv("PRIVATE_AGENT_MODEL_NAME", "app-model")
    monkeypatch.setenv("PRIVATE_AGENT_LLM_TIMEOUT", "30")
    monkeypatch.setenv("PRIVATE_AGENT_PROVIDER", "general")
    monkeypatch.setenv("PRIVATE_AGENT_LLM_HEADERS", '{"X-Team": "ai"}')
    llm = LLM()
    settings = llm.public()
    assert (settings["model_name"], settings["timeout"], settings["provider"], llm.secrets()["headers"]) == (
        "app-model",
        30.0,
        "general",
        [{"name": "X-Team", "value": "ai"}],
    )
    # A packaged backend reads only those: another program's API_KEY or BASE_URL is not this app's.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("API_KEY", "someone-elses")
    monkeypatch.setenv("BASE_URL", "https://elsewhere.example/v1")
    packaged = LLM().public()
    assert (packaged["model_name"], packaged["base_url"], packaged["has_api_key"]) == ("app-model", "", False)


def wait_until(condition, seconds=5):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "the condition never came true"
        time.sleep(0.01)


def cut(text):
    """An OpenAI answer the LLM stopped at its output limit."""
    return Response(body={"choices": [{"message": {"content": text}, "finish_reason": "length"}]})


def test_a_busy_or_failing_server_is_asked_again_after_a_growing_wait(monkeypatch, waits):
    calls = answer(monkeypatch, Response(503), Response(502), Response(body=OPENAI_REPLY))
    assert general().complete(HELLO) == "연결 성공"
    assert len(calls) == 3
    # Half a second, then a second, each less up to half of it at random.
    assert len(waits) == 2 and 0.25 <= waits[0] <= 0.5 and 0.5 <= waits[1] <= 1.0


def test_a_retry_after_is_honoured_in_seconds_milliseconds_or_as_a_date(monkeypatch, waits):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    later = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=20), usegmt=True)
    calls = answer(
        monkeypatch,
        Response(429, headers={"Retry-After": "3"}),
        Response(503, headers={"retry-after-ms": "1500"}),
        Response(529, headers={"Retry-After": later}),
        Response(body=OPENAI_REPLY),
    )
    assert general().complete(HELLO) == "연결 성공" and len(calls) == 4
    assert waits[:2] == [3.0, 1.5] and 15 <= waits[2] <= 20


def test_attempts_and_waits_are_bounded_and_lasting_failures_are_not_retried(monkeypatch, waits):
    calls = answer(monkeypatch, Response(503))
    with pytest.raises(LLMRequestError, match="HTTP 503") as error:
        general().complete(HELLO)
    assert len(calls) == module.ATTEMPTS == 4 and len(waits) == 3 and error.value.transient
    # A Retry-After longer than the waits allow ends the attempts at once, and says to try later.
    calls = answer(monkeypatch, Response(429, headers={"Retry-After": "120"}))
    with pytest.raises(LLMRequestError, match=r"HTTP 429\).*잠시 후"):
        general().complete(HELLO)
    assert len(calls) == 1
    # A rejected key or a wrong address would fail again: it is reported at once.
    for status in (401, 404):
        calls = answer(monkeypatch, Response(status))
        with pytest.raises(LLMRequestError, match=f"HTTP {status}") as error:
            general().complete(HELLO)
        assert len(calls) == 1 and not error.value.transient


def test_failed_and_timed_out_connections_are_retried_while_the_time_limit_leaves_room(monkeypatch):
    calls = answer(
        monkeypatch,
        requests.exceptions.ConnectionError("reset"),
        requests.exceptions.ConnectTimeout("slow"),
        requests.exceptions.ReadTimeout("slow"),
        Response(body=OPENAI_REPLY),
    )
    assert general().complete(HELLO) == "연결 성공" and len(calls) == 4
    # A call whose time limit leaves no room for another request reports the failure as it is.
    calls = answer(monkeypatch, requests.exceptions.ReadTimeout("slow"))
    with pytest.raises(LLMRequestError, match="제한 시간") as error:
        general().complete(HELLO, timeout=1)
    assert len(calls) == 1 and error.value.transient
    calls = answer(monkeypatch, requests.exceptions.ConnectionError("refused"))
    with pytest.raises(LLMRequestError, match="연결할 수 없습니다") as error:
        general().complete(HELLO)
    assert len(calls) == 4 and error.value.transient
    # A certificate that does not verify stays so.
    calls = answer(monkeypatch, requests.exceptions.SSLError("certificate verify failed"))
    with pytest.raises(LLMRequestError, match="인증서"):
        general().complete(HELLO)
    assert len(calls) == 1


def test_every_request_keeps_tls_verification_and_follows_no_redirect(monkeypatch):
    calls = answer(monkeypatch, Response(body=OPENAI_REPLY))
    llm = general(ca_bundle='"C:\\certs\\private-ca.pem"')
    llm.complete(HELLO)
    assert (calls[-1]["verify"], calls[-1]["allow_redirects"]) == ("C:\\certs\\private-ca.pem", False)
    llm.update({"ca_bundle": ""})
    llm.complete(HELLO)
    # Ten seconds to connect, and what is left of the time limit to answer.
    assert calls[-1]["verify"] is True and calls[-1]["timeout"][0] == 10 and 299 < calls[-1]["timeout"][1] <= 300


def test_failures_are_logged_by_host_status_and_time_never_with_secrets(monkeypatch, log):
    answer(monkeypatch, Response(503), Response(body=OPENAI_REPLY))
    llm = general(
        base_url="https://llm.example:8443/v1/chat/completions?key=query-secret-1",
        headers=[{"name": "X-Team", "value": "team-secret-123"}],
    )
    llm.complete(HELLO)
    answer(monkeypatch, Response(401))
    with pytest.raises(UserError):
        llm.complete(HELLO)
    written = log.read_text(encoding="utf-8")
    assert "LLM request failed · host llm.example:8443 · HTTP 503 · " in written
    assert "attempt 1/4 · retrying in" in written and "HTTP 401" in written and "giving up" in written
    assert all(secret not in written for secret in ("sk-test", "team-secret-123", "query-secret-1", "/v1/chat"))


def test_an_answer_cut_short_at_the_output_limit_says_so(monkeypatch):
    answer(monkeypatch, cut("긴 답변의 앞부분"))
    reply = general().complete(HELLO)
    assert reply == "긴 답변의 앞부분" and reply.truncated
    # A screen showing it as text adds a note; an answer that ended by itself is shown as it is.
    assert shown(reply) == f"긴 답변의 앞부분\n\n{module.TRUNCATED_NOTE}" and shown("다 쓴 답") == "다 쓴 답"
    answer(monkeypatch, Response(body={"content": [{"type": "text", "text": "앞부분"}], "stop_reason": "max_tokens"}))
    assert general(api_format="anthropic").complete(HELLO).truncated
    answer(monkeypatch, Response(body={"choices": [{"message": {"content": "끝"}, "finish_reason": "stop"}]}))
    assert not general().complete(HELLO).truncated
    # A reasoning model that spent its limit thinking is told so, rather than as an answer in the wrong format.
    answer(monkeypatch, cut(""))
    with pytest.raises(LLMTruncatedError, match="최대 출력"):
        general().complete(HELLO)


def test_a_json_answer_cut_short_is_asked_again_with_room_for_twice_as_much(monkeypatch):
    whole = Response(body={"choices": [{"message": {"content": '{"summary": "전체"}'}, "finish_reason": "stop"}]})
    calls = answer(monkeypatch, cut('{"summary": "앞'), whole)
    assert general().structured("system", "text") == {"summary": "전체"}
    # The first request left the cap to the server; the second names RETRY_TOKENS.
    assert "max_tokens" not in calls[0]["json"] and calls[1]["json"]["max_tokens"] == module.RETRY_TOKENS
    calls = answer(monkeypatch, cut('{"summary": "앞'), whole)
    general(max_tokens=3000).structured("system", "text")
    assert [call["json"]["max_tokens"] for call in calls] == [3000, 6000]
    # The Messages API always names one: its default doubles.
    calls = answer(
        monkeypatch,
        Response(body={"content": [{"type": "text", "text": '{"a": '}], "stop_reason": "max_tokens"}),
        Response(body={"content": [{"type": "text", "text": '{"a": 1}'}], "stop_reason": "end_turn"}),
    )
    assert general(api_format="anthropic").structured("system", "text") == {"a": 1}
    assert [call["json"]["max_tokens"] for call in calls] == [16000, 32000]


def test_a_json_answer_cut_short_again_is_a_clear_error(monkeypatch):
    calls = answer(monkeypatch, cut('{"summary": "앞'))
    with pytest.raises(LLMTruncatedError, match="최대 출력 토큰") as error:
        general().structured("system", "text")
    # A format error, so callers with a fallback of their own (PageIndex, the AI search) use it.
    assert len(calls) == 2 and isinstance(error.value, LLMFormatError)
    # More than the model writes at once is refused (HTTP 400): the same clear error.
    answer(monkeypatch, cut('{"summary": "앞'), Response(400))
    with pytest.raises(LLMTruncatedError, match="최대 출력 토큰"):
        general().structured("system", "text")
    # A cap that cannot grow any more is not asked again.
    calls = answer(monkeypatch, cut('{"summary": "앞'))
    with pytest.raises(LLMTruncatedError):
        general(max_tokens=64000).structured("system", "text")
    assert len(calls) == 1


def test_a_reasoning_model_gets_its_cap_as_max_completion_tokens(monkeypatch):
    calls = answer(monkeypatch, Response(400), Response(body=OPENAI_REPLY))
    general(max_tokens=2000).complete(HELLO)
    assert calls[0]["json"]["max_tokens"] == 2000 and "temperature" in calls[0]["json"]
    assert calls[1]["json"]["max_completion_tokens"] == 2000
    assert "max_tokens" not in calls[1]["json"] and "temperature" not in calls[1]["json"]


def test_output_limit_context_window_and_concurrency_settings(monkeypatch):
    llm = LLM()
    settings = llm.public()
    assert (settings["max_tokens"], settings["context_window"], settings["concurrency"]) == (0, 64000, 3)
    # The defaults leave a question the 56,000 characters it had before the window was a setting.
    assert llm.prompt_chars() == 56_000
    settings = llm.update({"max_tokens": 4096, "context_window": "128000", "concurrency": 5})
    assert (settings["max_tokens"], settings["context_window"], settings["concurrency"]) == (4096, 128000, 5)
    assert llm.prompt_chars() == 128000 - 4096 and llm.slots.limit == 5
    # A small window keeps a quarter of itself for an answer whose cap is the server's.
    llm.update({"max_tokens": 0, "context_window": 8192})
    assert llm.prompt_chars() == 8192 - 2048
    for values, message in (
        ({"max_tokens": 100}, "최대 출력 토큰"),
        ({"max_tokens": 1.5}, "최대 출력 토큰"),
        ({"max_tokens": True}, "최대 출력 토큰"),
        ({"context_window": 1000}, "컨텍스트"),
        ({"concurrency": 0}, "동시 요청"),
        ({"concurrency": 9}, "동시 요청"),
        ({"concurrency": None}, "동시 요청"),
    ):
        with pytest.raises(UserError, match=message):
            llm.update(values)
    assert llm.public()["concurrency"] == 5
    # From the environment, as text; a value that is not valid keeps its default.
    monkeypatch.setenv("LLM_MAX_TOKENS", "8000")
    monkeypatch.setenv("LLM_CONTEXT_WINDOW", "lots")
    monkeypatch.setenv("PRIVATE_AGENT_LLM_CONCURRENCY", "2")
    settings = LLM().public()
    assert (settings["max_tokens"], settings["context_window"], settings["concurrency"]) == (8000, 64000, 2)


def test_requests_the_user_waits_for_go_before_batch_work():
    slots, order = Slots(1), []

    def run(level, name):
        with slots.hold(level):
            order.append(name)

    with slots.hold(BATCH):
        batch = threading.Thread(target=run, args=(BATCH, "batch"))
        batch.start()
        wait_until(lambda: slots.waiting[BATCH] == 1)
        user = threading.Thread(target=run, args=(INTERACTIVE, "user"))
        user.start()
        wait_until(lambda: slots.waiting[INTERACTIVE] == 1)
    batch.join(5)
    user.join(5)
    assert order == ["user", "batch"]


def test_batch_work_leaves_the_user_a_slot_and_waits_can_end():
    slots, release = Slots(2), threading.Event()
    held = []

    def hold(level):
        with slots.hold(level):
            held.append(level)
            release.wait(5)

    threads = [threading.Thread(target=hold, args=(level,)) for level in (BATCH, BATCH, INTERACTIVE)]
    threads[0].start()
    wait_until(lambda: held == [BATCH])
    for thread in threads[1:]:
        thread.start()
    # Of two slots, batch work takes one; the user's request takes the other at once.
    wait_until(lambda: sorted(held) == [BATCH, INTERACTIVE] and slots.waiting[BATCH] == 1)
    # A wait ends with the request's time limit, or when the user stops it.
    with pytest.raises(LLMRequestError, match="요청이 많아") as error:
        with slots.hold(INTERACTIVE, timeout=0.2):
            pass
    assert error.value.transient
    cancel = Cancel()
    threading.Timer(0.2, cancel.set).start()
    with pytest.raises(Cancelled):
        with slots.hold(INTERACTIVE, cancel=cancel):
            pass
    release.set()
    for thread in threads:
        thread.join(5)
    assert sorted(held) == [BATCH, BATCH, INTERACTIVE] and slots.running == {INTERACTIVE: 0, BATCH: 0}


def test_requests_take_their_slot_at_the_priority_of_their_context(monkeypatch):
    seen, llm = [], general()

    def post(session, url, **kwargs):
        seen.append(dict(llm.slots.running))
        return Response(body=OPENAI_REPLY)

    monkeypatch.setattr("agent.llm.requests.Session.post", post)
    llm.complete(HELLO)
    with priority(INTERACTIVE):
        llm.complete(HELLO)
    assert seen == [{INTERACTIVE: 0, BATCH: 1}, {INTERACTIVE: 1, BATCH: 0}]
    assert llm.slots.running == {INTERACTIVE: 0, BATCH: 0}


def test_no_more_requests_go_at_once_than_the_setting_allows(monkeypatch):
    llm, lock, release = general(concurrency=2), threading.Lock(), threading.Event()
    active, peak = [0], [0]

    def post(session, url, **kwargs):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        release.wait(5)
        with lock:
            active[0] -= 1
        return Response(body=OPENAI_REPLY)

    def ask():
        with priority(INTERACTIVE):
            llm.complete(HELLO)

    monkeypatch.setattr("agent.llm.requests.Session.post", post)
    threads = [threading.Thread(target=ask) for _ in range(3)]
    for thread in threads:
        thread.start()
    wait_until(lambda: active[0] == 2 and llm.slots.waiting[INTERACTIVE] == 1)
    release.set()
    for thread in threads:
        thread.join(5)
    assert peak[0] == 2


def test_an_answer_is_streamed_as_it_is_written(monkeypatch):
    calls = answer(
        monkeypatch,
        events(openai_delta("안녕"), {"choices": []}, openai_delta("하세요"), openai_delta(finish="stop"), "[DONE]"),
    )
    seen = []
    reply = general().complete(HELLO, stream=seen.append)
    assert reply == "안녕하세요" and not reply.truncated and seen == ["안녕", "안녕하세요"]
    assert calls[0]["json"]["stream"] is True and calls[0]["stream"] is True
    answer(monkeypatch, events(openai_delta("앞"), openai_delta(finish="length")))
    assert general().complete(HELLO, stream=seen.append).truncated
    # Anthropic's events, of which only the text deltas are the answer: the thinking stays out.
    calls = answer(
        monkeypatch,
        events(
            {"type": "message_start", "message": {"content": []}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "생각"}},
            {"type": "ping"},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "답"}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "변"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
            {"type": "message_stop"},
        ),
    )
    seen = []
    reply = general(api_format="anthropic").complete(HELLO, stream=seen.append)
    assert reply == "답변" and seen == ["답", "답변"] and calls[0]["json"]["stream"] is True


def test_a_stream_that_breaks_off_starts_over(monkeypatch, waits):
    broken = events(openai_delta("첫"), {"error": {"message": "overloaded"}})
    dropped = Response(
        headers={"Content-Type": "text/event-stream"},
        chunks=[
            b"data: " + json.dumps(openai_delta("둘")).encode() + b"\n\n",
            requests.exceptions.ChunkedEncodingError("reset"),
        ],
    )
    calls = answer(monkeypatch, broken, dropped, events(openai_delta("다시"), openai_delta(finish="stop")))
    seen = []
    assert general().complete(HELLO, stream=seen.append) == "다시"
    assert seen == ["첫", "", "둘", "", "다시"] and len(calls) == 3 and len(waits) == 2


def test_a_server_that_does_not_stream_gives_its_answer_whole(monkeypatch):
    answer(monkeypatch, Response(body=OPENAI_REPLY, headers={"Content-Type": "application/json"}))
    seen = []
    assert general().complete(HELLO, stream=seen.append) == "연결 성공" and seen == ["연결 성공"]
    # One that refuses to stream is asked for the whole answer, and from then on at once.
    calls = answer(monkeypatch, Response(400), Response(400), Response(body=OPENAI_REPLY))
    llm, seen = general(), []
    assert llm.complete(HELLO, stream=seen.append) == "연결 성공" and seen == ["연결 성공"]
    assert [call["json"]["stream"] for call in calls] == [True, True, False]
    llm.complete(HELLO, stream=seen.append)
    assert len(calls) == 4 and calls[3]["json"]["stream"] is False and calls[3]["stream"] is False


def test_a_cancelled_request_stops_and_keeps_what_had_come(monkeypatch):
    answer(monkeypatch, events(openai_delta("앞"), openai_delta("뒤"), openai_delta(finish="stop")))
    cancel = Cancel()
    with pytest.raises(Cancelled) as stopped:
        general().complete(HELLO, stream=lambda text: cancel.set(), cancel=cancel)
    assert stopped.value.partial == "앞"
    # Stopped before it starts, nothing is sent; the CANCEL of the context stops it too.
    calls = answer(monkeypatch, Response(body=OPENAI_REPLY))
    with module.cancellable(cancel), pytest.raises(Cancelled):
        general().complete(HELLO)
    assert calls == []


def test_a_stream_stopped_while_its_read_waits_ends_at_once_even_if_nothing_wakes_the_read(monkeypatch):
    """As on a system where a socket shut down from another thread leaves a read waiting: the answer still ends at
    once, as the stream is read apart."""
    release = threading.Event()

    def chunks(chunk_size=None):
        yield b"data: " + json.dumps(openai_delta("앞")).encode() + b"\n\n"
        release.wait(10)  # A read nothing wakes.
        yield b"data: " + json.dumps(openai_delta("뒤")).encode() + b"\n\n"

    response = Response(headers={"Content-Type": "text/event-stream"})
    response.iter_content = chunks
    answer(monkeypatch, response)
    cancel, started = Cancel(), time.monotonic()
    try:
        with pytest.raises(Cancelled) as stopped:
            general().complete(HELLO, stream=lambda text: threading.Timer(0.1, cancel.set).start(), cancel=cancel)
    finally:
        release.set()
    assert time.monotonic() - started < 3 and stopped.value.partial == "앞"


def test_connections_unused_for_a_while_are_not_trusted_again(monkeypatch):
    answer(monkeypatch, Response(body=OPENAI_REPLY))
    llm, closed = general(), []
    monkeypatch.setattr(llm.session, "close", lambda: closed.append(True))
    llm.complete(HELLO)
    assert closed == []
    # A firewall may have dropped a connection unused for a minute without a word: the session opens new ones.
    llm.used -= module.IDLE_SECONDS + 1
    llm.complete(HELLO)
    llm.complete(HELLO)
    assert closed == [True]


@contextlib.contextmanager
def local_llm(monkeypatch, respond):
    """An LLM server on this PC: `respond(handler, body)` answers each request; the requests are kept."""
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            server.requests.append({"headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
            respond(self, body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads, server.requests = True, []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
    finally:
        server.shutdown()
        server.server_close()


def send_json(handler, status, body=None, headers=()):
    data = json.dumps(body or {}).encode()
    handler.send_response(status)
    for name, value in (("Content-Type", "application/json"), ("Content-Length", str(len(data))), *headers):
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(data)


def test_the_session_retries_a_real_server_and_keeps_no_cookie(monkeypatch, waits):
    def respond(handler, body):
        if len(server.requests) == 1:
            send_json(handler, 503, headers=[("Retry-After", "2"), ("Set-Cookie", "route=a1; Path=/")])
        else:
            send_json(handler, 200, OPENAI_REPLY, headers=[("Set-Cookie", "route=b2; Path=/")])

    with local_llm(monkeypatch, respond) as (server, url):
        llm = general(base_url=url)
        assert llm.complete(HELLO) == "연결 성공" and llm.complete(HELLO) == "연결 성공"
    assert waits == [2.0] and len(server.requests) == 3
    assert all("cookie" not in request["headers"] for request in server.requests)
    assert server.requests[0]["headers"]["authorization"] == "Bearer sk-test"


def test_a_streamed_answer_the_user_stops_ends_at_once(monkeypatch):
    release = threading.Event()

    def respond(handler, body):
        assert body["stream"] is True
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Transfer-Encoding", "chunked")
        handler.end_headers()
        data = b"data: " + json.dumps(openai_delta("앞부분")).encode() + b"\n\n"
        handler.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
        handler.wfile.flush()
        release.wait(20)  # The rest does not come before the user stops the answer.

    with local_llm(monkeypatch, respond) as (server, url):
        # A short time limit: a read the cancel failed to wake would end in a time-out, not hang the test.
        llm, cancel = general(base_url=url, timeout=5), Cancel()

        def on_text(text):
            if text:
                threading.Timer(0.2, cancel.set).start()

        started = time.monotonic()
        try:
            with pytest.raises(Cancelled) as stopped:
                llm.complete(HELLO, stream=on_text, cancel=cancel)
        finally:
            release.set()
    assert time.monotonic() - started < 10 and stopped.value.partial == "앞부분"
    assert llm.slots.running == {INTERACTIVE: 0, BATCH: 0}
