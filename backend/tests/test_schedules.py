import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from agent.db import Database
from agent.llm import UserError
from agent.schedules import MAX_ACTIVE, next_run
from agent.service import SCHEDULE_INPUTS, Service

KST = timezone(timedelta(hours=9))


class FakeLLM:
    def __init__(self):
        self.calls, self.error, self.gate = [], None, None

    def complete(self, messages):
        self.calls.append(messages)
        if self.gate:
            self.gate.wait(5)
        if self.error:
            raise self.error
        return "예약 실행 결과"


@pytest.fixture
def service(tmp_path):
    return Service(Database(tmp_path / "test.db"), FakeLLM())


def prompt(service, template="오늘 할 일: {{ 할 일 }}"):
    return service.prompts_save({"title": "아침 정리", "template": template, "category": "업무"})


def schedule(service, **values):
    source = values.pop("prompt", None) or prompt(service)
    return service.dispatch("schedules.save", {"prompt_id": source["id"], "variables": {"할 일": "보고서"},
                                               "frequency": "daily", "run_time": "09:00", **values})


def make_due(service, ident):
    past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    with service.db.connect() as db:
        db.execute("UPDATE schedules SET next_run_at=? WHERE id=?", (past, ident))
    return past


def execute(service, ident, due):
    return service.dispatch("schedules.execute", {"id": ident, "next_run_at": due})


@pytest.mark.parametrize("frequency,extra,after,expected", [
    ("hourly", {"run_time": "00:15"}, datetime(2026, 9, 29, 10, 20), datetime(2026, 9, 29, 11, 15)),
    ("hourly", {"run_time": "00:15"}, datetime(2026, 9, 29, 10, 10), datetime(2026, 9, 29, 10, 15)),
    ("daily", {}, datetime(2026, 9, 29, 8, 59), datetime(2026, 9, 29, 9, 0)),
    ("daily", {}, datetime(2026, 9, 29, 9, 0), datetime(2026, 9, 30, 9, 0)),  # strictly after
    ("weekdays", {}, datetime(2026, 10, 2, 10, 0), datetime(2026, 10, 5, 9, 0)),  # Friday -> Monday
    ("weekly", {"weekday": 0}, datetime(2026, 9, 29, 10, 0), datetime(2026, 10, 5, 9, 0)),
    ("monthly", {"month_day": 31}, datetime(2026, 9, 29, 10, 0), datetime(2026, 9, 30, 9, 0)),  # last day
    ("monthly", {"month_day": 31}, datetime(2026, 9, 30, 10, 0), datetime(2026, 10, 31, 9, 0)),
])
def test_next_run_follows_local_wall_time(frequency, extra, after, expected):
    # Naive times are the computer's wall clock in any time zone; Windows has no time.tzset to pin one.
    values = {"frequency": frequency, "run_time": "09:00", "weekday": 0, "month_day": 1, **extra}
    assert next_run(values, after) == expected.astimezone(timezone.utc).isoformat()


def test_save_validates_inputs_and_schedules_the_next_run(service):
    source = prompt(service)
    with pytest.raises(UserError, match="할 일"):
        service.dispatch("schedules.save", {"prompt_id": source["id"], "variables": {}, "frequency": "daily"})
    for invalid in ({"frequency": "yearly"}, {"run_time": "24:00"}, {"weekday": 7},
                    {"month_day": 0}, {"enabled": "yes"}, {"frequency": "once"}):
        with pytest.raises(UserError):
            schedule(service, prompt=source, **invalid)
    with pytest.raises(UserError, match="지났습니다"):
        schedule(service, prompt=source, frequency="once", run_at="2020-01-01T09:00:00+09:00")
    saved = schedule(service, prompt=source, name="", variables={"할 일": "보고서", "unused": "x"})
    assert saved["name"] == "아침 정리" and saved["prompt_title"] == "아침 정리"
    assert saved["variables"] == {"할 일": "보고서"}
    assert saved["enabled"] is True and saved["notify"] is True
    assert datetime.fromisoformat(saved["next_run_at"]).astimezone().strftime("%H:%M") == "09:00"
    paused = service.dispatch("schedules.toggle", {"id": saved["id"], "enabled": False})
    assert paused["enabled"] is False and paused["next_run_at"] is None
    resumed = service.dispatch("schedules.toggle", {"id": saved["id"], "enabled": True})
    assert resumed["next_run_at"] == saved["next_run_at"]


def test_active_schedules_are_capped(service):
    source = prompt(service)
    for _ in range(MAX_ACTIVE):
        schedule(service, prompt=source)
    with pytest.raises(UserError, match="최대"):
        schedule(service, prompt=source)
    paused = schedule(service, prompt=source, enabled=False)
    with pytest.raises(UserError, match="최대"):
        service.dispatch("schedules.toggle", {"id": paused["id"], "enabled": True})


def test_due_occurrence_runs_once_and_missed_ones_collapse(service):
    saved = schedule(service)
    assert service.dispatch("schedules.due", {}) == []
    due = make_due(service, saved["id"])
    assert service.dispatch("schedules.due", {}) == [
        {"id": saved["id"], "name": "아침 정리", "next_run_at": due, "notify": True}]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: execute(service, saved["id"], due), range(2)))
    assert sorted(result["ran"] for result in results) == [False, True]
    result = next(result for result in results if result["ran"])
    assert result["run"]["status"] == "success" and result["schedule"]["notify"] is True
    assert len(service.llm.calls) == 1
    current = service.dispatch("schedules.list", {})[0]
    assert current["last_status"] == "success"
    assert datetime.fromisoformat(current["next_run_at"]) > datetime.now(timezone.utc)
    run = service.runs_get({"id": result["run"]["id"]})
    assert (run["kind"], run["schedule_id"], run["schedule_name"]) == ("scheduled", saved["id"], "아침 정리")
    assert run["input"] == "오늘 할 일: 보고서" and run["output"] == "예약 실행 결과"
    assert run["duration_ms"] >= 0 and run["started_at"] <= run["created_at"]


def test_one_time_schedule_finishes_and_frees_its_slot(service):
    saved = schedule(service, frequency="once", run_at=(datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    assert saved["next_run_at"] == saved["run_at"]
    due = make_due(service, saved["id"])
    assert execute(service, saved["id"], due)["ran"]
    done = service.dispatch("schedules.list", {})[0]
    assert (done["enabled"], done["next_run_at"], done["last_status"]) == (False, None, "success")
    with service.db.connect() as db:
        db.execute("UPDATE schedules SET run_at=? WHERE id=?", (due, saved["id"]))
    with pytest.raises(UserError, match="지났습니다"):
        service.dispatch("schedules.toggle", {"id": saved["id"], "enabled": True})


def test_failed_runs_are_recorded_for_the_history(service):
    saved = schedule(service)
    service.llm.error = UserError("LLM API에 연결할 수 없습니다.")
    result = execute(service, saved["id"], make_due(service, saved["id"]))
    assert result["run"]["status"] == "error" and "연결할 수 없습니다" in result["run"]["error"]
    assert SCHEDULE_INPUTS not in result["run"]["error"]  # Its inputs were fine.
    listed = service.dispatch("schedules.list", {})[0]
    assert listed["last_status"] == "error" and "연결할 수 없습니다" in listed["last_error"]
    # A failed manual run is recorded too, and the renderer still receives the error.
    with pytest.raises(UserError, match="연결할 수 없습니다"):
        service.prompts_run({"prompt_id": saved["prompt_id"], "variables": {"할 일": "메일"}})
    failures = service.runs_list({"status": "error"})
    assert [run["kind"] for run in failures] == ["manual", "scheduled"]
    assert failures[0]["preview"] == "LLM API에 연결할 수 없습니다."


def test_prompt_changes_and_deletion_reach_schedules(service):
    source = prompt(service)
    saved = schedule(service, prompt=source)
    service.prompts_save({**source, "template": "{{ 할 일 }} / {{ 마감 }}"})
    # The schedule keeps its inputs; the one the template gained is asked for in the schedule's form.
    with pytest.raises(UserError, match="‘마감’ 입력란이 비어 있습니다. 예약을 열어"):
        service.dispatch("schedules.run", {"id": saved["id"]})
    run = execute(service, saved["id"], make_due(service, saved["id"]))["run"]
    assert (run["status"], run["error"]) == ("error", f"‘마감’ 입력란이 비어 있습니다. {SCHEDULE_INPUTS}")
    assert not service.llm.calls
    with pytest.raises(UserError, match="‘마감’ 입력란이 비어 있습니다.$"):  # The form itself says only what is missing.
        schedule(service, prompt=source)
    filled = schedule(service, prompt=source, id=saved["id"], variables={"할 일": "보고서", "마감": "금요일"})
    assert service.dispatch("schedules.run", {"id": filled["id"]})["input"] == "보고서 / 금요일"
    service.prompts_delete({"id": source["id"]})
    assert service.dispatch("schedules.list", {}) == []


def test_run_now_is_a_manual_run_linked_to_the_schedule(service):
    saved = schedule(service)
    run = service.dispatch("schedules.run", {"id": saved["id"]})
    assert (run["kind"], run["schedule_id"], run["status"]) == ("manual", saved["id"], "success")
    current = service.dispatch("schedules.list", {})[0]
    assert current["last_status"] == "success" and current["next_run_at"] == saved["next_run_at"]
    assert [item["id"] for item in service.runs_list({"schedule_id": saved["id"]})] == [run["id"]]


def test_active_runs_and_daily_statistics(service):
    saved = schedule(service)
    service.llm.gate = threading.Event()
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(service.dispatch, "schedules.run", {"id": saved["id"]})
        for _ in range(200):
            if service.runs_active({}):
                break
            time.sleep(0.01)
        assert [(run["title"], run["schedule_id"]) for run in service.runs_active({})] == [("아침 정리", saved["id"])]
        service.llm.gate.set()
        running.result()
    assert service.runs_active({}) == []
    service.llm.gate, service.llm.error = None, UserError("실패")
    execute(service, saved["id"], make_due(service, saved["id"]))
    stats = service.runs_stats({"days": 7, "timezone_offset": -540})
    assert len(stats["days"]) == 7
    assert stats["days"][-1] == {"date": datetime.now(KST).date().isoformat(), "success": 1, "error": 1, "scheduled": 1}
    assert {k: stats["totals"][k] for k in ("total", "success", "error", "scheduled")} == {
        "total": 2, "success": 1, "error": 1, "scheduled": 1}
    assert stats["totals"]["average_ms"] >= 0 and stats["active_schedules"] == 1
    other = service.prompts_save({"title": "다른 프롬프트", "template": "메모"})
    assert service.runs_stats({"days": 7, "prompt_id": other["id"]})["totals"]["total"] == 0
    with service.db.connect() as db:  # Runs before the period are outside the totals.
        db.execute("UPDATE runs SET created_at='2020-01-01T00:00:00+00:00'")
    assert service.runs_stats({"days": 30})["totals"]["total"] == 0
    with pytest.raises(UserError):
        service.runs_stats({"days": 3})


def test_run_filters_and_pages(service):
    source = prompt(service)
    for task in ("보고서", "메일", "회의"):
        service.prompts_run({"prompt_id": source["id"], "variables": {"할 일": task}})
    first = service.runs_list({"limit": 2})
    assert len(first) == 2
    rest = service.runs_list({"before": first[-1]["created_at"]})
    assert len(rest) == 1 and rest[0]["id"] not in {run["id"] for run in first}
    assert len(service.runs_list({"query": "메일"})) == 1
    # A search shows the passage with the words, from the response or else from the prompt sent.
    with service.db.connect() as db:
        db.execute("UPDATE runs SET output=? WHERE input LIKE '%회의%'", ("서론 " * 200 + "**결론**: 회의 결과 공유",))
    assert service.runs_list({"query": "결론"})[0]["preview"].endswith("결론: 회의 결과 공유")
    assert service.runs_list({"query": "메일"})[0]["preview"].startswith("입력 · ")
    assert len(service.runs_list({"kind": "manual", "prompt_id": source["id"]})) == 3
    for invalid in ({"status": "running"}, {"kind": "auto"}, {"limit": 0}, {"limit": True}):
        with pytest.raises(UserError):
            service.runs_list(invalid)


def test_restart_marks_interrupted_runs_failed(tmp_path):
    path = tmp_path / "test.db"
    first = Service(Database(path), FakeLLM())
    saved = schedule(first)
    with first.db.connect() as db:
        db.execute("UPDATE schedules SET last_status='running' WHERE id=?", (saved["id"],))
    listed = Service(Database(path), FakeLLM()).dispatch("schedules.list", {})[0]
    assert listed["last_status"] == "error" and "중단" in listed["last_error"]


def test_legacy_runs_gain_outcome_columns(tmp_path):
    path = tmp_path / "legacy.db"
    db = sqlite3.connect(path)
    db.execute("""CREATE TABLE runs (id TEXT PRIMARY KEY, prompt_id TEXT, title TEXT NOT NULL,
        input TEXT NOT NULL, output TEXT NOT NULL, created_at TEXT NOT NULL)""")
    db.execute("INSERT INTO runs VALUES ('old','p','이전 실행','입력','출력','2026-01-01T00:00:00+00:00')")
    db.commit()
    db.close()
    run = Service(Database(path), FakeLLM()).runs_get({"id": "old"})
    assert (run["status"], run["kind"], run["error"], run["duration_ms"]) == ("success", "manual", "", None)
