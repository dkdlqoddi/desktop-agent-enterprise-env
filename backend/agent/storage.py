"""First-run migration via SQLite's backup API, including any committed WAL data.

The copy is written beside the destination under a name of its own and renamed to wiki.db only once it is whole and
checked: a copy that fails, or that Electron stops because it made no progress for a minute, leaves no wiki.db in the
chosen folder, and the next attempt removes what it left and starts again."""
import os
import sqlite3
import time
import uuid
from pathlib import Path

# The name a copy has while it is written: "wiki.db.<random>.copying", with SQLite's -journal beside it.
PARTIAL = ".copying"
# Pages copied per step of the backup, each followed by a report of the progress (4 MB at SQLite's default page size).
STEP_PAGES = 1024
# SQLite instructions between reports of the progress of the integrity check, which reads every page again.
CHECK_STEPS = 100_000


def read_only_uri(path):
    """SQLite's read-only URI of `path`. A network path (\\\\server\\share\\…, also what resolve() makes of a mapped
    drive) keeps its server in the path, as SQLite refuses a URI with a host: file:////server/share/…"""
    uri = path.as_uri()
    if not uri.startswith("file:///"):
        uri = "file:////" + uri[len("file://"):]
    return uri + "?mode=ro"


def remove_partial(destination):
    """The copies an earlier attempt left beside `destination`, known by their name; one still being written by another
    attempt cannot be removed on Windows and stays."""
    for path in destination.parent.glob(f"{destination.name}.*{PARTIAL}*"):
        try:
            path.unlink()
        except OSError:
            pass


def place(copy, destination):
    """Gives the finished copy its name without ever replacing a wiki.db that appeared meanwhile: Windows refuses to
    rename onto an existing file, elsewhere a hard link does. Antivirus may hold the new file for a moment."""
    for attempt in range(1, 11):
        try:
            if os.name == "nt":
                os.rename(copy, destination)
            else:
                os.link(copy, destination)
                os.unlink(copy)
            return
        except PermissionError:
            if attempt == 10:
                raise
            time.sleep(0.05 * attempt)


def migrate_database(source, destination, progress=lambda: None):
    """Copies the database `source` to `destination`, which must not exist. `progress` is called as the copy and its
    check go on."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        return
    if not source.is_file():
        raise ValueError("이전 데이터베이스를 찾지 못했습니다.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Never over an existing user's database.
    if destination.exists():
        raise FileExistsError(destination)
    remove_partial(destination)
    copy = destination.with_name(f"{destination.name}.{uuid.uuid4().hex}{PARTIAL}")
    os.close(os.open(copy, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    try:
        original = sqlite3.connect(read_only_uri(source), uri=True)
        target = sqlite3.connect(copy)
        try:
            original.backup(target, pages=STEP_PAGES, progress=lambda status, remaining, total: progress())
            target.set_progress_handler(lambda: progress() or 0, CHECK_STEPS)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("기존 데이터베이스의 무결성 검사에 실패했습니다.")
            target.set_progress_handler(None, 0)
        finally:
            target.close()
            original.close()
        place(copy, destination)
    finally:
        for path in (copy, *(copy.with_name(copy.name + suffix) for suffix in ("-journal", "-wal", "-shm"))):
            path.unlink(missing_ok=True)
