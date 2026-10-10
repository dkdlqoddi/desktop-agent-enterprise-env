import json

import pytest
import requests

from agent.llm import ENVIRONMENT, LLM, LLMFormatError, UserError, json_object

OPENAI_REPLY = {"choices": [{"message": {"content": "연결 성공"}}]}
HELLO = [{"role": "user", "content": "hi"}]


class Response:
    def __init__(self, status=200, body=None):
        self.status_code, self.body = status, body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def json(self):
        if self.status_code != 200:
            raise AssertionError("Error body must not be read or disclosed")
        return self.body


def answer(monkeypatch, *replies):
    """Answers the LLM requests with the replies in turn, the last one from then on, and keeps the requests."""
    calls = []

    def post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return replies[min(len(calls), len(replies)) - 1]

    monkeypatch.setattr("agent.llm.requests.post", post)
    return calls


@pytest.fixture(autouse=True)
def no_llm_environment(monkeypatch):
    """A developer's own LLM settings in the environment change nothing here."""
    for name in ENVIRONMENT.values():
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("PRIVATE_AGENT_" + name, raising=False)


def general(**values):
    llm = LLM()
    llm.update({"provider": "general", "base_url": "https://llm.example/v1/chat/completions", "api_key": "sk-test",
                "model_name": "test-model", **values})
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
    assert llm.public()["headers"] == [{"name": "X-Team", "has_value": True, "uses_api_key": False},
                                       {"name": "api-key", "has_value": True, "uses_api_key": True}]


def test_a_header_sent_without_its_value_keeps_the_saved_one(monkeypatch):
    # An AI tool reads the headers' names only: it changes the model and sends the settings back as it read them.
    calls = answer(monkeypatch, Response(body=OPENAI_REPLY))
    llm = general(headers=[{"name": "X-Team", "value": "team-secret-123"},
                           {"name": "api-key", "value": "{api_key}"}])
    read = {**llm.public(), "headers": [{"name": header["name"]} for header in llm.public()["headers"]]}
    llm.update({**read, "model_name": "other-model"})
    assert llm.secrets()["headers"] == [{"name": "X-Team", "value": "team-secret-123"},
                                        {"name": "api-key", "value": "{api_key}"}]
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
    for change in ({"base_url": "https://other.example/v1/chat/completions"},
                   {"base_url": "https://other.example/v1/chat/completions", "headers": [{"name": "X-Team"}]}):
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
    assert (calls[-1]["url"], calls[-1]["headers"]["api-key"]) == ("https://other.example/v1/chat/completions",
                                                                        "sk-other")
    # Saved settings, read back at the start, are taken as they are.
    assert LLM().load({"base_url": "https://llm.example/v1", "api_key": "sk-test", "model_name": "m",
                       "provider": "general"})["has_api_key"] is True


def test_a_header_that_needs_the_key_waits_for_it(monkeypatch):
    calls = answer(monkeypatch, Response(body=OPENAI_REPLY))
    llm = general(api_key="", headers=[{"name": "api-key", "value": "{api_key}"}])
    assert not llm.public()["configured"]
    with pytest.raises(UserError, match="API Key"):
        llm.complete(HELLO)
    assert calls == []


def test_anthropic_messages_api(monkeypatch):
    calls = answer(monkeypatch, Response(body={
        "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "연결 "},
                    {"type": "text", "text": "성공"}], "stop_reason": "end_turn"}))
    llm = general(api_format="anthropic", base_url="https://api.anthropic.com/v1/messages", api_key="sk-ant",
                  model_name="claude-opus-5-5")
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    assert llm.complete(messages, temperature=0.1) == "연결 성공"
    assert calls[-1]["headers"] == {"anthropic-version": "2023-06-01", "x-api-key": "sk-ant"}
    # The system prompt is a field of its own, and current Claude models take no temperature.
    assert calls[-1]["json"] == {"model": "claude-opus-5-5", "max_tokens": 16000, "system": "sys",
                                 "messages": [{"role": "user", "content": "hi"}]}
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


@pytest.mark.parametrize("content, expected", [
    ("<think>사용자가 무엇을 원하는지…</think>\n\n회의를 정리했습니다.", "회의를 정리했습니다."),
    # Some gateways leave out the opening tag: the thinking is all before the first </think>.
    ("요청을 다시 읽어 보면…\n</think>\n\n회의를 정리했습니다.", "회의를 정리했습니다."),
    ("<THINK>a</THINK>회의를 정리했습니다. <think>잘린 생각", "회의를 정리했습니다."),
])
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
    for content in ('<think>초안 {"tasks": [{"title": "가짜"}]} 을 검토', '초안 {"tasks": [{"title": "가짜"}]} 을 검토…</think>'):
        answer(monkeypatch, reply(content))
        with pytest.raises(LLMFormatError, match="추론"):
            general().complete(HELLO)
        with pytest.raises(LLMFormatError):
            general().structured("system", "text")
        with pytest.raises(LLMFormatError, match="JSON"):
            json_object(content)


@pytest.mark.parametrize("content, summary", [
    # A raw line break inside the outer object's string: the outer object, not the relation inside it.
    ('{"summary": "첫 줄\n둘째 줄", "keywords": ["a"], "relations": [{"source": "a", "target": "b", "label": "x"}]}',
     "첫 줄\n둘째 줄"),
    # A Markdown escape copied from a document is no JSON escape; it stays as written.
    ('결과입니다.\n```json\n{"summary": "API\\_SPEC 검토\\n끝", "keywords": ["a\\\\b"]}\n```', "API\\_SPEC 검토\n끝"),
    ('{"summary": "목록,]", "relations": [{"source": "a"},],}', "목록,]"),
])
def test_json_a_model_writes_almost_right_is_read_as_its_outer_object(monkeypatch, content, summary):
    answer(monkeypatch, reply(content))
    result = general().structured("system", "text")
    assert result["summary"] == summary and "source" not in result


@pytest.mark.parametrize("headers, message", [
    ([{"name": "X-Author", "value": "홍길동"}], "영문"),
    ([{"name": "X-Note", "value": "a\r\nInjected: 1"}], "영문"),
    ([{"name": "Bad Name", "value": "x"}], "헤더 이름"),
    ([{"name": "", "value": "x"}], "헤더 이름"),
    ([{"name": "Host", "value": "evil.example"}], "앱이"),
    ([{"name": "X-A", "value": "1"}, {"name": "x-a", "value": "2"}], "두 번"),
    ([{"name": "X-A", "value": 1}], "글자"),
    ("X-A: 1", "목록"),
    ([{"name": f"X-{i}", "value": "1"} for i in range(31)], "30개"),
])
def test_custom_headers_are_checked_when_saved(headers, message):
    llm = LLM()
    with pytest.raises(UserError, match=message):
        llm.update({"headers": headers})
    assert llm.public()["headers"] == []


@pytest.mark.parametrize("values", [{"api_key": "키"}, {"provider": "openai"},
                                    {"api_format": "gemini"}, {"base_url": "ftp://llm"}])
def test_other_settings_are_checked_when_saved(values):
    with pytest.raises(UserError):
        LLM().update(values)


def test_a_saved_value_no_longer_valid_keeps_its_default():
    settings = LLM().load({"provider": "openai", "base_url": "ftp://llm", "model_name": "m", "timeout": 999,
                           "headers": [{"name": "X-Author", "value": "홍길동"}], "api_format": "anthropic"})
    assert (settings["provider"], settings["base_url"], settings["model_name"], settings["timeout"],
            settings["headers"], settings["api_format"]) == ("general", "", "m", 300.0, [], "anthropic")


def test_llm_defaults_from_the_environment_never_stop_the_backend(monkeypatch):
    import sys
    # Values that are not valid keep their defaults instead of failing the start.
    monkeypatch.setenv("LLM_TIMEOUT", "600")
    monkeypatch.setenv("TEMPERATURE", "warm")
    monkeypatch.setenv("BASE_URL", "localhost:8080")
    monkeypatch.setenv("MODEL_NAME", "dev-model")
    monkeypatch.setenv("LLM_HEADERS", "X-Team: ai")
    settings = LLM().public()
    assert (settings["timeout"], settings["temperature"], settings["base_url"], settings["model_name"],
            settings["headers"]) == (300.0, 0.5, "", "dev-model", [])
    # The PRIVATE_AGENT_ names come first; the headers are JSON.
    monkeypatch.setenv("PRIVATE_AGENT_MODEL_NAME", "app-model")
    monkeypatch.setenv("PRIVATE_AGENT_LLM_TIMEOUT", "30")
    monkeypatch.setenv("PRIVATE_AGENT_PROVIDER", "general")
    monkeypatch.setenv("PRIVATE_AGENT_LLM_HEADERS", '{"X-Team": "ai"}')
    llm = LLM()
    settings = llm.public()
    assert (settings["model_name"], settings["timeout"], settings["provider"], llm.secrets()["headers"]) == (
        "app-model", 30.0, "general", [{"name": "X-Team", "value": "ai"}])
    # A packaged backend reads only those: another program's API_KEY or BASE_URL is not this app's.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("API_KEY", "someone-elses")
    monkeypatch.setenv("BASE_URL", "https://elsewhere.example/v1")
    packaged = LLM().public()
    assert (packaged["model_name"], packaged["base_url"], packaged["has_api_key"]) == ("app-model", "", False)
