import json

import pytest

from agent.agent_chat import spoken
from agent.db import Database
from agent.llm import UserError
from agent.service import Service


class FakeLLM:
    """A connected LLM whose JSON plans the test sets (the planner of a source run without Hermes)."""

    def __init__(self):
        self.plans, self.seen = [], []

    def public(self):
        return {"configured": True}

    def structured(self, system, content):
        self.seen.append((system, content))
        return self.plans.pop(0)


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_AGENT_HERMES_DIR", str(tmp_path / "no-hermes"))
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    assert service.agent.engine() == {"engine": "direct", "ready": True}
    return service


def plan(service, message="", *actions):
    service.llm.plans.append({"message": message, "actions": [{"tool": t, "args": a} for t, a in actions]})


def send(service, words="쿠팡에서 노트북 광고 기록해 줘"):
    return service.dispatch("agent.send", {"text": words, "request_id": "r1"})


def fake_capture(service, calls):
    def capture(site, query, source="chat"):
        calls.append((site, query, source))
        return {
            "capture_id": "c1",
            "site": site,
            "name": "쿠팡",
            "query": query,
            "url": "https://shop.example.test/s",
            "counts": {"ad": 2, "product": 5, "result": 0},
            "ads": ["광고 A", "광고 B"],
            "products": ["상품 A"],
            "screenshot": True,
        }

    service.web.capture = capture


def test_a_site_action_waits_for_approval_and_runs_once(service):
    calls = []
    fake_capture(service, calls)
    plan(service, "쿠팡 검색 결과를 기록할게요.", ("web_capture", {"site": "coupang", "query": "노트북"}))
    sent = send(service)
    card = sent["reply"]["actions"][0]
    assert card["status"] == "pending" and card["approval"] and calls == []
    assert sent["reply"]["engine"] == "direct"
    done = service.dispatch("agent.approve", {"seq": sent["reply"]["seq"], "index": 0})
    assert done["actions"][0]["status"] == "done"
    assert "광고 2건, 상품 5건" in done["actions"][0]["result"]["text"]
    assert calls == [("coupang", "노트북", "chat")]
    with pytest.raises(UserError, match="이미 처리한"):
        service.dispatch("agent.approve", {"seq": sent["reply"]["seq"], "index": 0})
    assert calls == [("coupang", "노트북", "chat")]
    # The next plan reads what ran.
    plan(service, "네.")
    send(service, "방금 기록한 거 알려 줘")
    assert "광고 2건, 상품 5건" in service.llm.seen[-1][1]


def test_a_cancelled_action_never_runs(service):
    calls = []
    fake_capture(service, calls)
    plan(service, "", ("web_capture", {"site": "amazon", "query": "headphones"}))
    sent = send(service, "아마존 headphones")
    cancelled = service.dispatch("agent.reject", {"seq": sent["reply"]["seq"], "index": 0})
    assert cancelled["actions"][0]["status"] == "cancelled"
    with pytest.raises(UserError):
        service.dispatch("agent.approve", {"seq": sent["reply"]["seq"], "index": 0})
    assert calls == []


def test_reading_the_records_runs_at_once(service):
    service.web._keep(
        "naver",
        "캠핑의자",
        "chat",
        "t",
        {"url": "https://search.example.test", "items": [{"ref": 0, "kind": "ad", "title": "캠핑의자 특가"}]},
        b"",
    )
    plan(service, "기록을 요약했어요.", ("list_records", {"site": "naver", "kind": "ad"}))
    card = send(service, "네이버 광고 기록 보여줘")["reply"]["actions"][0]
    assert card["status"] == "done" and not card["approval"]
    assert card["result"]["records"][0]["title"] == "캠핑의자 특가"


def test_unknown_or_unsafe_actions_are_left_out(service):
    plan(
        service,
        "",
        ("click_ad", {"selector": ".ad"}),
        ("open_site", {"url": "javascript:alert(1)"}),
        ("web_capture", {"site": "elsewhere", "query": "a"}),
        ("open_site", {"site": "google"}),
    )
    reply = send(service, "광고 눌러 줘")["reply"]
    assert [card["tool"] for card in reply["actions"]] == ["open_site"]
    assert "확인할 수 없는 작업 3개는 뺐습니다" in reply["content"]


def test_scheduling_a_capture_makes_a_routine(service):
    plan(
        service,
        "",
        ("schedule_capture", {"site": "coupang", "query": "노트북", "time": "09:30", "days": "weekdays"}),
    )
    sent = send(service, "평일 9시 반에 쿠팡 노트북 광고 기록해 줘")
    assert sent["reply"]["actions"][0]["title"] == "평일마다 09:30에 쿠팡 '노트북' 기록 예약"
    done = service.dispatch("agent.approve", {"seq": sent["reply"]["seq"], "index": 0})
    assert done["actions"][0]["status"] == "done"
    routine = service.dispatch("routines.get", {"id": done["actions"][0]["result"]["routine_id"]})
    assert routine["kind"] == "web_capture" and routine["frequency"] == "weekdays" and routine["run_time"] == "09:30"
    assert routine["config"] == {"site": "coupang", "query": "노트북"}


def test_invalid_schedule_and_task_arguments_are_refused(service):
    plan(
        service,
        "",
        ("schedule_capture", {"site": "coupang", "query": "a", "time": "25:00"}),
        ("add_task", {"title": "보고서", "due": "다음 주"}),
        ("add_task", {"title": "광고 정리", "due": "2026-12-01"}),
    )
    sent = send(service, "할 일")
    assert [card["tool"] for card in sent["reply"]["actions"]] == ["add_task"]
    done = service.dispatch("agent.approve", {"seq": sent["reply"]["seq"], "index": 0})
    task = service.dispatch("tasks.list", {})
    tasks = task["tasks"] if isinstance(task, dict) else task
    assert done["actions"][0]["status"] == "done"
    assert any(item["title"] == "광고 정리" and item["due_at"].startswith("2026-12-01") for item in tasks)


def test_a_failed_action_says_why(service):
    def blocked(site, query, source="chat"):
        raise UserError("사이트가 로봇 확인(CAPTCHA)을 요청했습니다.")

    service.web.capture = blocked
    plan(service, "", ("web_capture", {"site": "google", "query": "laptop"}))
    sent = send(service, "구글 laptop 광고")
    done = service.dispatch("agent.approve", {"seq": sent["reply"]["seq"], "index": 0})
    assert done["actions"][0]["status"] == "failed"
    assert "로봇 확인" in done["actions"][0]["result"]["text"]


def test_without_an_llm_the_reply_says_how_to_connect(tmp_path):
    class Unconnected:
        def public(self):
            return {"configured": False}

    service = Service(Database(tmp_path / "wiki.db"), Unconnected())
    reply = service.dispatch("agent.send", {"text": "안녕"})["reply"]
    assert "LLM 연결" in reply["content"] and reply["actions"] == []


def test_one_message_at_a_time_and_history_is_kept_and_cleared(service):
    plan(service, "안녕하세요.")
    send(service, "안녕")
    service.agent.sending = True
    with pytest.raises(UserError, match="이전 메시지"):
        send(service, "또")
    service.agent.sending = False
    messages = service.dispatch("agent.messages", {})
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert json.dumps(messages, ensure_ascii=False).count("안녕") >= 2
    service.dispatch("agent.clear", {})
    assert service.dispatch("agent.messages", {}) == []


@pytest.mark.parametrize(
    ("said", "shown"),
    [
        ("<think>plan the capture</think>쿠팡 검색을 준비했습니다.", "쿠팡 검색을 준비했습니다."),
        ('{"actions": [{"tool": "web_capture"}]}', ""),
        ("<think>never closed", ""),
        ("  준비했습니다.  ", "준비했습니다."),
        (None, ""),
    ],
)
def test_the_planner_words_drop_reasoning_and_raw_plans(said, shown):
    assert spoken(said) == shown
