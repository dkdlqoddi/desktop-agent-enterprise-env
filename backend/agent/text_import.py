"""Text files as Wiki documents: one HTML page per .txt file, its text read through a hidden Notepad (notepad.py), so
a file arrives as the words a person sees in Notepad."""
import csv
import io
import sys
from html import escape

from .file_import import FileImporter
from .html_document import page
from .llm import UserError
from .notepad import notepad_path, read_with_notepad


def capabilities():
    if sys.platform != "win32":
        return {"available": False, "reason": "텍스트(.txt) 파일은 Windows에서 메모장으로 읽어 가져올 수 있습니다."}
    if not notepad_path().is_file():
        return {"available": False, "reason": f"메모장({notepad_path()})을 찾지 못했습니다."}
    return {"available": True, "reason": "창 없이 띄운 메모장으로 열어 메모장에 보이는 글을 HTML 문서로 저장합니다."}


def text_page(file, value):
    """The page of a .txt file (its text as Notepad shows it) or a .csv file (a table of that text), made without
    Excel. The bytes are never decoded here: Notepad has read them."""
    if not value.strip():
        raise UserError("빈 텍스트 파일입니다. 내용이 있는 문서라면 메모장에서 직접 열어 글이 보이는지 확인해 주세요.")
    if file.suffix.lower() == ".csv":
        rows = csv.reader(io.StringIO(value, newline=""))
        body = '<table class="grid">' + "".join(
            "<tr>" + "".join(f"<td>{escape(cell).replace(chr(10), '<br>')}</td>" for cell in row) + "</tr>"
            for row in rows) + "</table>"
    else:
        body = f"<pre>{escape(value)}</pre>"
    return page(file.stem, body, "text")


def notepad_page(file, cancel):
    return text_page(file, read_with_notepad(file, cancel))


class TextImporter(FileImporter):
    extensions = {".txt"}
    label = "텍스트"
    format_label = "텍스트(.txt)"
    source_table = "text_sources"
    uses_office = False

    def __init__(self, service, converter=None, capability_check=capabilities):
        super().__init__(service, converter or notepad_page, capability_check)
