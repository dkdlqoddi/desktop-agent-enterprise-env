import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from agent.db import Database
from agent.llm import UserError
from agent.markdown_import import html_to_markdown
from agent.service import Service
from test_rtf_import import PAGE, FakeLLM, wait


def test_markdown_preserves_structure_code_whitespace_links_and_literal_syntax():
    _, result = html_to_markdown(
        """<h1>업무 안내</h1><h2>상세</h2>
    <p><strong>중요</strong> <em>강조</em> <a href="/guide?q=a%20b">문서</a></p>
    <ul><li>첫 항목<ul><li>하위 항목</li></ul></li></ul>
    <blockquote>인용문</blockquote><pre>if x &lt; 3:\n  print("```")\n\n\n  done()</pre>
    <table><tr><th>키</th><th>값</th></tr><tr><td>a | b</td><td>&lt;tag&gt;</td></tr></table>
    <script>secret</script><style>secret</style><iframe>secret</iframe>
    <a href="javascript:alert(1)">안전한 텍스트</a><img src="https://test/track" alt="그림 설명">
    """,
        "https://wiki.test/page",
    )
    for value in (
        "# 업무 안내",
        "## 상세",
        "**중요**",
        "*강조*",
        "[문서](https://wiki.test/guide?q=a%20b)",
        "- 첫 항목",
        "  - 하위 항목",
        "> 인용문",
        "````\nif x < 3:",
        "\n\n\n  done()",
        r"| a \| b | \<tag\> |",
        "그림 설명",
    ):
        assert value in result
    assert all(value not in result for value in ("<table>", "secret", "javascript:", "https://test/track"))


def test_inline_code_in_tables_and_malformed_links():
    _, result = html_to_markdown(
        "<table><tr><th>표</th></tr><tr><td><code>a | b</code></td></tr></table>"
        '<p><a href="http://[invalid">주소 오류</a><a href="#section">목차</a></p>'
    )
    assert r"`a \| b`" in result
    assert "주소 오류" in result and "http://[invalid" not in result
    assert "[목차](#section)" in result


@pytest.mark.parametrize("kind", ["rtf", "office", "confluence", "jira"])
def test_legacy_html_upgrade_preserves_edits_and_metadata_and_removes_html_columns(tmp_path, kind):
    path = tmp_path / "wiki.db"
    service = Service(Database(path), FakeLLM())
    original = service.wiki_save({"title": "가져온 문서", "content": "기존 평문"})
    edited = service.wiki_save({"title": "편집한 문서", "content": "기존 평문"})
    service.wiki_save({"id": edited["id"], "title": "내 제목", "content": "내가 편집한 내용"})
    html = "<h1>제목</h1><table><tr><td>원문</td></tr></table>"
    with service.db.connect() as db:
        db.execute(f"DROP TABLE {kind}_sources")
        metadata = {
            "rtf": "source_path TEXT NOT NULL, source_hash TEXT NOT NULL UNIQUE",
            "office": "source_path TEXT NOT NULL, source_hash TEXT NOT NULL UNIQUE, application TEXT NOT NULL",
            "confluence": "source_url TEXT NOT NULL, page_id TEXT NOT NULL, source_hash TEXT NOT NULL",
            "jira": "source_url TEXT NOT NULL, issue_key TEXT NOT NULL, source_hash TEXT NOT NULL",
        }[kind]
        db.execute(
            f"CREATE TABLE {kind}_sources (doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE, {metadata}, html TEXT NOT NULL, "
            + ("html_path TEXT NOT NULL, " if kind != "rtf" else "")
            + "imported_at TEXT NOT NULL"
            + (", UNIQUE(source_url, source_hash)" if kind in {"confluence", "jira"} else "")
            + ")"
        )
        for index, doc in enumerate((original, edited)):
            values = [doc["id"]]
            values += {
                "rtf": ["/source.rtf", f"hash-{index}"],
                "office": ["/source.docx", f"hash-{index}", "Word"],
                "confluence": ["https://wiki.test/page", "123", f"hash-{index}"],
                "jira": ["https://jira.test/TEAM-1", "TEAM-1", f"hash-{index}"],
            }[kind]
            values += [html] + (["/legacy.html"] if kind != "rtf" else []) + ["2026-09-01"]
            db.execute(f"INSERT INTO {kind}_sources VALUES ({','.join('?' for _ in values)})", values)
    upgraded = Service(Database(path), FakeLLM())
    markdown = html_to_markdown(html)[1]
    assert upgraded.wiki_get({"id": original["id"]})["content"] == markdown
    assert upgraded.wiki_get({"id": edited["id"]})["content"] == "내가 편집한 내용"
    for doc in (original, edited):
        source = upgraded.wiki_source_markdown({"id": doc["id"]})
        assert source["markdown"] == markdown and source["filename"].endswith(".md")
    with upgraded.db.connect() as db:
        columns = {r[1] for r in db.execute(f"PRAGMA table_info({kind}_sources)")}
        assert "markdown" in columns and "html" not in columns and "html_path" not in columns
        assert db.execute(f"SELECT count(*) FROM {kind}_sources").fetchone()[0] == 2
        if kind in {"confluence", "jira"}:
            assert (
                db.execute(f"SELECT source_hash FROM {kind}_sources WHERE doc_id=?", (original["id"],)).fetchone()[0]
                == hashlib.sha256(markdown.encode()).hexdigest()
            )
    # Opening an upgraded database again is idempotent.
    again = Service(Database(path), FakeLLM())
    assert again.wiki_get({"id": original["id"]})["revision"] == 2
    again.wiki_delete({"id": original["id"]})
    again.wiki_purge({"ids": [original["id"]]})
    with again.db.connect() as db:
        assert db.execute(f"SELECT count(*) FROM {kind}_sources").fetchone()[0] == 1


@pytest.mark.parametrize("kind,extension", [("rtf", ".rtf"), ("office", ".docx")])
def test_local_import_never_creates_intermediate_files_and_rejects_changed_source(
    tmp_path, monkeypatch, kind, extension
):
    service = Service(Database(tmp_path / "data" / "wiki.db"), FakeLLM())
    importer = getattr(service, kind)
    importer.capability_check = lambda: {"available": True}
    source = tmp_path / ("original" + extension)
    source.write_bytes(b"original")
    opened = []
    original_open = Path.open

    def read_only_open(path, mode="r", *args, **kwargs):
        assert all(flag not in mode for flag in ("w", "a", "x", "+")), (path, mode)
        opened.append(path)
        return original_open(path, mode, *args, **kwargs)

    def convert(file, cancel):
        assert file == source
        return PAGE

    importer.converter = convert
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", read_only_open)
        importer.start({"path": str(source), "mode": "file"})
        assert wait(importer)["imported"] == 1
    assert opened and all(path == source for path in opened)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["data", source.name]
    source.write_bytes(b"new version")

    def changing_converter(file, cancel):
        file.write_bytes(b"edited during conversion")
        return PAGE

    importer.converter = changing_converter
    importer.start({"path": str(source), "mode": "file"})
    result = wait(importer)
    assert result["failed"] == 1 and "원본 파일이 변경" in result["results"][0]["message"]
    assert len(service.wiki_list({})) == 1


LARGE = "<p>" + "가" * 3_000_000 + "</p>"


def large_worker(source, sender, stop):
    # Pictures make pages far larger than a pipe's buffer.
    sender.send({"ok": True, "html": LARGE})
    sender.close()


def test_memory_worker_drains_large_result_without_deadlock(tmp_path, monkeypatch):
    from agent.word_converter import WordConverter

    monkeypatch.setattr(WordConverter, "check", lambda *args: None)
    monkeypatch.setattr(WordConverter, "worker", staticmethod(large_worker))
    assert WordConverter(timeout=10)(tmp_path / "source.rtf", threading.Event()) == LARGE
    assert list(tmp_path.iterdir()) == []


def finished_anyway_worker(source, sender, stop):
    # Stopped while converting, it still finishes and sends a page larger than a pipe holds.
    stop.wait(30)
    sender.send({"ok": True, "html": LARGE})
    sender.close()


def test_a_stopped_worker_that_finished_anyway_ends_without_waiting_out_the_grace(tmp_path, monkeypatch):
    from agent.word_converter import WordConverter

    monkeypatch.setattr(WordConverter, "check", lambda *args: None)
    monkeypatch.setattr(WordConverter, "worker", staticmethod(finished_anyway_worker))
    cancel = threading.Event()
    timer = threading.Timer(1, cancel.set)
    timer.start()
    started = time.monotonic()
    with pytest.raises(UserError, match="취소"):
        WordConverter(timeout=60, grace=30)(tmp_path / "source.rtf", cancel)
    timer.join()
    assert time.monotonic() - started < 15


def test_a_request_over_one_megabyte_is_refused_and_the_next_one_answered(tmp_path):
    # Electron sends at most 900,000 characters of parameters: a longer line is refused by its ID, unread.
    large = {"id": "large", "method": "wiki.save", "params": {"title": "큰 문서", "content": "가" * 1_000_000}}
    lines = json.dumps(large, ensure_ascii=False) + "\n" + json.dumps({"id": "small", "method": "health"}) + "\n"
    # The backend speaks UTF-8, and PYTHONUTF8 makes its errors UTF-8 too, as Electron starts it; text=True alone
    # would read both in the Windows code page (cp949).
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parents[1] / "main.py")],
        input=lines,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        env={**os.environ, "PRIVATE_AGENT_DATA_DIR": str(tmp_path), "PYTHONUTF8": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert [json.loads(line) for line in result.stdout.splitlines()] == [
        {"id": "large", "error": "요청이 너무 큽니다."},
        {"id": "small", "result": {"ok": True}},
    ]
    assert Service(Database(tmp_path / "wiki.db"), None).wiki_list({}) == []
