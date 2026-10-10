"""RTF files as Wiki documents: one HTML page per file, read by a hidden Word (word_converter.py)."""

from .file_import import FileImporter
from .word_converter import WordConverter, capabilities


class RtfImporter(FileImporter):
    extensions = {".rtf"}
    label = "RTF 문서"
    format_label = ".rtf"
    source_table = "rtf_sources"
    uses_office = True

    def __init__(self, service, converter=None, capability_check=capabilities):
        super().__init__(service, converter or WordConverter(), capability_check)
