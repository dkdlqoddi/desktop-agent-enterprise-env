"""The trash of what the user deletes beside documents (trash.py): a conversation, prompt run, task, prompt template,
schedule or automatic routine leaves its table, waits to be restored with what went with it, and is deleted for good
only by trash.purge or the retention policy."""

import json
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from agent import paging, routines, schedules, service as service_module, trash
from agent.db import Database
from agent.llm import UserError
from agent.service import Service, now


class FakeLLM:
    """Answers at once, or once `gate` opens."""

    def __init__(self):
        self.calls, self.gate = [], None

    def public(self):
        return {"configured": True}

    def complete(self, messages, **_):
        self.calls.append(messages)
        if self.gate:
            self.gate.wait(10)
        return "실행 결과"

    def structured(self, system, content, **_):
        return {"summary": "요약", "keywords": ["trash"], "relations": []}


@pytest.fixture
def service(tmp_path):
    found = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    yield found
    found.routines.shutdown()


def call(service, method, **params):
    return service.dispatch(method, params)


def ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def wait(condition, seconds=30):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.02)


def rows(service, table, order="rowid"):
    with service.db.connect() as db:
        return [dict(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY {order}")]


def entries(service, **params):
    return call(service, "trash.list", **params)["items"]


def prompt(service, title="아침 정리"):
    return call(service, "prompts.save", title=title, template="오늘 할 일: {{ 할 일 }}", category="업무")


def schedule(service, source, **values):
    return call(
        service,
        "schedules.save",
        **{"prompt_id": source["id"], "variables": {"할 일": "보고서"}, "frequency": "daily", **values},
    )


def conversation(service, ident, messages=4):
    stamp = ago(minutes=5)
    with service.db.connect() as db:
        db.execute("INSERT INTO conversations VALUES (?,?,?,?)", (ident, f"{ident} 질문", stamp, stamp))
        db.executemany(
            "INSERT INTO messages (conversation_id,role,content,citations,trace,created_at) VALUES (?,?,?,?,?,?)",
            [
                (
                    ident,
                    "user" if index % 2 == 0 else "assistant",
                    f"{ident} {index}",
                    json.dumps([{"id": "문서", "number": 1}] if index % 2 else []),
                    json.dumps({"terms": ["캐시"]}) if index % 2 else "",
                    stamp,
                )
                for index in range(messages)
            ],
        )


def test_a_deleted_task_waits_in_the_trash_without_reminding_and_comes_back_as_it_was(service):
    task = call(
        service, "tasks.save", title="보고서 제출", notes="금요일까지", remind_at=ago(minutes=1), repeat="daily"
    )
    assert [due["id"] for due in call(service, "reminders.due")] == [task["id"]]
    deleted = call(service, "tasks.delete", id=task["id"])
    assert deleted["ok"] and deleted["deleted"] == 1 and len(deleted["trash_ids"]) == 1
    # Nothing that reads the tasks meets it: the list, the reminders, the briefing.
    assert call(service, "tasks.list") == [] and call(service, "reminders.due") == []
    assert call(service, "briefing.get")["counts"]["open"] == 0
    listed = call(service, "trash.list")
    assert listed["days"] == service_module.TRASH_DAYS and listed["total"] == 1
    assert [{key: item[key] for key in ("id", "kind", "item_id", "title", "detail")} for item in listed["items"]] == [
        {
            "id": deleted["trash_ids"][0],
            "kind": "task",
            "item_id": task["id"],
            "title": "보고서 제출",
            "detail": "진행 중 · 알림 있음",
        }
    ]
    restored = call(service, "trash.restore", ids=deleted["trash_ids"])
    assert restored == {
        "restored": 1,
        "items": [{"id": deleted["trash_ids"][0], "kind": "task", "item_id": task["id"], "title": "보고서 제출"}],
        "notes": [],
    }
    assert rows(service, "tasks") == [task] and entries(service) == []
    # A reminder whose time passed in the trash is shown at the next look, as after the app was closed.
    assert [due["id"] for due in call(service, "reminders.due")] == [task["id"]]
    # Restoring it again, or deleting what is not there, changes nothing.
    assert call(service, "trash.restore", ids=deleted["trash_ids"])["restored"] == 0
    assert call(service, "tasks.delete", id="없는 할 일") == {"ok": True, "deleted": 0, "trash_ids": []}


def test_a_conversation_goes_with_its_messages_and_they_come_back_in_their_order(service):
    conversation(service, "첫 대화")
    conversation(service, "남는 대화", messages=2)
    before = rows(service, "messages", "seq")
    deleted = call(service, "assistant.delete", id="첫 대화")
    assert [item["id"] for item in call(service, "assistant.conversations")] == ["남는 대화"]
    assert call(service, "assistant.history", id="첫 대화") == []
    assert [(item["kind"], item["title"], item["detail"]) for item in entries(service)] == [
        ("conversation", "첫 대화 질문", "메시지 4개")
    ]
    call(service, "trash.restore", ids=deleted["trash_ids"])
    assert rows(service, "messages", "seq") == before
    history = call(service, "assistant.history", id="첫 대화")
    assert [message["content"] for message in history] == [f"첫 대화 {index}" for index in range(4)]
    assert history[1]["citations"] == [{"id": "문서", "number": 1}] and history[1]["trace"] == {"terms": ["캐시"]}
    # A number of theirs in use (a Wiki whose numbering began anew): they take new ones, in the same order.
    deleted = call(service, "assistant.delete", id="첫 대화")
    taken = before[1]["seq"]
    with service.db.connect() as db:
        db.execute(
            "INSERT INTO messages (seq,conversation_id,role,content,created_at) VALUES (?,?,?,?,?)",
            (taken, "남는 대화", "user", "그 번호", now()),
        )
    call(service, "trash.restore", ids=deleted["trash_ids"])
    assert [m["content"] for m in call(service, "assistant.history", id="첫 대화")] == [
        f"첫 대화 {index}" for index in range(4)
    ]
    assert [m["content"] for m in call(service, "assistant.history", id="남는 대화")] == [
        "그 번호",
        "남는 대화 0",
        "남는 대화 1",
    ]


def test_a_run_of_the_history_comes_back(service):
    source = prompt(service)
    run = call(service, "prompts.run", prompt_id=source["id"], variables={"할 일": "보고서"})
    deleted = call(service, "runs.delete", id=run["id"])
    assert call(service, "runs.list") == []
    assert [(item["kind"], item["title"], item["detail"]) for item in entries(service)] == [
        ("run", "아침 정리", "직접 실행")
    ]
    call(service, "trash.restore", ids=deleted["trash_ids"])
    assert call(service, "runs.get", id=run["id"])["output"] == "실행 결과"


def test_a_template_takes_its_schedules_along_and_brings_them_back_without_a_run_for_the_time_that_passed(service):
    source = prompt(service)
    on = schedule(service, source, name="매일")
    off = schedule(service, source, name="꺼 둔 예약", enabled=False)
    with service.db.connect() as db:  # Due now, as one whose time came while the app was closed.
        db.execute("UPDATE schedules SET next_run_at=? WHERE id=?", (ago(minutes=1), on["id"]))
    assert [due["id"] for due in call(service, "routines.due")] == [on["id"]]
    deleted = call(service, "prompts.delete", id=source["id"])
    assert call(service, "prompts.list") == [p for p in call(service, "prompts.list") if p["id"] != source["id"]]
    assert call(service, "schedules.list") == [] and call(service, "routines.due") == []
    assert [(item["kind"], item["title"], item["detail"]) for item in entries(service)] == [
        ("prompt", "아침 정리", "업무 · 예약 2개 포함")
    ]
    restored = call(service, "trash.restore", ids=deleted["trash_ids"])
    assert restored["restored"] == 1 and restored["notes"] == []
    assert source["id"] in {p["id"] for p in call(service, "prompts.list")}
    found = {item["id"]: item for item in call(service, "schedules.list")}
    assert set(found) == {on["id"], off["id"]}
    # The time that passed in the trash is not made up for: the next run is the rule's next from now.
    assert found[on["id"]]["enabled"] and found[on["id"]]["next_run_at"] > now()
    assert not found[off["id"]]["enabled"] and found[off["id"]]["next_run_at"] is None
    assert call(service, "routines.due") == []


def test_a_schedule_comes_back_with_the_template_deleted_after_it_and_not_when_that_is_gone_for_good(service):
    source = prompt(service)
    first, second = schedule(service, source, name="먼저 지운 예약"), schedule(service, source, name="함께 지운 예약")
    own = call(service, "schedules.delete", id=first["id"])["trash_ids"]
    template = call(service, "prompts.delete", id=source["id"])["trash_ids"]
    assert [(item["kind"], item["detail"]) for item in entries(service)] == [
        ("prompt", "업무 · 예약 1개 포함"),
        ("schedule", "‘아침 정리’ 템플릿의 예약"),
    ]
    restored = call(service, "trash.restore", ids=own)
    assert [(item["kind"], item["id"]) for item in restored["items"]] == [("prompt", template[0]), ("schedule", own[0])]
    assert {item["id"] for item in call(service, "schedules.list")} == {first["id"], second["id"]}
    assert entries(service) == []
    # The template purged from the trash: its schedule has nothing to run.
    own = call(service, "schedules.delete", id=first["id"])["trash_ids"]
    template = call(service, "prompts.delete", id=source["id"])["trash_ids"]
    assert call(service, "trash.purge", ids=template) == {"purged": 1}
    with pytest.raises(UserError, match="템플릿이 영구 삭제되어"):
        call(service, "trash.restore", ids=own)
    assert [item["id"] for item in entries(service)] == own


def test_a_schedule_whose_one_time_passed_or_beyond_the_limit_comes_back_turned_off(service, monkeypatch):
    source = prompt(service)
    once = schedule(
        service,
        source,
        name="한 번",
        frequency="once",
        run_at=(datetime.now().astimezone() + timedelta(hours=1)).isoformat(),
    )
    with service.db.connect() as db:
        db.execute(
            "UPDATE schedules SET run_at=?,next_run_at=? WHERE id=?", (ago(minutes=5), ago(minutes=5), once["id"])
        )
    restored = call(service, "trash.restore", ids=call(service, "schedules.delete", id=once["id"])["trash_ids"])
    assert restored["notes"] == ["‘한 번’ 예약은 실행 시각이 지나 꺼 둔 채로 복원했습니다. 새 시각을 정해 켜 주세요."]
    back = next(item for item in call(service, "schedules.list") if item["id"] == once["id"])
    assert (back["enabled"], back["next_run_at"]) == (False, None) and call(service, "routines.due") == []
    # One whose time is still to come keeps it.
    later = schedule(
        service,
        source,
        name="나중",
        frequency="once",
        run_at=(datetime.now().astimezone() + timedelta(hours=1)).isoformat(),
    )
    call(service, "trash.restore", ids=call(service, "schedules.delete", id=later["id"])["trash_ids"])
    back = next(item for item in call(service, "schedules.list") if item["id"] == later["id"])
    assert back["enabled"] and back["next_run_at"] == later["run_at"]
    # As many on as may be: one more comes back turned off, and says so.
    monkeypatch.setattr(schedules, "MAX_ACTIVE", 2)
    daily = schedule(service, source, name="매일")
    own = call(service, "schedules.delete", id=daily["id"])["trash_ids"]
    schedule(service, source, name="그 자리")
    restored = call(service, "trash.restore", ids=own)
    assert restored["notes"] == ["켜 둔 예약이 2개여서 ‘매일’ 예약은 꺼 둔 채로 복원했습니다."]
    assert not next(item for item in call(service, "schedules.list") if item["id"] == daily["id"])["enabled"]


def test_a_schedule_deleted_while_it_ran_shows_how_that_run_went_once_it_is_back(service):
    source = prompt(service)
    item = schedule(service, source)
    with service.db.connect() as db:
        db.execute("UPDATE schedules SET last_status='running' WHERE id=?", (item["id"],))
    own = call(service, "schedules.delete", id=item["id"])["trash_ids"]
    # Its run ended while it was in the trash, and the history kept it.
    run = service.record_run(
        {
            "id": "run-1",
            "prompt_id": source["id"],
            "title": "아침 정리",
            "input": "입력",
            "output": "",
            "status": "error",
            "error": "LLM 오류",
            "kind": "scheduled",
            "schedule_id": item["id"],
            "started_at": now(),
            "duration_ms": 5,
        }
    )
    call(service, "trash.restore", ids=own)
    back = call(service, "schedules.list")[0]
    assert (back["last_status"], back["last_error"], back["last_run_at"]) == ("error", "LLM 오류", run["created_at"])
    # Without a run in the history it never ran.
    with service.db.connect() as db:
        db.execute("UPDATE schedules SET last_status='running',last_run_at=NULL WHERE id=?", (item["id"],))
        db.execute("DELETE FROM runs")
    call(service, "trash.restore", ids=call(service, "schedules.delete", id=item["id"])["trash_ids"])
    assert call(service, "schedules.list")[0]["last_status"] is None


def test_a_schedule_restored_while_its_run_still_goes_on_takes_how_that_run_ends(service):
    source = prompt(service)
    item = schedule(service, source)
    with service.db.connect() as db:
        db.execute("UPDATE schedules SET next_run_at=? WHERE id=?", (ago(minutes=1), item["id"]))
    service.llm.gate = threading.Event()
    due = call(service, "routines.due")[0]
    assert call(service, "routines.execute", id=item["id"], next_run_at=due["next_run_at"], schedule=True)["queued"]
    wait(lambda: call(service, "runs.active"))
    # Deleted while its LLM request is under way, the run goes on; the schedule is back before it ends.
    own = call(service, "schedules.delete", id=item["id"])["trash_ids"]
    assert call(service, "trash.restore", ids=own)["restored"] == 1
    assert call(service, "schedules.list")[0]["last_status"] == "running"
    service.llm.gate.set()
    wait(lambda: not service.routines.holding(f"schedule:{item['id']}"))
    back = call(service, "schedules.list")[0]
    assert back["last_status"] == "success" and back["last_run_at"] and back["next_run_at"] > now()


def save(service, kind, **values):
    return call(service, "routines.save", kind=kind, allow_files=True, **values)


def test_a_routine_goes_with_its_runs_and_comes_back_without_running_at_once(service):
    service.wiki_save({"title": "새 문서", "content": "내용"})
    routine = save(service, "analyze_new", name="새 문서 분석", config={"limit": 10, "retry": False})
    call(service, "routines.run", id=routine["id"], logins=[], allow_files=True)
    wait(lambda: not service.routines.pending(routine["id"]))
    runs = call(service, "routines.runs", id=routine["id"])
    assert [run["status"] for run in runs] == ["success"]
    with service.db.connect() as db:  # Overdue, as an interval that ended while the app was closed.
        db.execute("UPDATE routines SET next_run_at=? WHERE id=?", (ago(minutes=1), routine["id"]))
    assert [due["id"] for due in call(service, "routines.due")] == [routine["id"]]
    saved = rows(service, "routines")[0]
    deleted = call(service, "routines.delete", id=routine["id"])
    assert deleted["deleted"] == 1
    assert call(service, "routines.list") == [] and call(service, "routines.runs") == []
    assert call(service, "routines.due") == []
    item = entries(service)[0]
    assert (item["kind"], item["title"], item["recipe"]) == ("routine", "새 문서 분석", "analyze_new")
    assert item["detail"] == f"{routines.RECIPES['analyze_new'].title} · 실행 기록 1개 포함"
    assert call(service, "trash.routines", ids=deleted["trash_ids"]) == [
        {"id": deleted["trash_ids"][0], "recipe": "analyze_new", "files": False, "needs": []}
    ]
    before = now()
    assert call(service, "trash.restore", ids=deleted["trash_ids"])["notes"] == []
    back = rows(service, "routines")[0]
    # One interval from now, rather than at once for the time that passed.
    assert back["next_run_at"] >= (datetime.fromisoformat(before) + timedelta(minutes=back["interval"])).isoformat()
    assert {**back, "next_run_at": None} == {**saved, "next_run_at": None}
    assert [run["id"] for run in call(service, "routines.runs", id=routine["id"])] == [runs[0]["id"]]
    assert call(service, "routines.due") == []
    # A time of its own that is still to come stays, and a rule of the clock gives its next time.
    later = (datetime.now(timezone.utc) + timedelta(minutes=7)).isoformat()
    with service.db.connect() as db:
        db.execute("UPDATE routines SET next_run_at=? WHERE id=?", (later, routine["id"]))
    call(service, "trash.restore", ids=call(service, "routines.delete", id=routine["id"])["trash_ids"])
    assert rows(service, "routines")[0]["next_run_at"] == later
    call(service, "routines.save", id=routine["id"], frequency="daily", run_time="09:00")
    with service.db.connect() as db:
        db.execute("UPDATE routines SET next_run_at=? WHERE id=?", (ago(days=1), routine["id"]))
    call(service, "trash.restore", ids=call(service, "routines.delete", id=routine["id"])["trash_ids"])
    assert rows(service, "routines")[0]["next_run_at"] > now()


def test_the_run_a_delete_stopped_is_shown_as_stopped_when_the_routine_is_back(service):
    routine = save(service, "analyze_new", enabled=False)
    with service.db.connect() as db:
        db.execute(
            "INSERT INTO routine_runs (id,routine_id,started_by,status,created_at,started_at) VALUES (?,?,?,?,?,?)",
            ("run-1", routine["id"], "manual", "running", ago(minutes=2), ago(minutes=2)),
        )
        db.execute("UPDATE routines SET last_status='running' WHERE id=?", (routine["id"],))
    call(service, "trash.restore", ids=call(service, "routines.delete", id=routine["id"])["trash_ids"])
    run = rows(service, "routine_runs")[0]
    assert (run["status"], bool(run["finished_at"])) == ("cancelled", True)
    assert rows(service, "routines")[0]["last_status"] == "cancelled"


def test_a_routine_that_reads_files_comes_back_only_for_a_caller_who_may_read_them(service, tmp_path, monkeypatch):
    folder = tmp_path / "자료"
    folder.mkdir()
    reader = save(service, "source_refresh", name="파일 업데이트", config={"paths": str(folder)})
    own = call(service, "routines.delete", id=reader["id"])["trash_ids"]
    assert call(service, "trash.routines", ids=own)[0]["files"] is True
    # An AI tool below the "full" access level: Electron does not say that it may have files read.
    with pytest.raises(UserError, match="이 PC의 파일을 읽는 자동 작업"):
        call(service, "trash.restore", ids=own)
    assert [item["id"] for item in entries(service)] == own and call(service, "routines.list") == []
    assert call(service, "trash.restore", ids=own, allow_files=True)["restored"] == 1
    # As many on as may be: it comes back turned off, and says so. No more than may be at all: it stays in the trash.
    own = call(service, "routines.delete", id=reader["id"])["trash_ids"]
    monkeypatch.setattr(routines, "MAX_ACTIVE", 1)
    save(service, "analyze_new")
    restored = call(service, "trash.restore", ids=own, allow_files=True)
    assert restored["notes"] == ["켜 둔 자동 작업이 1개여서 ‘파일 업데이트’은(는) 꺼 둔 채로 복원했습니다."]
    back = call(service, "routines.get", id=reader["id"])
    assert (back["enabled"], back["next_run_at"]) == (False, None)
    own = call(service, "routines.delete", id=reader["id"])["trash_ids"]
    monkeypatch.setattr(routines, "MAX_ROUTINES", 1)
    with pytest.raises(UserError, match="최대 1개까지"):
        call(service, "trash.restore", ids=own, allow_files=True)


def test_the_routines_of_a_recipe_the_main_process_names_are_left_out_for_an_ai_tool(service):
    web = save(service, "web_capture", config={"site": "naver", "query": "운동화"})
    other = save(service, "analyze_new")
    hidden = call(service, "routines.delete", id=web["id"])["trash_ids"]
    shown = call(service, "routines.delete", id=other["id"])["trash_ids"]
    task = call(service, "tasks.delete", id=call(service, "tasks.save", title="할 일")["id"])["trash_ids"]
    without = ["web_capture"]
    listed = call(service, "trash.list", without_recipes=without)
    assert {item["id"] for item in listed["items"]} == {*shown, *task} and listed["total"] == 2
    assert call(service, "trash.list")["total"] == 3
    # Named or not, it neither comes back nor goes for good.
    assert call(service, "trash.restore", ids=hidden, without_recipes=without)["restored"] == 0
    assert call(service, "trash.purge", ids=hidden, without_recipes=without) == {"purged": 0}
    assert call(service, "trash.purge", all=True, without_recipes=without) == {"purged": 2}
    assert [item["id"] for item in entries(service)] == hidden
    # The user's own screen restores it.
    assert call(service, "trash.restore", ids=hidden, allow_files=True)["restored"] == 1
    assert [item["kind"] for item in call(service, "routines.list")] == ["web_capture"]


def test_purged_entries_are_gone_for_good_and_old_ones_go_with_the_retention_policy(service):
    ids = [
        call(service, "tasks.delete", id=call(service, "tasks.save", title=f"할 일 {index}")["id"])["trash_ids"][0]
        for index in range(4)
    ]
    assert call(service, "trash.purge", ids=ids[:1]) == {"purged": 1}
    assert call(service, "trash.restore", ids=ids[:1])["restored"] == 0 and call(service, "tasks.list") == []
    # Past TRASH_DAYS: the retention policy removes it, as it does the documents of the trash.
    with service.db.connect() as db:
        db.execute("UPDATE trash SET deleted_at=? WHERE id=?", (ago(days=service_module.TRASH_DAYS + 1), ids[1]))
    assert service.retain()["trash"] == 1
    assert {item["id"] for item in entries(service)} == set(ids[2:])
    assert service.retain()["trash"] == 0
    assert call(service, "trash.purge", all=True) == {"purged": 2} and entries(service) == []
    for params in ({"ids": []}, {"ids": "전부"}, {"ids": [1]}, {"all": "예"}, {"ids": ["가"], "without_recipes": "웹"}):
        with pytest.raises(UserError):
            call(service, "trash.purge", **params)
    with pytest.raises(UserError, match="1~1,000개"):
        call(service, "trash.restore", ids=[])


def test_a_restore_is_all_or_none_and_refuses_an_id_in_use_again(service):
    first = call(service, "tasks.save", id="task-1", title="먼저")
    second = call(service, "tasks.save", id="task-2", title="나중")
    ids = [call(service, "tasks.delete", id=task["id"])["trash_ids"][0] for task in (first, second)]
    call(service, "tasks.save", id="task-2", title="같은 ID로 다시 만든 할 일")
    with pytest.raises(UserError, match="‘나중’ 할 일: 같은 ID의 항목이 이미 있어"):
        call(service, "trash.restore", ids=ids)
    # Neither came back: the first waits in the trash with the second.
    assert [task["title"] for task in call(service, "tasks.list")] == ["같은 ID로 다시 만든 할 일"]
    assert {item["id"] for item in entries(service)} == set(ids)
    assert call(service, "trash.restore", ids=ids[:1])["restored"] == 1


def test_a_task_comes_back_without_the_document_purged_meanwhile_and_by_the_columns_of_now(service):
    doc = service.wiki_save({"title": "출처", "content": "내용"})
    task = call(service, "tasks.save", title="출처 있는 할 일", source_id=doc["id"], assignee="담당")
    own = call(service, "tasks.delete", id=task["id"])["trash_ids"]
    call(service, "wiki.delete", id=doc["id"])
    call(service, "wiki.purge", ids=[doc["id"]])
    # What an earlier version kept: a column this one no longer has, and none of one it has since.
    with service.db.connect() as db:
        payload = json.loads(db.execute("SELECT payload FROM trash WHERE id=?", own).fetchone()[0])
        payload["priority"] = "high"
        del payload["assignee"]
        db.execute("UPDATE trash SET payload=? WHERE id=?", (json.dumps(payload), own[0]))
    call(service, "trash.restore", ids=own)
    back = call(service, "tasks.list")[0]
    assert (back["title"], back["source_id"], back["assignee"]) == ("출처 있는 할 일", None, "")
    # An entry of a kind this version does not know, or one it cannot read, stays where it is and says so.
    with service.db.connect() as db:
        db.execute(
            "INSERT INTO trash (id,kind,item_id,title,payload,deleted_at) VALUES ('e1','note','n1','메모','{}',?)",
            (now(),),
        )
        db.execute(
            "INSERT INTO trash (id,kind,item_id,title,payload,deleted_at) VALUES ('e2','task','t9','깨진 할 일','[',?)",
            (now(),),
        )
    with pytest.raises(UserError, match="이 버전에서 복원할 수 없는 종류"):
        call(service, "trash.restore", ids=["e1"])
    with pytest.raises(UserError, match="읽을 수 없어 복원하지 못했습니다"):
        call(service, "trash.restore", ids=["e2"])
    assert {item["id"] for item in entries(service)} == {"e1", "e2"}


def test_the_list_answers_the_newest_entries_and_counts_them_all(service, monkeypatch):
    monkeypatch.setattr(trash, "MAX_ENTRIES", 3)
    for index in range(5):
        call(service, "tasks.delete", id=call(service, "tasks.save", title=f"할 일 {index}")["id"])
    listed = call(service, "trash.list")
    assert [item["title"] for item in listed["items"]] == ["할 일 4", "할 일 3", "할 일 2"] and listed["total"] == 5


def test_the_list_comes_in_pages_of_all_kinds_or_one_and_counts_each_kind(service):
    binned = [
        call(service, "tasks.delete", id=call(service, "tasks.save", title=f"할 일 {index}")["id"])["trash_ids"][0]
        for index in range(5)
    ]
    run = call(service, "prompts.run", prompt_id=prompt(service)["id"], variables={"할 일": "보고서"})
    binned += call(service, "runs.delete", id=run["id"])["trash_ids"]
    # Entries deleted in the same instant (as the retention policy moves them) go by their titles, then their IDs.
    with service.db.connect() as db:
        db.execute("UPDATE trash SET deleted_at=? WHERE kind='task'", (ago(minutes=1),))
        db.execute("UPDATE trash SET title='할 일 1' WHERE id=?", (binned[3],))
    order = [binned[5], binned[0], *sorted([binned[1], binned[3]]), binned[2], binned[4]]
    assert [item["id"] for item in call(service, "trash.list")["items"]] == order
    seen, cursor, pages = [], None, 0
    while True:
        page = call(service, "trash.list", limit=2, **({"cursor": cursor} if cursor else {}))
        assert page["total"] == 6 and page["kinds"] == {"run": 1, "task": 5} and len(page["items"]) <= 2
        seen += [item["id"] for item in page["items"]]
        cursor, pages = page["next_cursor"], pages + 1
        if cursor is None:
            break
    # Every entry once, in the order of the whole list, the two of one title and one instant apart too.
    assert seen == order and pages == 3
    # One kind's entries, with the counts of every kind all the same.
    first = call(service, "trash.list", kind="task", limit=4)
    rest = call(service, "trash.list", kind="task", limit=4, cursor=first["next_cursor"])
    assert [item["id"] for item in first["items"] + rest["items"]] == order[1:]
    assert (first["total"], rest["next_cursor"], rest["kinds"]) == (5, None, {"run": 1, "task": 5})
    assert call(service, "trash.list", kind="schedule", limit=10) == {
        "items": [],
        "total": 0,
        "kinds": {"run": 1, "task": 5},
        "days": service_module.TRASH_DAYS,
        "next_cursor": None,
    }
    # Without a page the newest MAX_ENTRIES, and no cursor.
    unpaged = call(service, "trash.list", kind="task")
    assert [item["id"] for item in unpaged["items"]] == order[1:] and "next_cursor" not in unpaged
    for params in ({"kind": "note"}, {"limit": 0}, {"limit": 501}, {"limit": "10"}, {"cursor": "이어서"}):
        with pytest.raises(UserError):
            call(service, "trash.list", **params)
    # A cursor of another list is refused rather than read as one of these.
    with pytest.raises(UserError, match="cursor"):
        call(service, "trash.list", cursor=paging.encode("conversations", now(), "x"))


# The kinds the main process lets an AI tool meet in the trash (electron/api-catalog.cjs TRASH_KINDS).
TOOL_KINDS = ["conversation", "run", "task", "prompt", "schedule", "routine"]


def test_what_the_chat_and_the_web_records_put_in_the_trash_is_none_of_an_ai_tools(service):
    with service.db.connect() as db:
        service.agent.store(db, "user", "내 메일함을 정리해 줘")
    chat = call(service, "agent.clear")["trash_ids"]
    page = {"url": "https://shop.example.test/", "items": [{"ref": 0, "kind": "ad", "title": "광고"}]}
    record = service.web._keep("coupang", "병원 예약", "chat", "t1", page, b"jpeg")["capture_id"]
    web = call(service, "web.delete_capture", id=record)["trash_ids"]
    task = call(service, "tasks.delete", id=call(service, "tasks.save", title="할 일")["id"])["trash_ids"]
    hidden = {"only_kinds": TOOL_KINDS, "without_recipes": ["web_capture"]}
    # A tool's list has neither their entries nor their titles, nor counts them, in pages or not, of a kind or all.
    for params in ({}, {"limit": 10}, {"kind": "chat"}, {"kind": "web_capture", "limit": 10}):
        listed = call(service, "trash.list", **hidden, **params)
        assert [item["id"] for item in listed["items"]] == ([] if "kind" in params else task)
        assert listed["kinds"] == {"task": 1} and listed["total"] == (0 if "kind" in params else 1)
        assert "메일함" not in json.dumps(listed, ensure_ascii=False) and "병원" not in str(listed)
    # Named or not, they neither come back nor go for good, one by one or with all of the trash.
    assert call(service, "trash.restore", ids=[*chat, *web], allow_files=True, **hidden)["restored"] == 0
    assert call(service, "trash.purge", ids=[*chat, *web], **hidden) == {"purged": 0}
    assert call(service, "trash.purge", all=True, **hidden) == {"purged": 1}
    assert {item["id"] for item in entries(service)} == {*chat, *web}
    # The user's own screen meets them all and restores them.
    assert call(service, "trash.restore", ids=[*chat, *web])["restored"] == 2
    assert [m["content"] for m in call(service, "agent.messages")] == ["내 메일함을 정리해 줘"]
    assert [item["id"] for item in service.web.captures({})["captures"]] == [record]
    for value in ([], ["note"], "task", [1]):
        with pytest.raises(UserError, match="only_kinds"):
            call(service, "trash.list", only_kinds=value)
