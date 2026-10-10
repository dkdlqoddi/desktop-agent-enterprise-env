import contextlib
import sqlite3
import time
from pathlib import Path

import pytest

from agent.confluence import import_page
from agent.db import Database
from agent.llm import UserError
from agent.service import Service


@pytest.fixture
def service(tmp_path):
    return Service(Database(tmp_path / "wiki.db"), None)


def capture(service, body="<table><tr><td>한국어 표</td></tr></table><pre>if x:\n  return 1</pre>"):
    return {
        "source_url": "https://wiki.test/confluence/pages/viewpage.action?pageId=123",
        "page_id": "123",
        "html": f"<html><head><title>Confluence 테스트</title></head><body>{body}</body></html>",
    }


def test_import_search_source_persistence_duplicate_edits_and_delete(service):
    params = capture(service)
    result = import_page(service, params)
    assert result["status"] == "imported"
    doc = service.wiki_get({"id": result["doc_id"]})
    assert "| 한국어 표 |" in doc["content"]
    assert "```\nif x:" in doc["content"]
    assert not (Path(service.db.path).parent / "confluence").exists()
    assert "  return 1" in doc["content"]
    assert doc["source"]["kind"] == "confluence"
    assert doc["source"]["source_url"] == params["source_url"]
    assert len(service.wiki_search({"query": "한국어 표", "mode": "word"})["documents"]) == 1
    markdown = service.wiki_source_markdown({"id": doc["id"]})["markdown"]
    service.wiki_save({"id": doc["id"], "title": "수정", "content": "개인 메모"})
    assert import_page(service, params)["status"] == "skipped"
    assert service.wiki_source_markdown({"id": doc["id"]})["markdown"] == markdown
    assert service.wiki_get({"id": doc["id"]})["content"] == "개인 메모"
    changed = capture(service, "<p>변경된 페이지</p>")
    # The page changed, and so did the document: the user's text stays unless the update replaces it.
    skipped = import_page(service, changed)
    assert (skipped["status"], skipped["doc_id"]) == ("skipped", doc["id"]) and "‘원본에서 업데이트’" in skipped[
        "message"
    ]
    assert service.wiki_get({"id": doc["id"]})["content"] == "개인 메모"
    restarted = Service(Database(service.db.path), None)
    assert restarted.wiki_source_markdown({"id": doc["id"]})["markdown"] == markdown
    updated = import_page(service, {**changed, "doc_id": doc["id"], "replace": True})
    assert (updated["status"], updated["doc_ids"]) == ("updated", [doc["id"]])
    replaced = service.wiki_get({"id": doc["id"]})
    # The title the user gave stays.
    assert (replaced["title"], replaced["content"], replaced["source"]["edited"]) == ("수정", "변경된 페이지", False)
    assert service.wiki_source_markdown({"id": doc["id"]})["markdown"] == "변경된 페이지"
    assert len(service.wiki_list({})) == 1
    service.wiki_delete({"id": doc["id"]})
    with service.db.connect() as db:
        # In the trash the document keeps its source, so a restore brings it back as it was imported.
        assert db.execute("SELECT 1 FROM confluence_sources WHERE doc_id=?", (doc["id"],)).fetchone()
    assert service.wiki_purge({"ids": [doc["id"]]}) == {"purged": 1}
    with service.db.connect() as db:
        assert not db.execute("SELECT 1 FROM confluence_sources WHERE doc_id=?", (doc["id"],)).fetchone()


def test_code_macro_language_becomes_fence_info(service):
    body = (
        '<pre data-syntaxhighlighter-params="brush: java; gutter: false">int x = 1;</pre>'
        '<pre data-syntaxhighlighter-params="brush: text">plain</pre>'
    )
    content = service.wiki_get({"id": import_page(service, capture(service, body))["doc_id"]})["content"]
    assert "```java\nint x = 1;\n```" in content
    assert "```\nplain\n```" in content


def test_limits_and_in_memory_payload_validation(service, tmp_path):
    params = capture(service)
    with pytest.raises(UserError, match="원문"):
        import_page(service, {**params, "html": None, "html_path": "/outside.html"})
    with pytest.raises(UserError, match="HTTP"):
        import_page(service, {**params, "source_url": "file:///etc/passwd"})
    with pytest.raises(UserError, match="페이지 ID"):
        import_page(service, {**params, "page_id": "../123"})
    with pytest.raises(UserError, match="10MB"):
        import_page(service, capture(service, "a" * (10 * 1024 * 1024)))
    assert service.wiki_list({}) == []
    # The browser importer saves pages itself; Electron's old internal RPC is gone.
    with pytest.raises(UserError, match="지원하지 않는"):
        service.dispatch("confluence.import", params)


def test_a_page_longer_than_a_document_is_saved_as_documents_in_parts(service):
    sections = "".join(f"<h2>절 {i}</h2><p>{'가' * 1000}</p>" for i in range(100))
    result = import_page(service, capture(service, sections))
    assert result["message"] == "글이 80,000자를 넘어 Markdown 문서 2개로 나누어 Wiki에 저장했습니다."
    first, second = (service.wiki_get({"id": ident}) for ident in result["doc_ids"])
    assert result["doc_id"] == first["id"]
    assert (first["title"], second["title"]) == ("Confluence 테스트 (1/2)", "Confluence 테스트 (2/2)")
    assert first["content"].startswith("## 절 0\n\n가") and second["content"].startswith("## 절 ")
    assert len(first["content"]) <= 80000 and len(second["content"]) <= 80000
    assert (first["source"]["part"], second["source"]["part"], second["source"]["parts"]) == (1, 2, 2)
    assert (
        first["parts"]
        == second["parts"]
        == [
            {"id": first["id"], "title": first["title"], "part": 1},
            {"id": second["id"], "title": second["title"], "part": 2},
        ]
    )
    assert service.wiki_source_markdown({"id": second["id"]})["markdown"] == second["content"]
    # The same page is not saved again while any of its parts is left: a part in the trash comes back when the user
    # imports the page again (sources.plan), so the two parts are whole once more.
    service.wiki_delete({"id": first["id"]})
    assert import_page(service, capture(service, sections)) == {
        "status": "skipped",
        "doc_id": first["id"],
        "message": "동일한 페이지 내용이 이미 저장되어 있습니다.",
        "reason": "same",
    }
    assert [part["id"] for part in service.wiki_get({"id": second["id"]})["parts"]] == [first["id"], second["id"]]
    # Purged for good, the page is saved again as new documents.
    service.wiki_delete({"id": first["id"]})
    assert service.wiki_purge({"ids": [first["id"]]}) == {"purged": 1}
    assert [part["id"] for part in service.wiki_get({"id": second["id"]})["parts"]] == [second["id"]]


def test_what_a_watched_page_changed_is_compared_after_its_import_is_saved(service, monkeypatch):
    """Comparing a long page takes a while; the import holds the Wiki's write lock only to save, so a save meanwhile
    (an edit, another import) does not wait for the comparison or fail as locked."""
    import agent.changes as changes

    def tracked(body):
        return {**capture(service, body), "track_changes": True}

    import_page(service, tracked("<p>첫 판</p>"))
    compare, saved = changes.compare, []

    def compare_while_saving(before, after):
        with contextlib.closing(sqlite3.connect(service.db.path, timeout=1)) as other, other:
            other.execute("BEGIN IMMEDIATE")
            other.execute("UPDATE documents SET title=title")
        saved.append(True)
        return compare(before, after)

    monkeypatch.setattr(changes, "compare", compare_while_saving)
    result = import_page(service, tracked("<p>둘째 판</p>"))
    assert saved and result["change"]["lines"] == [{"op": "-", "text": "첫 판"}, {"op": "+", "text": "둘째 판"}]


def test_a_long_page_with_many_changed_lines_is_compared_quickly():
    from agent.changes import compare

    before = "\n".join(f"| {i} | 값 {i} |" for i in range(30000))
    after = "\n".join(f"| {i} | 값 {i}{' 바뀜' if i % 3 == 0 else ''} |" for i in range(30000))
    started = time.monotonic()
    change = compare(before, after)
    assert time.monotonic() - started < 5
    assert (change["added"], change["removed"], len(change["lines"])) == (10000, 10000, 60)
    assert change["lines"][:2] == [{"op": "-", "text": "| 0 | 값 0 |"}, {"op": "+", "text": "| 0 | 값 0 바뀜 |"}]


def test_a_change_of_a_page_saved_in_parts_shows_no_repeated_table_header(service):
    rows = "".join(f"<tr><td>{i}</td><td>{'값 ' * 60}{i}</td></tr>" for i in range(1000))
    table = f"<table><tr><th>번호</th><th>내용</th></tr>{rows}</table>"
    first = import_page(service, {**capture(service, table), "track_changes": True})
    assert len(first["doc_ids"]) > 1
    second = service.wiki_get({"id": first["doc_ids"][1]})["content"]
    assert second.startswith("| 번호 | 내용 |\n| --- | --- |\n")  # each part repeats the header
    changed = table.replace("<td>500</td>", "<td>500번</td>")
    result = import_page(service, {**capture(service, changed), "track_changes": True})
    assert (result["change"]["added"], result["change"]["removed"]) == (1, 1)
    assert [line["text"][:8] for line in result["change"]["lines"]] == ["| 500 | ", "| 500번 |"]


def test_a_page_with_a_title_longer_than_a_wiki_title_keeps_its_first_200_characters(service):
    params = capture(service)
    params["html"] = params["html"].replace("Confluence 테스트", "가" * 255)
    doc = service.wiki_get({"id": import_page(service, params)["doc_id"]})
    assert doc["title"] == "가" * 200


def test_existing_database_migration(service):
    doc = service.wiki_save({"title": "기존 문서", "content": "보존"})
    with service.db.connect() as db:
        db.execute("DROP TABLE confluence_sources")
    upgraded = Service(Database(service.db.path), None)
    assert upgraded.wiki_get({"id": doc["id"]})["content"] == "보존"
    assert import_page(upgraded, capture(upgraded))["status"] == "imported"
