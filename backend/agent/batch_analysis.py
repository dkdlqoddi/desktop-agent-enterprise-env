"""AI analysis of the documents chosen in the document list, one after another in a background thread, so the screens
stay free while the LLM reads them. Like an import job, the screens follow it, and it can be stopped and cleared."""
import threading
import uuid
from copy import deepcopy

from . import diagnostics
from .llm import BusyError, UserError

# Each analysis may take minutes (a long document's PageIndex several LLM rounds), so a run is kept to hours.
MAX_DOCUMENTS = 1000
ACTIVE = ("running", "cancelling")


class BatchAnalysis:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.cancel_event = threading.Event()
        self.job = None
        self.ids = []
        self.thread = None

    def start(self, params):
        """Analyze the documents `ids`. With `append` (the documents a Markdown import saved, files.import_text), a run
        under way takes them after its own instead of refusing them; one already waiting in it is not added twice."""
        from .service import document_ids
        ids = document_ids(params.get("ids"), MAX_DOCUMENTS)
        if not self.service.llm.public()["configured"]:
            raise UserError("AI 분석을 하려면 먼저 LLM을 연결해 주세요.")
        with self.lock:
            if self.job and self.job["status"] in ACTIVE:
                if params.get("append") is True and self.job["status"] == "running":
                    waiting = set(self.ids[self.job["processed"] + 1:])
                    added = [ident for ident in ids if ident not in waiting]
                    if len(self.ids) + len(added) <= MAX_DOCUMENTS:
                        self.ids.extend(added)
                        self.job["total"] = len(self.ids)
                        return self.status()
                raise BusyError("이미 문서를 AI로 분석하고 있습니다. 끝난 뒤 다시 실행해 주세요.")
            self.cancel_event = threading.Event()
            self.ids = ids
            self.job = {"id": str(uuid.uuid4()), "status": "running", "total": len(ids), "processed": 0,
                        "analyzed": 0, "skipped": 0, "failed": 0, "current": None, "results": [], "error": ""}
            self.thread = threading.Thread(target=self._run, args=(self.cancel_event,), daemon=True)
            self.thread.start()
            return self.status()

    def status(self, _=None):
        """The job and, while it runs, `queue`: the documents not analyzed yet, the one being analyzed first."""
        with self.lock:
            if not self.job:
                return None
            job = deepcopy(self.job)
            job["queue"] = self.ids[job["processed"]:] if job["status"] == "running" else []
            return job

    def cancel(self, _=None):
        """Stop after the document being analyzed: an LLM request already sent is not cut off."""
        with self.lock:
            if self.job and self.job["status"] in ACTIVE:
                self.cancel_event.set()
                self.job["status"] = "cancelling"
            return self.status()

    def dismiss(self, params):
        """The user cleared the finished job `id`: every screen leaves it out from now on."""
        with self.lock:
            if self.job and self.job["id"] == params.get("id") and self.job["status"] not in ACTIVE:
                self.job["dismissed"] = True
            return self.status()

    def _next(self, cancel):
        """The next document to analyze, or None once the run has ended: decided under the lock, so a document added at
        that moment (start with `append`) is analyzed rather than left out of a run that says it is done."""
        with self.lock:
            if not cancel.is_set() and self.job["processed"] < len(self.ids):
                return self.ids[self.job["processed"]]
            self.job.update(status="cancelled" if cancel.is_set() else "completed", error="", current=None)
            return None

    def _run(self, cancel):
        try:
            while (ident := self._next(cancel)) is not None:
                with self.service.db.connect() as db:
                    row = db.execute("SELECT title,status FROM documents WHERE id=? AND deleted_at IS NULL",
                                     (ident,)).fetchone()
                title = row["title"] if row else ""
                if row is None:
                    outcome, message = "skipped", "삭제된 문서입니다."
                elif row["status"] == "indexing":
                    outcome, message = "skipped", "다른 작업이 분석하고 있는 문서입니다."
                else:
                    with self.lock:
                        self.job["current"] = {"id": ident, "title": title}
                    try:
                        self.service.wiki_analyze({"id": ident})
                        outcome, message = "analyzed", ""
                    except Exception as error:
                        outcome, message = "failed", diagnostics.report(
                            error, "문서 분석에 실패했습니다. 다시 시도해 주세요.", "여러 문서 AI 분석")
                with self.lock:
                    self.job["processed"] += 1
                    self.job[outcome] += 1
                    self.job["current"] = None
                    # Analyzed documents show it in the list; the job keeps what needs the user's attention.
                    if outcome != "analyzed":
                        self.job["results"].append({"id": ident, "title": title, "status": outcome, "message": message})
        except Exception as failure:
            error = diagnostics.report(
                failure, "Wiki 문서를 읽지 못해 AI 분석을 멈췄습니다. 저장 폴더와 디스크 공간을 확인해 주세요.", "여러 문서 AI 분석")
            with self.lock:
                self.job.update(status="error", error=error, current=None)
