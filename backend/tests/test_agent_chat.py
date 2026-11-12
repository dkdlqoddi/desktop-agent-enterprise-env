import json
import threading

import pytest

from agent.agent_chat import DIRECT_SYSTEM, SYSTEM, spoken
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


def test_the_plan_is_asked_for_as_json_from_an_llm_told_it_has_no_tools(service):
    # A source run without Hermes has no tools: the LLM is told so and asked for the plan as JSON, which alone is read.
    # (Told that it acts with a terminal, it answers in words and no plan can be read from them.)
    plan(service, "네.")
    send(service, "안녕")
    system = service.llm.seen[-1][0]
    assert "Answer ONLY with JSON" in system and '{"message": "...", "actions": [' in system
    assert "You cannot browse or act yourself" in system
    for tool in service.agent.tools():
        assert f"- {tool} " in system
    assert "terminal" not in system and "clarify tool" not in system


def test_both_prompts_keep_the_agent_from_clicking_ads():
    # The agent's own browser cannot be kept from a page's ads by code (it drives the page freely), so the rule is
    # part of what it is told; the plan of a source run may not propose a click either.
    assert "Never click, open or follow an advertisement" in SYSTEM
    assert "never propose clicking ads" in DIRECT_SYSTEM


def test_a_chat_loaded_while_a_message_is_carried_out_finds_it(service):
    idle = {"request_id": "", "text": ""}
    assert service.dispatch("agent.active", {}) == idle
    started, release = threading.Event(), threading.Event()

    def structured(system, content):
        started.set()
        assert release.wait(10)
        return {"message": "네.", "actions": []}

    service.llm.structured = structured
    thread = threading.Thread(target=send, args=(service, "오래 걸리는 일"))
    thread.start()
    try:
        assert started.wait(10)
        assert service.dispatch("agent.active", {}) == {"request_id": "r1", "text": "오래 걸리는 일"}
        # The request is being carried out, which the engine's own phase does not tell for a plan of the LLM.
        status = service.dispatch("agent.status", {"request_id": "r1"})
        assert status["active"] is True and status["phase"] == "idle"
        assert service.dispatch("agent.status", {"request_id": "another"})["active"] is False
    finally:
        release.set()
        thread.join(10)
    assert service.dispatch("agent.active", {}) == idle
    assert service.dispatch("agent.status", {"request_id": "r1"})["active"] is False


def test_the_work_folder_is_the_users_while_it_exists(service, tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    assert service.agent.folder() == ""
    service.dispatch("agent.configure", {"workspace": str(folder)})
    assert service.agent.folder() == str(folder)
    # A folder that has gone, a file, a relative path or another kind of value: the agent's own home instead.
    folder.rmdir()
    assert service.agent.folder() == ""
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    for value in (str(tmp_path / "file.txt"), "relative/folder", 7, None, "x" * 2000):
        service.dispatch("agent.configure", {"workspace": value})
        assert service.agent.folder() == ""
    # The options sent without a folder leave the folder as it was.
    folder.mkdir()
    service.dispatch("agent.configure", {"workspace": str(folder)})
    service.dispatch("agent.configure", {"computer_use": False})
    assert service.agent.folder() == str(folder) and service.agent.settings["computer_use"] is False


def test_the_web_services_are_the_ones_hermes_knows(service):
    def settings():
        return service.agent.settings["search_backend"], service.agent.settings["extract_backend"]

    assert settings() == ("ddgs", "ddgs")
    service.dispatch("agent.configure", {"search_backend": "brave-free", "extract_backend": "firecrawl"})
    assert settings() == ("brave-free", "firecrawl")
    # Options sent without them leave them as they were; a name Hermes does not know is the search without a key.
    service.dispatch("agent.configure", {"computer_use": True})
    assert settings() == ("brave-free", "firecrawl")
    service.dispatch("agent.configure", {"search_backend": "elsewhere", "extract_backend": ["exa"]})
    assert settings() == ("ddgs", "ddgs")


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


def test_without_an_llm_the_message_says_how_to_connect(tmp_path):
    class Unconnected:
        def public(self):
            return {"configured": False}

    service = Service(Database(tmp_path / "wiki.db"), Unconnected())
    with pytest.raises(UserError, match="LLM 연결"):
        service.dispatch("agent.send", {"text": "안녕"})
    # The message is not stored when the connection is missing: nothing to answer it.
    assert service.dispatch("agent.messages", {}) == []


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
