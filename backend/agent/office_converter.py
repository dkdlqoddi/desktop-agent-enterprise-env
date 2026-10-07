"""Read local Office documents in disposable, hidden Windows COM workers."""
import os
import sys
import tempfile
from pathlib import Path

from .folders import remove_tree
from .html_document import page
from .llm import UserError
from .office_html import excel_html, powerpoint_html, visio_html
from .office_pictures import Exports, scratch
from .office_session import LEFT_RUNNING, OfficeSession, run_worker
from .word_converter import WordConverter, convert_with_word

FORMATS = {".doc": "Word", ".docx": "Word", ".docm": "Word",
           ".xls": "Excel", ".xlsx": "Excel", ".xlsm": "Excel", ".xlsb": "Excel",
           ".ppt": "PowerPoint", ".pptx": "PowerPoint", ".pptm": "PowerPoint",
           ".vsd": "Visio", ".vsdx": "Visio", ".vsdm": "Visio"}
# Visio.InvisibleApp starts a Visio that never shows a window.
PROGIDS = {"Word": "Word.Application", "Excel": "Excel.Application",
           "PowerPoint": "PowerPoint.Application", "Visio": "Visio.InvisibleApp"}
# visOpenRO | visOpenDontList | visOpenMacrosDisabled | visOpenNoWorkspace
VISIO_OPEN = 0x2 | 0x8 | 0x80 | 0x100


def capabilities():
    apps = {}
    for name, progid in PROGIDS.items():
        available = False
        if sys.platform == "win32":
            try:
                import winreg
                import pythoncom  # noqa: F401
                import win32com.client  # noqa: F401
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\CLSID"):
                    available = True
            except (ImportError, OSError):
                pass
        apps[name] = available
    installed = [name for name, available in apps.items() if available]
    return {"available": bool(installed), "applications": apps,
            "reason": ("사용 가능: " + ", ".join(installed) + ". 해당 Office 앱으로 백그라운드에서 읽습니다.")
            if installed else "Microsoft Word·Excel·PowerPoint·Visio 데스크톱 앱이 설치된 Windows에서 사용할 수 있습니다."}


def same_file(first, second):
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def read_excel(path, tick, exports=None):
    with OfficeSession("Excel", PROGIDS["Excel"]) as office:
        excel = office.application
        office.hide()
        office.quiet(DisplayAlerts=False, ScreenUpdating=False)
        office.change(excel, EnableEvents=False, AskToUpdateLinks=False,
                      AutomationSecurity=3)  # msoAutomationSecurityForceDisable
        with office.opening():
            workbook = office.track(excel.Workbooks.Open(
                Filename=str(path), UpdateLinks=0, ReadOnly=True,
                Password="", WriteResPassword="", IgnoreReadOnlyRecommended=True,
                Notify=False, AddToMru=False,
            ), lambda book: book.Close(SaveChanges=False))
        with office.reading():
            return excel_html(workbook, tick, exports)


def read_powerpoint(path, tick, exports=None):
    in_use = "PowerPoint에 이 파일이 열려 있습니다. PowerPoint에서 파일을 닫은 뒤 다시 가져와 주세요."
    with OfficeSession("PowerPoint", PROGIDS["PowerPoint"]) as office:
        powerpoint = office.application
        with office.starting():
            # PowerPoint runs one process per user, so DispatchEx returns the PowerPoint the user runs.
            # That one is only borrowed: it keeps its alerts and stays open. Until PowerPoint tells, it may be the
            # user's, so a failure here never quits it; one started here is ended from outside then (Watch).
            office.owned = False
            try:
                office.owned = not (powerpoint.Visible or powerpoint.Presentations.Count)
            except Exception:
                LEFT_RUNNING.append("PowerPoint")
                raise
        if office.owned:
            office.quiet(DisplayAlerts=1)  # ppAlertsNone
        office.change(powerpoint, AutomationSecurity=3)
        with office.opening():
            presentations = powerpoint.Presentations
            if any(same_file(presentations.Item(i).FullName, path) for i in range(1, presentations.Count + 1)):
                raise UserError(in_use)
            presentation = presentations.Open(FileName=str(path), ReadOnly=True, Untitled=False, WithWindow=False)
            # Opened without a window, so a presentation with windows is one the user already had open.
            if presentation.Windows.Count:
                raise UserError(in_use)
            office.track(presentation, lambda item: item.Close())
        with office.reading():
            return powerpoint_html(presentation, tick, exports)


def close_drawing(drawing):
    drawing.Saved = True  # nothing to save; no prompt even if Visio marked it changed while opening
    drawing.Close()


def read_visio(path, tick, exports=None):
    with OfficeSession("Visio", PROGIDS["Visio"]) as office:
        visio = office.application
        # Answer any alert with "No" instead of showing it, and run no document event code.
        office.quiet(AlertResponse=7, ScreenUpdating=False)
        office.change(visio, EventsEnabled=False)
        with office.opening():
            drawing = office.track(visio.Documents.OpenEx(str(path), VISIO_OPEN), close_drawing)
        with office.reading():
            return visio_html(drawing, tick, exports)


READERS = {"Excel": read_excel, "PowerPoint": read_powerpoint, "Visio": read_visio}


def convert_with_office(source, stop=None, folder=None):
    """The HTML page of an Office file. Runs only in the disposable STA worker. Pictures PowerPoint, Excel
    and Visio export go through `folder` (a new scratch folder without one) and are deleted once read."""
    path = Path(source).resolve()
    name = FORMATS.get(path.suffix.lower())
    if not name:
        raise UserError("지원하지 않는 Office 파일 형식입니다.")
    if name == "Word":
        return convert_with_word(path)

    def tick():
        if stop is not None and stop.is_set():
            raise UserError("가져오기를 취소했습니다.")

    with scratch(folder) as place:
        body = READERS[name](path, tick, Exports(place))
    if not body.strip():
        raise UserError(f"{name} 파일에서 표시할 내용을 찾지 못했습니다.")
    # The application has closed the file and quit; the page is assembled in Python.
    return page(path.stem, body, name.lower())


def _worker(source, sender, stop, folder=None):
    run_worker(lambda: convert_with_office(source, stop, folder), sender, "Office 변환",
               "해당 앱에서 파일을 열 수 있는지, 암호·DRM·보안 정책을 확인해 주세요.")


class OfficeConverter(WordConverter):
    worker = staticmethod(_worker)
    application_label = "Office"

    # Formatting, comments and charts take more calls than values alone, and pictures of slides, pages,
    # charts and shapes take more time still, so a large deck gets more time.
    def __init__(self, timeout=300, grace=10):
        super().__init__(timeout, grace)

    def __call__(self, source, cancel):
        # The Office application writes pictures into this folder; it goes even if the worker is killed.
        folder = tempfile.mkdtemp(prefix="private-agent-office-")
        try:
            return super().__call__(source, cancel, folder)
        finally:
            remove_tree(folder)

    def applications(self, source):
        name = FORMATS.get(Path(source).suffix.lower())
        return (name,) if name else ()

    def check(self, source):
        name = FORMATS.get(Path(source).suffix.lower())
        if not name:
            raise UserError("지원하지 않는 Office 파일 형식입니다.")
        if not capabilities()["applications"][name]:
            raise UserError(f"{name} 데스크톱 앱이 설치된 Windows에서 이 파일을 가져올 수 있습니다.")
