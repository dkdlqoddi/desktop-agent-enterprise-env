"""One hidden Office application for one conversion, used only through its object model.

DispatchEx starts the application. Settings that could show a dialog, run a macro or update a link are
changed with their previous values remembered, the file opens read-only without a window, and its
content is read into memory. However the conversion ends, the file closes without saving, the settings
go back and the application quits. An application someone uses keeps running with every setting back:
the PowerPoint the user runs, which serves every client, or one the user opened a document in."""
import gc
import time
import traceback
from contextlib import contextmanager
from types import MethodType

from .llm import UserError

# RPC_E_CALL_REJECTED, RPC_E_SERVERCALL_RETRYLATER: Office is still starting or has a dialog open.
# A rejected call never ran, so it is safe to repeat. pywin32 cannot register an IMessageFilter.
BUSY = (-2147418111, -2147417846)
PATIENCE = 30
CLEANUP_PATIENCE = 5
# The Office applications this worker started that may still run with its file open: closing or quitting failed (a
# dialog nobody sees rejects every call), or the start failed after Office may have begun (CO_E_SERVER_EXEC_FAILURE).
# The answer says so, and the backend ends such a hidden application from outside once the worker has ended, as after
# a worker that gave no answer (word_converter.end_left_office).
LEFT_RUNNING = []


def failure(stage, error, hint=""):
    """`Word 파일 열기 실패 [0x800A1066]: Office's own explanation. hint` for the job result."""
    info = getattr(error, "excepinfo", None) or ()
    text = ((info[2] if len(info) > 2 else None) or getattr(error, "strerror", None)
            or (f"{type(error).__name__}: {error}" if str(error) else type(error).__name__))
    code = (info[5] if len(info) > 5 else None) or getattr(error, "hresult", None) or getattr(error, "winerror", None)
    text = " ".join(str(text).split())[:300]
    if not text.endswith((".", "!", "?")):
        text += "."
    if getattr(error, "hresult", None) in BUSY:
        hint = "Office 앱에 열린 대화 상자가 있다면 닫고 다시 시도해 주세요."
    shown = f" [0x{code & 0xFFFFFFFF:08X}]" if isinstance(code, int) else ""
    return UserError(f"{stage} 실패{shown}: {text}" + (f" {hint}" if hint else ""))


@contextmanager
def step(stage, hint=""):
    """Say which step failed, instead of one message for every Office failure."""
    try:
        yield
    except UserError:
        raise
    except Exception as error:
        raise failure(stage, error, hint) from None


def run_worker(convert, sender, stage, hint, key="html"):
    """In a disposable worker, send what `convert()` returns (the HTML page, or under another `key` what a writer did)
    or why it failed.

    COM is initialized for the conversion and uninitialized after it, once no Office object is left."""
    import pythoncom
    pythoncom.CoInitializeEx(pythoncom.COINIT_APARTMENTTHREADED)
    try:
        result = outcome(convert, stage, hint, key)
        # A failure's traceback may still hold Office objects in reference cycles.
        gc.collect()
        sender.send(result)
    finally:
        sender.close()
        pythoncom.CoUninitialize()


def outcome(convert, stage, hint, key="html"):
    """What the worker answers; "left" names the Office applications it may have left running (LEFT_RUNNING)."""
    LEFT_RUNNING.clear()
    try:
        result = {"ok": True, key: convert()}
    except UserError as error:
        result = {"ok": False, "error": str(error), "trace": trace(error.__context__)}
    except Exception as error:
        result = {"ok": False, "error": str(failure(stage, error, hint)), "trace": trace(error)}
    if LEFT_RUNNING:
        result["left"] = sorted(set(LEFT_RUNNING))
    return result


def trace(error):
    """The traceback of an unexpected failure, as text for the app's log: one that step() turned into a message is the
    context of that UserError. A UserError raised on purpose has none."""
    if error is None or isinstance(error, UserError):
        return ""
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))[-20_000:]


class Patient:
    """A COM object whose calls wait while its Office application reports that it is busy."""
    __slots__ = ("_target", "_session")

    def __init__(self, target, session):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_session", session)

    def __getattr__(self, name):
        return self._session.call(getattr, self._target, name)

    def __setattr__(self, name, value):
        self._session.call(setattr, self._target, name, value)

    def __call__(self, *args, **kwargs):
        return self._session.call(self._target, *args, **kwargs)


DISPATCH_PROPERTYGET = 2


def property_with(target, name, *args):
    """A property read with arguments, such as Excel's Range.Value(11): late-bound pywin32 reads properties
    only without them, so this goes through IDispatch::Invoke, waiting while Office is busy."""
    if isinstance(target, Patient):
        session, target = object.__getattribute__(target, "_session"), object.__getattribute__(target, "_target")
    else:
        session = None
    dispatch = target._oleobj_
    if session is None:
        return dispatch.Invoke(dispatch.GetIDsOfNames(name), 0, DISPATCH_PROPERTYGET, True, *args)
    # Asking for the name is a call to Office as well, which a busy Office rejects as any other.
    ident = session.call(dispatch.GetIDsOfNames, name)
    return session.call(dispatch.Invoke, ident, 0, DISPATCH_PROPERTYGET, True, *args)


class OfficeSession:
    """Runs only inside `run_worker`, which owns the COM apartment. `dispatch` is injectable for contract tests."""

    def __init__(self, name, progid, *, quit_args=None, dispatch=None, running=None):
        self.name, self.progid = name, progid
        self.quit_args, self.dispatch = quit_args or {}, dispatch
        self.running = running
        self.application = None
        # False for the PowerPoint the user runs: it is borrowed, and only the imported file closes.
        self.owned = running is None
        self.patience = PATIENCE
        self.saved, self.documents = [], []

    def __enter__(self):
        if self.running is not None:
            self.application = Patient(self.running, self)
            return self
        from win32com.client import DispatchEx
        try:
            with self.starting():
                self.application = Patient((self.dispatch or DispatchEx)(self.progid), self)
        except Exception:
            LEFT_RUNNING.append(self.name)  # it may have started without answering
            raise
        return self

    def call(self, function, *args, **kwargs):
        deadline = time.monotonic() + self.patience
        delay = 0.05
        while True:
            try:
                result = function(*args, **kwargs)
                break
            except Exception as error:
                if getattr(error, "hresult", None) not in BUSY or time.monotonic() + delay > deadline:
                    raise
            time.sleep(delay)
            delay = min(delay * 2, 1)
        return Patient(result, self) if hasattr(result, "_oleobj_") or isinstance(result, MethodType) else result

    def starting(self):
        return step(f"{self.name} 실행", f"{self.name} 데스크톱 앱 설치 상태를 확인해 주세요.")

    def opening(self):
        return step(f"{self.name} 파일 열기", f"{self.name}에서 직접 열리는지, 암호·DRM·보안 정책을 확인해 주세요.")

    def reading(self):
        return step(f"{self.name} 내용 읽기")

    def writing(self):
        return step(f"{self.name} 내용 고치기")

    def saving(self):
        return step(f"{self.name} 파일 저장", "파일이 읽기 전용이거나 다른 프로그램이 쓰고 있는지, 저장 공간을 확인해 주세요.")

    def hide(self):
        """Keep the application without a window, as DispatchEx started it."""
        with step(f"{self.name} 설정"):
            self.application.Visible = False

    def quiet(self, **settings):
        """Turn off alerts and screen updates until the application quits, so quitting shows no dialog."""
        self._change(self.application, settings, quiet=True)

    def change(self, target, **settings):
        """Change settings of the application or its Options; they go back before it quits, because Office
        saves some of them (Word's UpdateLinksAtOpen, Excel's AskToUpdateLinks) as the user's preferences."""
        self._change(target, settings, quiet=False)

    def _change(self, target, settings, quiet):
        with step(f"{self.name} 설정"):
            for name, value in settings.items():
                self.saved.append((target, name, getattr(target, name), quiet))
                setattr(target, name, value)

    def track(self, document, close):
        """Close `document` with `close(document)` however the conversion ends."""
        self.documents.append((document, close))
        return document

    def in_use(self):
        """A document the user opened in the hidden application meanwhile shows it."""
        try:
            return bool(self.application.Visible)
        except Exception:
            return False

    def __exit__(self, *_):
        self.patience = CLEANUP_PATIENCE
        left = False
        while self.documents:
            document, close = self.documents.pop()
            try:
                close(document)
            except Exception:
                left = True
        if self.application is not None:
            quitting = self.owned and not self.in_use()
            for target, name, value, quiet in reversed(self.saved):
                if quitting and quiet:
                    continue
                try:
                    setattr(target, name, value)
                except Exception:
                    pass
            if quitting:
                try:
                    self.application.Quit(**self.quit_args)
                except Exception:
                    left = True
        if left and self.running is None:
            # The file may still be open in an Office this worker started; one already running is never ended.
            LEFT_RUNNING.append(self.name)
        self.saved.clear()
        self.application = None
        return False
