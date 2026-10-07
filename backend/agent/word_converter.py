"""Hidden Windows Word in a disposable COM worker. DispatchEx starts a Word of its own for every file."""
import multiprocessing
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import diagnostics
from .html_document import validate_html
from .llm import UserError
from .office_processes import Watch
from .office_session import OfficeSession, run_worker, step
from .word_html import word_xml_to_html

WAITING = "다른 가져오기·원본 반영의 {name} 작업이 끝나기를 기다리는 중입니다."
# How long a worker may take to answer once the password prompt of its file was cancelled: it fails at once then.
PROMPT_SECONDS = 15
PASSWORD = ("암호가 걸린 {name} 파일이라 열지 않았습니다. {name}이(가) 띄운 암호 입력 창은 앱이 취소했습니다. 암호를 "
            "푼 파일을 가져와 주세요.")


def capabilities():
    if sys.platform != "win32":
        return {"available": False, "reason": ".rtf 변환은 Microsoft Word가 설치된 Windows에서 사용할 수 있습니다."}
    try:
        import winreg
        import pythoncom  # noqa: F401 — verify that the packaged runtime includes pywin32
        import win32com.client  # noqa: F401
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CLSID"):
            pass
    except (ImportError, OSError):
        return {"available": False, "reason": "Word COM을 찾을 수 없습니다. Microsoft Word 데스크톱 앱 설치를 확인해 주세요."}
    return {"available": True, "reason": "Microsoft Word를 백그라운드로 실행해 변환합니다."}


def close_document(document):
    document.Close(SaveChanges=0)  # wdDoNotSaveChanges


def read_word_xml(source, *, dispatch=None):
    """The document body as Flat OPC XML, handed over by COM in memory. Nothing is saved or exported."""
    with OfficeSession("Word", "Word.Application", quit_args={"SaveChanges": 0}, dispatch=dispatch) as office:
        word = office.application
        office.hide()
        office.quiet(DisplayAlerts=0)  # wdAlertsNone
        office.change(word, AutomationSecurity=3)  # msoAutomationSecurityForceDisable
        office.change(word.Options, UpdateLinksAtOpen=False)
        with office.opening():
            document = office.track(word.Documents.Open(
                FileName=str(Path(source).resolve()), ReadOnly=True, AddToRecentFiles=False,
                ConfirmConversions=False, Visible=False, PasswordDocument="",
                OpenAndRepair=False, NoEncodingDialog=True,
            ), close_document)
        with office.reading():
            xml = document.Content.WordOpenXML
            if not isinstance(xml, str) or not xml.strip():
                raise UserError("Word 내용 읽기 실패: Word가 본문 XML을 돌려주지 않았습니다.")
            return xml


def convert_with_word(source, *, dispatch=None):
    """The HTML page of a Word file or .rtf documents. Runs only in the disposable STA worker.
    `dispatch` is injectable for contract tests."""
    xml = read_word_xml(source, dispatch=dispatch)
    # Word has closed the file and quit; only Python reads the XML from here.
    with step("Word 본문 변환"):
        return word_xml_to_html(xml, Path(source).stem)


def _worker(source, sender, stop):
    run_worker(lambda: convert_with_word(source), sender, "Word 변환",
               "Word에서 파일을 직접 열 수 있는지, 문서 암호·DRM·보안 정책을 확인해 주세요.")


def end_left_office(watch):
    """A worker that ended without an answer (stopped after the grace, killed or crashed), or that said it could not
    close its file or quit its Office app, left that hidden app running: it is ended from outside. A failure here never
    hides the import's own outcome."""
    try:
        ended = watch.end_leftovers()
    except Exception as error:
        diagnostics.report(error, "", "숨은 Office 정리")
        return
    for found in ended:
        diagnostics.LOG.info("ended the hidden %s (pid %d) an import or write-back worker left: %s",
                             found.name, found.pid, found.command_line)


class Turns:
    """One worker per Office application at a time, across every import and write-back, in the order they asked.

    What a worker leaves running is told by what started while it ran (office_processes.Watch), and a PowerPoint reader
    quits the PowerPoint it started, which serves every client: a second worker of the same application beside it would
    have its Office ended or quit under it. A worker whose turn has not come waits rather than fails, and says so."""

    def __init__(self):
        self.condition = threading.Condition()
        self.queues = {}  # application: the tickets of the workers that asked, the first one's worker running

    @contextmanager
    def turn(self, names, cancel, waiting=None, cancelled="가져오기를 취소했습니다."):
        """Hold the applications `names` while the block runs. Until their turn comes `waiting(text)` is told what the
        worker waits for (and "" once it may run); `cancel` gives up waiting with UserError(`cancelled`)."""
        names, ticket, said = sorted(set(names)), object(), False
        with self.condition:
            for name in names:
                self.queues.setdefault(name, []).append(ticket)
        try:
            while True:
                with self.condition:
                    busy = next((name for name in names if self.queues[name][0] is not ticket), None)
                    if busy is not None and said and not cancel.is_set():
                        self.condition.wait(0.2)
                        continue
                if busy is None:
                    break
                if cancel.is_set():
                    raise UserError(cancelled)
                said = True
                if waiting is not None:
                    waiting(WAITING.format(name=busy))
            if said and waiting is not None:
                waiting("")
            yield
        finally:
            with self.condition:
                for name in names:
                    self.queues[name].remove(ticket)
                    if not self.queues[name]:
                        del self.queues[name]
                self.condition.notify_all()


TURNS = Turns()


class WordConverter:
    worker = staticmethod(_worker)
    application_label = "Word"
    stage = "변환"  # what the worker does, in messages
    cancelled = "가져오기를 취소했습니다."
    # What a job shows while its worker waits for its turn (Turns): an importer sets it to update its job.
    waiting = None

    def __init__(self, timeout=90, grace=10):
        self.timeout, self.grace = timeout, grace

    def answer(self, result):
        """What the caller gets of the worker's result: the page it read."""
        return validate_html(result.get("html"))

    def cancellable(self, result):
        """Whether a result that arrives after the request was cancelled is dropped: a page read is."""
        return True

    def stopped(self):
        """What a cancelled request says when the worker gave no answer within the grace."""
        return self.cancelled

    def check(self, source):
        capability = capabilities()
        if not capability["available"]:
            raise UserError(capability["reason"])

    def applications(self, source):
        """The Office applications the worker for `source` starts."""
        return ("Word",)

    def password(self, name):
        """What the user is told when `name` asked for a password the app cancelled."""
        return PASSWORD.format(name=name)

    def __call__(self, source, cancel, *extra):
        """The page of `source`, converted in a worker; `extra` arguments go to the worker. A worker of the same Office
        application for another job runs first (Turns)."""
        self.check(source)
        with TURNS.turn(self.applications(source), cancel, self.waiting, self.cancelled):
            return self._run(source, cancel, *extra)

    def _run(self, source, cancel, *extra):
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        stop = context.Event()
        process = context.Process(target=self.worker, args=(str(source), sender, stop, *extra), daemon=True)
        # The Office processes before the worker starts, to tell which one it may leave behind.
        watch = Watch(self.applications(source))
        answered = left = False
        try:
            process.start()
            sender.close()
            deadline = time.monotonic() + self.timeout
            label = f"{self.application_label} {self.stage}"
            cancelled = refused = False
            look = time.monotonic()
            while True:
                if cancel.is_set() and not cancelled:
                    if self.cancellable({"ok": True}):
                        raise UserError(self.cancelled)
                    # A worker that may be saving is asked to stop and heard out: it stops before it saves, or says
                    # that it has saved.
                    cancelled = True
                    stop.set()
                    deadline = min(deadline, time.monotonic() + self.grace)
                if watch.prompting and time.monotonic() >= look:
                    # PowerPoint asks a password where Word and Excel fail at once: its prompt is cancelled, so the
                    # worker fails now instead of when the time is up (unverified on Windows).
                    look = time.monotonic() + 1
                    if watch.refuse_prompts() and not refused:
                        refused = True
                        deadline = min(deadline, time.monotonic() + PROMPT_SECONDS)
                if time.monotonic() >= deadline:
                    if refused:
                        raise UserError(self.password("PowerPoint"))
                    if cancelled:
                        raise UserError(self.stopped())
                    raise UserError(f"{label}이 {self.timeout}초를 초과했습니다. 파일의 열기 대화상자나 보안 정책을 확인해 주세요.")
                # Drain the pipe while the worker is alive: a full pipe blocks sender.send(). On Windows, polling a
                # pipe whose worker has ended without a word raises BrokenPipeError.
                try:
                    ready = receiver.poll(0.1)
                except OSError:
                    raise UserError(f"{label} 프로세스가 예기치 않게 종료되었습니다.") from None
                if ready:
                    try:
                        result = receiver.recv()
                    except (EOFError, OSError):
                        raise UserError(f"{label} 결과를 받지 못했습니다.") from None
                    answered, left = True, bool(result.get("left"))
                    if cancel.is_set() and self.cancellable(result):
                        raise UserError(self.cancelled)
                    if not result.get("ok"):
                        # The traceback of the worker's own failure goes to the log either way.
                        failed = diagnostics.worker_failure(result["error"], result.get("trace"), label)
                        raise UserError(self.password("PowerPoint") if refused else failed)
                    return self.answer(result)
                if not process.is_alive():
                    raise UserError(f"{label} 프로세스가 예기치 않게 종료되었습니다.")
        finally:
            started = process.pid is not None
            if started:
                # A stopped worker closes its file and quits its own Office app between sheets, slides
                # and pages. One stuck in an Office dialog is killed; the hidden app it leaves is ended
                # afterwards, from outside (end_left_office).
                stop.set()
                deadline = time.monotonic() + self.grace
                while process.is_alive() and time.monotonic() < deadline:
                    try:
                        # A worker that finished anyway, its Office app closed, is sending its page: read it,
                        # so a page larger than the pipe holds does not keep the worker waiting out the grace.
                        if receiver.poll(0.1):
                            late = receiver.recv()
                            answered = True
                            left = left or isinstance(late, dict) and bool(late.get("left"))
                    except (EOFError, OSError):
                        process.join(max(0.0, deadline - time.monotonic()))
                if process.is_alive():
                    process.terminate()
                    process.join(5)
                if process.is_alive():
                    process.kill()
                    process.join(5)
                try:
                    process.close()
                except ValueError:
                    pass  # still running after the kill, so it cannot be closed: the outcome stands all the same
            receiver.close()
            sender.close()
            # Once the worker has ended: the hidden Office it could not close or quit, if it said so or said nothing.
            if started and (left or not answered):
                end_left_office(watch)
