"""Write edited Wiki content back to local Office files, keeping backups and change history.

Before a write the file is copied into the Wiki folder's 원본 백업 folder and the copy recorded (writeback_backups); the
newest KEEP_BACKUPS copies of each file stay, and one of them can be put back in the file's place (restore_backup). A
write that a crash or a kill cuts short leaves its record unfinished: the next write to the file looks at what the file
holds now (classify) and tells the user before it writes anything."""

import contextlib
import hashlib
import json
import os
import shutil
import threading
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from . import diagnostics, sources
from .file_import import MAX_FILE_BYTES
from .html_document import validate_html
from .llm import UserError
from .office_converter import OfficeConverter
from .office_write import OfficeWriter
from .parts import page_parts
from .source_edits import Unchanged, changes, excel_changes, excel_witness, units, witness

ACTIVE = ("scanning", "login", "running", "cancelling")
BACKUPS = "원본 백업"
# Copies kept of each file: the file before each write, or before a restore replaced it. Older ones go.
KEEP_BACKUPS = 5
# The Office formats since 2007 are ZIP packages, whose every part a whole file holds with its checksum; the older ones
# are OLE compound files, which begin with this signature.
PACKAGES = {".docx", ".docm", ".xlsx", ".xlsm", ".xlsb", ".pptx", ".pptm", ".vsdx", ".vsdm"}
COMPOUND = bytes.fromhex("d0cf11e0a1b11ae1")
BUSY = "원본에 반영하는 중입니다. 끝난 뒤 다시 시도해 주세요."
IMPORTING = "Office·RTF 문서 가져오기가 진행 중입니다. 끝난 뒤 원본에 반영해 주세요."
UNSUPPORTED = "이 PC의 Office 파일(Word·Excel·PowerPoint·Visio)에서 가져온 문서만 원본에 반영할 수 있습니다."
EDITED_MEANWHILE = (
    "반영하는 동안 문서가 바뀌어 문서는 원본 파일에서 다시 읽지 않았습니다(바뀐 내용은 그대로 둠). 그 내용도 "
    "원본에 쓰려면 다시 ‘원본에 반영’을 눌러 주세요."
)


def checksum(path, limit=MAX_FILE_BYTES):
    """The sha256 of a file as an import records it; a file larger than `limit` (the most an import reads) is refused,
    unless the limit is None, as for the copies of a file, whatever it holds."""
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if limit and size > limit:
                raise UserError("파일이 50MB를 초과합니다.")
            digest.update(chunk)
    return digest.hexdigest()


def whole(path):
    """Whether the Office file at `path` is a whole file of its format, as far as can be told without Office: a ZIP
    package whose every part reads back with its checksum, or a compound file with its signature. False for no file,
    an empty one or one cut short; None when it cannot be read now (another program holds it)."""
    file = Path(path)
    try:
        if not file.is_file() or not file.stat().st_size:
            return False
        if file.suffix.lower() not in PACKAGES:
            with file.open("rb") as source:
                return source.read(len(COMPOUND)) == COMPOUND
        with zipfile.ZipFile(file) as package:
            return "[Content_Types].xml" in package.namelist() and package.testzip() is None
    except OSError:
        return None
    except Exception:  # A broken package: no central directory, a bad checksum, a part cut short.
        return False


def classify(path, before):
    """What a write that did not end as it should left of the file at `path`, whose bytes before it had the checksum
    `before`: "intact" (the file is as it was, nothing was written), "partial" (no file, or one cut short or broken: it
    was partly written) or "unknown" (another whole file: the write may have saved all of it, or another program
    changed the file, which only opening it tells)."""
    try:
        if Path(path).is_file() and checksum(path, limit=None) == before:
            return "intact"
    except OSError:
        return "unknown"
    return "partial" if whole(path) is False else "unknown"


def interruption(state, name, phase=""):
    """What the user is told of a write a crash or a kill cut short that left the file `state` (classify), before the
    next write; `name` is the copy of the file before it, `phase` how far its save got (OfficeWriter's journal)."""
    copy = f"Wiki 폴더의 ‘{BACKUPS}’에 있는 {name}"
    if state == "intact":
        return (
            "지난번 원본 반영이 파일을 저장하기 전에 중단되었습니다(앱이 강제로 종료됨). 원본 파일은 반영 전 그대로입니다. "
            "다시 ‘원본에 반영’을 누르면 반영합니다."
        )
    if state == "partial":
        return (
            "지난번 원본 반영이 파일을 저장하는 도중에 중단되어(앱이 강제로 종료됨) 원본 파일이 없거나 일부만 저장된 것으로 "
            f"보입니다. 반영 전 파일인 {copy}을(를) 복원한 뒤 다시 반영해 주세요."
        )
    done = (
        "지난번 원본 반영은 파일 저장까지 마쳤지만 그 뒤 앱이 강제로 종료되어 문서를 다시 읽지 못했습니다. "
        if phase == "saved"
        else "지난번 원본 반영이 끝나기 전에 앱이 강제로 종료되었습니다. 원본 파일이 반영 전과 "
        "달라졌지만 반영한 내용을 끝까지 저장했는지는 알 수 없습니다. "
    )
    return (
        f"{done}파일을 열어 확인하고, 반영 전으로 되돌리려면 {copy}을(를) 복원해 주세요. 그대로 두려면 다시 ‘원본에 "
        "반영’을 누르면 진행합니다."
    )


def remains(state, name):
    """A sentence on what a write that failed or was stopped left of the file, when it is not as it was ('' when it
    is): `name` is the copy of the file before the write."""
    copy = f"Wiki 폴더의 ‘{BACKUPS}’에 있는 {name}"
    return {
        "partial": f" 확인해 보니 원본 파일이 없거나 일부만 저장된 것으로 보입니다. {copy}을(를) 복원해 주세요.",
        "unknown": f" 확인해 보니 원본 파일이 반영 전과 달라졌습니다. 파일을 열어 확인하고, 되돌리려면 {copy}을(를) 복원해 "
        "주세요.",
    }.get(state, "")


def bases(parts, written=()):
    """The texts the file of a document's parts [(doc id, Markdown imported, Markdown now, HTML page or None)] may hold,
    each as a list of every part's: what write-backs put into it (`written`, newest first), then the imported text."""
    found = []
    for texts in [*written, [old for _, old, _, _ in parts]]:
        if list(texts) not in found:
            found.append(list(texts))
    return found


def plan_of(application, parts, written=()):
    """The changes of a document's parts [(doc id, Markdown imported, Markdown now, HTML page or None)] for the
    Office writer: Excel's by cell, the others' by unit of text. When write-backs put other texts into the file
    (`written`, see bases), the plan is {"choices": [...]}, the changes against each text the file may hold in that
    order, with what tells the file holding it from one holding another (`witness`): the writer takes the first the file
    is found to hold."""
    texts, now = bases(parts, written), [new for _, _, new, _ in parts]
    pages = [html for _, _, _, html in parts]
    if len(texts) == 1:
        return against(application, texts[0], now, pages)
    choices = []
    for base in texts:
        try:
            choice = against(application, base, now, pages)
        except Unchanged:  # The file may hold the document's text already.
            choice = (
                {"cells": []}
                if application == "Excel"
                else {"units": [list(unit) for unit in units("\n\n".join(base), application)], "edits": []}
            )
        choice["witness"] = told(application, base, [other for other in texts if other is not base], pages)
        choices.append(choice)
    return {"choices": choices}


def against(application, base, now, pages):
    """The changes that make the parts' texts `base` the texts `now`, as plan_of has them."""
    if application == "Excel":
        return {"cells": excel_changes(list(zip(base, now, pages)))}
    # The parts are read as one text: a part that goes on with a slide or a page is still in its section.
    old = units("\n\n".join(base), application)
    new = units("\n\n".join(now), application)
    return {"units": [list(unit) for unit in old], "edits": changes(old, new, application)}


def told(application, base, others, pages):
    """What tells a file holding the parts' texts `base` from one holding any of `others` (source_edits.witness)."""
    if application == "Excel":
        return [cell for other in others for cell in excel_witness(list(zip(base, other, pages)))]
    mine = units("\n\n".join(base), application)
    found, gaps = set(), set()
    for other in others:
        seen = witness(mine, units("\n\n".join(other), application))
        found.update(seen["units"])
        gaps.update(tuple(gap) for gap in seen["gaps"])
    return {"units": sorted(found), "gaps": [list(gap) for gap in sorted(gaps)]}


def count(plan):
    plan = plan["choices"][0] if "choices" in plan else plan
    return len(plan["cells"]) if "cells" in plan else len(plan["edits"])


def writes(parts):
    """The texts write-backs put into the file since the import, newest first, each a list of every part's, from the
    parts' `written` (Writeback.remember)."""
    histories = []
    for part in parts:
        try:
            history = json.loads(part["written"] or "[]")
        except ValueError:
            history = []
        valid = isinstance(history, list) and all(isinstance(text, str) for text in history)
        histories.append(history if valid else [])
    depth = min((len(history) for history in histories), default=0)
    return [[history[number] for history in histories] for number in range(depth)]


class Writeback:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.job = None
        self.thread = None
        self.stop = threading.Event()
        # The hidden Office application that writes a file on this PC, and the reader that takes it in again.
        self.writer = OfficeWriter()
        self.reader = OfficeConverter()
        # How many copies of each file stay (KEEP_BACKUPS).
        self.keep = KEEP_BACKUPS

    # -- RPC ----------------------------------------------------------------

    def start(self, params):
        """Write the changes of the document `id` (every part of a long one) into the file it came from: a background
        job, whose `status` tells how it went."""
        from .service import text

        ident = text(params.get("id"), "문서 ID", 80)
        table, kind, row, rows = self.source(ident)
        name = row["application"]
        if name not in ("Word", "Excel", "PowerPoint", "Visio"):
            raise UserError(
                "이 파일은 원본에 반영할 수 없습니다. Word·Excel·PowerPoint·Visio 문서만 반영할 수 있습니다."
            )
        parts = [(part["doc_id"], part["markdown"], part["content"], None) for part in rows]
        if name == "Excel":
            parts = self.pages(parts)
        written = writes(rows)
        plan = plan_of(name, parts, written)
        # The texts the file may hold and the one this write makes, for what it puts there to be kept (remember).
        record = {
            "table": table,
            "parts": rows,
            "bases": bases(parts, written),
            "texts": [new for _, _, new, _ in parts],
        }
        with self.lock:
            if self.busy():
                raise UserError(BUSY)
            if self.importing():
                raise UserError(IMPORTING)
            path = Path(row["source_path"])
            # A write that a crash or a kill cut short is told before anything is written again.
            cut = self.interrupted(path)
            if cut:
                raise UserError(cut)
            if not path.is_file():
                raise UserError("원본 파일을 찾을 수 없습니다.")
            self.writer.ready(name)
            stop = threading.Event()
            job = {
                "id": str(uuid.uuid4()),
                "doc_id": ident,
                "kind": kind,
                "application": name,
                "file": Path(row["source_path"]).name,
                "status": "running",
                "changes": count(plan),
                "notice": "",
                "waiting": "",
                "message": "",
                "error": "",
                "backup": "",
                "reread": False,
            }
            thread = threading.Thread(
                target=self._write_file, args=(job, row, name, plan, stop, record), name="writeback", daemon=True
            )
            thread.start()
            self.thread = thread
            self.job, self.stop = job, stop
            return deepcopy(job)

    def status(self, params=None):
        """The current write, or None; with `id`, only that one."""
        with self.lock:
            job = self.job
            ident = params.get("id") if isinstance(params, dict) else None
            return deepcopy(job) if job and (not isinstance(ident, str) or job["id"] == ident) else None

    def cancel(self, params=None):
        """Stop the write before the file is saved. One already saved stays saved, and the job says so."""
        with self.lock:
            job = self.job
            if job and job["status"] in ACTIVE:
                self.stop.set()
                job["status"] = "cancelling"
            return deepcopy(job)

    def dismiss(self, params):
        with self.lock:
            job = self.job
            if job and job["id"] == params.get("id") and job["status"] not in ACTIVE:
                job["dismissed"] = True
            return deepcopy(job)

    def busy(self):
        with self.lock:
            return bool(self.job and self.job["status"] in ACTIVE)

    def backups(self, params):
        """The copies that write-backs made of the file the document `doc_id` came from, the newest first: id, name,
        when it was made, size, what made it (kind: a write, or a restore that replaced the file), how the write went
        (state) and whether the file holds it now (current). `interrupted` tells what a write a crash or a kill cut
        short left of the file, as the next write will tell it; `keep` is how many copies of a file stay."""
        from .service import text

        _, _, row, _ = self.source(text(params.get("doc_id"), "문서 ID", 80))
        path, folder = Path(row["source_path"]), self.folder()
        try:
            current = checksum(path, limit=None) if path.is_file() else None
        except OSError:
            current = None
        with self.service.db.connect() as db:
            found = [
                dict(item)
                for item in db.execute(
                    """SELECT * FROM writeback_backups WHERE source_path=?
                ORDER BY rowid DESC""",
                    (str(path),),
                )
            ]
        listed = [
            {
                "id": item["id"],
                "name": item["name"],
                "created_at": item["created_at"],
                "size": item["size"],
                "kind": item["kind"],
                "state": item["state"],
                "current": item["checksum"] == current,
            }
            for item in found
            if (folder / item["name"]).is_file()
        ]
        with self.lock:
            pending = [] if self.busy() else self.pending(found)
        cut = None
        if pending:
            state = classify(path, pending[0]["checksum"])
            cut = {
                "state": state,
                "backup": pending[0]["id"],
                "name": pending[0]["name"],
                "message": interruption(state, pending[0]["name"], self.phase(pending[0])),
            }
        return {"file": path.name, "keep": self.keep, "backups": listed, "interrupted": cut}

    def restore_backup(self, params):
        """Put the copy `backup` (an id from backups) of the file the document `doc_id` came from back in its place:
        a write the user wants undone, or a file a write cut short. Only a copy the app made of that file is taken, and
        only while it still holds what it held. The file as it is now is copied first, unless it is that copy already
        or not there, so a restore can be undone as well. The document stays as it is; ‘원본에서 업데이트’ reads the
        restored file into it."""
        from .service import text

        ident = text(params.get("doc_id"), "문서 ID", 80)
        chosen = text(params.get("backup"), "백업", 80)
        _, _, row, _ = self.source(ident)
        path = Path(row["source_path"])
        with self.lock:
            if self.busy():
                raise UserError(BUSY)
            if self.importing():
                raise UserError(IMPORTING)
            with self.service.db.connect() as db:
                record = db.execute(
                    "SELECT * FROM writeback_backups WHERE id=? AND source_path=?", (chosen, str(path))
                ).fetchone()
            if record is None:
                raise UserError(
                    "이 문서의 원본 파일을 백업한 것이 아닙니다. 백업 목록(writeback.backups)에서 골라 주세요."
                )
            copy = self.folder() / record["name"]
            try:
                kept = copy.is_file() and checksum(copy, limit=None) == record["checksum"]
            except OSError:
                kept = False
            if not kept:
                raise UserError("백업 파일이 없거나 백업한 뒤 바뀌어 복원하지 않았습니다.")
            try:
                current = checksum(path, limit=None) if path.is_file() else None
            except OSError:
                raise UserError(
                    "원본 파일을 읽지 못해 복원하지 않았습니다. 다른 프로그램에서 열려 있다면 닫고 다시 시도해 주세요."
                ) from None
            if current == record["checksum"]:
                self.resolve(path)
                return {
                    "restored": False,
                    "backup": "",
                    "message": "원본 파일이 이미 이 백업과 같아 그대로 두었습니다.",
                }
            before = None
            if current is not None:
                try:
                    before = self.backup(path, ident, kind="restore")
                except OSError:
                    raise UserError(
                        f"지금의 원본 파일을 먼저 ‘{BACKUPS}’에 복사하지 못해 복원하지 않았습니다. 저장 공간과 "
                        "권한을 확인해 주세요."
                    ) from None
            try:
                self.replace(copy, path)
            except OSError:
                raise UserError(
                    "원본 파일이 다른 프로그램에서 열려 있거나 쓸 수 없어 복원하지 못했습니다. 파일을 닫고 다시 "
                    "시도해 주세요. 원본 파일은 바뀌지 않았습니다."
                ) from None
            self.resolve(path)
            self.prune(path)
        message = f"백업 {record['name']}을(를) 원본 파일 자리에 복원했습니다."
        if before:
            message += f" 복원하기 전의 파일은 ‘{BACKUPS}’에 {before['name']}(으)로 남겼습니다."
        return {
            "restored": True,
            "backup": before["name"] if before else "",
            "message": message + " 문서에 반영하려면 문서 창에서 ‘원본에서 업데이트’를 눌러 주세요.",
        }

    # -- helpers --------------------------------------------------------------

    def importing(self):
        """Whether an import that uses Office runs: Office and RTF files."""
        jobs = (self.service.office.job, self.service.rtf.job)
        return any(job and job["status"] in ACTIVE for job in jobs)

    def source(self, ident):
        """(table, kind, source row, parts) of the import the document `ident` belongs to; parts are its documents in
        order, each with doc_id, markdown (the text imported), imported_at, written (what write-backs put into the file
        since, as JSON), content (the text now) and revision."""
        with self.service.db.connect() as db:
            if not db.execute("SELECT 1 FROM documents WHERE id=? AND deleted_at IS NULL", (ident,)).fetchone():
                raise UserError("문서를 찾을 수 없습니다.")
            table, kind = "office_sources", "office"
            row = db.execute(f"SELECT * FROM {table} WHERE doc_id=?", (ident,)).fetchone()
            if not row:
                raise UserError(UNSUPPORTED)
            columns = sources.key(table)
            where = " AND ".join(f"s.{column}=?" for column in columns)
            parts = db.execute(
                f"""SELECT s.doc_id, s.markdown, s.imported_at, s.written, d.content, d.revision
                FROM {table} s JOIN documents d ON d.id=s.doc_id AND d.deleted_at IS NULL WHERE {where} ORDER BY s.part""",
                [row[column] for column in columns],
            ).fetchall()
        return table, kind, dict(row), [dict(part) for part in parts]

    def pages(self, parts):
        """The parts with the HTML page each was imported as, which keeps the address of each Excel cell."""
        found = []
        with self.service.db.connect() as db:
            for ident, old, new, _ in parts:
                row = db.execute("SELECT html FROM document_html WHERE doc_id=?", (ident,)).fetchone()
                found.append((ident, old, new, row["html"] if row else None))
        return found

    def _set(self, job, **values):
        with self.lock:
            if job["status"] == "cancelling":
                values.pop("status", None)
            job.update(values)

    def folder(self):
        """The Wiki folder's 원본 백업 folder, which holds the copies."""
        return Path(self.service.db.path).parent / BACKUPS

    def backup(self, path, doc_id="", kind="write"):
        """Copy the file at `path` into the backup folder, named after when the copy was made, and record the copy
        (writeback_backups): what it holds, and the document whose write or restore made it (kind). A write's copy says
        that the write began (state "writing") until settle() records how it ended. The record, with the copy's `path`
        and the `journal` file its save notes its progress in (OfficeWriter)."""
        from .service import now

        folder = self.folder()
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = folder / f"{stamp} {path.name}"
        number = 1
        # Not a name a record still holds (its copy removed by hand) either, or the record would name the new copy.
        with self.service.db.connect() as db:
            taken = {
                row[0] for row in db.execute("SELECT name FROM writeback_backups WHERE name LIKE ?", (f"{stamp}%",))
            }
        while target.exists() or target.name in taken:
            number += 1
            target = folder / f"{stamp}-{number} {path.name}"
        shutil.copy2(path, target)
        try:
            record = {
                "id": str(uuid.uuid4()),
                "source_path": str(path),
                "doc_id": doc_id,
                "name": target.name,
                "checksum": checksum(target, limit=None),
                "size": target.stat().st_size,
                "kind": kind,
                "state": "writing" if kind == "write" else "done",
                "created_at": now(),
            }
            with self.service.db.connect() as db:
                db.execute(
                    f"INSERT INTO writeback_backups({','.join(record)}) VALUES({','.join('?' * len(record))})",
                    list(record.values()),
                )
        except BaseException:
            with contextlib.suppress(OSError):
                target.unlink()
            raise
        return {**record, "path": target, "journal": self.journal(record)}

    def journal(self, record):
        """The file in which the save of the write that made the copy `record` notes how far it got."""
        return self.folder() / f"{record['name']}.writing"

    def phase(self, record):
        """How far the save of the write that made the copy `record` got: '' (it did not begin), 'saving' or 'saved'."""
        try:
            return self.journal(record).read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def prune(self, path):
        """Keep the newest `keep` copies of the file at `path`, in the order they were recorded (a clock the system sets
        back would put a new copy among the old ones); older ones go with their records. A copy that cannot be removed
        now (open in another program) stays recorded for the next time, and a record whose copy is gone (removed by
        hand) goes. Copies of other files, and files the user put in the folder, stay."""
        folder, removed, kept = self.folder(), [], 0
        with self.service.db.connect() as db:
            found = db.execute(
                """SELECT id, name FROM writeback_backups WHERE source_path=?
                ORDER BY rowid DESC""",
                (str(path),),
            ).fetchall()
        for record in found:
            copy = folder / record["name"]
            if not copy.is_file():
                removed.append(record["id"])
                continue
            kept += 1
            if kept <= self.keep:
                continue
            try:
                copy.unlink()
            except OSError:
                continue
            with contextlib.suppress(OSError):
                self.journal(record).unlink(missing_ok=True)
            removed.append(record["id"])
        if removed:
            with self.service.db.connect() as db:
                db.execute(f"DELETE FROM writeback_backups WHERE id IN ({','.join('?' * len(removed))})", removed)

    @staticmethod
    def pending(found):
        """Of the copies `found` of a file (records, newest first), those of writes a crash or a kill cut short that
        the next write is still to tell: not told yet ("writing" while no write runs), or told while they left the file
        partly written, which is told until it is whole again."""
        return [
            item
            for item in found
            if item["state"] == "writing" or (item["state"] == "interrupted" and item["outcome"] == "partial")
        ]

    def interrupted(self, path):
        """What a write to the file at `path` that a crash or a kill cut short left, to tell before the next write: a
        message, or None when there is nothing (left) to tell. Called with the lock held while no write runs. A write
        cut short is told once, as the file is now (classify); a file it left partly written is told until it is whole
        again, restored from its copy (restore_backup) or by hand."""
        with self.service.db.connect() as db:
            found = self.pending(
                [
                    dict(item)
                    for item in db.execute(
                        """SELECT * FROM writeback_backups
                WHERE source_path=? ORDER BY rowid DESC""",
                        (str(path),),
                    )
                ]
            )
        if not found:
            return None
        latest = found[0]
        state, phase = classify(path, latest["checksum"]), self.phase(latest)
        news = any(item["state"] == "writing" for item in found)
        with self.service.db.connect() as db:
            db.execute(
                f"UPDATE writeback_backups SET state='interrupted', outcome=? "
                f"WHERE id IN ({','.join('?' * len(found))})",
                [state, *(item["id"] for item in found)],
            )
        for item in found:
            with contextlib.suppress(OSError):
                self.journal(item).unlink(missing_ok=True)
        return interruption(state, latest["name"], phase) if news or state == "partial" else None

    def resolve(self, path):
        """The file at `path` was restored from a copy: what writes cut short left of it is told no more."""
        with self.service.db.connect() as db:
            db.execute(
                """UPDATE writeback_backups SET state='interrupted', outcome='restored' WHERE source_path=? AND
                (state='writing' OR (state='interrupted' AND outcome='partial'))""",
                (str(path),),
            )

    def settle(self, copy, written):
        """Record how the write that made the copy `copy` ended: `written` (it saved the file, whose checksum is kept)
        or not, with what the file was found to hold then (classify). A sentence for the user when the write left the
        file changed though it did not end as it should ('' otherwise). The journal of its save goes."""
        path, state, outcome, digest = Path(copy["source_path"]), "failed", "", ""
        if written:
            state = "written"
            with contextlib.suppress(OSError):
                digest = checksum(path, limit=None)
        else:
            outcome = classify(path, copy["checksum"])
        try:
            with self.service.db.connect() as db:
                db.execute(
                    "UPDATE writeback_backups SET state=?, outcome=?, written=? WHERE id=?",
                    (state, outcome, digest, copy["id"]),
                )
        except Exception as error:
            diagnostics.report(error, "원본 백업 기록을 고치지 못했습니다.", "원본 반영 기록")
        with contextlib.suppress(OSError):
            copy["journal"].unlink(missing_ok=True)
        return remains(outcome, copy["name"])

    @staticmethod
    def replace(copy, path):
        """Put the bytes of `copy` at `path` at once: written beside it first, then moved over it, so that a crash
        leaves the file as it was or as the copy, never part of each. The file takes the time of the restore, so that
        imports and the routines read it again."""
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.restoring")
        try:
            shutil.copyfile(copy, temporary)
            os.replace(temporary, path)
        finally:
            with contextlib.suppress(OSError):
                temporary.unlink(missing_ok=True)

    def remember(self, record, summary):
        """Keep what a write put into the file with the import's documents (`written`), for the next write to be worked
        out against it too: every part's text the write made, then the text the write was made against when an earlier
        write had put it there (summary["base"], the number among record["bases"]), which records the last successful write. An import of the file again forgets them. One not kept is logged."""
        try:
            base = record["bases"][summary.get("base", 0)]
            imported = [part["markdown"] for part in record["parts"]]
            with self.service.db.connect() as db:
                for number, part in enumerate(record["parts"]):
                    kept = [record["texts"][number]] + ([base[number]] if base != imported else [])
                    db.execute(
                        f"UPDATE {record['table']} SET written=? WHERE doc_id=? AND imported_at=?",
                        (json.dumps(kept, ensure_ascii=False), part["doc_id"], part["imported_at"]),
                    )
        except Exception as error:
            diagnostics.report(error, "원본에 반영한 내용을 기록하지 못했습니다.", "원본 반영 기록")

    def _write_file(self, job, row, name, plan, stop, record):
        path = Path(row["source_path"])
        # One write at a time (busy): while another import still has the Office application, the job says so.
        self.writer.waiting = self.reader.waiting = lambda text: self._set(job, waiting=text)
        copy, settled = None, False
        try:
            try:
                copy = self.backup(path, job["doc_id"])
            except OSError:
                raise UserError(
                    f"원본 파일을 고치기 전에 Wiki 폴더의 ‘{BACKUPS}’에 복사하지 못했습니다. 저장 공간과 "
                    "권한을 확인해 주세요. 원본 파일은 바뀌지 않았습니다."
                ) from None
            self.prune(path)
            self._set(
                job,
                backup=str(copy["path"]),
                notice=f"{name}{'을' if name == 'Excel' else '를'} 창 없이 열어 고친 내용을 쓰고 저장합니다.",
            )
            summary = self.writer(path, {"application": name, "plan": plan, "journal": str(copy["journal"])}, stop)
            settled = True
            self.settle(copy, written=True)
            written = summary.get("changes", job["changes"])
            message = f"원본 파일에 고친 곳 {written}개를 저장했습니다."
            self.remember(record, summary)  # The base of the next write, unless the file is read again below.
            try:
                revisions = {part["doc_id"]: part["revision"] for part in record["parts"]}
                said, again = self.reread(path, row, name, job["doc_id"], revisions)
                message += " " + said
                self._set(job, reread=again)
            except Exception as error:
                reason = diagnostics.report(error, "원본 파일을 다시 읽지 못했습니다.", "원본 반영 뒤 다시 읽기")
                message += (
                    f" 문서는 원본 파일에서 다시 읽지 못했습니다({reason.rstrip('.')}). 문서 창의 ‘원본에서 "
                    "업데이트’로 다시 읽어 주세요."
                )
            with self.lock:
                job.update(status="completed", message=message, notice="", changes=written)
        except Exception as error:
            # What the file holds now: a write that failed or was stopped after it began to save may have changed it.
            note = self.settle(copy, written=False) if copy and not settled else ""
            # Stopped by the user: the writer says whether the file was left as it was.
            cancelled = stop.is_set() and isinstance(error, UserError)
            reason = (
                str(error)
                if cancelled
                else diagnostics.report(
                    error,
                    "원본 파일에 반영하지 못했습니다." + ("" if note else " 원본 파일은 바뀌지 않았습니다."),
                    "원본 반영",
                )
            )
            reason += note
            with self.lock:
                job.update(
                    status="cancelled" if cancelled else "error",
                    error="" if cancelled else reason,
                    message=reason if cancelled else "",
                    notice="",
                )

    def reread(self, path, row, name, ident, revisions):
        """Read the written file into the document `ident` (every part), as an update from the file replacing the
        text the user wrote, which the file now holds; unless a part is no longer at the revision the write was worked
        out from ({doc id: revision}): the user changed it meanwhile, and that change stays. (What it did, as a
        sentence; whether the document was read again.)"""
        from .service import now

        def edited(db):
            found = db.execute(
                f"SELECT id, revision FROM documents WHERE id IN ({','.join('?' * len(revisions))})", list(revisions)
            ).fetchall()
            return {item["id"]: item["revision"] for item in found} != revisions

        with self.service.db.connect() as db:
            if edited(db):
                return EDITED_MEANWHILE, False
        digest = checksum(path)
        split = page_parts(validate_html(self.reader(path, threading.Event())))
        if checksum(path) != digest:
            raise UserError("다시 읽는 동안 원본 파일이 바뀌었습니다.")
        stat = sources.file_stat(path)
        with self.service.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if edited(db):
                return EDITED_MEANWHILE, False
            found = sources.plan(db, "office_sources", row["source_path"], {"source_hash": digest}, ident, True)
            if found.action == "skip":
                return "같은 내용의 문서가 이미 있어 이 문서는 그대로 두었습니다.", False
            ids, removed = sources.save(
                db,
                "office_sources",
                found,
                split,
                path.stem,
                now(),
                {
                    "source_path": row["source_path"],
                    "source_hash": digest,
                    "application": name,
                    "source_size": stat[0],
                    "source_mtime": stat[1],
                },
            )
        message = "문서는 저장한 파일의 내용으로 다시 읽었습니다."
        if removed:
            message += f" 글이 줄어 남는 부분 문서 {removed}개는 지웠습니다."
        return message, True
