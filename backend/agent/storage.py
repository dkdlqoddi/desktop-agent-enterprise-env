"""The Wiki's database file (wiki.db) in the folder the user chose: the first-run copy of an earlier version's Wiki, the
check every start makes before the Wiki is opened, its backups in the folder's backups folder, and the restore of one.

First-run copy (migrate_database, main.py --migrate-storage): SQLite's backup API copies the earlier Wiki with any
committed WAL data. The copy is written beside the destination under a name of its own and renamed to wiki.db only once
it is whole and checked: a copy that fails, or that Electron stops because it made no progress for a minute, leaves no
wiki.db in the chosen folder, and the next attempt removes what it left and starts again.

Check (prepare, from db.Database before anything writes to the Wiki): PRAGMA quick_check and the schema version
(migrations.py), read through a read-only connection. A damaged Wiki, or one a later version of the app changed, is
refused as it is (Refused) with what the user can do, and the start fails with that on the screen.

Backups (snapshot): VACUUM INTO writes a consistent, compact copy of the Wiki as a single file into
<Wiki folder>/backups, named by the local time it was made: wiki-YYYYMMDD-HHMMSS.db once a day while the app runs
(Service.upkeep) and wiki-manual-YYYYMMDD-HHMMSS.db when the user or an AI tool asks (wiki.backup). Each kind keeps its
newest KEEP, so backups asked for again and again never push out the daily ones. The Wiki a restore replaced is kept
there too, as wiki-before-restore-YYYYMMDD-HHMMSS.db, until the user removes it.

Restore (stage_restore, apply_restore): see the section below."""
import contextlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import diagnostics, migrations
from .llm import UserError

# The name a copy has while it is written: "wiki.db.<random>.copying", with SQLite's -journal beside it.
PARTIAL = ".copying"
# Pages copied per step of the backup, each followed by a report of the progress (4 MB at SQLite's default page size).
STEP_PAGES = 1024
# SQLite instructions between reports of the progress of a check, which reads every page again, and between looks at
# the time a check at a start may take.
CHECK_STEPS = 100_000

# The folder of the backups, inside the Wiki folder: on the same drive, so a restore is a rename.
BACKUPS = "backups"
# Backups of each kind kept: the newest week of daily ones, and as many asked for.
KEEP = 7
# How long the check at a start may read. quick_check reads every page, and Electron gives the whole start of the
# backend a minute: a Wiki too large to read in this time (a few GB, or a slow network drive) opens unchecked, which the
# log says, rather than never.
CHECK_BUDGET = 20.0
# wiki-YYYYMMDD-HHMMSS.db (daily), wiki-manual-… (asked for) and wiki-before-restore-… (the Wiki a restore replaced),
# numbered -2, -3… after one of the same second.
BACKUP_NAME = re.compile(r"wiki-(?:(manual|before-restore)-)?(\d{8}-\d{6})(?:-(\d+))?\.db")
KINDS = {None: "auto", "manual": "manual", "before-restore": "before_restore"}
PREFIXES = {"auto": "wiki-", "manual": "wiki-manual-", "before_restore": "wiki-before-restore-"}
# What SQLite keeps beside a database by its name: the WAL, its index and a rollback journal.
SIDECARS = ("-wal", "-shm", "-journal")
# A backup being written; a stopped backend leaves it, and the next backup removes it.
WRITING = ".partial"
# The restore the next start carries out, and how the last one went.
MARKER = "restore.json"
RESULT = "last-restore.json"
# The copy of the backup a restore puts in the Wiki's place, and what writing it leaves while it is under way.
STAGED = re.compile(r"restore-[0-9a-f]{32}\.db")
STAGED_FILES = re.compile(r"restore-[0-9a-f]{32}\.db(?:\.partial)?(?:-wal|-shm|-journal)?")


class Refused(Exception):
    """The Wiki cannot be opened as it is: damaged, or changed by a later version of the app. The message names the
    file and says what the user can do; main.py answers every request with it, so the start fails with it."""


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


# --- Check -----------------------------------------------------------------------------------------------------------


def inspect(path, budget=None):
    """What the database at `path` holds, read through a read-only connection, which never writes to it (not even the
    checkpoint a last connection makes as it closes): {"state", "detail", "version", "tables"}. state is "ok",
    "damaged" (quick_check found a fault, or SQLite could not read the file as a database) or "unchecked" (quick_check
    did not end within `budget` seconds, whatever it had found by then). Raises sqlite3.OperationalError when the file
    cannot be read at all (locked, missing, no permission), which says nothing of damage."""
    connection = sqlite3.connect(read_only_uri(Path(path).resolve()), uri=True)
    found = {"state": "ok", "detail": "", "version": 0, "tables": []}
    timer = None
    try:
        found["version"] = connection.execute("PRAGMA user_version").fetchone()[0]
        found["tables"] = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if budget is not None:
            deadline = time.monotonic() + budget
            connection.set_progress_handler(lambda: time.monotonic() > deadline, CHECK_STEPS)
            # SQLite before 3.41 calls no progress handler while quick_check walks the b-trees, most of its work; an
            # interrupt from another thread stops that walk on every version.
            timer = threading.Timer(max(budget, 0), connection.interrupt)
            timer.daemon = True
            timer.start()
        rows = [row[0] for row in connection.execute("PRAGMA quick_check(20)")]
        if rows != ["ok"]:
            # A check its time stopped is no verdict: the pages it never reached read as faults.
            late = budget is not None and time.monotonic() > deadline
            found.update(state="unchecked" if late else "damaged", detail="\n".join(rows))
    except sqlite3.OperationalError as error:
        if budget is None or "interrupt" not in str(error).lower():
            raise
        found["state"] = "unchecked"
    except sqlite3.DatabaseError as error:
        # SQLITE_CORRUPT and SQLITE_NOTADB (malformed, not a database) are this class itself; its subclasses are other
        # failures (a constraint, a misuse) that say nothing of the file.
        if type(error) is not sqlite3.DatabaseError:
            raise
        found.update(state="damaged", detail=str(error))
    finally:
        if timer is not None:
            timer.cancel()
            timer.join()  # An interrupt under way ends before the connection closes, as SQLite requires.
        connection.close()
    return found


def prepare(path):
    """Ready the Wiki at `path` for db.Database before anything writes to it: a restore asked for in the last run takes
    its place (apply_restore), then a Wiki that is damaged, or newer than this version of the app, is refused (Refused)
    and left as it is. Returns whether the Wiki is new: no file yet, or one without tables."""
    path = Path(path)
    apply_restore(path)
    if not path.exists():
        return True
    found = inspect(path, budget=CHECK_BUDGET)
    if found["state"] == "damaged":
        diagnostics.LOG.error("quick_check of %s failed; the Wiki was not opened:\n%s", path, found["detail"])
        raise Refused(damaged(path))
    if found["version"] > migrations.SCHEMA_VERSION:
        diagnostics.LOG.error("%s has schema version %d, newer than %d; the Wiki was not opened", path,
                              found["version"], migrations.SCHEMA_VERSION)
        raise Refused(f"이 Wiki는 더 새로운 버전의 Private Agent가 바꾼 데이터베이스(버전 {found['version']})라서 "
                      f"버전 {migrations.SCHEMA_VERSION}까지 아는 이 앱은 열지 않았습니다: {path}. 파일은 그대로 두었습니다. "
                      "최신 버전의 Private Agent로 열어 주세요.")
    if found["state"] == "unchecked":
        diagnostics.LOG.warning("quick_check of %s did not end within %d seconds; the Wiki was opened unchecked", path,
                                CHECK_BUDGET)
    return not found["tables"]


def damaged(path):
    """What the user is told of the damaged Wiki at `path`: where it is, that it was left as it is, and how to put the
    newest backup in its place by hand, as the backend that would restore it cannot start."""
    folder = backup_folder(path)
    newest = next((item["file"] for item in backups(path) if item["kind"] != "before_restore"), None)
    # The -wal and -shm go too: SQLite would apply the damaged Wiki's log to the copy put in its place.
    aside = f"앱을 닫고 Wiki 폴더의 {path.name}, {path.name}-wal, {path.name}-shm 파일을 다른 폴더로 옮긴 다음"
    message = f"Wiki 데이터베이스 파일이 손상되어 열지 않았습니다: {path}. 파일은 고치거나 지우지 않고 그대로 두었습니다. "
    if newest:
        return message + (f"백업으로 되돌리려면 {aside}, 백업 폴더({folder})의 가장 최근 백업({newest})을 Wiki 폴더에 "
                          f"{path.name}라는 이름으로 복사하고 앱을 다시 실행해 주세요.")
    return message + (f"백업 폴더({folder})에 백업이 없습니다. 따로 복사해 둔 Wiki가 있으면 {aside} 그 Wiki의 {path.name}를 "
                      "넣고 앱을 다시 실행해 주세요. 없으면 ‘진단 정보 복사’로 문제를 알려 주세요.")


# --- Backups ---------------------------------------------------------------------------------------------------------


def backup_folder(path):
    return Path(path).parent / BACKUPS


def backups(path):
    """The backups of the Wiki at `path`, the newest first: [{"file", "kind", "created_at", "size"}]. kind is auto (the
    daily one), manual (asked for) or before_restore (the Wiki a restore replaced); created_at is the UTC time its
    name says, in the local time it was made."""
    folder = backup_folder(path)
    found = []
    for item in folder.iterdir() if folder.is_dir() else ():
        match = BACKUP_NAME.fullmatch(item.name)
        if not match or item.is_symlink():
            continue
        try:
            made = datetime.strptime(match[2], "%Y%m%d-%H%M%S").astimezone().astimezone(timezone.utc)
            details = item.stat()
        except (ValueError, OverflowError, OSError):  # A date the system's clock cannot place (year 1 on Windows).
            continue
        if not item.is_file():
            continue
        found.append(((match[2], int(match[3] or 1)), {
            "file": item.name, "kind": KINDS[match[1]], "created_at": made.isoformat(), "size": details.st_size}))
    return [entry for _, entry in sorted(found, key=lambda pair: pair[0], reverse=True)]


def ahead(item, current):
    """Whether the backup `item` is dated after `current`: made while the clock was set wrong."""
    return datetime.fromisoformat(item["created_at"]) > current


def backup_due(path, every=timedelta(days=1)):
    """Whether the newest daily backup of the Wiki at `path` is `every` old, or there is none. One dated ahead of the
    clock is not the newest: taken for it, it would hold the next ones back or, seen as old, start one each look."""
    current = datetime.now(timezone.utc)
    newest = next((item for item in backups(path) if item["kind"] == "auto" and not ahead(item, current)), None)
    return newest is None or current - datetime.fromisoformat(newest["created_at"]) >= every


def new_name(folder, kind):
    """A name for a backup of `kind` made now, numbered after every backup of the same second, so that it sorts as the
    newest: the first free number could be one the oldest left as it was removed."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    numbers = [int(match[3] or 1) for match in (BACKUP_NAME.fullmatch(item.name) for item in
                                                (folder.iterdir() if folder.is_dir() else ()))
               if match and match[2] == stamp and KINDS[match[1]] == kind]
    number = max(numbers, default=0) + 1
    return f"{PREFIXES[kind]}{stamp}.db" if number == 1 else f"{PREFIXES[kind]}{stamp}-{number}.db"


def snapshot(connection, path, kind="auto", keep=KEEP):
    """Back up the Wiki at `path`, open as `connection` outside any transaction: VACUUM INTO writes it into a file of
    its own in the backups folder, which is flushed to the disk, checked with quick_check and given its name only when
    whole. Then the oldest backups of the same `kind` (auto or manual) beyond the newest `keep` are removed. Returns
    (the backup as backups() lists it, the names removed)."""
    folder = backup_folder(path)
    folder.mkdir(parents=True, exist_ok=True)
    for leftover in folder.glob(f"wiki-*{WRITING}*"):
        with contextlib.suppress(OSError):  # One being written by a backend still running elsewhere stays.
            leftover.unlink()
    name = new_name(folder, kind)
    writing = folder / f"{name}.{uuid.uuid4().hex}{WRITING}"
    try:
        connection.execute("VACUUM INTO ?", (str(writing),))
        # SQLite leaves the new file in the system's cache; a backup must be on the disk before it counts as one.
        with open(writing, "r+b") as written:
            os.fsync(written.fileno())
        found = inspect(writing)
        if found["state"] != "ok":
            raise sqlite3.DatabaseError(f"quick_check of the new backup failed: {found['detail']}")
        place(writing, folder / name)
    finally:
        for item in (writing, *(writing.with_name(writing.name + suffix) for suffix in SIDECARS)):
            with contextlib.suppress(OSError):
                item.unlink(missing_ok=True)
    found, current = backups(path), datetime.now(timezone.utc)
    made, removed = next(item for item in found if item["file"] == name), []
    # The others of its kind, the newest first; those dated ahead of the clock count as the oldest and go first.
    older = sorted((item for item in found if item["kind"] == kind and item["file"] != name),
                   key=lambda item: not ahead(item, current), reverse=True)
    for old in older[max(keep - 1, 0):]:
        with contextlib.suppress(OSError):  # One another program holds goes the next time.
            (folder / old["file"]).unlink()
            removed.append(old["file"])
    return made, removed


# --- Restore ---------------------------------------------------------------------------------------------------------
# A backup cannot safely take the Wiki's place while the backend runs. Every request and every background job (imports,
# analysis, the routines, the assistant) opens a connection of its own whenever it needs one, so there is no moment
# when all of them are closed and stay closed; SQLite keeps committed data in wiki.db-wal and wiki.db-shm, which belong
# to the file by name, so a file replaced under an open connection gets mixed with the old one's log; Windows refuses
# to rename a file that is open; and the jobs would go on with what they had read from the Wiki being replaced.
#
# So a restore has two halves. The request (stage_restore) checks the backup, copies it into the backups folder as
# restore-<id>.db and writes restore.json naming it; the app goes on as it is, and tells the user to start it again.
# The next start of the backend (apply_restore, from prepare), before anything opens the Wiki, folds the Wiki's WAL into
# wiki.db, moves it into the backups folder as wiki-before-restore-YYYYMMDD-HHMMSS.db and renames the copy to wiki.db:
# renames on the same drive, so a Wiki of any size is restored within the start's time limit. The copy is made before
# the Wiki is moved and the Wiki is moved before the copy takes its name, so wiki.db is always one Wiki or the other,
# whole; a start stopped between the two renames leaves restore.json, and the next one finishes the restore. Before the
# last rename restore.json says so ("moving"), so that a start stopped right after it is known to have restored.


def backup_file(path, name):
    """The backup `name` of the Wiki at `path`: a file of its backups folder by one of the names backups() lists, never
    a path elsewhere (a link that leads out of the folder included)."""
    if not isinstance(name, str) or not BACKUP_NAME.fullmatch(name):
        raise UserError("복원할 백업을 백업 목록(wiki.backups)에 있는 파일 이름으로 골라 주세요.")
    folder = backup_folder(path)
    candidate = folder / name
    try:
        found = candidate.resolve(strict=True)
    except OSError:
        found = None
    if found is None or candidate.is_symlink() or found.parent != folder.resolve() or not found.is_file():
        raise UserError(f"백업 파일을 찾지 못했습니다: {name}. 백업 목록을 새로고침해 주세요.")
    return found


def read_json(target):
    """The JSON object in `target`, or None when there is none that can be read."""
    try:
        value = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def write_json(target, value):
    """Write `value` to `target` whole or not at all."""
    writing = target.with_name(f"{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        writing.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        os.replace(writing, target)
    finally:
        writing.unlink(missing_ok=True)


def requested(folder):
    """The restore restore.json asks for: {"file", "staged", "requested_at"}, or None (none, or a file not of ours)."""
    request = read_json(folder / MARKER)
    if not request or not isinstance(request.get("file"), str) or not BACKUP_NAME.fullmatch(request["file"]) \
            or not isinstance(request.get("staged"), str) or not STAGED.fullmatch(request["staged"]):
        return None
    return request


def remove_staged(folder, keep=""):
    """Remove the copies of earlier restore requests, and what writing them left, all but `keep`."""
    for item in folder.glob("restore-*") if folder.is_dir() else ():
        if item.name != keep and STAGED_FILES.fullmatch(item.name):
            with contextlib.suppress(OSError):
                item.unlink()


def stage_restore(path, name):
    """Ask the next start to put the backup `name` in the place of the Wiki at `path`: the backup is checked
    (quick_check, a Wiki's tables, a version this code opens) and copied into the backups folder, and restore.json names
    the copy. An earlier request is replaced. Returns {"file", "requested_at"}."""
    source = backup_file(path, name)
    found = inspect(source)
    if found["state"] != "ok":
        raise UserError(f"백업 파일이 손상되어 복원할 수 없습니다: {name}. 다른 백업을 골라 주세요.")
    if found["version"] > migrations.SCHEMA_VERSION:
        raise UserError(f"더 새로운 버전의 Private Agent가 만든 백업(데이터베이스 버전 {found['version']})이라 이 앱에서는 "
                        f"복원할 수 없습니다: {name}. 최신 버전의 앱에서 복원해 주세요.")
    if "documents" not in found["tables"]:
        raise UserError(f"Wiki의 백업이 아닌 파일입니다: {name}.")
    folder = backup_folder(path)
    staged = folder / f"restore-{uuid.uuid4().hex}.db"
    writing = staged.with_name(staged.name + WRITING)
    try:
        # SQLite's backup API, which also reads a WAL a before-restore copy may still have beside it.
        original = sqlite3.connect(read_only_uri(source), uri=True)
        target = sqlite3.connect(writing)
        try:
            original.backup(target, pages=STEP_PAGES)
        finally:
            target.close()
            original.close()
        os.replace(writing, staged)
        request = {"file": name, "staged": staged.name, "requested_at": datetime.now(timezone.utc).isoformat()}
        write_json(folder / MARKER, request)
    except BaseException:
        for item in (writing, staged, *(writing.with_name(writing.name + suffix) for suffix in SIDECARS)):
            with contextlib.suppress(OSError):
                item.unlink(missing_ok=True)
        raise
    remove_staged(folder, keep=staged.name)
    return {"file": name, "requested_at": request["requested_at"]}


def cancel_restore(path):
    """Take back the restore the next start would carry out. Returns whether there was one."""
    folder = backup_folder(path)
    found = requested(folder) is not None
    (folder / MARKER).unlink(missing_ok=True)
    remove_staged(folder)
    return found


def pending_restore(path):
    """The restore the next start carries out: {"file", "requested_at"}, or None."""
    folder = backup_folder(path)
    request = requested(folder)
    if request is None or not (folder / request["staged"]).is_file():
        return None
    return {"file": request["file"], "requested_at": request["requested_at"]}


def last_restore(path):
    """How the last restore went: {"file", "status" (restored or failed), "before", "error", "at"}, or None."""
    return read_json(backup_folder(path) / RESULT)


def fold(path):
    """Make the database at `path` whole in one file: what its WAL holds is written into it and it leaves WAL mode, so
    the -wal and -shm files go and a read-only look at it later leaves none behind (db.Database turns WAL on again
    when it opens a Wiki). A database SQLite cannot read, or another program has open, keeps them."""
    with contextlib.suppress(sqlite3.Error), contextlib.closing(sqlite3.connect(path, timeout=5)) as connection:
        connection.execute("PRAGMA journal_mode=DELETE")


def record(folder, outcome):
    """Keep how a restore went for wiki.backups; a file that cannot be written leaves only the log's line."""
    with contextlib.suppress(OSError):
        write_json(folder / RESULT, outcome)


def move(source, target):
    """Rename the database `source` to `target` with what SQLite keeps beside it. The -wal goes first, so that a stop
    midway never leaves a log beside a file it does not belong to; what was moved goes back when a rename fails."""
    moved = []
    try:
        for suffix in (*SIDECARS, ""):
            if Path(str(source) + suffix).exists():
                os.rename(str(source) + suffix, str(target) + suffix)
                moved.append(suffix)
    except OSError:
        for suffix in reversed(moved):
            with contextlib.suppress(OSError):
                os.rename(str(target) + suffix, str(source) + suffix)
        raise


def apply_restore(path):
    """Carry out the restore restore.json asks for, at a start of the backend before anything opens the Wiki at `path`
    (see the section above). How it went is kept in last-restore.json, for wiki.backups, and in the log. A restore that
    cannot be carried out leaves the Wiki as it is: one whose copy is gone is given up, one stopped by a file another
    program holds is tried again at the next start. Returns what last-restore.json now says, or None."""
    path = Path(path)
    folder = backup_folder(path)
    if not (folder / MARKER).exists():
        remove_staged(folder)  # A copy no request names: a backend stopped while it copied, or a cancel half done.
        return None
    request = requested(folder)
    outcome = {"file": request["file"] if request else "", "status": "failed", "before": None, "error": "",
               "at": datetime.now(timezone.utc).isoformat()}
    staged = folder / request["staged"] if request else None
    copied = staged is not None and staged.is_file()
    # What a start that got as far as the last rename wrote down before it (see below).
    before = request.get("before") if request and isinstance(request.get("before"), str) \
        and BACKUP_NAME.fullmatch(request["before"]) else None
    if request and request.get("moving") is True and not copied and path.exists():
        pass  # A start stopped after the copy took the Wiki's name, before it could say so: the restore is done.
    elif not copied:
        outcome["error"] = "복원할 백업의 사본을 찾지 못해 복원하지 않았습니다. 백업 목록에서 다시 복원해 주세요."
        diagnostics.LOG.error("restore of %s given up: its staged copy is missing",
                              outcome["file"] or "an unreadable request")
        with contextlib.suppress(OSError):
            (folder / MARKER).unlink(missing_ok=True)
        remove_staged(folder)
        record(folder, outcome)
        return outcome
    else:
        asked = {key: request[key] for key in ("file", "staged", "requested_at") if key in request}
        moved = None
        try:
            if path.exists() and os.path.samefile(path, staged):
                # A start stopped inside place(), between its link and unlink: the copy is the Wiki already, by two
                # names.
                staged.unlink()
            else:
                if path.exists():
                    fold(path)
                    moved = new_name(folder, "before_restore")
                    move(path, folder / moved)
                # What SQLite kept beside a Wiki that is gone would be applied to the copy that takes its name.
                for suffix in SIDECARS:
                    Path(str(path) + suffix).unlink(missing_ok=True)
                # Written down before the last rename, so that a start stopped right after it knows the restore done.
                before = moved
                write_json(folder / MARKER, {**asked, "moving": True, "before": before})
                place(staged, path)
        except OSError as error:
            if moved is not None and not path.exists():
                with contextlib.suppress(OSError):  # The Wiki back in its place rather than a folder without one.
                    move(folder / moved, path)
            with contextlib.suppress(OSError):
                write_json(folder / MARKER, asked)
            diagnostics.LOG.error("restore of %s not done, tried again at the next start: %r", request["file"], error)
            outcome["error"] = ("Wiki 파일을 옮기지 못해 복원하지 못했습니다. 다른 프로그램(백신, 동기화 프로그램 등)이 "
                                "파일을 쓰고 있는지 확인한 뒤 앱을 다시 실행하면 다시 복원합니다.")
            record(folder, outcome)
            return outcome
    with contextlib.suppress(OSError):  # Left behind, it would name a copy no longer there: given up next time.
        (folder / MARKER).unlink(missing_ok=True)
    remove_staged(folder)
    outcome.update(status="restored", before=before)
    diagnostics.LOG.info("restored the Wiki from %s; the Wiki before it is %s", request["file"], before)
    record(folder, outcome)
    return outcome
