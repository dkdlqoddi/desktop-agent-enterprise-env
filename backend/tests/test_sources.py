"""Imports keep where each document came from: another import of the same file or link updates its documents in
place instead of saving new ones, an unchanged source changes nothing, and what the user wrote stays unless the update
is asked to replace it."""
import os
import sqlite3
from html import escape

import pytest

from agent.confluence import import_page
from agent.db import Database
from agent.html_document import page
from agent.llm import UserError
from agent.rtf_import import RtfImporter
from agent.service import Service
from test_rtf_import import FakeLLM, wait

URL = "https://wiki.test/confluence/pages/viewpage.action?pageId=123"


def convert(file, cancel):
    """The page of an RTF file whose bytes are its text; "long:<n>" makes n long paragraphs (150 fill three
    documents)."""
    words = file.read_text(encoding="utf-8")
    if words.startswith("long:"):
        body = "".join(f"<p>{index}번째 문단 {'내용 ' * 400}</p>" for index in range(int(words[5:])))
    else:
        body = f"<p>{escape(words)}</p>"
    return page(file.stem, body, "word")


@pytest.fixture
def importer(tmp_path):
    service = Service(Database(tmp_path / "data" / "wiki.db"), FakeLLM())
    return RtfImporter(service, converter=convert, capability_check=lambda: {"available": True})


def run(importer, file, **options):
    importer.start({"path": str(file), "mode": "file", **options})
    job = wait(importer)
    assert job["status"] == "completed", job
    return job["results"][0]


def capture(body, title="Confluence 테스트"):
    return {"source_url": URL, "page_id": "123",
            "html": f"<html><head><title>{title}</title></head><body>{body}</body></html>"}


def test_an_import_of_the_same_file_again_updates_its_document_in_place(importer, tmp_path):
    service = importer.service
    rtf = tmp_path / "주간 보고.rtf"
    rtf.write_text("첫 번째 보고", encoding="utf-8")
    first = run(importer, rtf)
    ident = first["doc_id"]
    doc = service.wiki_get({"id": ident})
    with service.db.connect() as db:
        db.execute("UPDATE documents SET summary='요약',status='ready',index_mode='wiki' WHERE id=?", (ident,))
        db.execute("INSERT INTO keywords VALUES (?, '보고')", (ident,))
    task = service.dispatch("tasks.save", {"title": "보고 확인", "source_id": ident})
    # The same bytes again change nothing.
    assert run(importer, rtf) == {"path": str(rtf), "status": "skipped", "doc_id": ident,
                                   "message": "이미 가져온 동일한 RTF 문서입니다.", "reason": "same"}
    rtf.write_text("두 번째 보고", encoding="utf-8")
    importer.start({"path": str(rtf), "mode": "file"})
    job = wait(importer)
    assert (job["imported"], job["updated"], job["skipped"]) == (0, 1, 0)
    assert job["results"][0] == {"path": str(rtf), "status": "updated", "doc_id": ident, "doc_ids": [ident],
                                 "message": "원본이 바뀌어 문서를 새 내용으로 업데이트했습니다."}
    updated = service.wiki_get({"id": ident})
    assert "두 번째 보고" in updated["html"] and updated["content"] == "두 번째 보고"
    assert (updated["revision"], updated["created_at"], updated["title"]) == (2, doc["created_at"], "주간 보고")
    assert updated["updated_at"] > doc["updated_at"] and updated["source"]["imported_at"] > doc["source"]["imported_at"]
    # The analysis was of the old text; the task still points at the document.
    assert (updated["status"], updated["summary"], updated["index_mode"], updated["keywords"]) == ("pending", "", "", [])
    assert service.wiki_source_markdown({"id": ident})["markdown"] == "두 번째 보고"
    assert [doc["id"] for doc in service.wiki_list({})] == [ident]
    assert [(item["id"], item["source_id"]) for item in service.dispatch("tasks.list", {})] == [(task["id"], ident)]


def test_what_the_user_wrote_stays_unless_the_update_replaces_it(importer, tmp_path):
    service = importer.service
    rtf = tmp_path / "회의.rtf"
    rtf.write_text("원래 안건", encoding="utf-8")
    ident = run(importer, rtf)["doc_id"]
    # A title the user gave stays when the text follows the file.
    service.wiki_save({"id": ident, "title": "9월 회의", "content": "원래 안건"})
    rtf.write_text("바뀐 안건", encoding="utf-8")
    assert run(importer, rtf)["status"] == "updated"
    doc = service.wiki_get({"id": ident})
    assert (doc["title"], doc["content"], doc["source"]["edited"]) == ("9월 회의", "바뀐 안건", False)
    # Text the user changed is not replaced by an import of the changed file.
    service.wiki_save({"id": ident, "title": "9월 회의", "content": "내가 정리한 안건"})
    assert service.wiki_get({"id": ident})["source"]["edited"] is True
    rtf.write_text("다시 바뀐 안건", encoding="utf-8")
    skipped = run(importer, rtf)
    assert (skipped["status"], skipped["doc_id"], skipped["reason"]) == ("skipped", ident, "edited")
    assert skipped["message"].startswith("가져온 뒤 본문을 고친 문서라 원본의 내용으로 바꾸지 않았습니다.")
    assert service.wiki_get({"id": ident})["content"] == "내가 정리한 안건"
    # Asked to, the update replaces it; the page the import made shows again.
    replaced = run(importer, rtf, doc_id=ident, replace=True)
    assert (replaced["status"], replaced["message"]) == ("updated", "원본이 바뀌어 문서를 새 내용으로 업데이트했습니다.")
    doc = service.wiki_get({"id": ident})
    assert (doc["format"], doc["content"], doc["source"]["edited"]) == ("html", "다시 바뀐 안건", False)
    # Text changed while the file stayed the same: replacing it brings the file's text back.
    service.wiki_save({"id": ident, "title": "9월 회의", "content": "또 고친 글"})
    assert run(importer, rtf)["status"] == "skipped"
    restored = run(importer, rtf, doc_id=ident, replace=True)
    assert (restored["status"], restored["message"]) == ("updated", "고친 본문 대신 원본의 내용으로 되돌렸습니다.")
    assert service.wiki_get({"id": ident})["content"] == "다시 바뀐 안건"
    # Replacing a document that is as it was imported changes nothing.
    assert run(importer, rtf, doc_id=ident, replace=True)["status"] == "skipped"
    assert service.wiki_get({"id": ident})["revision"] == 6
    for options in ({"replace": "yes"}, {"doc_id": 7}, {"doc_id": ""}):
        with pytest.raises(UserError, match="doc_id|replace"):
            importer.start({"path": str(rtf), "mode": "file", **options})


def test_an_update_adds_or_deletes_documents_as_the_text_grows_or_shrinks(importer, tmp_path, monkeypatch):
    service = importer.service
    rtf = tmp_path / "긴 문서.rtf"
    rtf.write_text("짧은 문서", encoding="utf-8")
    ident = run(importer, rtf)["doc_id"]
    rtf.write_text("long:150", encoding="utf-8")
    analyzed = []
    monkeypatch.setattr(service, "wiki_analyze", lambda params: analyzed.append(params["id"]))
    grown = run(importer, rtf, analyze=True)
    assert grown["status"] == "updated" and len(grown["doc_ids"]) == 3 and grown["doc_ids"][0] == ident
    assert grown["message"] == ("원본이 바뀌어 문서를 새 내용으로 업데이트했습니다. "
                                "글이 80,000자를 넘어 HTML 문서 3개로 나누어 Wiki에 저장했습니다.")
    assert analyzed == grown["doc_ids"]
    docs = [service.wiki_get({"id": doc_id}) for doc_id in grown["doc_ids"]]
    assert [doc["title"] for doc in docs] == ["긴 문서 (1/3)", "긴 문서 (2/3)", "긴 문서 (3/3)"]
    assert all([part["id"] for part in doc["parts"]] == grown["doc_ids"] for doc in docs)
    rtf.write_text("long:100", encoding="utf-8")
    shrunk = run(importer, rtf)
    assert shrunk["doc_ids"] == grown["doc_ids"][:2]
    assert shrunk["message"].endswith("HTML 문서 2개로 나누어 Wiki에 저장했습니다. 글이 줄어 남는 부분 문서 1개는 지웠습니다.")
    assert {doc["id"] for doc in service.wiki_list({})} == set(shrunk["doc_ids"])
    assert service.wiki_get({"id": ident})["title"] == "긴 문서 (1/2)"
    rtf.write_text("다시 짧은 문서", encoding="utf-8")
    assert run(importer, rtf)["doc_ids"] == [ident]
    doc = service.wiki_get({"id": ident})
    assert (doc["title"], doc["content"], doc["parts"], doc["source"]["parts"]) == ("긴 문서", "다시 짧은 문서", [], 1)
    assert [doc["id"] for doc in service.wiki_list({})] == [ident]


def test_an_update_follows_the_document_it_was_asked_for_and_otherwise_the_latest_import(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), None)
    first = import_page(service, capture("<p>첫 판</p>"))["doc_id"]
    # Before imports updated their documents, a changed page became a document of its own.
    with service.db.connect() as db:
        db.execute("INSERT INTO documents(id,title,content,created_at,updated_at) VALUES "
                   "('older-copy','Confluence 테스트','둘째 판','2026-09-02','2026-09-02')")
        db.execute("INSERT INTO confluence_sources(doc_id,source_url,page_id,source_hash,markdown,imported_at) "
                   "VALUES ('older-copy',?,'123','hash-2','둘째 판','2999-01-01')", (URL,))
    assert import_page(service, capture("<p>셋째 판</p>"))["doc_ids"] == ["older-copy"]
    assert import_page(service, {**capture("<p>넷째 판</p>"), "doc_id": first})["doc_ids"] == [first]
    assert service.wiki_get({"id": first})["content"] == "넷째 판"
    assert service.wiki_get({"id": "older-copy"})["content"] == "셋째 판"
    # A document of another source is no target: the page's latest import is updated, the one updated last.
    other = service.wiki_save({"title": "메모", "content": "직접 쓴 글"})["id"]
    assert import_page(service, {**capture("<p>다섯째 판</p>"), "doc_id": other})["doc_ids"] == [first]
    # A page whose title changed brings its new title, as long as the user kept the one it had.
    renamed = import_page(service, {**capture("<p>여섯째 판</p>", "새 제목"), "doc_id": first})
    assert service.wiki_get({"id": renamed["doc_id"]})["title"] == "새 제목"
    # Content that another import holds is not saved twice.
    assert import_page(service, {**capture("<p>여섯째 판</p>", "새 제목"), "doc_id": "older-copy"}) == {
        "status": "skipped", "doc_id": first, "message": "동일한 페이지 내용이 이미 저장되어 있습니다.", "reason": "same"}


def test_markdown_files_keep_their_source_and_update_like_other_imports(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), None)
    note = tmp_path / "노트.md"
    note.write_bytes("﻿# 회의\n\n결정 사항\n".encode("utf-8"))
    result = service.dispatch("markdown.import", {"path": str(note)})
    assert result == {"status": "imported", "doc_id": result["doc_id"], "doc_ids": [result["doc_id"]],
                      "message": "Markdown 문서를 Wiki에 저장했습니다.", "title": "노트", "path": str(note)}
    doc = service.wiki_get({"id": result["doc_id"]})
    assert (doc["content"], doc["format"], doc["source"]["kind"], doc["source"]["source_path"]) == (
        "# 회의\n\n결정 사항", "markdown", "markdown", str(note))
    assert service.wiki_source_markdown({"id": doc["id"]})["markdown"] == "# 회의\n\n결정 사항"
    assert service.dispatch("markdown.import", {"path": str(note)})["message"] == "이미 가져온 동일한 Markdown 파일입니다."
    # The line breaks of Windows editors (\r\n) become the Wiki's (\n).
    note.write_bytes("# 회의\r\n\r\n바뀐 결정".encode("utf-8"))
    assert service.dispatch("markdown.import", {"path": str(note)})["status"] == "updated"
    assert service.wiki_get({"id": doc["id"]})["content"] == "# 회의\n\n바뀐 결정"
    # A text longer than one document is saved in parts.
    note.write_text("\n\n".join(f"## {index}\n\n{'가' * 1000}" for index in range(100)), encoding="utf-8")
    long = service.dispatch("markdown.import", {"path": str(note)})
    assert long["status"] == "updated" and len(long["doc_ids"]) == 2 and long["title"] == "노트 (1/2)"
    for name, data, error in (("a.txt", b"x", ".md, .markdown"), ("빈.md", b" \n", "빈 파일"),
                              ("깨진.md", b"\xff\xfe\x00", "UTF-8"), ("없는.md", None, "찾을 수 없습니다")):
        if data is not None:
            (tmp_path / name).write_bytes(data)
        with pytest.raises(UserError, match=error):
            service.dispatch("markdown.import", {"path": str(tmp_path / name)})
    with pytest.raises(UserError, match="전체 경로"):
        service.dispatch("markdown.import", {"path": "노트.md"})


def analyzed(service, ident):
    """Mark the document as analyzed, with a summary and a keyword."""
    with service.db.connect() as db:
        db.execute("UPDATE documents SET summary='요약',status='ready',index_mode='wiki' WHERE id=?", (ident,))
        db.execute("INSERT INTO keywords VALUES (?, '보고')", (ident,))


def test_a_file_whose_bytes_change_but_not_its_text_keeps_its_document_and_analysis(tmp_path):
    """A file saved again as it was (or by a DRM agent) has new bytes but the same text: the document, its analysis and
    revision stay, the source takes the new hash, and the import says it is the same, as the routine that keeps sources
    up to date counts it. A change of formatting only reaches the HTML page."""
    def convert(file, cancel):
        lines = file.read_text(encoding="utf-8").splitlines()
        # The first line is the text; a line "color" colors it, which the text does not show.
        color = ' style="color:#C00000"' if "color" in lines[1:] else ""
        return page(file.stem, f"<p{color}>{escape(lines[0])}</p>", "word")

    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    importer = RtfImporter(service, converter=convert, capability_check=lambda: {"available": True})
    rtf = tmp_path / "보고.rtf"
    rtf.write_text("주간 보고\n", encoding="utf-8")
    ident = run(importer, rtf)["doc_id"]
    analyzed(service, ident)
    rtf.write_text("주간 보고\n다시 저장만 함", encoding="utf-8")
    assert run(importer, rtf) == {"path": str(rtf), "status": "skipped", "doc_id": ident, "reason": "same",
                                   "message": "원본 파일의 글이 그대로라 문서와 AI 분석을 그대로 두었습니다."}
    doc = service.wiki_get({"id": ident})
    assert (doc["revision"], doc["status"], doc["summary"], doc["keywords"]) == (1, "ready", "요약", ["보고"])
    assert service.dispatch("wiki.source_status", {"id": ident})["state"] == "same"
    assert run(importer, rtf)["message"] == "이미 가져온 동일한 RTF 문서입니다."
    # Only the formatting changed: the page shows it, and the analysis of the same text stays.
    rtf.write_text("주간 보고\ncolor", encoding="utf-8")
    formatted = run(importer, rtf)
    assert (formatted["status"], formatted["reason"]) == ("skipped", "same")
    assert formatted["message"] == "원본 파일의 글이 그대로라 문서와 AI 분석은 그대로 두고, 바뀐 서식·그림만 HTML 문서에 반영했습니다."
    doc = service.wiki_get({"id": ident})
    assert "color:#C00000" in doc["html"] and (doc["revision"], doc["summary"]) == (1, "요약")
    # New text updates the document as before.
    rtf.write_text("바뀐 보고\ncolor", encoding="utf-8")
    assert run(importer, rtf)["status"] == "updated"
    assert (service.wiki_get({"id": ident})["summary"], service.wiki_get({"id": ident})["revision"]) == ("", 2)


def test_a_markdown_file_that_only_changed_its_line_breaks_keeps_its_analysis(tmp_path):
    service = Service(Database(tmp_path / "wiki.db"), None)
    note = tmp_path / "노트.md"
    note.write_bytes("# 회의\n\n결정 사항\n".encode("utf-8"))
    ident = service.dispatch("markdown.import", {"path": str(note)})["doc_id"]
    analyzed(service, ident)
    # Saved again by a Windows editor: \r\n and a byte order mark, the same text.
    note.write_bytes("﻿# 회의\r\n\r\n결정 사항\r\n".encode("utf-8"))
    kept = service.dispatch("markdown.import", {"path": str(note)})
    assert (kept["status"], kept["reason"], kept["doc_id"]) == ("skipped", "same", ident)
    assert (service.wiki_get({"id": ident})["summary"], service.wiki_get({"id": ident})["revision"]) == ("요약", 1)
    assert service.dispatch("wiki.source_status", {"id": ident})["state"] == "same"
    # The text the user changed stays, and asked to, an update brings back the file's.
    service.wiki_save({"id": ident, "title": "노트", "content": "내 메모"})
    note.write_bytes("# 회의\n\n결정 사항".encode("utf-8"))
    assert service.dispatch("markdown.import", {"path": str(note)})["reason"] == "same"
    assert service.wiki_get({"id": ident})["content"] == "내 메모"
    restored = service.dispatch("markdown.import", {"path": str(note), "doc_id": ident, "replace": True})
    assert (restored["status"], service.wiki_get({"id": ident})["content"]) == ("updated", "# 회의\n\n결정 사항")


def test_the_file_a_document_came_from_is_checked_without_importing_it(importer, tmp_path):
    service = importer.service
    rtf = tmp_path / "확인.rtf"
    rtf.write_text("처음", encoding="utf-8")
    ident = run(importer, rtf)["doc_id"]
    status = service.dispatch("wiki.source_status", {"id": ident})
    assert status["state"] == "same" and status["modified_at"].endswith("+00:00")
    rtf.write_text("나중", encoding="utf-8")
    assert service.dispatch("wiki.source_status", {"id": ident})["state"] == "changed"
    os.remove(rtf)
    assert service.dispatch("wiki.source_status", {"id": ident}) == {"state": "missing"}
    page_doc = import_page(service, capture("<p>페이지</p>"))["doc_id"]
    assert service.dispatch("wiki.source_status", {"id": page_doc}) == {"state": "unknown"}
    with pytest.raises(UserError, match="가져온 문서가 아닙니다"):
        service.dispatch("wiki.source_status", {"id": service.wiki_save({"title": "메모", "content": "글"})["id"]})
    with pytest.raises(UserError, match="찾을 수 없습니다"):
        service.dispatch("wiki.source_status", {"id": "nothing"})


def test_sources_of_an_earlier_version_get_a_title_and_an_index(tmp_path):
    path = tmp_path / "wiki.db"
    Service(Database(path), None)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE text_sources")
        db.execute("CREATE TABLE text_sources (doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE, "
                   "source_path TEXT NOT NULL, source_hash TEXT NOT NULL, markdown TEXT NOT NULL, "
                   "imported_at TEXT NOT NULL, part INTEGER NOT NULL DEFAULT 1, parts INTEGER NOT NULL DEFAULT 1, "
                   "UNIQUE(source_hash, part))")
    Database(path)
    with sqlite3.connect(path) as db:
        assert "title" in {row[1] for row in db.execute("PRAGMA table_info(text_sources)")}
        indexes = {row[1] for row in db.execute("PRAGMA index_list(text_sources)")}
        assert "idx_text_sources_identity" in indexes
