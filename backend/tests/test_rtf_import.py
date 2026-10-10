import contextlib
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

from agent.db import Database
from agent.html_document import index_markdown, page
from agent.llm import UserError
from agent.rtf_import import RtfImporter
from agent.markdown_import import html_to_markdown
from agent.parts import CUT
from agent.service import Service
from agent.storage import migrate_database, read_only_uri
from agent.word_converter import capabilities, convert_with_word

HTML = """<html><head><meta charset="utf-8"><title>회의 안내</title><style>hidden css</style></head>
<body><p>작성자: 홍길동</p><p>안건: API 설계</p><table><tr><td>일시</td><td>월요일</td></tr></table>
<p>Python &amp; 캐시를 논의합니다.</p><script>alert('secret');</script></body></html>"""

# What a converter hands over: the HTML page of the file.
PAGE = page("회의 안내", '<p>작성자: 홍길동</p><p>안건: API 설계</p><table><tr><td>일시</td><td>월요일</td></tr></table>'
            "<p>Python &amp; 캐시를 논의합니다.</p>", "word")
TEXT = index_markdown(PAGE)
WORD_XML = """<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>작성자: 홍길동</w:t></w:r></w:p>
<w:p><w:r><w:t>안건: API 설계</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>일시</w:t></w:r></w:p></w:tc>
<w:tc><w:p><w:r><w:t>월요일</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
</w:body></w:document>"""


class FakeLLM:
    def public(self):
        return {"configured": True}

@pytest.fixture
def importer(tmp_path):
    service = Service(Database(tmp_path / "data" / "wiki.db"), FakeLLM())
    return RtfImporter(service, converter=lambda *args: PAGE, capability_check=lambda: {"available": True})


def wait(importer):
    importer.thread.join(5)
    assert not importer.thread.is_alive()
    return importer.status()


def test_html_extracts_korean_text_tables_and_excludes_active_content():
    title, content = html_to_markdown(HTML)
    assert title == "회의 안내"
    assert "작성자: 홍길동" in content
    assert "| 일시 | 월요일 |" in content
    assert r"Python \& 캐시" in content
    assert "hidden css" not in content
    assert "secret" not in content
    with pytest.raises(UserError, match="본문"):
        html_to_markdown('<html><head><title>not body</title></head><body><img src="x"></body></html>')
    # Text longer than a Wiki document holds arrives whole; it is saved in parts (parts.py).
    assert html_to_markdown("<p>" + "a" * 80001 + "</p>")[1] == "a" * 80001


def test_single_file_saves_the_html_page_and_searches_its_text_without_llm(importer, tmp_path):
    rtf = tmp_path / "회의.rtf"
    original = b"original document"
    rtf.write_bytes(original)
    importer.start({"path": str(rtf), "mode": "file"})
    result = wait(importer)
    assert result["status"] == "completed"
    assert result["imported"] == 1 and result["results"][0]["message"] == "HTML 문서로 Wiki에 저장했습니다."
    ident = result["results"][0]["doc_id"]
    doc = importer.service.wiki_get({"id": ident})
    assert doc["source"]["source_path"] == str(rtf)
    assert doc["status"] == "pending"
    # The window shows the page; search, PageIndex and the LLM read its text.
    assert (doc["format"], doc["html"], doc["has_html"], doc["content"]) == ("html", PAGE, True, TEXT)
    assert "| 일시 | 월요일 |" in TEXT and "<" not in TEXT
    assert importer.service.wiki_source_html({"id": ident}) == {"html": PAGE, "filename": "회의.html"}
    assert importer.service.wiki_source_markdown({"id": ident})["markdown"] == TEXT
    listed = importer.service.wiki_list({})[0]
    assert "html" not in listed and listed["format"] == "html"
    found = importer.service.wiki_search({"query": "홍길동", "mode": "word"})["documents"][0]
    assert found["id"] == ident and "html" not in found
    assert rtf.read_bytes() == original
    # A new title keeps the page; edited text makes it a Markdown document whose original stays.
    importer.service.wiki_save({"id": ident, "title": "새 제목", "content": TEXT})
    assert importer.service.wiki_get({"id": ident})["html"] == PAGE
    edited = importer.service.wiki_save({"id": ident, "title": "편집", "content": "사용자 편집"})
    assert (edited["format"], edited["has_html"], "html" in edited) == ("markdown", True, False)
    assert importer.service.wiki_source_html({"id": ident})["html"] == PAGE
    assert importer.service.wiki_source_markdown({"id": ident})["markdown"] == TEXT
    importer.service.wiki_delete({"id": ident})
    importer.service.wiki_purge({"ids": [ident]})
    with importer.service.db.connect() as db:
        assert db.execute("SELECT count(*) FROM rtf_sources").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM document_html").fetchone()[0] == 0
    with pytest.raises(UserError, match="HTML 원문이 없습니다"):
        importer.service.wiki_source_html({"id": importer.service.wiki_save({"title": "메모", "content": "직접 쓴 글"})["id"]})


def test_a_markdown_import_of_an_earlier_version_becomes_the_html_page(importer, tmp_path):
    """Files imported as Markdown before become HTML when they are imported again, unless their text was edited."""
    service = importer.service
    first, second = tmp_path / "a.rtf", tmp_path / "b.rtf"
    first.write_bytes(b"first document")
    second.write_bytes(b"second document")
    old = {}
    for file in (first, second):
        doc = service.wiki_save({"title": file.stem, "content": "예전 Markdown"})
        with service.db.connect() as db:
            db.execute("UPDATE documents SET summary='요약',status='ready',index_mode='wiki' WHERE id=?", (doc["id"],))
            db.execute("INSERT INTO keywords VALUES (?, '캐시')", (doc["id"],))
            db.execute("INSERT INTO rtf_sources(doc_id,source_path,source_hash,markdown,imported_at) VALUES (?,?,?,?,?)",
                       (doc["id"], str(file), importer._checksum(file), "예전 Markdown", "2026-09-01"))
        old[file.name] = doc
    service.wiki_save({"id": old["b.rtf"]["id"], "title": "b", "content": "내가 고친 글"})
    importer.start({"path": str(tmp_path), "mode": "directory"})
    job = wait(importer)
    assert (job["updated"], job["skipped"], job["edited"]) == (1, 1, 1)
    assert "Markdown으로 가져왔던 문서를 HTML 문서로 바꿨습니다." in [r["message"] for r in job["results"]]
    upgraded = service.wiki_get({"id": old["a.rtf"]["id"]})
    assert (upgraded["format"], upgraded["html"], upgraded["content"]) == ("html", PAGE, TEXT)
    # The text changed, so its earlier analysis is gone.
    assert (upgraded["status"], upgraded["summary"], upgraded["keywords"], upgraded["revision"]) == ("pending", "", [], 2)
    assert service.wiki_source_markdown({"id": upgraded["id"]})["markdown"] == TEXT
    kept = service.wiki_get({"id": old["b.rtf"]["id"]})
    assert (kept["format"], kept["content"], kept["has_html"]) == ("markdown", "내가 고친 글", False)
    assert "본문을 고친 문서라 원본의 내용으로 바꾸지 않았습니다" in next(r["message"] for r in job["results"]
                                                        if r["status"] == "skipped")
    assert len(service.wiki_list({})) == 2
    # Imported again, both are duplicates.
    importer.start({"path": str(tmp_path), "mode": "directory"})
    assert wait(importer)["skipped"] == 2


def test_a_file_longer_than_a_document_is_saved_as_html_documents_in_parts(importer, tmp_path, monkeypatch):
    service = importer.service
    rtf = tmp_path / "긴 문서.rtf"
    rtf.write_bytes(b"long document")
    long_page = page("긴 문서", "".join(f"<p>{index}번째 문단 {'내용 ' * 400}</p>" for index in range(150)), "word")
    importer.converter = lambda *args: long_page
    analyzed = []
    monkeypatch.setattr(service, "wiki_analyze", lambda params: analyzed.append(params["id"]))
    importer.start({"path": str(rtf), "mode": "file", "analyze": True})
    job = wait(importer)
    result = job["results"][0]
    assert (job["imported"], result["message"]) == (1, "글이 80,000자를 넘어 HTML 문서 3개로 나누어 Wiki에 저장했습니다.")
    assert analyzed == result["doc_ids"] and result["doc_id"] == result["doc_ids"][0]
    docs = [service.wiki_get({"id": ident}) for ident in result["doc_ids"]]
    assert [doc["title"] for doc in docs] == ["긴 문서 (1/3)", "긴 문서 (2/3)", "긴 문서 (3/3)"]
    for number, doc in enumerate(docs, 1):
        assert doc["format"] == "html" and index_markdown(doc["html"]) == doc["content"]
        assert len(doc["content"]) <= 80000 and f"<title>긴 문서 ({number}/3)</title>" in doc["html"]
        assert (doc["source"]["part"], doc["source"]["parts"], doc["source"]["source_path"]) == (number, 3, str(rtf))
        assert [part["id"] for part in doc["parts"]] == result["doc_ids"]
    assert "".join(doc["content"] for doc in docs).count("번째 문단") == 150
    # The same file is a duplicate while any of its parts is left; a part in the trash comes back with the import.
    service.wiki_delete({"id": docs[0]["id"]})
    importer.start({"path": str(rtf), "mode": "file"})
    assert wait(importer)["results"][0] == {"path": str(rtf), "status": "skipped", "doc_id": docs[0]["id"],
                                            "message": "이미 가져온 동일한 RTF 문서입니다.", "reason": "same"}
    assert service.wiki_get({"id": docs[0]["id"]})["title"] == "긴 문서 (1/3)"
    assert service.dispatch("wiki.trash", {})["documents"] == []
    # Beyond the most parts, the rest is left out and the result and the last part say so.
    monkeypatch.setattr("agent.parts.MAX_PARTS", 2)
    other = tmp_path / "더 긴 문서.rtf"
    other.write_bytes(b"longer document")
    importer.start({"path": str(other), "mode": "file"})
    result = wait(importer)["results"][0]
    assert result["message"] == ("글이 80,000자를 넘어 HTML 문서 2개로 나누어 Wiki에 저장했습니다. "
                                 "문서 2개를 넘는 뒷부분은 저장하지 않았습니다.")
    assert service.wiki_get({"id": result["doc_ids"][1]})["content"].endswith(CUT)


def test_folder_recursion_duplicates_failure_and_continuation(importer, tmp_path):
    root = tmp_path / "documents"
    root.mkdir()
    nested = root / "nested"
    nested.mkdir()
    (root / "a.rtf").write_bytes(b"same document")
    (root / "b.rtf").write_bytes(b"same document")
    (root / "bad.rtf").write_bytes(b"corrupt")
    (root / "ignore.txt").write_text("not rtf")
    (nested / "c.rtf").write_bytes(b"another document")

    def converter(source, *_):
        if source.read_bytes() == b"corrupt":
            raise UserError("테스트 변환 실패")
        return PAGE
    importer.converter = converter
    importer.start({"path": str(root), "mode": "directory", "recursive": True})
    job = wait(importer)
    assert (job["total"], job["imported"], job["skipped"], job["failed"]) == (4, 2, 1, 1)
    assert any(r["message"] == "테스트 변환 실패" for r in job["results"])
    importer.start({"path": str(root), "mode": "directory", "recursive": False})
    assert wait(importer)["total"] == 3


def test_a_folder_of_more_files_than_a_batch_is_imported_batch_after_batch(importer, tmp_path, monkeypatch):
    monkeypatch.setattr("agent.file_import.BATCH_FILES", 2)
    monkeypatch.setattr("agent.file_import.MAX_RESULTS", 3)
    root = tmp_path / "documents"
    (root / "nested").mkdir(parents=True)
    names = ["a.rtf", "b.rtf", "c.rtf", "d.rtf", "nested/e.rtf"]
    for name in names:
        (root / name).write_bytes(name.encode())
    found = []
    assert importer._scan(root, "directory", True, found.append) == [root / name for name in names]
    assert found == [4, 5]  # How many it found, after each folder.
    batches = {}

    def converter(source, *_):
        batches[source.name] = importer.status()["batch"]
        if source.name == "a.rtf":
            raise UserError("테스트 변환 실패")
        return page(source.stem, f"<p>{source.stem} 문서</p>", "word")
    importer.converter = converter
    importer.start({"path": str(root), "mode": "directory", "recursive": True})
    job = wait(importer)
    assert (job["status"], job["total"], job["processed"], job["imported"], job["failed"]) == \
        ("completed", 5, 5, 4, 1), job
    assert batches == {"a.rtf": 1, "b.rtf": 1, "c.rtf": 2, "d.rtf": 2, "e.rtf": 3}
    assert (job["batch"], job["batches"]) == (3, 3)
    # It lists its failure and its latest results; every file's document is saved.
    assert [(Path(r["path"]).name, r["status"]) for r in job["results"]] == [
        ("a.rtf", "failed"), ("d.rtf", "imported"), ("e.rtf", "imported")]
    assert sorted(doc["title"] for doc in importer.service.wiki_list({})) == ["b", "c", "d", "e"]
    # Imported again, the files saved are skipped and the one that failed is read again.
    importer.start({"path": str(root), "mode": "directory", "recursive": True})
    job = wait(importer)
    assert (job["processed"], job["skipped"], job["failed"], len(job["results"])) == (5, 4, 1, 3), job


def test_half_of_a_surrogate_pair_from_windows_text_is_replaced(importer, tmp_path):
    # Office and Notepad give UTF-16 text, which may hold half of a surrogate pair; UTF-8 cannot store it.
    source = tmp_path / "broken.rtf"
    source.write_bytes(b"document")
    importer.converter = lambda *_: page("문서", "<p>앞\ud83d뒤</p>", "word")
    importer.start({"path": str(source), "mode": "file"})
    job = wait(importer)
    assert job["imported"] == 1, job
    assert "앞\ufffd뒤" in importer.service.wiki_get({"id": job["results"][0]["doc_id"]})["content"]


def test_folders_that_cannot_be_read_are_left_out_but_the_chosen_one_must_be(importer, tmp_path, monkeypatch):
    root = tmp_path / "documents"
    (root / "locked").mkdir(parents=True)
    (root / "a.rtf").write_bytes(b"first document")
    (root / "locked" / "b.rtf").write_bytes(b"second document")
    real_scandir = os.scandir

    def scandir(path="."):
        # As Documents\\My Music on Windows: its parent lists it, but nobody may list what is inside.
        if Path(path) == root / "locked":
            raise PermissionError(13, "Access is denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    assert importer._scan(root, "directory", True) == [root / "a.rtf"]
    with pytest.raises(UserError, match="폴더를 읽지 못했습니다"):
        importer._scan(root / "locked", "directory", True)


@pytest.mark.skipif(sys.platform != "win32", reason="Junctions and hidden system folders are Windows features")
def test_junctions_and_hidden_system_folders_are_not_entered(importer, tmp_path):
    root, elsewhere = tmp_path / "documents", tmp_path / "elsewhere"
    (root / "System Volume Information").mkdir(parents=True)
    elsewhere.mkdir()
    (root / "a.rtf").write_bytes(b"first document")
    (root / "System Volume Information" / "b.rtf").write_bytes(b"second document")
    (elsewhere / "c.rtf").write_bytes(b"third document")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "My Music"), str(elsewhere)], check=True,
                   capture_output=True)
    subprocess.run(["attrib", "+h", "+s", str(root / "System Volume Information")], check=True, capture_output=True)
    assert importer._scan(root, "directory", True) == [root / "a.rtf"]


def test_backend_shutdown_stops_imports_and_waits_for_them(tmp_path):
    """Electron asks this before it ends the process tree: a stopped Office worker quits the Office app it started."""
    service = Service(Database(tmp_path / "data" / "wiki.db"), FakeLLM())
    source = tmp_path / "a.rtf"
    source.write_bytes(b"document")
    started, finished = threading.Event(), threading.Event()

    def converter(file, cancel):
        started.set()
        cancel.wait(10)
        time.sleep(0.5)  # The worker closing its file and quitting Office.
        finished.set()
        raise UserError("가져오기를 취소했습니다.")

    service.rtf.converter, service.rtf.capability_check = converter, lambda: {"available": True}
    service.rtf.start({"path": str(source), "mode": "file"})
    assert started.wait(5)
    assert service.dispatch("backend.shutdown", {}) == {"ok": True}
    assert finished.is_set() and not service.rtf.thread.is_alive()
    assert service.rtf.status()["status"] == "cancelled"
    assert service.wiki_list({}) == []


def test_cancel_does_not_commit_half_converted_document(importer, tmp_path):
    source = tmp_path / "cancel.rtf"
    source.write_bytes(b"cancel me")
    started = threading.Event()
    def convert(source, cancel):
        started.set()
        assert cancel.wait(3)
        return PAGE
    importer.converter = convert
    importer.start({"path": str(source), "mode": "file"})
    assert started.wait(3)
    with pytest.raises(UserError, match="이미"):
        importer.start({"path": str(source), "mode": "file"})
    importer.cancel()
    assert wait(importer)["status"] == "cancelled"
    assert importer.service.wiki_list({}) == []


def test_analysis_failure_keeps_the_imported_document(importer, tmp_path):
    source = tmp_path / "analyze.rtf"
    source.write_bytes(b"analyze me")
    def fail(_):
        raise UserError("LLM 오프라인")
    importer.service.wiki_analyze = fail
    importer.start({"path": str(source), "mode": "file", "analyze": True})
    job = wait(importer)
    assert (job["imported"], job["failed"], job["analysis_failed"]) == (1, 0, 1)
    assert len(importer.service.wiki_list({})) == 1


def test_empty_directory_platform_and_wrong_extension(importer, tmp_path):
    with pytest.raises(UserError, match="전체 경로"):
        importer.start({"path": "relative", "mode": "directory"})
    source = tmp_path / "file.txt"
    source.write_text("x")
    with pytest.raises(UserError, match="rtf"):
        importer.start({"path": str(source), "mode": "file"})
    importer.start({"path": str(tmp_path), "mode": "directory"})
    assert wait(importer)["status"] == "error"
    if sys.platform != "win32":
        assert capabilities()["available"] is False


def test_schema_upgrade_does_not_discard_existing_documents(tmp_path):
    source = tmp_path / "old.db"
    # Simulate an earlier schema by dropping only the newly added table.
    service = Service(Database(source), FakeLLM())
    doc = service.wiki_save({"title": "이전 문서", "content": "보존해야 하는 내용"})
    with service.db.connect() as db:
        db.execute("DROP TABLE rtf_sources")
    upgraded = Service(Database(source), FakeLLM())
    assert upgraded.wiki_get({"id": doc["id"]})["content"] == "보존해야 하는 내용"


def test_migration_includes_wal_preserves_original_and_refuses_overwrite(tmp_path):
    source, destination = tmp_path / "old.db", tmp_path / "new" / "wiki.db"
    conn = sqlite3.connect(source)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE example(value TEXT)")
    conn.execute("INSERT INTO example VALUES ('이전 기록')")
    conn.commit()
    migrate_database(source, destination)
    with contextlib.closing(sqlite3.connect(destination)) as db:  # A connection's own with only commits.
        assert db.execute("SELECT value FROM example").fetchone()[0] == "이전 기록"
    assert source.exists()
    with pytest.raises(FileExistsError):
        migrate_database(source, destination)
    conn.close()


def test_read_only_uri_keeps_a_network_server_in_the_path():
    assert read_only_uri(PureWindowsPath(r"C:\Users\나\wiki.db")) == "file:///C:/Users/%EB%82%98/wiki.db?mode=ro"
    # SQLite refuses file://server/…, as a mapped drive or redirected AppData folder resolves to.
    assert (read_only_uri(PureWindowsPath(r"\\server\share\Private Agent\wiki.db"))
            == "file:////server/share/Private%20Agent/wiki.db?mode=ro")


def fake_modules(monkeypatch, application, calls):
    """pywin32 whose DispatchEx serves `application`; `application` may be an exception DispatchEx raises."""
    def dispatch(progid):
        calls.append(("dispatch", progid))
        if isinstance(application, Exception):
            raise application
        return application
    client = SimpleNamespace(DispatchEx=dispatch)
    modules = {
        "pythoncom": SimpleNamespace(COINIT_APARTMENTTHREADED=2, CoInitializeEx=lambda mode: calls.append(("init", mode)),
                                     CoUninitialize=lambda: calls.append(("uninit",))),
        "win32com": SimpleNamespace(client=client), "win32com.client": client,
    }
    for name, module in modules.items(): monkeypatch.setitem(sys.modules, name, module)


def fake_com(monkeypatch, *, fail=False, adopt=False, xml=WORD_XML):
    calls = []
    options = SimpleNamespace(UpdateLinksAtOpen=True)
    word = SimpleNamespace(Options=options, Visible=False, DisplayAlerts=-1, AutomationSecurity=1)
    doc = SimpleNamespace(Content=SimpleNamespace(WordOpenXML=xml), Close=lambda **kw: calls.append(("close_doc", kw)))
    # Which settings Word has when it quits: the ones it saves as preferences must be the user's again.
    word.Quit = lambda **kw: calls.append(("quit", kw, (word.DisplayAlerts, word.AutomationSecurity, options.UpdateLinksAtOpen)))
    def open_document(**kwargs):
        calls.append(("open", kwargs))
        assert (word.Visible, word.DisplayAlerts, word.AutomationSecurity, options.UpdateLinksAtOpen) == (False, 0, 3, False)
        if fail: raise RuntimeError("Word failure")
        # The user opens their own document in this Word while the file is read.
        word.Visible = adopt
        return doc
    word.Documents = SimpleNamespace(Open=open_document)
    fake_modules(monkeypatch, word, calls)
    return calls, word, doc


def test_word_com_contract_hidden_readonly_memory_conversion_and_cleanup(monkeypatch, tmp_path):
    calls, word, doc = fake_com(monkeypatch)
    source = tmp_path / "in.rtf"
    html = convert_with_word(source)
    assert html.startswith("<!doctype html>") and "<title>in</title>" in html
    assert "<td" in html and ">일시</p></td>" in html and ">월요일</p></td>" in html
    opened = next(c[1] for c in calls if c[0] == "open")
    assert opened == {"FileName": str(source.resolve()), "ReadOnly": True, "AddToRecentFiles": False,
                      "ConfirmConversions": False, "Visible": False, "PasswordDocument": "",
                      "OpenAndRepair": False, "NoEncodingDialog": True}
    # Nothing but the file: no helper document, and the file closes before Word quits.
    assert [c[0] for c in calls] == ["dispatch", "open", "close_doc", "quit"]
    assert calls[2] == ("close_doc", {"SaveChanges": 0})
    # Link updates and macro security are the user's again when Word quits; alerts stay off meanwhile.
    assert calls[3] == ("quit", {"SaveChanges": 0}, (0, 1, True))
    assert not list(tmp_path.iterdir())


def test_word_failure_names_the_step_and_still_quits_its_word(monkeypatch, tmp_path):
    calls, _, _ = fake_com(monkeypatch, fail=True)
    with pytest.raises(UserError, match=r"^Word 파일 열기 실패: RuntimeError: Word failure\. Word에서 직접 열리는지, "
                                        r"암호·DRM·보안 정책을 확인해 주세요\.$"):
        convert_with_word(tmp_path / "in.rtf")
    assert calls[-1] == ("quit", {"SaveChanges": 0}, (0, 1, True))


def test_word_that_cannot_start_or_returns_no_xml_says_so(monkeypatch, tmp_path):
    class ComError(Exception):
        hresult, strerror = -2147221164, "클래스가 등록되지 않았습니다."
    calls = []
    fake_modules(monkeypatch, ComError(), calls)
    with pytest.raises(UserError, match=r"^Word 실행 실패 \[0x80040154\]: 클래스가 등록되지 않았습니다\. "
                                        r"Word 데스크톱 앱 설치 상태를 확인해 주세요\.$"):
        convert_with_word(tmp_path / "in.rtf")
    assert calls == [("dispatch", "Word.Application")]
    calls, _, _ = fake_com(monkeypatch, xml="  ")
    with pytest.raises(UserError, match="^Word 내용 읽기 실패: Word가 본문 XML을 돌려주지 않았습니다"):
        convert_with_word(tmp_path / "in.rtf")
    assert calls[-1][0] == "quit"
    # XML that Python cannot read fails after Word has quit.
    calls, _, _ = fake_com(monkeypatch, xml="<w:document")
    with pytest.raises(UserError, match="^Word 본문 변환 실패: ParseError"):
        convert_with_word(tmp_path / "in.rtf")
    assert calls[-1][0] == "quit"


def test_word_stays_open_for_a_document_the_user_opened_in_it(monkeypatch, tmp_path):
    calls, word, _ = fake_com(monkeypatch, adopt=True)
    assert "| 일시 | 월요일 |" in index_markdown(convert_with_word(tmp_path / "in.rtf"))
    assert ("close_doc", {"SaveChanges": 0}) in calls
    assert not any(c[0] == "quit" for c in calls)
    # It behaves as before the import, alerts included.
    assert (word.DisplayAlerts, word.AutomationSecurity, word.Options.UpdateLinksAtOpen) == (-1, 1, True)


def test_worker_runs_the_conversion_in_its_own_com_apartment(monkeypatch):
    from agent.office_session import run_worker
    calls = []
    fake_modules(monkeypatch, None, calls)

    class Sender:
        def send(self, message): calls.append(("send", message))
        def close(self): calls.append(("close",))

    run_worker(lambda: calls.append(("convert",)) or "<p>본문</p>", Sender(), "Word 변환", "안내")
    assert calls == [("init", 2), ("convert",), ("send", {"ok": True, "html": "<p>본문</p>"}), ("close",), ("uninit",)]
    calls.clear()

    def fail():
        raise OSError(5, "액세스가 거부되었습니다")
    run_worker(fail, Sender(), "Word 변환", "안내")
    sent = calls[1][1]
    assert (sent["ok"], sent["error"]) == (False, "Word 변환 실패: 액세스가 거부되었습니다. 안내")
    # The traceback goes along for the app's log; the user sees only the message.
    assert "OSError" in sent["trace"] and "in fail" in sent["trace"]
    assert calls[-1] == ("uninit",)
    calls.clear()

    def refuse():
        raise UserError("그대로")
    run_worker(refuse, Sender(), "Word 변환", "안내")
    assert calls[1] == ("send", {"ok": False, "error": "그대로", "trace": ""})
    calls.clear()

    # A step that turned an unexpected failure into a message still sends where it happened.
    from agent.office_session import step

    def broken():
        with step("Word 본문 변환"):
            return {}["missing"]
    run_worker(broken, Sender(), "Word 변환", "안내")
    sent = calls[1][1]
    assert sent["error"].startswith("Word 본문 변환 실패: KeyError")
    assert "in broken" in sent["trace"] and "KeyError" in sent["trace"]


@pytest.mark.skipif(sys.platform != "win32" or os.getenv("PRIVATE_AGENT_WORD_TEST") != "1", reason="Requires Windows desktop Word; opt in with PRIVATE_AGENT_WORD_TEST=1")
def test_real_word_conversion(tmp_path):
    from agent.word_converter import WordConverter
    source = tmp_path / "sample.rtf"
    source.write_text(r"{\rtf1\ansi Word COM integration sample}", encoding="ascii")
    result = WordConverter()(source, threading.Event())
    assert "Word COM integration sample" in result
