"""The import box's .txt files: each is read through the hidden Notepad (a stand-in here; test_notepad.py covers the
reader) and becomes an HTML page. Python never decodes a file's bytes, which DRM software leaves encrypted."""

import re
import sys
from html import unescape
from pathlib import Path

import pytest

from agent import text_import
from agent.db import Database
from agent.llm import UserError
from agent.notepad import ENCRYPTED
from agent.service import Service
from agent.text_import import capabilities, notepad_page
from test_rtf_import import FakeLLM, wait


class Notepad:
    """The hidden Notepad: the text it shows for each file name, or what it refuses; records the files it read."""

    def __init__(self):
        self.texts, self.calls = {}, []

    def __call__(self, path, cancel):
        self.calls.append(Path(path).name)
        text = self.texts[Path(path).name]
        if isinstance(text, Exception):
            raise text
        return text


@pytest.fixture
def notepad(monkeypatch):
    notepad = Notepad()
    monkeypatch.setattr(text_import, "read_with_notepad", notepad)
    return notepad


@pytest.fixture
def importer(tmp_path, notepad):
    importer = Service(Database(tmp_path / "data" / "wiki.db"), FakeLLM()).texts
    importer.capability_check = lambda: {"available": True}
    return importer


def test_folder_import_saves_the_text_notepad_shows_as_html_documents(importer, notepad, tmp_path):
    folder = tmp_path / "메모"
    (folder / "하위").mkdir(parents=True)
    # The bytes stand for DRM-encrypted files: only the text Notepad shows reaches the Wiki.
    for name in ("회의록.txt", "보호.txt", "빈 메모.txt", "하위/일정.txt"):
        (folder / name).write_bytes(b"\x9b\x01 encrypted " + name.encode())
    (folder / "같은 회의록.TXT").write_bytes((folder / "회의록.txt").read_bytes())
    (folder / "설명.md").write_text("# Markdown은 Electron이 가져옵니다", encoding="utf-8")
    (folder / "그림.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    notepad.texts.update(
        {
            "같은 회의록.TXT": "1. 일정 확정\n2. 담당: <김개발>",
            "보호.txt": UserError(ENCRYPTED),
            "빈 메모.txt": " \n",
            "일정.txt": "다음 주 월요일 배포",
        }
    )
    importer.start({"path": str(folder), "mode": "directory", "recursive": True})
    job = wait(importer)
    assert (job["status"], job["total"], job["imported"], job["skipped"], job["failed"]) == ("completed", 5, 2, 1, 2)
    # Files are read in name order; a file with the same bytes as one already imported is not read again.
    assert notepad.calls == ["같은 회의록.TXT", "보호.txt", "빈 메모.txt", "일정.txt"]
    results = {Path(r["path"]).name: r for r in job["results"]}
    assert results["회의록.txt"]["status"] == "skipped"
    assert results["보호.txt"]["message"] == ENCRYPTED
    assert results["빈 메모.txt"]["message"].startswith("빈 텍스트 파일입니다.")

    service = importer.service
    doc = service.wiki_get({"id": results["같은 회의록.TXT"]["doc_id"]})
    assert (doc["title"], doc["format"], doc["source"]["kind"]) == ("같은 회의록", "html", "text")
    assert doc["source"]["source_path"] == str(folder / "같은 회의록.TXT")
    assert '<div class="doc text"><pre>1. 일정 확정\n2. 담당: &lt;김개발&gt;</pre></div>' in doc["html"]
    assert "2. 담당: <김개발>" in doc["content"]
    assert service.wiki_source_html({"id": doc["id"]})["filename"] == "같은 회의록.html"
    assert service.wiki_search({"query": "월요일", "mode": "auto"})["documents"][0]["title"] == "일정"

    # The same file again is skipped; a document purged from the trash takes its source with it.
    importer.start({"path": str(folder / "회의록.txt"), "mode": "file"})
    assert wait(importer)["results"][0]["message"] == "이미 가져온 동일한 텍스트입니다."
    service.wiki_delete({"id": doc["id"]})
    service.wiki_purge({"ids": [doc["id"]]})
    with service.db.connect() as db:
        assert db.execute("SELECT count(*) FROM text_sources").fetchone()[0] == 1


def test_text_import_takes_txt_files_only_and_never_reads_an_empty_one(importer, notepad, tmp_path):
    markdown = tmp_path / "설명.md"
    markdown.write_text("# 제목", encoding="utf-8")
    with pytest.raises(UserError, match=r"존재하는 텍스트\(\.txt\) 파일을 선택해 주세요"):
        importer.start({"path": str(markdown), "mode": "file"})
    importer.start({"path": str(tmp_path), "mode": "directory"})
    assert wait(importer)["error"] == "선택한 폴더에 텍스트(.txt) 파일이 없습니다."
    (tmp_path / "빈 파일.txt").write_bytes(b"")
    importer.start({"path": str(tmp_path / "빈 파일.txt"), "mode": "file"})
    assert wait(importer)["results"][0]["message"] == "빈 텍스트 파일입니다."
    assert notepad.calls == []


def test_text_import_needs_windows_notepad(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert capabilities() == {
        "available": False,
        "reason": "텍스트(.txt) 파일은 Windows에서 메모장으로 읽어 가져올 수 있습니다.",
    }
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", str(tmp_path))
    assert capabilities()["available"] is False and "을 찾지 못했습니다" in capabilities()["reason"]
    (tmp_path / "System32").mkdir()
    (tmp_path / "System32" / "notepad.exe").write_bytes(b"")
    service = Service(Database(tmp_path / "wiki.db"), FakeLLM())
    assert service.dispatch("text.capabilities", {})["available"] is True
    # Each file goes to the hidden Notepad reader.
    assert service.texts.converter is notepad_page


def test_a_text_longer_than_a_wiki_document_is_saved_in_parts_cut_between_its_lines(importer, notepad, tmp_path):
    file = tmp_path / "로그.txt"
    file.write_bytes(b"\x9b\x01 encrypted")
    notepad.texts["로그.txt"] = "".join(f"{index}번째 줄 <기록> & {'내용 ' * 50}\n" for index in range(1000))
    importer.start({"path": str(file), "mode": "file"})
    result = wait(importer)["results"][0]
    docs = [importer.service.wiki_get({"id": ident}) for ident in result["doc_ids"]]
    assert result["message"] == "글이 80,000자를 넘어 HTML 문서 3개로 나누어 Wiki에 저장했습니다."
    assert [(doc["title"], doc["source"]["part"], doc["source"]["kind"]) for doc in docs] == [
        ("로그 (1/3)", 1, "text"),
        ("로그 (2/3)", 2, "text"),
        ("로그 (3/3)", 3, "text"),
    ]
    # Every line arrives once, whole, and each part is a text page of its own.
    pages = [re.search(r'<div class="doc text"><pre>(.*)</pre></div>', doc["html"], re.S).group(1) for doc in docs]
    assert "".join(unescape(text) for text in pages) == notepad.texts["로그.txt"]
    assert all(text.endswith("\n") for text in pages)
