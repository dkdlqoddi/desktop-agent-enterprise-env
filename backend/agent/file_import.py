"""Background jobs that import a file, or every file of one kind in a folder, as HTML documents of the Wiki."""
import hashlib
import math
import os
import stat
import threading
import uuid
from copy import deepcopy
from pathlib import Path

from . import diagnostics, sources
from .html_document import validate_html
from .llm import BusyError, UserError
from .parts import page_parts
from .word_converter import WordConverter

MAX_FILE_BYTES = 50 * 1024 * 1024
# A folder of more files is imported in batches of this many, one batch after another in the same job; the screens show
# a batch's documents once it is done.
BATCH_FILES = 5000
MAX_RESULTS = BATCH_FILES  # Results a job keeps for the screens; its counts are exact. Failures are kept first.
IO_REPARSE_TAG_MOUNT_POINT = 0xA0000003  # A Windows junction; the stat module names it on Windows only.
HIDDEN_SYSTEM = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM


def skipped_folder(path):
    """A folder that a folder import never enters: a link, including a Windows junction such as Documents\\My Music
    that nobody may list, or a hidden system folder such as System Volume Information or $RECYCLE.BIN."""
    try:
        info = os.lstat(path)
    except OSError:
        return True
    return (stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) == IO_REPARSE_TAG_MOUNT_POINT
            or getattr(info, "st_file_attributes", 0) & HIDDEN_SYSTEM == HIDDEN_SYSTEM)


class FileImporter:
    """The import job of one kind of file. A subclass names the extensions it takes, how its jobs and messages call
    them, the source table that remembers each import, and the converter that makes a file's HTML page."""
    extensions = set()
    label = ""
    format_label = ""
    source_table = ""
    # Whether its files are read by a hidden Office application, which must not meet one writing a file (writeback.py).
    uses_office = False

    def __init__(self, service, converter, capability_check):
        self.service = service
        self.converter = converter
        if isinstance(self.converter, WordConverter):
            # The job says so while another job's worker uses the same Office application (word_converter.Turns).
            self.converter.waiting = lambda text: self._set(waiting=text)
        self.capability_check = capability_check
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.job = None
        self.thread = None

    def start(self, params):
        from .service import routine_name, text
        raw = text(params.get("path"), "파일 또는 폴더 경로", 32767).strip('"')
        mode = params.get("mode")
        if mode not in ("file", "directory"):
            raise UserError("단일 파일 또는 폴더를 선택해 주세요.")
        root = Path(raw).expanduser()
        if not root.is_absolute():
            raise UserError("파일 또는 폴더의 전체 경로를 입력해 주세요.")
        if mode == "file" and (not root.is_file() or root.suffix.lower() not in self.extensions):
            raise UserError(f"존재하는 {self.format_label} 파일을 선택해 주세요.")
        if mode == "directory" and not root.is_dir():
            raise UserError("존재하는 폴더를 선택해 주세요.")
        target, replace = sources.options(params)
        available = self.capability_check()
        if not available["available"]:
            raise UserError(available["reason"])
        if params.get("analyze") and not self.service.llm.public()["configured"]:
            raise UserError("자동 AI 분석을 사용하려면 먼저 LLM을 연결해 주세요.")
        with self.lock:
            if self.job and self.job["status"] in ("scanning", "running", "cancelling"):
                raise BusyError(f"이미 {self.label} 가져오기가 진행 중입니다. 완료 후 다시 실행해 주세요.")
            if self.uses_office and self.service.writeback.busy():
                raise BusyError("원본에 반영하는 중입니다. 끝난 뒤 다시 가져와 주세요.")
            self.cancel_event = threading.Event()
            # `edited` counts the files skipped because their documents' text was changed in the Wiki, `ignored`
            # those a routine leaves out because their documents were purged from the trash (both also in
            # `skipped`); `batch` is the batch of files being imported, of `batches`.
            self.job = {"id": str(uuid.uuid4()), "status": "scanning", "total": 0, "processed": 0,
                        "imported": 0, "updated": 0, "skipped": 0, "edited": 0, "ignored": 0, "failed": 0,
                        "analysis_failed": 0,
                        "batch": 0, "batches": 0, "current_file": "", "notice": "", "waiting": "", "results": [],
                        "error": "", "routine": routine_name(params)}
            self.thread = threading.Thread(target=self._run, args=(
                root, mode, bool(params.get("recursive", True)), bool(params.get("analyze", False)), target, replace),
                daemon=True)
            self.thread.start()
            return deepcopy(self.job)

    def status(self, _=None):
        with self.lock:
            return deepcopy(self.job)

    def cancel(self, _=None):
        with self.lock:
            if self.job and self.job["status"] in ("scanning", "running", "cancelling"):
                self.cancel_event.set()
                self.job["status"] = "cancelling"
            return deepcopy(self.job)

    def dismiss(self, params):
        """The user cleared the finished job `id` from the import box: every screen leaves it out from now on."""
        with self.lock:
            job = self.job
            if job and job["id"] == params.get("id") and job["status"] not in ("scanning", "running", "cancelling"):
                job["dismissed"] = True
            return deepcopy(job)

    def _set(self, **values):
        with self.lock:
            self.job.update(values)

    def _scan(self, root, mode, recursive, progress=lambda count: None):
        """The file `root`, or every file of the importer's kinds in the folder `root`, however many: the job imports
        them in batches. `progress` hears how many were found so far after each folder."""
        if mode == "file":
            return [root]
        found = []
        def scan_error(error):
            # The chosen folder must be read; a folder inside that cannot be, such as another user's, is left out.
            name = error.filename or root
            if os.path.normcase(os.path.abspath(name)) == os.path.normcase(os.path.abspath(root)):
                raise UserError(f"폴더를 읽지 못했습니다: {name}")
        for current, dirs, files in os.walk(root, followlinks=False, onerror=scan_error):
            dirs[:] = sorted(d for d in dirs if not skipped_folder(os.path.join(current, d))) if recursive else []
            if self.cancel_event.is_set():
                return []
            for name in sorted(files):
                if self.cancel_event.is_set():  # A folder may hold many thousands of files.
                    return []
                item = Path(current, name)
                if item.suffix.lower() in self.extensions and not item.is_symlink() and not name.startswith("~$"):
                    found.append(item)
            progress(len(found))
        return found

    def _checksum(self, file):
        digest, size = hashlib.sha256(), 0
        with file.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                if self.cancel_event.is_set():
                    return None
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise UserError("파일이 50MB를 초과합니다.")
                digest.update(chunk)
        if not size:
            raise UserError(f"빈 {self.label} 파일입니다.")
        return digest.hexdigest()

    def _plan(self, db, path, checksum, target, replace, split=None, stat=None):
        """What importing the file at `path` with the bytes `checksum` does (sources.plan): a file the same bytes were
        imported from is skipped, another one updates the earlier import of the same path unless its converted `split`
        has the text that import saved. A file moved from where its bytes were imported takes their documents along,
        with its `stat` (sources.file_stat)."""
        found = sources.plan(db, self.source_table, path, {"source_hash": checksum}, target, replace, split,
                             automatic=self._automatic(), stat=stat)
        if found.action == "skip" and found.reason == "same" and not db.execute(
                "SELECT 1 FROM document_html WHERE doc_id=?", (found.rows[0]["doc_id"],)).fetchone():
            # Saved as Markdown by an earlier version: the same documents become the HTML page, unless the user changed
            # their text.
            return sources.Plan("skip", found.rows, "edited") if sources.edited(found.rows) and not replace \
                else sources.Plan("update", found.rows, "page")
        return found

    def _automatic(self):
        """Whether the job runs for an automatic routine, which leaves out what the user put in the trash or purged."""
        return bool(self.job and self.job.get("routine"))

    def _one(self, file, analyze, target=None, replace=False):
        from .service import now
        path = str(file.resolve())
        stat = sources.file_stat(file)
        with self.service.db.connect() as db:
            if self._automatic() and sources.ignored(db, self.source_table, path):
                return sources.skipped(sources.Plan("skip", [], "ignored"), "")
            # A file whose size and modification time are the ones its import recorded is not read again to tell it
            # is the same: a routine over a folder of large files would read every one of them at every run.
            checksum = sources.stored_hash(db, self.source_table, path, stat)
        if checksum is None:
            checksum = self._checksum(file)
            if checksum is None:
                return None
        with self.service.db.connect() as db:
            found = self._plan(db, path, checksum, target, replace, stat=stat)
            if found.action == "skip" and found.reason == "same":
                # The same bytes: their size and time are recorded, so that the next import need not read them.
                sources.touch(db, self.source_table, found.rows, {"source_size": stat[0], "source_mtime": stat[1]},
                              path)
        if found.action == "skip":
            return sources.skipped(found, f"이미 가져온 동일한 {self.label}입니다.")
        if self.cancel_event.is_set():
            return None
        notice = self.notice(file)
        if notice:
            self._set(notice=notice)
        html = self.converter(file, self.cancel_event)
        if self.cancel_event.is_set():
            return None
        split = page_parts(validate_html(html))
        # Reading the original in place avoids snapshots. Reject a source edited during conversion.
        if self._checksum(file) != checksum:
            if self.cancel_event.is_set():
                return None
            raise UserError("변환 중 원본 파일이 변경되었습니다. 다시 가져와 주세요.")
        timestamp = now()
        with self.service.db.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            found = self._plan(db, path, checksum, target, replace, split, stat)
            if found.action == "skip":
                return sources.skipped(found, f"이미 가져온 동일한 {self.label}입니다.")
            if self.cancel_event.is_set():
                return None
            ids, removed = sources.save(db, self.source_table, found, split, file.stem, timestamp,
                                        {"source_path": path, "source_hash": checksum, "source_size": stat[0],
                                         "source_mtime": stat[1], **self.details(file)})
        result = sources.outcome(found, split, ids, removed, "HTML 문서로 Wiki에 저장했습니다.", "HTML")
        if analyze:
            for ident in ids:
                if self.cancel_event.is_set():
                    break
                try:
                    self.service.wiki_analyze({"id": ident})
                except UserError as error:
                    result.setdefault("analysis_error", str(error))
            if "analysis_error" in result:
                done = "업데이트" if result["status"] == "updated" else "저장"
                result["message"] = f"{done} 완료 · AI 분석 실패 (문서에서 재시도 가능)" if len(ids) == 1 else \
                    f"{result['message']} AI 분석에 실패한 문서가 있습니다 (문서에서 재시도 가능)."
        return result

    def notice(self, file):
        """What the user should know once `file` is about to be read; the app shows it as a notification."""
        return ""

    def details(self, file):
        """What the source table keeps of `file` besides its path and bytes."""
        return {}

    def _record(self, file, result):
        with self.lock:
            job = self.job
            job["results"].append({"path": str(file), **result})
            job[result["status"]] += 1
            job["processed"] += 1
            if result.get("reason") in ("edited", "ignored"):
                job[result["reason"]] += 1
            if result.get("analysis_error"):
                job["analysis_failed"] += 1
            if len(job["results"]) > MAX_RESULTS:
                # The counts stay exact; failures stay listed while there are other results to make room.
                drop = next((i for i, item in enumerate(job["results"]) if item["status"] != "failed"), 0)
                del job["results"][drop]

    def _run(self, root, mode, recursive, analyze, target=None, replace=False):
        try:
            files = self._scan(root, mode, recursive, lambda count: self._set(total=count))
            if not files and not self.cancel_event.is_set():
                raise UserError(f"선택한 폴더에 {self.format_label} 파일이 없습니다.")
            self._set(total=len(files), batches=math.ceil(len(files) / BATCH_FILES), status="running")
            for index, file in enumerate(files):
                if self.cancel_event.is_set():
                    break
                self._set(current_file=str(file), batch=index // BATCH_FILES + 1)
                try:
                    result = self._one(file, analyze, target, replace)
                except Exception as error:
                    if self.cancel_event.is_set():
                        break
                    result = {"status": "failed", "message": diagnostics.report(
                        error, "파일을 읽거나 Wiki에 저장하지 못했습니다. 권한과 디스크 공간을 확인해 주세요.",
                        f"{self.label} 가져오기")}
                if result is None:
                    break
                self._record(file, result)
            self._set(status="cancelled" if self.cancel_event.is_set() else "completed", current_file="")
        except Exception as error:
            self._set(status="cancelled" if self.cancel_event.is_set() else "error", current_file="",
                      error=diagnostics.report(error, f"{self.label} 가져오기를 시작하지 못했습니다.", f"{self.label} 가져오기"))
