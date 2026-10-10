"""Write edited Wiki content back to local Office files, keeping backups and change history."""
import contextlib
import hashlib
import json
import re
import shutil
import threading
import uuid
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
KEEP_BACKUPS = 30
BACKUP_NAME = re.compile(r"\d{8}-\d{6}(?:-\d+)? ")
BUSY = "원본에 반영하는 중입니다. 끝난 뒤 다시 시도해 주세요."
IMPORTING = "Office·RTF 문서 가져오기가 진행 중입니다. 끝난 뒤 원본에 반영해 주세요."
UNSUPPORTED = ("이 PC의 Office 파일(Word·Excel·PowerPoint·Visio)에서 가져온 문서만 원본에 반영할 수 "
               "있습니다.")
EDITED_MEANWHILE = ("반영하는 동안 문서가 바뀌어 문서는 원본 파일에서 다시 읽지 않았습니다(바뀐 내용은 그대로 둠). 그 내용도 "
                    "원본에 쓰려면 다시 ‘원본에 반영’을 눌러 주세요.")


def checksum(path):
    """The sha256 of a file as an import records it."""
    digest, size = hashlib.sha256(), 0
    with Path(path).open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise UserError("파일이 50MB를 초과합니다.")
            digest.update(chunk)
    return digest.hexdigest()


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
            choice = {"cells": []} if application == "Excel" else {
                "units": [list(unit) for unit in units("\n\n".join(base), application)], "edits": []}
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

    # -- RPC ----------------------------------------------------------------

    def start(self, params):
        """Write the changes of the document `id` (every part of a long one) into the file it came from: a background
        job, whose `status` tells how it went."""
        from .service import text
        ident = text(params.get("id"), "문서 ID", 80)
        table, kind, row, rows = self.source(ident)
        name = row["application"]
        if name not in ("Word", "Excel", "PowerPoint", "Visio"):
            raise UserError("이 파일은 원본에 반영할 수 없습니다. Word·Excel·PowerPoint·Visio 문서만 반영할 수 있습니다.")
        parts = [(part["doc_id"], part["markdown"], part["content"], None) for part in rows]
        if name == "Excel":
            parts = self.pages(parts)
        written = writes(rows)
        plan = plan_of(name, parts, written)
        # The texts the file may hold and the one this write makes, for what it puts there to be kept (remember).
        record = {"table": table, "parts": rows, "bases": bases(parts, written),
                  "texts": [new for _, _, new, _ in parts]}
        with self.lock:
            if self.busy():
                raise UserError(BUSY)
            if self.importing():
                raise UserError(IMPORTING)
            path = Path(row["source_path"])
            if not path.is_file():
                raise UserError("원본 파일을 찾을 수 없습니다.")
            self.writer.ready(name)
            stop = threading.Event()
            job = {
                "id": str(uuid.uuid4()), "doc_id": ident, "kind": kind, "application": name,
                "file": Path(row["source_path"]).name, "status": "running", "changes": count(plan), "notice": "",
                "waiting": "", "message": "", "error": "", "backup": "", "reread": False}
            thread = threading.Thread(target=self._write_file, args=(job, row, name, plan, stop, record),
                                      name="writeback", daemon=True)
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
            parts = db.execute(f"""SELECT s.doc_id, s.markdown, s.imported_at, s.written, d.content, d.revision
                FROM {table} s JOIN documents d ON d.id=s.doc_id AND d.deleted_at IS NULL WHERE {where} ORDER BY s.part""",
                               [row[column] for column in columns]).fetchall()
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

    def backup(self, path):
        """A copy of the file as it was, in the Wiki folder's 원본 백업 folder, named after when it was made. The newest
        KEEP_BACKUPS of the copies stay; one that cannot be removed now (open in Office) is left for later."""
        folder = Path(self.service.db.path).parent / BACKUPS
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = folder / f"{stamp} {path.name}"
        number = 1
        while target.exists():
            number += 1
            target = folder / f"{stamp}-{number} {path.name}"
        shutil.copy2(path, target)
        # By name, the time of the copy: copy2 keeps the original's time, which says nothing of the copy's age.
        copies = sorted((item for item in folder.iterdir() if BACKUP_NAME.match(item.name) and item.is_file()),
                        key=lambda item: item.name, reverse=True)
        for old in copies[KEEP_BACKUPS:]:
            with contextlib.suppress(OSError):
                old.unlink()
        return target

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
                    db.execute(f"UPDATE {record['table']} SET written=? WHERE doc_id=? AND imported_at=?",
                               (json.dumps(kept, ensure_ascii=False), part["doc_id"], part["imported_at"]))
        except Exception as error:
            diagnostics.report(error, "원본에 반영한 내용을 기록하지 못했습니다.", "원본 반영 기록")

    def _write_file(self, job, row, name, plan, stop, record):
        path = Path(row["source_path"])
        # One write at a time (busy): while another import still has the Office application, the job says so.
        self.writer.waiting = self.reader.waiting = lambda text: self._set(job, waiting=text)
        try:
            try:
                copy = self.backup(path)
            except OSError:
                raise UserError(f"원본 파일을 고치기 전에 Wiki 폴더의 ‘{BACKUPS}’에 복사하지 못했습니다. 저장 공간과 "
                                "권한을 확인해 주세요. 원본 파일은 바뀌지 않았습니다.") from None
            self._set(job, backup=str(copy), notice=f"{name}{'을' if name == 'Excel' else '를'} 창 없이 열어 고친 "
                      "내용을 쓰고 저장합니다.")
            summary = self.writer(path, {"application": name, "plan": plan}, stop)
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
                message += (f" 문서는 원본 파일에서 다시 읽지 못했습니다({reason.rstrip('.')}). 문서 창의 ‘원본에서 "
                            "업데이트’로 다시 읽어 주세요.")
            with self.lock:
                job.update(status="completed", message=message, notice="", changes=written)
        except Exception as error:
            # Stopped by the user: the writer says whether the file was left as it was.
            cancelled = stop.is_set() and isinstance(error, UserError)
            reason = str(error) if cancelled else diagnostics.report(
                error, "원본 파일에 반영하지 못했습니다. 원본 파일은 바뀌지 않았습니다.", "원본 반영")
            with self.lock:
                job.update(status="cancelled" if cancelled else "error", error="" if cancelled else reason,
                           message=reason if cancelled else "", notice="")

    def reread(self, path, row, name, ident, revisions):
        """Read the written file into the document `ident` (every part), as an update from the file replacing the
        text the user wrote, which the file now holds; unless a part is no longer at the revision the write was worked
        out from ({doc id: revision}): the user changed it meanwhile, and that change stays. (What it did, as a
        sentence; whether the document was read again.)"""
        from .service import now

        def edited(db):
            found = db.execute(f"SELECT id, revision FROM documents WHERE id IN ({','.join('?' * len(revisions))})",
                               list(revisions)).fetchall()
            return {item["id"]: item["revision"] for item in found} != revisions
        with self.service.db.connect() as db:
            if edited(db):
                return EDITED_MEANWHILE, False
        digest = checksum(path)
        split = page_parts(validate_html(self.reader(path, threading.Event())))
        if checksum(path) != digest:
            raise UserError("다시 읽는 동안 원본 파일이 바뀌었습니다.")
        with self.service.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if edited(db):
                return EDITED_MEANWHILE, False
            found = sources.plan(db, "office_sources", row["source_path"], {"source_hash": digest}, ident, True)
            if found.action == "skip":
                return "같은 내용의 문서가 이미 있어 이 문서는 그대로 두었습니다.", False
            ids, removed = sources.save(db, "office_sources", found, split, path.stem, now(),
                                        {"source_path": row["source_path"], "source_hash": digest, "application": name})
        message = "문서는 저장한 파일의 내용으로 다시 읽었습니다."
        if removed:
            message += f" 글이 줄어 남는 부분 문서 {removed}개는 지웠습니다."
        return message, True
