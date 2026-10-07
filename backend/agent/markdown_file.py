"""Markdown (.md, .markdown) files as Wiki documents: the text of a UTF-8 file as it is. The import box hands the path
over through Electron (files.import_text); the file is kept as the document's source like any other import, so an
import of the same path again updates the document. A text longer than one document holds is saved in parts."""
import hashlib
from pathlib import Path

from . import sources
from .llm import UserError
from .markdown_import import MAX_SOURCE_BYTES
from .parts import markdown_parts
from .service import now, text

EXTENSIONS = (".md", ".markdown")


def import_file(service, params):
    file = Path(text(params.get("path"), "가져올 Markdown 파일 경로", 4096))
    target, replace = sources.options(params)
    if file.suffix.lower() not in EXTENSIONS:
        raise UserError(".md, .markdown 파일만 Markdown으로 가져올 수 있습니다.")
    if not file.is_absolute():
        raise UserError("파일의 전체 경로를 입력해 주세요.")
    try:
        if not file.is_file():
            raise UserError("파일 경로를 입력해 주세요." if file.exists() else "파일을 찾을 수 없습니다. 경로를 확인해 주세요.")
        if file.stat().st_size > MAX_SOURCE_BYTES:
            raise UserError("Markdown 파일이 10MB를 초과해 가져오지 않았습니다.")
        data = file.read_bytes()
    except OSError:
        raise UserError("파일을 읽지 못했습니다. 경로와 권한을 확인해 주세요.") from None
    try:
        # A byte order mark is not part of the text, as editors that write one read it. Line breaks as Windows editors
        # write them (\r\n) become the Wiki's \n, so the editor and the comparisons with the source see the same text.
        content = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n").strip()
    except UnicodeDecodeError:
        raise UserError("UTF-8 텍스트 파일만 가져올 수 있습니다.") from None
    if not content:
        raise UserError("빈 파일은 가져올 수 없습니다.")
    path, title = str(file.resolve()), file.stem.strip()[:200] or "가져온 문서"
    checksum = hashlib.sha256(data).hexdigest()
    split = markdown_parts(content)
    timestamp = now()
    with service.db.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # The file is told apart by its bytes, as the document window checks it; one whose text stayed (only its line
        # breaks or byte order mark changed) keeps its document and analysis and records its new bytes.
        found = sources.plan(db, "markdown_sources", path, {"source_hash": checksum}, target, replace, split)
        if found.action == "skip":
            result = sources.skipped(found, "이미 가져온 동일한 Markdown 파일입니다.")
        else:
            ids, removed = sources.save(db, "markdown_sources", found, split, title, timestamp,
                                        {"source_path": path, "source_hash": checksum})
            result = sources.outcome(found, split, ids, removed, "Markdown 문서를 Wiki에 저장했습니다.", "Markdown")
        result["title"] = db.execute("SELECT title FROM documents WHERE id=?", (result["doc_id"],)).fetchone()[0]
    return {**result, "path": path}
