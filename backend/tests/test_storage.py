"""An earlier version's Wiki copied into the folder chosen on the first-run screen (agent/storage.py, main.py
--migrate-storage): written under a name of its own and renamed to wiki.db only when whole, so a copy stopped midway
or failed never leaves the folder refused as "wiki.db가 이미 있습니다"."""
import contextlib
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from agent import storage
from agent.storage import migrate_database

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
    (folder / "notes.txt").write_text("사용자 파일")
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
    result = subprocess.run([*command, str(source), str(destination)], cwd=BACKEND, capture_output=True,
                            encoding="utf-8", timeout=60)
    assert (result.returncode, result.stdout.splitlines()[:1], result.stderr) == (0, ["copying"], "")
    assert destination.exists()
    result = subprocess.run([*command, str(source), str(destination)], cwd=BACKEND, capture_output=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 1
    assert result.stderr.strip() == "기존 저장소를 복사하지 못했습니다. 대상 폴더, 기존 파일, 권한과 디스크 공간을 확인해 주세요."
