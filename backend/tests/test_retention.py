"""The retention policy of what the Wiki logs (Service.retain), applied at each start, and the daily upkeep that backs
up the Wiki and applies the policy, started from the routine runner's look at what is due (routines.due)."""

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from agent import service as service_module, storage
from agent.db import Database
from agent.service import Service


def ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


@pytest.fixture
def service(tmp_path):
    return Service(Database(tmp_path / "wiki.db"), None)


def add_runs(service, stamps):
    with service.db.connect() as db:
        db.executemany(
            "INSERT INTO runs (id,prompt_id,title,input,output,created_at) VALUES (?,NULL,'실행','입력','출력',?)",
            [(f"run-{index}", stamp) for index, stamp in enumerate(stamps)],
        )


def add_conversation(service, ident, updated_at, messages):
    with service.db.connect() as db:
        db.execute("INSERT INTO conversations VALUES (?,?,?,?)", (ident, ident, updated_at, updated_at))
        db.executemany(
            "INSERT INTO messages (conversation_id,role,content,created_at) VALUES (?,?,?,?)",
            [
                (ident, "user" if index % 2 == 0 else "assistant", f"{ident} {index}", updated_at)
                for index in range(messages)
            ],
        )


def test_the_policy_keeps_the_newest_runs_messages_and_conversations(service, monkeypatch):
    for name, value in (("RUNS_KEPT", 5), ("MESSAGES_KEPT", 4), ("CONVERSATIONS_KEPT", 2)):
        monkeypatch.setattr(service_module, name, value)
    # Three runs older than RUNS_DAYS and eight of the last days, of which the newest five stay.
    add_runs(
        service,
        [ago(days=service_module.RUNS_DAYS + 1, minutes=index) for index in range(3)]
        + [ago(hours=index) for index in range(8)],
    )
    for index, ident in enumerate(("오늘", "어제", "지난주")):
        add_conversation(service, ident, ago(days=index * 3), messages=6)
    old, recent = (
        service.wiki_save({"title": title, "content": "글"})["id"] for title in ("오래된 휴지통", "최근 휴지통")
    )
    service.wiki_delete({"id": old})
    service.wiki_delete({"id": recent})
    with service.db.connect() as db:
        db.execute("UPDATE documents SET deleted_at=? WHERE id=?", (ago(days=service_module.TRASH_DAYS + 1), old))
    service.backup("manual")  # Which still holds what the policy removes.
    assert service.retain() == {"runs": 6, "conversations": 1, "messages": 4, "documents": 1}
    with service.db.connect() as db:
        assert [row[0] for row in db.execute("SELECT id FROM runs ORDER BY created_at DESC")] == [
            f"run-{index}" for index in range(3, 8)
        ]
        kept = {
            row[0]: [
                m[0] for m in db.execute("SELECT content FROM messages WHERE conversation_id=? ORDER BY seq", (row[0],))
            ]
            for row in db.execute("SELECT id FROM conversations")
        }
    assert kept == {
        "오늘": [f"오늘 {index}" for index in range(2, 6)],
        "어제": [f"어제 {index}" for index in range(2, 6)],
    }
    assert [doc["id"] for doc in service.wiki_trash({})["documents"]] == [recent]
    # Nothing more to remove: the same again changes nothing.
    assert service.retain() == {"runs": 0, "conversations": 0, "messages": 0, "documents": 0}


def test_at_each_start_the_trash_is_emptied_and_the_logs_trimmed_once_a_backup_holds_them(service):
    add_runs(service, [ago(days=service_module.RUNS_DAYS + 1), ago(minutes=1)])
    old = service.wiki_save({"title": "오래된 휴지통", "content": "글"})["id"]
    service.wiki_delete({"id": old})
    with service.db.connect() as db:
        db.execute("UPDATE documents SET deleted_at=? WHERE id=?", (ago(days=service_module.TRASH_DAYS + 1), old))
    # The first start of this version: no backup holds the old run yet, so it stays; the trash goes as it always did.
    first = Service(Database(service.db.path), None)
    assert [run["id"] for run in first.runs_list({})] == ["run-1", "run-0"]
    assert first.wiki_trash({})["documents"] == [] and first.retained_at == 0
    first.dispatch("wiki.backup", {})
    assert [run["id"] for run in Service(Database(service.db.path), None).runs_list({})] == ["run-1"]


def test_the_daily_upkeep_starts_from_the_routine_runner_and_runs_once_a_day(service):
    folder = storage.backup_folder(service.db.path)
    add_runs(service, [ago(days=service_module.RUNS_DAYS + 1)])
    # Not in the first minute, while the screens load.
    service.dispatch("routines.due", {})
    assert service.upkeep_thread is None and not folder.exists()
    service.upkeep_at = 0
    assert service.dispatch("routines.due", {}) == []
    service.upkeep_thread.join(30)
    first = service.dispatch("wiki.backups", {})["backups"]
    assert [item["kind"] for item in first] == ["auto"]
    # The logs waited for this first backup, which holds the old run.
    assert service.runs_list({}) == []
    # Looked at again only after UPKEEP_EVERY, and no second backup the same day.
    thread = service.upkeep_thread
    service.dispatch("routines.due", {})
    assert service.upkeep_thread is thread
    service.upkeep_at = 0
    service.dispatch("routines.due", {})
    service.upkeep_thread.join(30)
    assert service.dispatch("wiki.backups", {})["backups"] == first
    # A day later (the newest daily backup's name says yesterday) a new one, and the policy again.
    yesterday = (datetime.now() - timedelta(days=1, minutes=1)).strftime("%Y%m%d-%H%M%S")
    os.rename(folder / first[0]["file"], folder / f"wiki-{yesterday}.db")
    add_runs(service, [ago(days=service_module.RUNS_DAYS + 1)])
    service.retained_at = time.time() - service_module.RETAIN_EVERY
    service.upkeep_at = 0
    service.dispatch("routines.due", {})
    service.upkeep_thread.join(30)
    assert [item["kind"] for item in service.dispatch("wiki.backups", {})["backups"]] == ["auto", "auto"]
    assert service.runs_list({}) == []


def test_a_daily_backup_that_fails_is_tried_again_at_the_next_look(service, monkeypatch):
    def gone(*_):
        raise OSError("드라이브 없음")

    add_runs(service, [ago(days=service_module.RUNS_DAYS + 1)])
    monkeypatch.setattr(storage, "snapshot", gone)
    service.daily()
    # No backup holds the old run: it stays until one does.
    assert storage.backups(service.db.path) == [] and len(service.runs_list({})) == 1
    monkeypatch.undo()
    with service.backup_lock:  # A backup asked for is being made: this look leaves it.
        service.daily()
    assert storage.backups(service.db.path) == [] and len(service.runs_list({})) == 1
    service.daily()
    assert [item["kind"] for item in storage.backups(service.db.path)] == ["auto"]
    assert service.runs_list({}) == [] and service.retained_at > 0
