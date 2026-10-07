"""Background Office import jobs that persist Markdown directly to the Wiki."""
from .file_import import FileImporter
from .office_converter import FORMATS, OfficeConverter, capabilities

# PowerPoint runs one process per user, so a presentation may be read in the PowerPoint the user has open.
POWERPOINT_NOTICE = ("PowerPoint 파일을 읽습니다. 열려 있는 PowerPoint가 있으면 그 안에서 창 없이 열어 읽고 "
                     "그 파일만 닫습니다. 가져올 파일을 편집 중이면 먼저 닫고, PowerPoint에 열린 대화 상자가 "
                     "있으면 닫아 주세요.")


class OfficeImporter(FileImporter):
    extensions = set(FORMATS)
    label = "Office 문서"
    format_label = "Office(Word·Excel·PowerPoint·Visio)"
    source_table = "office_sources"
    uses_office = True

    def __init__(self, service, converter=None, capability_check=capabilities):
        super().__init__(service, converter or OfficeConverter(), capability_check)

    def notice(self, file):
        return POWERPOINT_NOTICE if FORMATS[file.suffix.lower()] == "PowerPoint" else ""

    def details(self, file):
        return {"application": FORMATS[file.suffix.lower()]}
