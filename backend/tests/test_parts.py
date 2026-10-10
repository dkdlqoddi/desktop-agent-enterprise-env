"""Imports too long for one Wiki document are saved as several: where the text is cut, what a part repeats, and how
the sources of an earlier version become part 1 of 1."""

import re
import sqlite3
import threading
from html import escape, unescape

import pytest

from agent.db import Database
from agent.html_document import page, page_text
from agent.llm import UserError
from agent.parts import CUT, blocks, joined, part_title, split_markdown, split_page
from agent.service import Service

PNG = "data:image/png;base64,iVBORw0KGgo="


def test_part_titles_fit_a_title():
    assert part_title("보고서", 1, 1) == "보고서"
    assert part_title("보고서", 2, 5) == "보고서 (2/5)"
    assert part_title("가" * 200, 12, 100) == "가" * 191 + " (12/100)"


def test_markdown_is_cut_between_blocks_and_a_heading_goes_on_with_its_text():
    text = "\n\n".join(f"## 섹션 {i}\n\n" + "가" * 300 for i in range(12))
    found, cut = split_markdown(text, 1000, 50)
    assert not cut and len(found) == 4 and all(len(part) <= 1000 for part in found)
    assert all(part.startswith("## 섹션") and not part.splitlines()[-1].startswith("#") for part in found)
    assert re.findall(r"섹션 \d+", "".join(found)) == [f"섹션 {i}" for i in range(12)]


def test_a_code_block_a_table_and_a_line_too_long_for_one_part_are_cut_inside():
    code = "```python\n" + "".join(f"x = {i}\n" for i in range(400)) + "```"
    table = "| 키 | 값 |\n| --- | --- |\n" + "".join(f"| {i} | 값 {i} |\n" for i in range(150))
    found, cut = split_markdown(f"# 제목\n\n{code}\n\n{table}\n" + "가" * 2500, 1000, 50)
    assert not cut and all(len(part) <= 1000 for part in found)
    codes = [part for part in found if "x = " in part]
    # Every piece of the code block is a code block of its own, and every piece of the table repeats its header.
    assert len(codes) > 2 and all(part.count("```") == 2 and "```python\n" in part for part in codes)
    assert re.findall(r"x = (\d+)", "".join(codes)) == [str(i) for i in range(400)]
    tables = [part for part in found if "| 키 | 값 |" in part]
    assert len(tables) > 2 and all("| 키 | 값 |\n| --- | --- |\n" in part for part in tables)
    assert re.findall(r"^\| (\d+) \|", "\n".join(tables), re.M) == [str(i) for i in range(150)]
    assert "".join(part for part in found if part.startswith("가")) == "가" * 2500


def test_a_page_is_cut_between_its_elements_and_each_part_is_a_page_of_its_own():
    body = "".join(
        f"<h1>장 {i}</h1>"
        + "".join(f"<p>문단 {i}-{j} {'설명 ' * 40}</p>" for j in range(4))
        + f'<p><img src="{PNG}" alt="그림 {i}"></p>'
        for i in range(12)
    )
    pages, cut = split_page(page("보고서 & 계획", body, "word", "font-size:10pt"), 3000, 50)
    assert not cut and len(pages) == 3
    for number, (part, text) in enumerate(pages, 1):
        assert page_text(part) == text and len(text) <= 3000
        assert f"<title>보고서 &amp; 계획 ({number}/{len(pages)})</title>" in part
        assert '<div class="doc word" style="font-size:10pt">' in part and part.endswith("</div></body></html>")
        # A heading goes on to the next part with its text rather than end a part.
        assert not text.splitlines()[-1].startswith("#")
    texts = "".join(text for _, text in pages)
    assert re.findall(r"문단 (\d+-\d+)", texts) == [f"{i}-{j}" for i in range(12) for j in range(4)]
    # Each picture comes back in the part that shows it.
    assert "".join(part for part, _ in pages).count(PNG) == 12 and "private-agent-picture" not in "".join(
        part for part, _ in pages
    )


def test_a_long_table_goes_on_with_its_section_heading_columns_and_header_row():
    rows = "".join(
        f'<tr><th>{r}</th><td>값 {r}</td><td data-formula="=B{r}*2">{r * 2}</td></tr>' for r in range(1, 301)
    )
    body = (
        '<section class="sheet"><h2>매출</h2><table class="cells"><colgroup><col><col style="width:40pt"><col>'
        f"</colgroup><tr><th></th><th>B</th><th>C</th></tr>{rows}</table></section>"
    )
    pages, cut = split_page(page("매출", body, "excel"), 4000, 50)
    assert not cut and len(pages) > 2
    for number, (part, text) in enumerate(pages, 1):
        assert text.startswith(
            "## 매출" + (" (계속)" if number > 1 else "") + "\n\n|  | B | C |\n| --- | --- | --- |\n"
        )
        assert part.count("width:40pt") == 1 and part.count('<section class="sheet">') == 1
    assert re.findall(r"^\| (\d+) \|", "\n".join(text for _, text in pages), re.M) == [str(r) for r in range(1, 301)]


def test_a_long_text_is_cut_between_its_lines_and_a_numbered_list_keeps_its_numbers():
    text = "".join(f"{i}번째 줄 <태그> & 기호\n" for i in range(2000))
    pages, cut = split_page(page("로그", f"<pre>{escape(text, quote=False)}</pre>", "text"), 5000, 50)
    assert not cut and len(pages) > 5 and all(len(found) <= 5000 for _, found in pages)
    assert "".join(unescape(re.search(r"<pre>(.*?)</pre>", part, re.S).group(1)) for part, _ in pages) == text
    items = "".join(f"<li>항목 {i} {'내용 ' * 30}</li>" for i in range(1, 61))
    pages, _ = split_page(page("목록", f"<ol>{items}</ol>", "word"), 3000, 50)
    first = re.search(r"<li>항목 (\d+)", pages[1][0]).group(1)
    assert f'<ol start="{first}">' in pages[1][0] and pages[1][1].startswith(f"{first}. 항목 {first}")


def test_parts_beyond_the_most_are_left_out_and_the_last_part_says_so():
    pages, cut = split_page(page("긴 글", "".join(f"<p>{'글 ' * 200}</p>" for _ in range(50)), "word"), 2000, 3)
    assert cut and len(pages) == 3
    assert pages[-1][0].endswith(f'<p class="caption">{CUT}</p></div></body></html>')
    assert pages[-1][1].endswith(CUT) and len(pages[-1][1]) <= 2000
    texts, cut = split_markdown("\n\n".join("글 " * 200 for _ in range(50)), 2000, 3)
    assert cut and len(texts) == 3 and texts[-1].endswith("> " + CUT) and len(texts[-1]) <= 2000
    # One element longer than a whole part cannot be cut.
    with pytest.raises(UserError, match="긴 항목"):
        split_page(page("그림", f'<p><img src="{PNG}" alt="{"가" * 5000}"></p>', "word"), 3000, 50)


def test_a_fence_or_header_too_long_to_repeat_stays_with_the_text():
    """A code block's opening line or a table's header longer than half a part is not repeated in every part, and it
    is not lost either: it is cut like any long line."""
    code = "```" + "a" * 597 + "\n" + "".join(f"code {i}\n" for i in range(200)) + "```"
    table = "| " + "h" * 596 + " |\n| --- |\n" + "".join(f"| {i} |\n" for i in range(300))
    for block, letter, count in ((code, "a", 597), (table, "h", 596)):
        found, _ = split_markdown(f"{block}\n\n끝", 1000, 50)
        assert all(len(part) <= 1000 for part in found)
        assert "".join(found).count(letter) == count and found[-1].endswith("끝")
    # A block of one line, a fence that never closes, is cut too, not dropped.
    found, _ = split_markdown("```" + "b" * 2000, 1000, 50)
    assert found and "".join(found).count("b") == 2000


def test_code_blocks_end_at_their_closing_fence_whatever_the_line_breaks():
    assert [kind for kind, _ in blocks("```\r\ncode\r\n```\r\n문단\r\n")] == ["code", "text"]


def test_a_page_of_elements_without_text_is_still_cut_into_parts():
    """Elements estimated at nothing (<hr> becomes ---) once let a part take all of them; the cutting never ended."""
    finished = []
    worker = threading.Thread(
        target=lambda: finished.append(split_page(page("선", "<hr>" * 3000, "word"), 2000, 50)), daemon=True
    )
    worker.start()
    worker.join(30)
    assert finished and not worker.is_alive()
    pages, cut = finished[0]
    assert not cut and len(pages) > 3 and all(len(text) <= 2000 for _, text in pages)
    assert sum(text.count("---") for _, text in pages) == 3000


def test_the_parts_of_a_page_joined_again_repeat_no_header():
    table = "| 키 | 값 |\n| --- | --- |\n" + "".join(f"| {i} | 값 {i} |\n" for i in range(150))
    code = "```python\n" + "".join(f"x = {i}\n" for i in range(400)) + "```"
    text = f"# 제목\n\n{table}\n{code}\n\n끝"
    found, _ = split_markdown(text, 1000, 50)
    assert len(found) > 5
    lines = [line for line in joined(found).splitlines() if line.strip()]
    assert lines == [line for line in text.splitlines() if line.strip()]


def test_sources_of_an_earlier_version_become_part_1_of_1(tmp_path):
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
    assert doc["source"] == {
        "kind": "rtf",
        "source_path": "/a.rtf",
        "imported_at": "2026-09-01",
        "part": 1,
        "parts": 1,
        "edited": False,
    }
    assert doc["parts"] == []
    assert upgraded.wiki_get({"id": ticket["id"]})["source"]["issue_key"] == "A-1"
    other = upgraded.wiki_save({"title": "둘째", "content": "본문"})
    with upgraded.db.connect() as db:
        # The same file may now have a row for each of its parts, but only one for each.
        db.execute(
            "INSERT INTO rtf_sources(doc_id,source_path,source_hash,markdown,imported_at,part,parts) "
            "VALUES (?,?,?,?,?,2,2)",
            (other["id"], "/a.rtf", "hash", "본문", "2026-09-01"),
        )
    with pytest.raises(sqlite3.IntegrityError), upgraded.db.connect() as db:
        db.execute(
            "INSERT INTO rtf_sources(doc_id,source_path,source_hash,markdown,imported_at,part,parts) "
            "VALUES (?,?,?,?,?,2,2)",
            (ticket["id"], "/a.rtf", "hash", "본문", "2026-09-01"),
        )
    # Opening the upgraded database again changes nothing.
    again = Service(Database(path), None)
    assert [part["id"] for part in again.wiki_get({"id": other["id"]})["parts"]] == [rtf["id"], other["id"]]
