"""An import is one Wiki document: what is kept of a text longer than a document holds, and how the documents that
earlier versions cut a long import into become one again."""

import re
import threading
from html import escape, unescape

import pytest

from agent import parts, sources
from agent.confluence import import_page
from agent.db import Database
from agent.html_document import page, page_text
from agent.llm import UserError
from agent.parts import (
    CUT,
    Fitted,
    blocks,
    first_markdown,
    first_page,
    fit_markdown,
    fit_page,
    join_markdown,
    join_pages,
    merge_all,
    whole_title,
)
from agent.service import Service

PNG = "data:image/png;base64,iVBORw0KGgo="
NOTE = "\n\n> " + CUT
URL = "https://wiki.test/confluence/pages/viewpage.action?pageId=123"


def test_a_text_that_fits_is_the_document_as_it_is():
    assert fit_markdown("# 제목\n\n본문") == Fitted(None, "# 제목\n\n본문", False)
    html = page("보고서", "<h1>장</h1><p>문단</p>", "word")
    assert fit_page(html) == Fitted(html, "# 장\n\n문단", False)
    # Far more than the 80,000 characters at which earlier versions began another document.
    long = "\n\n".join(f"## 절 {i}\n\n" + "가" * 1000 for i in range(300))
    assert fit_markdown(long) == Fitted(None, long, False)


def test_a_longer_text_keeps_its_first_blocks_and_no_heading_without_its_text():
    text = "\n\n".join(f"## 섹션 {i}\n\n" + "가" * 300 for i in range(12))
    found, cut = first_markdown(text, 1000)
    assert cut and len(found) <= 1000 and found.endswith(NOTE)
    body = found[: -len(NOTE)]
    assert text.startswith(body) and not body.splitlines()[-1].startswith("#")
    assert re.findall(r"섹션 \d+", body) == ["섹션 0", "섹션 1", "섹션 2"] and body.count("가" * 300) == 3
    # A text that fits once its surrounding blank lines are gone is whole, without the note.
    assert first_markdown("\n" * 500 + "본문" + "\n" * 500, 1000) == ("본문", False)


def test_a_block_too_long_by_itself_gives_its_first_lines():
    code = "```python\n" + "".join(f"x = {i}\n" for i in range(400)) + "```"
    found, cut = first_markdown(code, 1000)
    body = found[: -len(NOTE)]
    # A code block still: its first lines, closed.
    assert cut and len(found) <= 1000 and body.startswith("```python\nx = 0\n") and body.endswith("\n```")
    kept = re.findall(r"x = (\d+)", body)
    assert body.count("```") == 2 and kept == [str(i) for i in range(len(kept))] and len(kept) > 50
    table = "| 키 | 값 |\n| --- | --- |\n" + "".join(f"| {i} | 값 {i} |\n" for i in range(150))
    found, cut = first_markdown(table, 1000)
    rows = re.findall(r"^\| (\d+) \|", found, re.M)
    assert cut and found.count("| 키 | 값 |") == 1 and rows == [str(i) for i in range(len(rows))] and len(rows) > 30
    # One line longer than a document is cut after a space.
    found, cut = first_markdown("가나다 " * 2000, 1000)
    assert cut and len(found) <= 1000 and found[: -len(NOTE)].endswith("가나다") and found.count("가나다") > 200
    # A heading is not all that is kept when what it introduces has a beginning that fits.
    found, cut = first_markdown("# 제목\n\n" + "가나다 " * 5000, 10000)
    assert cut and len(found) <= 10000 and found.startswith("# 제목\n\n가나다 가나다") and found.count("가나다") > 2000
    # A fence that never closes, and a fence too long to close again, are cut like any line.
    for block in ("```" + "b" * 2000, "```" + "a" * 600 + "\n" + "code\n" * 200 + "```"):
        found, cut = first_markdown(block, 1000)
        assert cut and len(found) <= 1000 and found.startswith("```") and found.endswith(NOTE)


def test_code_blocks_end_at_their_closing_fence_whatever_the_line_breaks():
    assert [kind for kind, _ in blocks("```\r\ncode\r\n```\r\n문단\r\n")] == ["code", "text"]


def test_a_longer_page_keeps_its_first_elements_with_their_pictures():
    body = "".join(
        f"<h1>장 {i}</h1>"
        + "".join(f"<p>문단 {i}-{j} {'설명 ' * 40}</p>" for j in range(4))
        + f'<p><img src="{PNG}" alt="그림 {i}"></p>'
        for i in range(12)
    )
    found, text, cut = first_page(page("보고서 & 계획", body, "word", "font-size:10pt"), 3000)
    assert cut and page_text(found) == text and len(text) <= 3000 and text.endswith(CUT)
    # The page it was, with its title and style, and the note where it ends.
    assert "<title>보고서 &amp; 계획</title>" in found and '<div class="doc word" style="font-size:10pt">' in found
    assert found.endswith(f'<p class="caption">{CUT}</p></div></body></html>')
    kept = re.findall(r"문단 (\d+-\d+)", text)
    assert kept == [f"{i}-{j}" for i in range(12) for j in range(4)][: len(kept)] and len(kept) > 8
    # A heading goes with its text rather than end what is kept, and each picture is back where it was.
    assert not text[: -len(CUT)].rstrip().splitlines()[-1].startswith("#")
    assert found.count(PNG) == text.count("[그림: ") > 1 and "private-agent-picture" not in found


def test_a_long_table_is_cut_between_its_rows():
    rows = "".join(
        f'<tr><th>{r}</th><td>값 {r}</td><td data-formula="=B{r}*2">{r * 2}</td></tr>' for r in range(1, 301)
    )
    body = (
        '<section class="sheet"><h2>매출</h2><table class="cells"><colgroup><col><col style="width:40pt"><col>'
        f"</colgroup><tr><th></th><th>B</th><th>C</th></tr>{rows}</table></section>"
    )
    found, text, cut = first_page(page("매출", body, "excel"), 4000)
    assert cut and len(text) <= 4000 and text.startswith("## 매출\n\n|  | B | C |\n| --- | --- | --- |\n")
    kept = re.findall(r"^\| (\d+) \|", text, re.M)
    assert kept == [str(r) for r in range(1, len(kept) + 1)] and len(kept) > 50
    # Whole rows in the one table of the one sheet, closed before the note.
    assert found.count("<tr>") == len(kept) + 1 and found.count('<section class="sheet">') == 1
    assert f'</table></section><p class="caption">{CUT}</p>' in found and found.count("width:40pt") == 1


def test_a_long_text_is_cut_between_its_lines_and_a_list_between_its_items():
    text = "".join(f"{i}번째 줄 <태그> & 기호\n" for i in range(2000))
    found, shown, cut = first_page(page("로그", f"<pre>{escape(text, quote=False)}</pre>", "text"), 5000)
    kept = unescape(re.search(r"<pre>(.*?)</pre>", found, re.S).group(1))
    assert cut and len(shown) <= 5000 and text.startswith(kept) and kept.endswith("\n") and len(kept) > 1500
    items = "".join(f"<li>항목 {i} {'내용 ' * 30}</li>" for i in range(1, 61))
    found, shown, cut = first_page(page("목록", f"<ol>{items}</ol>", "word"), 3000)
    numbers = re.findall(r"<li>항목 (\d+) ", found)
    assert cut and numbers == [str(i) for i in range(1, len(numbers) + 1)] and len(numbers) > 10
    assert found.count("</ol>") == 1 and shown.startswith("1. 항목 1 ")


def test_what_cannot_be_cut_is_refused_and_the_cutting_always_ends():
    # One element longer than a whole document cannot be cut.
    with pytest.raises(UserError, match="긴 항목"):
        first_page(page("그림", f'<p><img src="{PNG}" alt="{"가" * 5000}"></p>', "word"), 3000)
    with pytest.raises(UserError, match="구조"):
        first_page("<html><body><p>" + "가" * 5000 + "</p></body></html>", 3000)
    # Elements estimated at nothing (<hr> becomes ---) once let the first try take all of them, and it never ended.
    finished = []
    worker = threading.Thread(
        target=lambda: finished.append(first_page(page("선", "<hr>" * 3000, "word"), 2000)), daemon=True
    )
    worker.start()
    worker.join(30)
    assert finished and not worker.is_alive()
    _, text, cut = finished[0]
    assert cut and len(text) <= 2000 and 100 < text.count("---") < 3000


# --- The parts of earlier versions -----------------------------------------------------------------------------------


def test_the_parts_of_a_markdown_page_are_one_text_again():
    """As earlier versions cut a page: a table's header and a code block's fences repeated in the part that went on
    with them, each part without the blank lines around it."""
    found = [
        "# 제목\n\n| 키 | 값 |\n| --- | --- |\n| 1 | 가 |\n| 2 | 나 |",
        "| 키 | 값 |\n| --- | --- |\n| 3 | 다 |\n\n```python\nx = 1\n```",
        "```python\nx = 2\n```\n\n문단 하나",
        "## 다음 절\n\n문단 둘",
    ]
    assert join_markdown(found) == (
        "# 제목\n\n| 키 | 값 |\n| --- | --- |\n| 1 | 가 |\n| 2 | 나 |\n| 3 | 다 |\n\n"
        "```python\nx = 1\nx = 2\n```\n\n문단 하나\n\n## 다음 절\n\n문단 둘"
    )
    # Another table, or other code, that happens to follow is not taken for the one before.
    assert join_markdown(["| 가 | 나 |\n| --- | --- |\n| 1 | 2 |", "| 다 | 라 |\n| --- | --- |\n| 3 | 4 |"]) == (
        "| 가 | 나 |\n| --- | --- |\n| 1 | 2 |\n\n| 다 | 라 |\n| --- | --- |\n| 3 | 4 |"
    )
    assert join_markdown(["```\n가\n```", "```js\n나\n```"]) == "```\n가\n```\n\n```js\n나\n```"
    assert join_markdown(["하나뿐인 부분"]) == "하나뿐인 부분" and join_markdown([]) == ""


def test_the_pages_of_a_files_parts_are_one_page_again():
    first = page("보고서 & 계획 (1/2)", "<h1>장 1</h1><p>문단 1</p>", "word", "font-size:10pt")
    second = page("보고서 & 계획 (2/2)", f'<h1>장 2</h1><p><img src="{PNG}" alt="그림"></p>', "word", "font-size:10pt")
    whole = join_pages([first, second], "보고서 & 계획")
    assert whole == page(
        "보고서 & 계획",
        f'<h1>장 1</h1><p>문단 1</p><h1>장 2</h1><p><img src="{PNG}" alt="그림"></p>',
        "word",
        "font-size:10pt",
    )
    # Pages that are not the app's own still become one page with all they show.
    odd = join_pages(["<html><body><p>하나</p></body></html>", "<p>둘</p>"], "이상한 문서")
    assert "<title>이상한 문서</title>" in odd and "<p>하나</p><p>둘</p>" in odd


# A sheet as earlier versions cut it between parts: each part that went on opened the sheet's section again with its
# heading and " (계속)", and its table with the columns and header (parts.repeated).
COLUMNS = '<colgroup><col style="width:40pt"><col></colgroup><thead><tr><th></th><th>A</th></tr></thead>'
SHEET = '<section class="sheet"><h2>매출 &amp; 비용</h2><table class="cells">' + COLUMNS
GOES_ON = '<section class="sheet"><h2>매출 &amp; 비용 (계속)</h2><table class="cells">' + COLUMNS
MEMO = '<section class="sheet"><h2>메모</h2><p>끝</p></section>'


def rows(*numbers):
    return "".join(f"<tr><th>{number}</th><td>값 {number}</td></tr>" for number in numbers)


def sheet_pages():
    """The three parts of a workbook's page, and the page they make whole (as the parser writes it again)."""
    pages = [
        page("매출 (1/3)", f"{SHEET}<tbody>{rows(1, 2)}</tbody></table></section>", "excel"),
        page("매출 (2/3)", f"{GOES_ON}<tbody>{rows(3, 4)}</tbody></table></section>", "excel"),
        page("매출 (3/3)", f"{GOES_ON}<tbody>{rows(5)}</tbody></table></section>{MEMO}", "excel"),
    ]
    # The parser writes the columns again as <col/>.
    columns = '<colgroup><col style="width:40pt"/><col/></colgroup><thead><tr><th></th><th>A</th></tr></thead>'
    whole = page(
        "매출",
        f'<section class="sheet"><h2>매출 &amp; 비용</h2><table class="cells">{columns}<tbody>{rows(1, 2, 3, 4, 5)}'
        f"</tbody></table></section>{MEMO}",
        "excel",
    )
    return pages, whole


def test_what_an_earlier_version_repeated_where_it_cut_a_table_or_a_section_goes():
    """As earlier versions cut a long page: the part that went on opened the section again with its heading and
    " (계속)", and the table with its columns and header, or a table of Word's with its header row, and a numbered list
    numbered on. The page made one shows each once, as the page was before it was cut."""
    pages, whole = sheet_pages()
    assert join_pages(pages, "매출") == whole
    word = '<table style="border-collapse:collapse">'
    header = "<tr><th>키</th><th>값</th></tr>"
    first = page(
        "문서 (1/2)", f"<p>소개</p>{word}<tbody>{header}<tr><td>1</td><td>가</td></tr></tbody></table>", "word"
    )
    second = page(
        "문서 (2/2)", f"{word}{header}<tbody><tr><td>2</td><td>나</td></tr></tbody></table><p>맺음</p>", "word"
    )
    assert join_pages([first, second], "문서") == page(
        "문서",
        f"<p>소개</p>{word}<tbody>{header}<tr><td>1</td><td>가</td></tr><tr><td>2</td><td>나</td></tr></tbody></table>"
        "<p>맺음</p>",
        "word",
    )
    listed = [page("목록 (1/2)", "<ol><li>하나</li><li>둘</li></ol>", "word")]
    listed.append(page("목록 (2/2)", '<ol start="3"><li>셋</li></ol>', "word"))
    assert join_pages(listed, "목록") == page("목록", "<ol><li>하나</li><li>둘</li><li>셋</li></ol>", "word")
    # What only looks alike is left as the parts have it: another table that follows (without the header repeated, or
    # of other attributes), a section of another heading, a list numbered otherwise, and a part that begins with text.
    for following in (
        f"{word}<tbody><tr><td>2</td><td>나</td></tr></tbody></table>",
        f'<table style="width:100%">{header}<tbody><tr><td>2</td><td>나</td></tr></tbody></table>',
        "글로 시작하는 부분" + f"{word}{header}<tbody><tr><td>2</td></tr></tbody></table>",
    ):
        joined = join_pages([first, page("문서 (2/2)", following, "word")], "문서")
        assert joined == page(
            "문서", f"<p>소개</p>{word}<tbody>{header}<tr><td>1</td><td>가</td></tr></tbody></table>{following}", "word"
        )
    joined = join_pages([pages[0], page("매출 (2/2)", MEMO, "excel")], "매출")
    assert joined == page("매출", f"{SHEET}<tbody>{rows(1, 2)}</tbody></table></section>{MEMO}", "excel")
    other = page("목록 (2/2)", '<ol start="7"><li>일곱</li></ol>', "word")
    assert join_pages([listed[0], other], "목록") == page(
        "목록", '<ol><li>하나</li><li>둘</li></ol><ol start="7"><li>일곱</li></ol>', "word"
    )


def test_the_title_of_a_part_gives_the_title_of_its_import():
    assert whole_title("보고서 (2/5)", 2, 5) == "보고서"
    assert whole_title("보고서 (2/5)", 3, 5) == "보고서 (2/5)"  # not the number this part had
    assert whole_title("보고서 (1/1)", 1, 1) == "보고서 (1/1)"  # a title the user wrote so
    assert whole_title("가" * 191 + " (12/100)", 12, 100) == "가" * 191


def legacy(service, table, values, texts, title="설계서", pages=None, stamp="2026-09-01T00:00:00+00:00"):
    """A long import as an earlier version saved it: a document per text titled "<title> (n/m)" with a source row
    numbered so, and its ids. `values` are the source's own columns."""
    ids = []
    with service.db.connect() as db:
        for number, text in enumerate(texts, 1):
            ident, name = f"{title}-{number}", f"{title} ({number}/{len(texts)})"
            db.execute(
                "INSERT INTO documents(id,title,content,format,summary,status,index_mode,created_at,updated_at) "
                "VALUES(?,?,?,?,'요약','ready','wiki',?,?)",
                (ident, name, text, "html" if pages else "markdown", stamp, stamp[:18] + str(number) + stamp[19:]),
            )
            row = {
                "doc_id": ident,
                **values,
                "markdown": text,
                "imported_at": stamp,
                "part": number,
                "parts": len(texts),
                "title": name,
            }
            db.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
            if pages:
                db.execute("INSERT INTO document_html(doc_id,html) VALUES(?,?)", (ident, pages[number - 1]))
            db.execute("INSERT INTO keywords VALUES (?, ?)", (ident, f"키워드{number}"))
            ids.append(ident)
        from agent.db import index_text

        index_text(db, ids)
    return ids


def page_values(checksum="hash-1"):
    return {"source_url": URL, "page_id": "123", "source_hash": checksum}


def test_the_documents_of_a_long_import_of_an_earlier_version_become_one(tmp_path):
    path = tmp_path / "wiki.db"
    service = Service(Database(path), None)
    texts = [
        "# 설계\n\n| 키 | 값 |\n| --- | --- |\n| 1 | 가 |",
        "| 키 | 값 |\n| --- | --- |\n| 2 | 나 |",
        "## 끝\n\n맺음말",
    ]
    first, second, third = legacy(service, "confluence_sources", page_values(), texts)
    other = service.wiki_save({"title": "메모", "content": "직접 쓴 글"})["id"]
    task = service.dispatch("tasks.save", {"title": "둘째 부분 확인", "source_id": second})
    # The backend's next start makes them one document: the first part's, with the texts of all in their order.
    service = Service(Database(path), None)
    assert {doc["id"] for doc in service.wiki_list({})} == {first, other}
    doc = service.wiki_get({"id": first})
    whole = "# 설계\n\n| 키 | 값 |\n| --- | --- |\n| 1 | 가 |\n| 2 | 나 |\n\n## 끝\n\n맺음말"
    assert (doc["title"], doc["content"], doc["format"]) == ("설계서", whole, "markdown")
    assert doc["source"] == {
        "kind": "confluence",
        "source_url": URL,
        "page_id": "123",
        "imported_at": "2026-09-01T00:00:00+00:00",
        "edited": False,
    }
    assert service.wiki_source_markdown({"id": first})["markdown"] == whole
    # The analysis was of each part: the document is analyzed again as one.
    assert (doc["status"], doc["summary"], doc["keywords"], doc["index_mode"], doc["revision"]) == (
        "pending",
        "",
        [],
        "",
        2,
    )
    assert (doc["created_at"], doc["updated_at"]) == ("2026-09-01T00:00:00+00:00", "2026-09-01T00:00:03+00:00")
    # What named another part finds the document: a link or citation by its id, a task by its source.
    assert service.wiki_get({"id": second})["id"] == service.wiki_get({"id": third})["id"] == first
    assert [item["source_id"] for item in service.dispatch("tasks.list", {}) if item["id"] == task["id"]] == [first]
    assert service.dispatch("wiki.search", {"query": "맺음말", "mode": "word"})["documents"][0]["id"] == first
    with service.db.connect() as db:
        assert [tuple(row) for row in db.execute("SELECT doc_id,part,parts,title FROM confluence_sources")] == [
            (first, 1, 1, "설계서")
        ]
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 2
        assert sorted(tuple(row) for row in db.execute("SELECT id,doc_id FROM merged_documents")) == [
            (second, first),
            (third, first),
        ]
        assert parts.parted(db, "confluence_sources") is None
    # Opening the Wiki again changes nothing, and the page as it was imported is known as the same.
    service = Service(Database(path), None)
    assert service.wiki_get({"id": first})["revision"] == 2
    capture = {
        "source_url": URL,
        "page_id": "123",
        "html": "<html><head><title>설계서</title></head><body></body></html>",
    }
    with service.db.connect() as db:
        assert sources.held(db, "confluence_sources", page_values())["doc_id"] == first
    # Its next change updates the one document.
    capture["html"] = capture["html"].replace("<body>", "<body><p>새 본문</p>")
    assert import_page(service, capture) == {
        "status": "updated",
        "doc_id": first,
        "message": "원본이 바뀌어 문서를 새 내용으로 업데이트했습니다.",
    }
    assert service.wiki_get({"id": third})["content"] == "새 본문"
    # Text meant for a part neither replaces the whole document nor becomes a document under the part's id.
    with pytest.raises(UserError, match="한 문서로 합쳐졌습니다"):
        service.wiki_save({"id": second, "title": "설계서 (2/3)", "content": "둘째 부분만 고침"})
    assert service.wiki_get({"id": first})["content"] == "새 본문" and len(service.wiki_list({})) == 2


def test_a_part_the_user_changed_renamed_or_deleted_stays_so(tmp_path):
    path = tmp_path / "wiki.db"
    service = Service(Database(path), None)
    first, second, third = legacy(service, "confluence_sources", page_values(), ["첫 부분", "둘째 부분", "셋째 부분"])
    with service.db.connect() as db:  # As the earlier version left them: a new title, a changed text, one in the trash.
        db.execute("UPDATE documents SET title='내가 붙인 제목' WHERE id=?", (first,))
        db.execute("UPDATE documents SET content='고친 셋째 부분', revision=2 WHERE id=?", (third,))
        db.execute("UPDATE documents SET deleted_at='2026-11-01T00:00:00+00:00' WHERE id=?", (second,))
    service = Service(Database(path), None)
    doc = service.wiki_get({"id": first})
    # The title the user gave stays; the text they changed stays changed, beside the text imported.
    assert (doc["title"], doc["content"], doc["source"]["edited"]) == (
        "내가 붙인 제목",
        "첫 부분\n\n고친 셋째 부분",
        True,
    )
    assert service.wiki_source_markdown({"id": first})["markdown"] == "첫 부분\n\n셋째 부분"
    # The part in the trash was not brought back: it waits there as a document of its own, without a source.
    trash = service.dispatch("wiki.trash", {})["documents"]
    assert [(item["id"], item["title"], item["source"]) for item in trash] == [(second, "설계서 (2/3)", None)]
    assert service.dispatch("wiki.restore", {"ids": [second]}) == {"restored": 1}
    assert service.wiki_get({"id": second})["content"] == "둘째 부분"
    assert {entry["id"] for entry in service.wiki_list({})} == {first, second}
    # Parts that are all in the trash are one document there.
    ids = legacy(
        service,
        "jira_sources",
        {"source_url": "https://jira.test/browse/A-1", "issue_key": "A-1", "source_hash": "h"},
        ["가", "나"],
        "티켓",
    )
    for ident in ids:
        service.wiki_delete({"id": ident})
    service = Service(Database(path), None)
    trash = service.dispatch("wiki.trash", {})["documents"]
    assert [(item["id"], item["title"], item["source"]["issue_key"]) for item in trash] == [(ids[0], "티켓", "A-1")]
    service.dispatch("wiki.restore", {"ids": [ids[0]]})
    assert service.wiki_get({"id": ids[1]})["content"] == "가\n\n나"


def test_the_parts_of_a_file_become_one_html_document(tmp_path):
    path = tmp_path / "wiki.db"
    service = Service(Database(path), None)
    bodies = ["<h1>장 1</h1><p>문단 1</p>", "<h1>장 2</h1><p>문단 2</p>", "<h1>장 3</h1><p>문단 3</p>"]
    pages = [page(f"보고서 ({number}/3)", body, "word") for number, body in enumerate(bodies, 1)]
    values = {"source_path": "/문서/보고서.rtf", "source_hash": "bytes-1", "source_size": 10, "source_mtime": 20}
    first, second, third = legacy(service, "rtf_sources", values, [page_text(item) for item in pages], "보고서", pages)
    with service.db.connect() as db:
        db.execute(
            """INSERT INTO writeback_backups(id,source_path,doc_id,name,checksum,size,created_at)
            VALUES ('copy-1','/문서/보고서.rtf',?,'copy','hash',1,'2026-01-01')""",
            (third,),
        )
    service = Service(Database(path), None)
    doc = service.wiki_get({"id": second})
    assert (doc["id"], doc["title"], doc["format"]) == (first, "보고서", "html")
    assert doc["html"] == page("보고서", "".join(bodies), "word")
    assert doc["content"] == "# 장 1\n\n문단 1\n\n# 장 2\n\n문단 2\n\n# 장 3\n\n문단 3" == page_text(doc["html"])
    assert doc["source"]["source_path"] == "/문서/보고서.rtf" and doc["source"]["edited"] is False
    with service.db.connect() as db:
        assert db.execute("SELECT doc_id FROM writeback_backups").fetchone()[0] == first
        assert db.execute("SELECT count(*) FROM document_html").fetchone()[0] == 1
    # A part whose text the user changed makes the document the Markdown they wrote; the page stays its original.
    values = {"source_path": "/문서/회의.rtf", "source_hash": "bytes-2"}
    pages = [page(f"회의 ({number}/2)", f"<p>회의 {number}</p>", "word") for number in (1, 2)]
    ids = legacy(service, "rtf_sources", values, ["회의 1", "회의 2"], "회의", pages)
    with service.db.connect() as db:  # As the earlier version left a part whose text the user changed.
        db.execute("UPDATE documents SET content='고친 회의 2', format='markdown', revision=2 WHERE id=?", (ids[1],))
    service = Service(Database(path), None)
    doc = service.wiki_get({"id": ids[0]})
    assert (doc["format"], doc["content"], doc["has_html"]) == ("markdown", "회의 1\n\n고친 회의 2", True)
    assert service.wiki_source_html({"id": ids[0]})["html"] == page("회의", "<p>회의 1</p><p>회의 2</p>", "word")


def test_the_parts_of_a_sheet_become_one_page_without_what_they_repeated(tmp_path):
    path = tmp_path / "wiki.db"
    service = Service(Database(path), None)
    pages, whole = sheet_pages()
    values = {"source_path": "/문서/매출.xlsx", "source_hash": "bytes-4", "application": "Excel"}
    texts = [page_text(item) for item in pages]
    assert "(계속)" in texts[1] and texts[1].count("| --- |") == 1  # As the parts were saved.
    first, *_ = legacy(service, "office_sources", values, texts, "매출", pages)
    service = Service(Database(path), None)
    doc = service.wiki_get({"id": first})
    # The page shows the sheet once; the text is the page's, so search and the AI read no repeated header either.
    assert (doc["title"], doc["format"], doc["html"]) == ("매출", "html", whole)
    assert doc["content"] == page_text(whole) and "(계속)" not in doc["content"]
    assert doc["content"].count("| --- |") == 1 and doc["source"]["edited"] is False
    assert service.wiki_source_markdown({"id": first})["markdown"] == doc["content"]
    # A part whose text the user changed keeps their text in the document; the page is made one all the same.
    pages, whole = sheet_pages()
    values = {"source_path": "/문서/비용.xlsx", "source_hash": "bytes-5", "application": "Excel"}
    ids = legacy(service, "office_sources", values, [page_text(item) for item in pages], "비용", pages)
    with service.db.connect() as db:
        db.execute("UPDATE documents SET content='고친 둘째 부분', format='markdown', revision=2 WHERE id=?", (ids[1],))
    service = Service(Database(path), None)
    doc = service.wiki_get({"id": ids[0]})
    assert (doc["format"], doc["source"]["edited"]) == ("markdown", True)
    assert doc["content"] == "\n\n".join([page_text(pages[0]), "고친 둘째 부분", page_text(pages[2])])
    assert service.wiki_source_html({"id": ids[0]})["html"] == whole.replace(
        "<title>매출</title>", "<title>비용</title>"
    )


def test_a_part_left_alone_only_loses_its_number(tmp_path):
    path = tmp_path / "wiki.db"
    service = Service(Database(path), None)
    first, second = legacy(service, "confluence_sources", page_values(), ["첫 부분", "둘째 부분"])
    service.wiki_delete({"id": first})
    service.wiki_purge({"ids": [first]})
    service = Service(Database(path), None)
    doc = service.wiki_get({"id": second})
    # Its text and its analysis are what they were.
    assert (doc["title"], doc["content"], doc["summary"], doc["keywords"]) == (
        "설계서",
        "둘째 부분",
        "요약",
        ["키워드2"],
    )
    assert (doc["status"], doc["revision"]) == ("ready", 2)
    with service.db.connect() as db:
        assert tuple(db.execute("SELECT part,parts,title FROM confluence_sources").fetchone()) == (1, 1, "설계서")


def test_a_start_merges_for_as_long_as_its_time_allows_and_reading_an_import_merges_it_sooner(tmp_path, monkeypatch):
    path = tmp_path / "wiki.db"
    service = Service(Database(path), None)
    ids = {
        number: legacy(service, "confluence_sources", page_values(f"hash-{number}"), ["앞", "뒤"], f"문서{number}")
        for number in range(4)
    }
    # Out of time after each import: one is merged a look, and the look says that more are left.
    assert merge_all(service.db.connect, budget=0) is False
    assert len(service.wiki_list({})) == 7
    # A start that ran out of time leaves the rest to its thread; meanwhile whatever reads an import merges it first.
    monkeypatch.setattr(Database, "_merge", lambda self, budget=None: True)
    service = Service(Database(path), None)
    assert len(service.wiki_list({})) == 7
    opened = service.wiki_get({"id": ids[3][1]})
    assert (opened["id"], opened["title"], opened["content"]) == (ids[3][0], "문서3", "앞\n\n뒤")
    with service.db.connect() as db:
        assert sources.held(db, "confluence_sources", page_values("hash-2"))["doc_id"] == ids[2][0]
    assert len(service.wiki_list({})) == 5
    monkeypatch.undo()
    assert merge_all(service.db.connect) is True
    assert sorted(doc["title"] for doc in service.wiki_list({})) == ["문서0", "문서1", "문서2", "문서3"]


def test_an_import_that_cannot_be_merged_does_not_keep_the_wiki_from_opening(tmp_path, monkeypatch):
    path = tmp_path / "wiki.db"
    service = Service(Database(path), None)
    legacy(service, "confluence_sources", page_values(), ["앞", "뒤"])

    def broken(db, table, key):
        raise ValueError("unexpected rows")

    monkeypatch.setattr(parts, "merge", broken)
    service = Service(Database(path), None)
    assert len(service.wiki_list({})) == 2


def test_sources_of_before_parts_are_read_as_they_are(tmp_path):
    """The source tables of the first versions had no part numbers: they gain the columns, as part 1 of 1."""
    path = tmp_path / "wiki.db"
    service = Service(Database(path), None)
    rtf, ticket = (service.wiki_save({"title": title, "content": "본문"}) for title in ("문서", "티켓"))
    with service.db.connect() as db:
        db.execute("DROP TABLE rtf_sources")
        db.execute("DROP TABLE jira_sources")
        db.execute(
            "CREATE TABLE rtf_sources (doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE, "
            "source_path TEXT NOT NULL, source_hash TEXT NOT NULL UNIQUE, markdown TEXT NOT NULL, "
            "imported_at TEXT NOT NULL)"
        )
        db.execute(
            "CREATE TABLE jira_sources (doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE, "
            "source_url TEXT NOT NULL, issue_key TEXT NOT NULL, source_hash TEXT NOT NULL, markdown TEXT NOT NULL, "
            "imported_at TEXT NOT NULL, UNIQUE(source_url, source_hash))"
        )
        db.execute("INSERT INTO rtf_sources VALUES (?,?,?,?,?)", (rtf["id"], "/a.rtf", "hash", "본문", "2026-09-01"))
        db.execute(
            "INSERT INTO jira_sources VALUES (?,?,?,?,?,?)",
            (ticket["id"], "https://jira.test/browse/A-1", "A-1", "hash", "본문", "2026-09-01"),
        )
    upgraded = Service(Database(path), None)
    doc = upgraded.wiki_get({"id": rtf["id"]})
    assert doc["source"] == {"kind": "rtf", "source_path": "/a.rtf", "imported_at": "2026-09-01", "edited": False}
    assert (doc["title"], doc["revision"]) == ("문서", rtf["revision"]) and "parts" not in doc
    assert upgraded.wiki_get({"id": ticket["id"]})["source"]["issue_key"] == "A-1"
    with upgraded.db.connect() as db:
        assert tuple(db.execute("SELECT part,parts FROM rtf_sources").fetchone()) == (1, 1)
