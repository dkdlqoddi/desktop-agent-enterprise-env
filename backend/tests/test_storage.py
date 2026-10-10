"""The Wiki's database file (agent/storage.py). An earlier version's Wiki copied into the folder chosen on the first-run
screen (main.py --migrate-storage): written under a name of its own and renamed to wiki.db only when whole, so a copy
stopped midway or failed never leaves the folder refused as "wiki.db가 이미 있습니다". The check before every start opens
the Wiki, its backups in the backups folder, and the restore of one at the next start."""

import contextlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent import migrations, storage
from agent.db import Database
from agent.llm import UserError
from agent.service import Service
from agent.storage import Refused, migrate_database

BACKEND = Path(__file__).resolve().parents[1]


def wiki(path, rows=50):
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE example(value TEXT)")
        db.executemany("INSERT INTO example VALUES (?)", [("기록 " + "가" * 500,)] * rows)
        db.commit()
    return path


def leftovers(folder):
    return sorted(path.name for path in folder.iterdir() if storage.PARTIAL in path.name)


def test_a_copy_reports_progress_and_takes_its_name_only_when_whole(tmp_path, monkeypatch):
    source, destination = wiki(tmp_path / "old.db", rows=400), tmp_path / "new" / "wiki.db"
    monkeypatch.setattr(storage, "STEP_PAGES", 5)
    seen = []

    def progress():
        # Until the copy is checked, the folder holds no wiki.db: only the copy under its own name (and its journal).
        seen.append((destination.exists(), {name.split("-")[0] for name in leftovers(destination.parent)}))

    migrate_database(source, destination, progress)
    assert len(seen) > 10
    names = {name for exists, partial in seen for name in partial}
    assert not any(exists for exists, _ in seen) and len(names) == 1
    assert names.pop().startswith("wiki.db.") and all(partial for _, partial in seen)
    with contextlib.closing(sqlite3.connect(destination)) as db:
        assert db.execute("SELECT count(*) FROM example").fetchone()[0] == 400
    assert leftovers(destination.parent) == []


def test_what_a_stopped_copy_left_is_removed_and_the_copy_made_again(tmp_path):
    source, folder = wiki(tmp_path / "old.db"), tmp_path / "chosen"
    folder.mkdir()
    # Electron stopped a copy that made no progress: its file and SQLite's journal stay behind.
    (folder / "wiki.db.0123abcd.copying").write_bytes(b"half a database")
    (folder / "wiki.db.0123abcd.copying-journal").write_bytes(b"journal")
    (folder / "notes.txt").write_text("사용자 파일", encoding="utf-8")
    migrate_database(source, folder / "wiki.db")
    assert sorted(path.name for path in folder.iterdir()) == ["notes.txt", "wiki.db"]


def test_a_failed_copy_leaves_no_wiki_and_the_original_as_it_was(tmp_path):
    source, destination = tmp_path / "old.db", tmp_path / "chosen" / "wiki.db"
    source.write_bytes(b"not a database" * 100)
    with pytest.raises(sqlite3.DatabaseError):
        migrate_database(source, destination)
    assert not destination.exists() and leftovers(destination.parent) == []
    assert source.read_bytes() == b"not a database" * 100


def test_a_wiki_that_appears_meanwhile_is_never_replaced(tmp_path):
    source, destination = wiki(tmp_path / "old.db"), tmp_path / "chosen" / "wiki.db"

    def progress():
        if not destination.exists():
            destination.write_bytes(b"another Wiki")

    with pytest.raises(FileExistsError):
        migrate_database(source, destination, progress)
    assert destination.read_bytes() == b"another Wiki"
    assert leftovers(destination.parent) == []


def test_the_command_line_as_electron_runs_it(tmp_path):
    source, destination = wiki(tmp_path / "old.db"), tmp_path / "chosen" / "wiki.db"
    command = [sys.executable, str(BACKEND / "main.py"), "--migrate-storage"]
    result = subprocess.run(
        [*command, str(source), str(destination)], cwd=BACKEND, capture_output=True, encoding="utf-8", timeout=60
    )
    assert (result.returncode, result.stdout.splitlines()[:1], result.stderr) == (0, ["copying"], "")
    assert destination.exists()
    result = subprocess.run(
        [*command, str(source), str(destination)], cwd=BACKEND, capture_output=True, encoding="utf-8", timeout=60
    )
    assert result.returncode == 1
    assert (
        result.stderr.strip()
        == "기존 저장소를 복사하지 못했습니다. 대상 폴더, 기존 파일, 권한과 디스크 공간을 확인해 주세요."
    )


# --- The check before the Wiki opens, its backups and the restore of one ---------------------------------------------


def filled(path, documents=20):
    """A Wiki of `documents` documents, written through the service as the app writes it."""
    service = Service(Database(path), None)
    for index in range(documents):
        service.wiki_save({"title": f"문서 {index}", "content": f"{index}번 문서의 내용 " * 200})
    return service


def corrupt(path):
    """Overwrite pages in the middle of the Wiki at `path`, as a failing disk or a copy cut short would."""
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    data = bytearray(path.read_bytes())
    assert len(data) > 4096 * 8
    data[4096 * 2 : 4096 * 6] = b"\xa5" * (4096 * 4)
    path.write_bytes(bytes(data))


def names(folder):
    return sorted(path.name for path in folder.iterdir())


def test_a_damaged_wiki_is_refused_with_what_to_do_and_left_as_it_is(tmp_path):
    path = tmp_path / "wiki.db"
    newest = filled(path).dispatch("wiki.backup", {})["file"]
    corrupt(path)
    damaged = path.read_bytes()
    with pytest.raises(Refused) as refused:
        Database(path)
    message = str(refused.value)
    assert f"손상되어 열지 않았습니다: {path}." in message
    # How to put the newest backup in its place by hand, the damaged one's WAL files included.
    assert f"백업 폴더({tmp_path / 'backups'})의 가장 최근 백업({newest})" in message
    assert "wiki.db, wiki.db-wal, wiki.db-shm 파일을 다른 폴더로 옮긴 다음" in message
    assert path.read_bytes() == damaged
    # A file that is no database at all, in a folder without backups.
    other = tmp_path / "other" / "wiki.db"
    other.parent.mkdir()
    other.write_bytes(b"no database" * 1000)
    with pytest.raises(Refused, match="백업 폴더.*에 백업이 없습니다"):
        Database(other)
    assert other.read_bytes() == b"no database" * 1000


def test_a_check_that_outlasts_its_time_opens_the_wiki_unchecked(tmp_path, monkeypatch):
    """quick_check reads every page, and Electron gives the backend's start a minute: a Wiki too large to read in time
    opens unchecked rather than never."""
    path = tmp_path / "wiki.db"
    filled(path, documents=2)
    # A time already past: the clock of Windows before Python 3.13 moves in steps of 15.6 ms, as long as a small check.
    monkeypatch.setattr(storage, "CHECK_STEPS", 1)
    assert storage.inspect(path, budget=-1)["state"] == "unchecked"
    monkeypatch.setattr(storage, "CHECK_BUDGET", -1)
    assert len(Service(Database(path), None).wiki_list({})) == 2


def test_what_a_check_its_time_stopped_found_is_no_verdict(tmp_path, monkeypatch):
    """Stopped midway, the b-tree walk of SQLite before 3.41 reads the pages it never reached as faults: a Wiki is
    refused only for what a whole check found."""
    path = tmp_path / "nulls.db"
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE notes (text TEXT)")
        db.execute("INSERT INTO notes VALUES (NULL)")
        db.execute("PRAGMA writable_schema=ON")
        db.execute("UPDATE sqlite_master SET sql='CREATE TABLE notes (text TEXT NOT NULL)' WHERE name='notes'")
        db.commit()
    found = storage.inspect(path)
    assert found["state"] == "damaged" and "NULL value in notes.text" in found["detail"]
    monkeypatch.setattr(storage, "CHECK_STEPS", 10**9)  # Only the time decides, as the old walk never asks.
    assert storage.inspect(path, budget=-1)["state"] == "unchecked"


def test_the_backend_answers_every_request_with_why_the_wiki_did_not_open(tmp_path):
    """Electron's start asks health first and shows the answer on the first-run screen; a backend that ended instead
    would leave it only "the backend ended" to say."""
    folder = tmp_path / "Wiki"
    folder.mkdir()
    (folder / "wiki.db").write_bytes(b"no database" * 1000)
    environment = {**os.environ, "PRIVATE_AGENT_DATA_DIR": str(folder), "PYTHONPATH": "", "PYTHONUTF8": "1"}
    environment.pop("PRIVATE_AGENT_LOG_DIR", None)
    lines = (
        "".join(
            json.dumps({"id": ident, "method": method, "params": {}}) + "\n"
            for ident, method in (("first", "health"), ("second", "wiki.list"))
        )
        + "not json\n"
    )
    result = subprocess.run(
        [sys.executable, str(BACKEND / "main.py")],
        input=lines,
        cwd=BACKEND,
        capture_output=True,
        encoding="utf-8",
        env=environment,
        timeout=120,
    )
    answers = [json.loads(line) for line in result.stdout.splitlines()]
    assert [answer["id"] for answer in answers] == ["first", "second", None]
    assert all(f"손상되어 열지 않았습니다: {folder / 'wiki.db'}." in answer["error"] for answer in answers)
    assert result.returncode == 0
    assert (folder / "wiki.db").read_bytes() == b"no database" * 1000


def test_backups_are_checked_single_files_and_each_kind_keeps_its_newest(tmp_path):
    service = filled(tmp_path / "wiki.db", documents=3)
    folder = tmp_path / "backups"
    made = [service.dispatch("wiki.backup", {}) for _ in range(storage.KEEP + 2)]
    files = [item["file"] for item in made]
    # Backups of the same second are numbered rather than written over.
    assert len(set(files)) == len(files) and all(storage.BACKUP_NAME.fullmatch(name) for name in files)
    assert [item["removed"] for item in made] == [[]] * storage.KEEP + [[files[0]], [files[1]]]
    listed = service.dispatch("wiki.backups", {})
    assert (listed["folder"], listed["keep"]) == (str(folder), storage.KEEP)
    assert [item["file"] for item in listed["backups"]] == files[::-1][: storage.KEEP]
    assert {item["kind"] for item in listed["backups"]} == {"manual"} and names(folder) == sorted(files[2:])
    newest = made[-1]
    assert newest["size"] == (folder / newest["file"]).stat().st_size
    assert abs(datetime.fromisoformat(newest["created_at"]) - datetime.now(timezone.utc)) < timedelta(minutes=1)
    # A whole Wiki in one file: its documents and schema version, and no WAL to carry along.
    with contextlib.closing(sqlite3.connect(storage.read_only_uri(folder / newest["file"]), uri=True)) as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 3
        assert db.execute("PRAGMA user_version").fetchone()[0] == migrations.SCHEMA_VERSION
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    # Backups asked for again and again never push out the daily ones.
    daily, _ = service.backup("auto")
    for _ in range(storage.KEEP + 1):
        service.dispatch("wiki.backup", {})
    kinds = [item["kind"] for item in service.dispatch("wiki.backups", {})["backups"]]
    assert (kinds.count("auto"), kinds.count("manual")) == (1, storage.KEEP)
    assert daily["file"] in names(folder)


def test_a_backup_dated_ahead_of_the_clock_neither_holds_back_nor_hurries_the_daily_ones(tmp_path):
    service = filled(tmp_path / "wiki.db", documents=1)
    folder = tmp_path / "backups"
    today = service.backup("auto")[0]["file"]
    ahead = f"wiki-{(datetime.now() + timedelta(days=30)).strftime('%Y%m%d-%H%M%S')}.db"
    (folder / ahead).write_bytes((folder / today).read_bytes())
    assert not storage.backup_due(service.db.path)
    (folder / today).unlink()
    assert storage.backup_due(service.db.path)
    service.backup("auto")
    assert not storage.backup_due(service.db.path)
    # Counted as the oldest, it is the first to go once the kind is full.
    for _ in range(storage.KEEP - 1):
        service.backup("auto")
    kept = [item["file"] for item in service.dispatch("wiki.backups", {})["backups"]]
    assert ahead not in kept and len(kept) == storage.KEEP


def test_a_backup_that_fails_its_check_leaves_nothing_and_one_is_made_at_a_time(tmp_path, monkeypatch):
    service = filled(tmp_path / "wiki.db", documents=1)
    real = storage.inspect
    monkeypatch.setattr(storage, "inspect", lambda path, budget=None: {**real(path), "state": "damaged"})
    with pytest.raises(UserError, match=r"백업을 만들지 못했습니다\..* \(오류 ID [0-9a-f]{8}\)"):
        service.dispatch("wiki.backup", {})
    assert names(tmp_path / "backups") == []
    monkeypatch.setattr(storage, "inspect", real)
    with service.backup_lock:
        for method, params in (("wiki.backup", {}), ("wiki.restore_backup", {"cancel": True})):
            with pytest.raises(UserError, match="다른 백업이나 복원 준비가 진행 중입니다"):
                service.dispatch(method, params)
    assert service.dispatch("wiki.backup", {})["kind"] == "manual"


def test_a_restore_takes_the_wikis_place_at_the_next_start_and_keeps_the_one_it_replaced(tmp_path):
    path, folder = tmp_path / "wiki.db", tmp_path / "backups"
    service = filled(path, documents=2)
    kept = service.dispatch("wiki.backup", {})["file"]
    later = service.wiki_save({"title": "백업 뒤 문서", "content": "나중에 쓴 글"})
    answer = service.dispatch("wiki.restore_backup", {"file": kept})
    assert (answer["restart_required"], answer["file"]) == (True, kept) and "앱을 다시 실행하면" in answer["message"]
    # Until the next start the Wiki stays as it is.
    assert service.wiki_get({"id": later["id"]})["title"] == "백업 뒤 문서"
    assert service.dispatch("wiki.backups", {})["pending_restore"]["file"] == kept
    restarted = Service(Database(path), None)
    assert len(restarted.wiki_list({})) == 2 and later["id"] not in {doc["id"] for doc in restarted.wiki_list({})}
    listed = restarted.dispatch("wiki.backups", {})
    outcome, before = listed["last_restore"], listed["last_restore"]["before"]
    assert listed["pending_restore"] is None
    assert (outcome["file"], outcome["status"], outcome["error"]) == (kept, "restored", "")
    assert [item["kind"] for item in listed["backups"] if item["file"] == before] == ["before_restore"]
    # What it replaced is one whole file; the copy and the request are gone.
    assert names(folder) == sorted([kept, before, storage.RESULT])
    # And it can be put back in turn.
    restarted.dispatch("wiki.restore_backup", {"file": before})
    assert Service(Database(path), None).wiki_get({"id": later["id"]})["title"] == "백업 뒤 문서"


def test_a_restore_can_be_taken_back_before_the_next_start(tmp_path):
    path, folder = tmp_path / "wiki.db", tmp_path / "backups"
    service = filled(path, documents=1)
    kept = service.dispatch("wiki.backup", {})["file"]
    later = service.wiki_save({"title": "남길 문서", "content": "글"})
    service.dispatch("wiki.restore_backup", {"file": kept})
    # A second request replaces the first, and its copy.
    service.dispatch("wiki.restore_backup", {"file": kept})
    assert len([name for name in names(folder) if storage.STAGED.fullmatch(name)]) == 1
    assert service.dispatch("wiki.restore_backup", {"cancel": True}) == {
        "restart_required": False,
        "cancelled": True,
        "message": "예약한 복원을 취소했습니다.",
    }
    assert names(folder) == [kept]
    assert service.dispatch("wiki.restore_backup", {"cancel": True})["cancelled"] is False
    # What a backend stopped while it copied leaves, which no request names, goes at the next start.
    for orphan in ("restore-" + "a" * 32 + ".db", "restore-" + "b" * 32 + ".db.partial"):
        (folder / orphan).write_bytes(b"half a copy")
    assert Service(Database(path), None).wiki_get({"id": later["id"]})["title"] == "남길 문서"
    assert names(folder) == [kept]
    with pytest.raises(UserError, match="cancel"):
        service.dispatch("wiki.restore_backup", {"cancel": "yes"})


@pytest.mark.parametrize(
    "name",
    [
        None,
        7,
        "",
        "wiki.db",
        "../wiki.db",
        "backups/wiki-20261009-093000.db",
        "..\\wiki-20261009-093000.db",
        "/tmp/wiki-20261009-093000.db",
        "C:\\Windows\\wiki-20261009-093000.db",
        "wiki-20261009-093000.db\x00",
        "wiki-20261009-093000.DB",
        "restore-" + "0" * 32 + ".db",
        storage.MARKER,
    ],
)
def test_only_a_backup_the_backups_folder_lists_is_restored(tmp_path, name):
    service = filled(tmp_path / "wiki.db", documents=1)
    service.dispatch("wiki.backup", {})
    with pytest.raises(UserError, match="백업 목록"):
        service.dispatch("wiki.restore_backup", {"file": name})
    assert service.dispatch("wiki.backups", {})["pending_restore"] is None


def test_a_backup_that_is_missing_damaged_newer_or_no_wiki_is_not_restored(tmp_path):
    path, folder = tmp_path / "wiki.db", tmp_path / "backups"
    service = filled(path, documents=20)
    kept = service.dispatch("wiki.backup", {})["file"]
    with pytest.raises(UserError, match="백업 파일을 찾지 못했습니다: wiki-20200101-000000.db"):
        service.dispatch("wiki.restore_backup", {"file": "wiki-20200101-000000.db"})
    damaged = folder / "wiki-20200102-000000.db"
    damaged.write_bytes((folder / kept).read_bytes())
    corrupt(damaged)
    with pytest.raises(UserError, match="손상되어 복원할 수 없습니다"):
        service.dispatch("wiki.restore_backup", {"file": damaged.name})
    newer = folder / "wiki-20200103-000000.db"
    newer.write_bytes((folder / kept).read_bytes())
    with contextlib.closing(sqlite3.connect(newer)) as db:
        db.execute(f"PRAGMA user_version={migrations.SCHEMA_VERSION + 1}")
    with pytest.raises(UserError, match="더 새로운 버전"):
        service.dispatch("wiki.restore_backup", {"file": newer.name})
    wiki(folder / "wiki-20200104-000000.db")
    with pytest.raises(UserError, match="Wiki의 백업이 아닌 파일"):
        service.dispatch("wiki.restore_backup", {"file": "wiki-20200104-000000.db"})
    if os.name != "nt":  # A link that leads out of the folder, which Windows lets only administrators make.
        (folder / "wiki-20200105-000000.db").symlink_to(path)
        with pytest.raises(UserError, match="찾지 못했습니다"):
            service.dispatch("wiki.restore_backup", {"file": "wiki-20200105-000000.db"})
    assert service.dispatch("wiki.backups", {})["pending_restore"] is None


def fail(error):
    """A stand-in that fails as a file another program holds makes a rename fail."""

    def failing(*_):
        raise error

    return failing


def test_a_restore_the_next_start_cannot_carry_out_leaves_the_wiki_as_it_is(tmp_path, monkeypatch):
    path, folder = tmp_path / "wiki.db", tmp_path / "backups"
    service = filled(path, documents=1)
    kept = service.dispatch("wiki.backup", {})["file"]
    later = service.wiki_save({"title": "그대로 둘 문서", "content": "글"})["id"]
    # The Wiki held by another program: tried again at the next start.
    service.dispatch("wiki.restore_backup", {"file": kept})
    monkeypatch.setattr(storage, "move", fail(PermissionError("held")))
    held = Service(Database(path), None)
    assert held.wiki_get({"id": later})
    listed = held.dispatch("wiki.backups", {})
    assert listed["pending_restore"]["file"] == kept
    assert listed["last_restore"]["status"] == "failed" and "다른 프로그램" in listed["last_restore"]["error"]
    monkeypatch.undo()
    with pytest.raises(UserError, match="찾을 수 없습니다"):
        Service(Database(path), None).wiki_get({"id": later})
    # The copy cannot take the Wiki's name: the Wiki goes back to its place.
    service = Service(Database(path), None)
    service.wiki_save({"id": later, "title": "그대로 둘 문서", "content": "글"})
    service.dispatch("wiki.restore_backup", {"file": kept})
    monkeypatch.setattr(storage, "place", fail(PermissionError("held")))
    assert Service(Database(path), None).wiki_get({"id": later})
    monkeypatch.undo()
    # Its copy gone: given up, the Wiki as it is.
    for staged in folder.glob("restore-*.db"):
        staged.unlink()
    assert Service(Database(path), None).wiki_get({"id": later})
    listed = Service(Database(path), None).dispatch("wiki.backups", {})
    assert listed["pending_restore"] is None and "사본을 찾지 못해" in listed["last_restore"]["error"]
    assert storage.MARKER not in names(folder)


def test_the_log_of_a_wiki_that_is_gone_is_not_applied_to_the_restored_one(tmp_path):
    path, folder = tmp_path / "wiki.db", tmp_path / "backups"
    service = filled(path, documents=1)
    kept = service.dispatch("wiki.backup", {})["file"]
    service.dispatch("wiki.restore_backup", {"file": kept})
    # The Wiki removed by hand, a log of committed pages left behind (another database's, here).
    path.unlink()
    other = tmp_path / "other" / "wiki.db"
    other.parent.mkdir()
    with contextlib.closing(sqlite3.connect(other)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA wal_autocheckpoint=0")
        db.execute("CREATE TABLE stale (value INTEGER)")
        db.executemany("INSERT INTO stale VALUES (?)", [(index,) for index in range(200)])
        db.commit()
        Path(str(path) + "-wal").write_bytes(Path(str(other) + "-wal").read_bytes())
    restarted = Service(Database(path), None)
    assert [doc["title"] for doc in restarted.wiki_list({})] == ["문서 0"]
    with restarted.db.connect() as db:
        assert "stale" not in {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
    assert storage.inspect(path)["state"] == "ok"


def test_a_start_stopped_right_after_the_last_rename_is_known_to_have_restored(tmp_path):
    path, folder = tmp_path / "wiki.db", tmp_path / "backups"
    service = filled(path, documents=1)
    kept = service.dispatch("wiki.backup", {})["file"]
    service.wiki_save({"title": "백업 뒤 문서", "content": "글"})
    before = "wiki-before-restore-20261009-093000.db"
    service.dispatch("wiki.restore_backup", {"file": kept})
    request = json.loads((folder / storage.MARKER).read_text(encoding="utf-8"))
    # What apply_restore had done: the Wiki moved, restore.json told, the copy renamed; then the start stopped.
    path.rename(folder / before)
    (folder / storage.MARKER).write_text(json.dumps({**request, "moving": True, "before": before}), encoding="utf-8")
    (folder / request["staged"]).rename(path)
    restarted = Service(Database(path), None)
    assert len(restarted.wiki_list({})) == 1
    outcome = restarted.dispatch("wiki.backups", {})["last_restore"]
    assert (outcome["status"], outcome["before"]) == ("restored", before)
    assert storage.MARKER not in names(folder) and before in names(folder)
    if os.name != "nt":  # Stopped inside the rename, which is a link and an unlink there: two names of the copy.
        later = restarted.wiki_save({"title": "다시 쓴 문서", "content": "글"})
        restarted.dispatch("wiki.restore_backup", {"file": kept})
        request = json.loads((folder / storage.MARKER).read_text(encoding="utf-8"))
        path.rename(folder / "wiki-before-restore-20261009-093001.db")
        os.link(folder / request["staged"], path)
        again = Service(Database(path), None)
        with pytest.raises(UserError, match="찾을 수 없습니다"):
            again.wiki_get({"id": later["id"]})
        assert not (folder / request["staged"]).exists()
        assert again.dispatch("wiki.backups", {})["last_restore"]["status"] == "restored"


def test_a_start_stopped_between_the_two_renames_is_finished_by_the_next(tmp_path):
    path, folder = tmp_path / "wiki.db", tmp_path / "backups"
    service = filled(path, documents=1)
    kept = service.dispatch("wiki.backup", {})["file"]
    service.wiki_save({"title": "백업 뒤 문서", "content": "글"})
    service.dispatch("wiki.restore_backup", {"file": kept})
    path.rename(folder / "wiki-before-restore-20261009-093000.db")  # The Wiki moved, the copy not yet renamed.
    restarted = Service(Database(path), None)
    assert len(restarted.wiki_list({})) == 1
    assert restarted.dispatch("wiki.backups", {})["last_restore"]["status"] == "restored"
