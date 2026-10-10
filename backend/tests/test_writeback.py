"""Writing a document's changes back into its Office file: the changes worked out from the texts (source_edits.py),
found and made in the file's own text through stand-ins of the Office object models (office_write.py), the hidden
application's session, and the job that backs the file up, writes it and reads it again (writeback.py).
Windows runs the real Office (PRIVATE_AGENT_OFFICE_TEST=1)."""

import contextlib
import os
import re
import sys
import threading
import time
import zipfile
from pathlib import Path
from random import Random
from types import SimpleNamespace as NS

import pytest

from agent import office_write, source_edits
from agent.db import Database
from agent.html_document import index_markdown, page
from agent.llm import UserError
from agent.office_import import OfficeImporter
from agent.office_write import CANCELLED, edit_excel, edit_powerpoint, edit_visio, edit_word, word_units, write_word
from agent.service import Service
from agent.source_edits import Changed, Missing, changes, excel_changes, plain, units
from agent.writeback import BACKUPS, EDITED_MEANWHILE, checksum, classify, count, plan_of, whole
from test_rtf_import import FakeLLM, fake_modules

# --- Markdown as text ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "markdown,expected",
    [
        (r"첫 문단 **굵게** 그리고 *기울임*\.", "첫 문단 굵게 그리고 기울임."),
        (
            r"\[괄호\] 1\. 숫자 \# 해시 \+ 플러스 \- 마이너스 \& 앰퍼샌드 \<태그\>",
            "[괄호] 1. 숫자 # 해시 + 플러스 - 마이너스 & 앰퍼샌드 <태그>",
        ),
        (
            "링크 [여기](https://x.test/a) 와 ![그림 설명](data:,x) 그리고 <https://y.test>",
            "링크 여기 와 그림 설명 그리고 https://y.test",
        ),
        ("코드 `a*b*c` 와 ``x`y``", "코드 a*b*c 와 x`y"),
        ("5 * 3 = 15, snake_case_name, ~~지움~~, __밑줄 강조__", "5 * 3 = 15, snake_case_name, 지움, 밑줄 강조"),
        ("**굵게 *안에* 기울임**", "굵게 안에 기울임"),
        ("탭과   여러   공백", "탭과 여러 공백"),
    ],
)
def test_markdown_reads_as_the_text_it_shows(markdown, expected):
    assert plain(markdown) == expected


def test_units_of_a_word_page():
    body = (
        '<h1>사업 계획</h1><p>첫 문단</p><p><span style="x">1.\t</span>목록 하나</p><p>줄<br>바꿈</p>'
        "<table><tr><td><p>담당</p></td><td><p>홍길동</p></td></tr></table><p>끝</p>"
    )
    found = units(index_markdown(page("t", body, "word")), "Word")
    assert [(unit.text, unit.kind, unit.joined) for unit in found] == [
        ("사업 계획", "h", False),
        ("첫 문단", "p", False),
        ("1. 목록 하나", "p", False),
        ("줄", "p", False),
        ("바꿈", "p", True),
        ("", "cell", False),
        ("", "cell", False),
        ("담당", "cell", False),
        ("홍길동", "cell", False),
        ("끝", "p", False),
    ]
    assert all(unit.editable and unit.scope == 0 for unit in found)
    assert [(unit.table, unit.row, unit.column) for unit in found if unit.kind == "cell"] == [
        (0, 0, 0),
        (0, 0, 1),
        (0, 1, 0),
        (0, 1, 1),
    ]
    # Lists, quotes and code as a user writes them.
    found = units("- 항목\n  이어서\n\n3. 번호\n\n> 인용\n\n```\n코드 * 그대로\n```", "Word")
    assert [(unit.text, unit.kind) for unit in found] == [
        ("항목", "li"),
        ("이어서", "p"),
        ("번호", "li"),
        ("인용", "p"),
        ("코드 * 그대로", "code"),
    ]


def test_units_of_slides_and_drawings_know_what_the_reader_wrote():
    slides = (
        "## 슬라이드 1 · 개발 계획 2026\n\n본문\n\n### 발표자 노트\n\n노트\n\n## 슬라이드 2 (숨긴 슬라이드)\n\n둘째\n\n"
        "## 슬라이드 2 (숨긴 슬라이드) (계속)\n\n이어짐"
    )
    found = units(slides, "PowerPoint")
    assert [(unit.text, unit.kind, unit.scope, unit.editable) for unit in found] == [
        ("개발 계획 2026", "title", 1, True),
        ("본문", "p", 1, True),
        ("발표자 노트", "h", 1, False),
        ("노트", "p", 1, True),
        ("", "title", 2, True),
        ("둘째", "p", 2, True),
        ("슬라이드 2 (숨긴 슬라이드) (계속)", "h", 2, False),
        ("이어짐", "p", 2, True),
    ]
    drawing = (
        "## 페이지 1 · 구성\n\n### 도형 텍스트\n\n- 서버\n  - 하위\n- 클라이언트\n\n### 연결\n\n- 서버 → 클라이언트\n\n"
        "### 도형 데이터\n\n| 도형 | 항목 | 값 |\n| --- | --- | --- |\n| 서버 | IP | 10.0.0.1 |"
    )
    found = units(drawing, "Visio")
    assert [(unit.text, unit.scope, unit.editable) for unit in found if unit.kind != "cell"] == [
        ("페이지 1 · 구성", 1, False),
        ("도형 텍스트", 1, False),
        ("서버", 1, True),
        ("하위", 1, True),
        ("클라이언트", 1, True),
        ("연결", 1, False),
        ("서버 → 클라이언트", 1, False),
        ("도형 데이터", 1, False),
    ]
    assert all(not unit.editable for unit in found if unit.kind == "cell")


# --- What changed ----------------------------------------------------------------------------------------------------


def edits(old, new, application="Word"):
    return changes(units(old, application), units(new, application), application)


def test_changes_are_the_characters_that_changed_and_the_lines_added_or_deleted():
    old = "# 제목\n\n일정은 3월 15일까지\n\n둘째 문단\n\n셋째 문단"
    assert edits(old, old.replace("3월 15일", "4월 1일")) == [
        {"op": "edit", "unit": 1, "spans": [[4, 5, "4"], [8, 9, ""]]}
    ]
    assert edits(old, old.replace("둘째 문단\n\n", "")) == [{"op": "delete", "unit": 2}]
    assert edits(old, old.replace("둘째 문단", "둘째 문단\n\n새 문단\n\n또 새 문단")) == [
        {"op": "insert", "after": 2, "before": 3, "lines": [["새 문단", "p", False], ["또 새 문단", "p", False]]}
    ]
    assert edits(old, "새 첫 줄\n\n" + old)[0] == {
        "op": "insert",
        "after": -1,
        "before": 0,
        "lines": [["새 첫 줄", "p", False]],
    }
    # A paragraph rewritten and another added beside it: the most alike are paired, so formatting stays.
    found = edits(old, old.replace("둘째 문단", "둘째 문단 고침\n\n덧붙인 문단"))
    assert found == [
        {"op": "edit", "unit": 2, "spans": [[5, 5, " 고침"]]},
        {"op": "insert", "after": 2, "before": 3, "lines": [["덧붙인 문단", "p", False]]},
    ]
    # A line added before one that changed, and another deleted after it: each change goes to its own line, so the
    # changed paragraph (a link in it) is not rewritten as another.
    old = "탭 중간 문단\n\n자세한 내용은 사이트 안내를 보세요.\n\n지울 문단\n\n마지막 문단"
    assert edits(old, "탭 중간 문단\n\n새 문단\n\n자세한 내용은 사이트 소개를 보세요.\n\n마지막 문단") == [
        {"op": "edit", "unit": 1, "spans": [[12, 14, "소개"]]},
        {"op": "delete", "unit": 2},
        {"op": "insert", "after": 0, "before": 1, "lines": [["새 문단", "p", False]]},
    ]
    old = "# 제목\n\n일정은 3월 15일까지\n\n둘째 문단\n\n셋째 문단"
    # Formatting alone and white space are not changes.
    with pytest.raises(UserError, match="고친 내용이 없어"):
        edits(old, old.replace("둘째 문단", "**둘째**   문단").replace("# 제목", "## 제목"))


def test_changes_the_file_cannot_take_are_refused_before_it_opens():
    table = "| 담당 | 이름 |\n| --- | --- |\n| 개발 | 홍길동 |\n| 기획 | 김기획 |"
    with pytest.raises(UserError, match="표의 행이나 열을 지운 것은 원본 파일에 반영할 수 없습니다: ‘기획’"):
        edits(table, table.replace("\n| 기획 | 김기획 |", ""))
    with pytest.raises(UserError, match="표에 행이나 열을 더한 것은"):
        edits(table, table + "\n| 운영 | 박운영 |")
    assert edits(table, table.replace("홍길동", "홍길순")) == [{"op": "edit", "unit": 3, "spans": [[2, 3, "순"]]}]
    slides = "## 슬라이드 1 · 계획\n\n본문\n\n### 발표자 노트\n\n노트"
    with pytest.raises(UserError, match="앱이 만든 줄"):
        edits(slides, slides.replace("발표자 노트", "노트 제목"), "PowerPoint")
    assert edits(slides, slides.replace("· 계획", "· 새 계획"), "PowerPoint") == [
        {"op": "edit", "unit": 0, "spans": [[0, 0, "새 "]]}
    ]
    drawing = "## 페이지 1\n\n### 도형 텍스트\n\n- 서버\n\n### 연결\n\n- 서버 → DB"
    with pytest.raises(UserError, match="앱이 만든 줄"):
        edits(drawing, drawing.replace("→ DB", "→ 캐시"), "Visio")
    with pytest.raises(UserError, match="Visio에 새 도형을 만드는 것은"):
        edits(drawing, drawing.replace("- 서버", "- 서버\n- 새 도형", 1), "Visio")
    assert edits(drawing, drawing.replace("- 서버", "- 웹 서버", 1), "Visio") == [
        {"op": "edit", "unit": 2, "spans": [[0, 0, "웹 "]]}
    ]


# --- Finding the places in the file's text ---------------------------------------------------------------------------


def apply(text, found):
    for _, start, end, new, _ in found:
        text = text[:start] + new + text[end:]
    return text


def word_plan(old, new):
    old_units, new_units = units(old, "Word"), units(new, "Word")
    return {"units": [list(unit) for unit in old_units], "edits": changes(old_units, new_units, "Word")}


WORD_BODY = (
    "<h1>사업 계획</h1><p>첫 문단 <b>굵게</b> 그리고 <i>기울임</i>.</p>"
    '<p><span style="x">1.\t</span>목록 항목 하나</p><p><span style="x">2.\t</span>목록 항목 둘</p>'
    "<p>탭\t중간 그리고   공백</p><p>줄<br>바꿈</p>"
    "<table><tr><td><p>담당자</p></td><td><p>홍길동</p></td></tr>"
    "<tr><td><p>기한</p></td><td><p></p></td></tr></table><p>해당 없음</p><p>마지막 문단</p><p>해당 없음</p>"
)
# The main text Word hands over for that page: list numbers drawn, a tab, a line break, cell and row ends.
WORD_TEXT = (
    "사업 계획\r첫 문단 굵게 그리고 기울임.\r목록 항목 하나\r목록 항목 둘\r탭\t중간 그리고   공백\r줄\v바꿈\r"
    "담당자\r\x07홍길동\r\x07\r\x07기한\r\x07\r\x07\r\x07해당 없음\r마지막 문단\r해당 없음\r"
)


def word_markdown():
    return index_markdown(page("t", WORD_BODY, "word"))


def test_places_are_found_past_list_numbers_tabs_and_twins():
    old = word_markdown()
    new = (
        old.replace("목록 항목 하나", "목록 항목 첫째")
        .replace("탭 중간", "탭 사이")
        .replace("| 기한 |  |", "| 기한 | 10월 |")
        .replace("마지막 문단\n\n해당 없음", "마지막 문단\n\n해당 있음")
        .replace("2\\. 목록 항목 둘", "2\\. 목록 항목 둘\n\n3\\. 셋째 항목")
    )
    found = source_edits.replacements(word_plan(old, new), word_units(WORD_TEXT), ("\r", "\v"), marked_rows=True)
    assert apply(WORD_TEXT, found) == WORD_TEXT.replace("항목 하나", "항목 첫째").replace(
        "탭\t중간", "탭\t사이"
    ).replace("기한\r\x07\r\x07", "기한\r\x0710월\r\x07").replace(
        "마지막 문단\r해당 없음", "마지막 문단\r해당 있음"
    ).replace("목록 항목 둘\r", "목록 항목 둘\r셋째 항목\r")
    # The blank cell is reached from the cell beside it, as Word has no text there to search for: one cell on, in the
    # same row, in the second column.
    blank = next(item for item in found if item[3] == "10월")
    assert blank[4] == (WORD_TEXT.index("기한"), WORD_TEXT.index("기한") + 2, 1, 0, 2)
    # The second "해당 없음" changed: the first stays.
    assert apply(WORD_TEXT, found).count("해당 없음") == 1 and apply(WORD_TEXT, found).index("해당 없음") < apply(
        WORD_TEXT, found
    ).index("마지막")


def test_deleted_lines_take_their_break_along():
    old = word_markdown()
    for removed, expected in [
        ("첫 문단 **굵게** 그리고 *기울임*.\n\n", "첫 문단 굵게 그리고 기울임.\r"),
        ("\n\n해당 없음", "\r해당 없음"),
        ("줄  \n", "줄\v"),
    ]:
        new = old.replace(removed, "") if removed != "\n\n해당 없음" else old[: old.rindex("\n\n해당 없음")]
        found = source_edits.replacements(word_plan(old, new), word_units(WORD_TEXT), ("\r", "\v"), marked_rows=True)
        result = apply(WORD_TEXT, found)
        if removed == "\n\n해당 없음":
            # Word's last paragraph keeps its mark, so the one before keeps its own and its formatting.
            assert result == WORD_TEXT[: WORD_TEXT.rindex("해당 없음")] + "\r"
        else:
            assert result == WORD_TEXT.replace(expected, "", 1)
    # The last line of a paragraph of lines takes the line break before it: the next paragraph stays apart.
    found = source_edits.replacements(
        word_plan(old, old.replace("줄  \n바꿈", "줄")), word_units(WORD_TEXT), ("\r", "\v"), marked_rows=True
    )
    assert apply(WORD_TEXT, found) == WORD_TEXT.replace("줄\v바꿈\r", "줄\r")
    # Every line of a paragraph deleted: the paragraph goes whole, not each line with the same break.
    found = source_edits.replacements(
        word_plan(old, old.replace("줄  \n바꿈\n\n", "")), word_units(WORD_TEXT), ("\r", "\v"), marked_rows=True
    )
    assert apply(WORD_TEXT, found) == WORD_TEXT.replace("줄\v바꿈\r", "")
    text = "앞\r하나\v둘\v셋\r뒤\r"
    for new, expected in [
        ("앞\n\n셋\n\n뒤", "앞\r셋\r뒤\r"),
        ("앞\n\n하나\n\n뒤", "앞\r하나\r뒤\r"),
        ("앞\n\n뒤", "앞\r뒤\r"),
    ]:
        found = source_edits.replacements(
            word_plan("앞\n\n하나  \n둘  \n셋\n\n뒤", new), word_units(text), ("\r", "\v"), marked_rows=True
        )
        assert apply(text, found) == expected, new
    # A changed line the file holds with other text is refused, saying what it holds now: nothing is written.
    text = WORD_TEXT.replace("탭\t중간", "다른 글")
    with pytest.raises(
        Changed,
        match=re.escape(
            "원본 파일의 이 줄이 가져온 때와 달라 반영하지 않았습니다: ‘탭 중간 그리고 "
            "공백’(지금 원본: ‘다른 글 그리고 공백’)"
        ),
    ):
        source_edits.replacements(
            word_plan(old, old.replace("탭 중간", "탭 사이")), word_units(text), ("\r", "\v"), True
        )
    # One it does not have at all is not found.
    text = WORD_TEXT.replace("탭\t중간 그리고   공백", "")
    with pytest.raises(Missing, match="원본 파일에서 고칠 곳을 찾지 못했습니다: ‘탭 중간 그리고 공백’"):
        source_edits.replacements(
            word_plan(old, old.replace("탭 중간", "탭 사이")), word_units(text), ("\r", "\v"), True
        )
    # So is a list number the file draws.
    with pytest.raises(Missing):
        source_edits.replacements(
            word_plan(old, old.replace("1\\. 목록", "7\\. 목록")), word_units(WORD_TEXT), ("\r", "\v"), True
        )


def test_insertions_at_one_point_keep_the_order_the_text_has():
    text = "머리\r둘째 문단\r셋째 문단\r"
    cases = [
        # Text added at the end of a line, and a paragraph added after it: both where the line ends.
        (
            "# 머리\n\n둘째 문단\n\n셋째 문단",
            "# 머리\n\n둘째 문단 완료\n\n새 문단\n\n셋째 문단",
            "머리\r둘째 문단 완료\r새 문단\r셋째 문단\r",
        ),
        # A heading added before a heading whose start changed: both where that heading starts.
        (
            "# 머리\n\n# 둘째 문단\n\n셋째 문단",
            "# 머리\n\n# 새 장\n\n# 바뀐 둘째 문단\n\n셋째 문단",
            "머리\r새 장\r바뀐 둘째 문단\r셋째 문단\r",
        ),
    ]
    for old, new, expected in cases:
        found = source_edits.replacements(word_plan(old, new), word_units(text), ("\r", "\v"), True)
        assert apply(text, found) == expected
        # Word finds the place by its own search: the same text comes out.
        document = WordDocument(["머리", "둘째 문단", "셋째 문단"])
        edit_word(document, word_plan(old, new), threading.Event())
        assert document.Content.Text == expected


def test_a_line_added_where_a_changed_line_begins_goes_in_with_that_change():
    # Word finds a place by the text after it: written one after the other, the line added would no longer be found
    # once the change after it was made.
    cases = [
        # The first paragraph under a heading split in two.
        (
            ["제목", "첫 문단 내용 길게", "끝"],
            "# 제목\n\n첫 문단 내용 길게\n\n끝",
            "# 제목\n\n첫 문단\n\n내용 길게\n\n끝",
            "제목\r첫 문단\r내용 길게\r끝\r",
        ),
        # The document's first paragraph split.
        (
            ["첫 문단 내용 길게", "끝"],
            "첫 문단 내용 길게\n\n끝",
            "첫 문단\n\n내용 길게\n\n끝",
            "첫 문단\r내용 길게\r끝\r",
        ),
        # A heading added before one renumbered.
        (
            ["1. 서론", "본문", "2. 개요", "내용"],
            "# 1\\. 서론\n\n본문\n\n# 2\\. 개요\n\n내용",
            "# 1\\. 서론\n\n본문\n\n# 2\\. 배경\n\n# 3\\. 개요\n\n내용",
            "1. 서론\r본문\r2. 배경\r3. 개요\r내용\r",
        ),
    ]
    for blocks, old, new, expected in cases:
        document = WordDocument(blocks)
        edit_word(document, word_plan(old, new), threading.Event())
        assert document.Content.Text == expected
    # PowerPoint takes places by position: the two are made one after the other there.
    deck = NS(
        Slides=collection(
            [
                NS(
                    Shapes=collection([title_shape("제목"), text_shape("첫 문단 내용 길게\r끝")]),
                    NotesPage=NS(Shapes=collection([])),
                )
            ]
        )
    )
    old = "## 슬라이드 1 · 제목\n\n첫 문단 내용 길게\n\n끝"
    assert edit_powerpoint(
        deck, application_plan("PowerPoint", old, old.replace("문단 내용", "문단\n\n내용")), threading.Event()
    ) == (2, 0)
    assert deck.Slides.Item(1).Shapes.Item(2).TextFrame.TextRange.Text == "첫 문단\r내용 길게\r끝"


def test_a_blank_cell_is_reached_from_a_found_cell_with_the_row_and_column_word_must_confirm():
    body = (
        '<table><tr><td colspan="2"><p>병합</p></td><td><p></p></td></tr>'
        "<tr><td><p>a</p></td><td><p>b</p></td><td><p>c</p></td></tr></table><p>끝</p>"
    )
    old = index_markdown(page("t", body, "word"))
    assert "| 병합 | |  |" in old
    text = "병합\r\x07\r\x07\r\x07a\r\x07b\r\x07c\r\x07\r\x07끝\r"
    found = source_edits.replacements(
        word_plan(old, old.replace("| 병합 | |  |", "| 병합 | | 셋 |")), word_units(text), ("\r", "\v"), True
    )
    # Two cells on from 병합, in its row and column 3 of the page: Word, where 병합 spans two columns, says otherwise.
    assert found[0][4] == (0, 2, 2, 0, 3)


# --- Word ------------------------------------------------------------------------------------------------------------


FIND_CODES = {"^^": "^", "^t": "\t", "^l": "\v", "^p": "\r", "^s": "\xa0", "^~": "\x1e", "^-": "\x1f"}


class WordDocument:
    """Word's main text as the writer sees it: characters by position, some hidden (a field's code), which positions
    count but Text and the search leave out; tables whose cells go on with Next and Previous. `blocks` are paragraphs,
    with ⟦hidden⟧ text, or tables as rows of cell texts."""

    def __init__(self, blocks):
        self.chars, self.cells = [], []
        self.TrackRevisions = False
        self.ReadOnly = False
        self.events = []
        for block in blocks:
            if isinstance(block, str):
                self.add(block)
                self.chars.append(("\r", True))
            else:
                for row_number, row in enumerate(block, 1):
                    for column_number, cell in enumerate(row, 1):
                        start = len(self.chars)
                        self.add(cell)
                        self.cells.append([start, len(self.chars), row_number, column_number])
                        self.chars.extend([("\r", True), ("\x07", True)])
                    self.chars.extend([("\r", True), ("\x07", True)])

    def add(self, text):
        hidden = False
        for char in text:
            if char in "⟦⟧":
                hidden = char == "⟦"
            else:
                self.chars.append((char, not hidden))

    def text(self, start, end):
        return "".join(char for char, shown in self.chars[start:end] if shown)

    def replace(self, start, end, new):
        self.chars[start:end] = [(char, True) for char in new]
        delta = len(new) - (end - start)
        for cell in self.cells:
            if cell[0] >= end:
                cell[0] += delta
            if cell[1] >= end:
                cell[1] += delta

    @property
    def Content(self):
        return WordRange(self, 0, len(self.chars))

    def Range(self, start, end):
        return WordRange(self, start, end)

    def Save(self):
        self.events.append("save")

    def Close(self, **kwargs):
        self.events.append(("close", kwargs))


class WordRange:
    def __init__(self, document, start, end):
        self.document, self.Start, self.End = document, start, end

    @property
    def Text(self):
        return self.document.text(self.Start, self.End)

    @Text.setter
    def Text(self, value):
        self.document.replace(self.Start, self.End, value)
        self.End = self.Start + len(value)

    @property
    def Find(self):
        return WordFind(self)

    def Collapse(self, direction):
        if direction == 0:
            self.Start = self.End
        else:
            self.End = self.Start

    @property
    def Cells(self):
        index = next(i for i, (start, end, _, _) in enumerate(self.document.cells) if start <= self.Start <= end)
        return NS(Item=lambda number: WordCell(self.document, index))


class WordCell:
    def __init__(self, document, index):
        self.document, self.index = document, index

    @property
    def Next(self):
        return WordCell(self.document, self.index + 1) if self.index + 1 < len(self.document.cells) else None

    @property
    def Previous(self):
        return WordCell(self.document, self.index - 1) if self.index else None

    @property
    def RowIndex(self):
        return self.document.cells[self.index][2]

    @property
    def ColumnIndex(self):
        return self.document.cells[self.index][3]

    @property
    def Range(self):
        start, end, _, _ = self.document.cells[self.index]
        cell = WordRange(self.document, start, end + 2)
        document = self.document

        class CellRange(WordRange):
            @property
            def Text(self):
                return document.text(self.Start, self.End)

            @Text.setter
            def Text(self, value):  # The cell's end stays.
                document.replace(self.Start, self.End - 2, value)

        return CellRange(document, cell.Start, cell.End)


class WordFind:
    def __init__(self, found):
        self.found = found
        self.calls = []

    def ClearFormatting(self):
        pass

    def Execute(self, FindText, **options):
        assert options["MatchCase"] and not options["MatchWildcards"] and options["Wrap"] == 0
        needle = FindText
        for code, char in FIND_CODES.items():
            needle = needle.replace(code, char)
        document = self.found.document
        positions = [position for position, (_, shown) in enumerate(document.chars) if shown]
        visible = "".join(document.chars[position][0] for position in positions)
        first = next(
            (index for index, position in enumerate(positions) if position >= self.found.Start), len(positions)
        )
        at = visible.find(needle, first)
        if at < 0 or not needle:
            return False
        self.found.Start, self.found.End = positions[at], positions[at + len(needle) - 1] + 1
        return True


def word_document():
    return WordDocument(
        [
            "사업 계획",
            "첫 문단 굵게 그리고 기울임.",
            "목록 항목 하나",
            "목록 항목 둘",
            "탭\t중간 그리고   공백",
            "줄\v바꿈",
            [["담당자", "홍길동"], ["기한", ""]],
            "해당 없음",
            "마지막 문단",
            "해당 없음",
        ]
    )


def test_word_document_stand_in_matches_the_page():
    assert word_document().Content.Text == WORD_TEXT


def test_word_changes_are_made_in_place_and_read_back():
    document = word_document()
    old = word_markdown()
    new = (
        old.replace("목록 항목 하나", "목록 항목 첫째")
        .replace("**굵게**", "**아주 굵게**")
        .replace("| 기한 |  |", "| 기한 | 10월 |")
        .replace("마지막 문단", "마지막 문단\n\n새 문단")
        .replace("탭 중간 그리고 공백\n\n", "")
    )
    assert edit_word(document, word_plan(old, new), threading.Event()) == (5, 0)
    assert document.Content.Text == WORD_TEXT.replace("하나", "첫째").replace("굵게", "아주 굵게").replace(
        "기한\r\x07\r\x07", "기한\r\x0710월\r\x07"
    ).replace("마지막 문단\r", "마지막 문단\r새 문단\r").replace("탭\t중간 그리고   공백\r", "")


def test_word_positions_past_a_field_code_are_found_by_search():
    # A link's field code sits in the paragraph: positions after it differ from places in the text.
    document = WordDocument(['⟦HYPERLINK "https://x.test" ⟧사이트 안내 문단입니다', "둘째"])
    old = "사이트 안내 문단입니다\n\n둘째"
    edit_word(document, word_plan(old, old.replace("안내", "소개")), threading.Event())
    assert document.Content.Text == "사이트 소개 문단입니다\r둘째\r"
    # Changes after the code, and an insertion right before it, land where the text says.
    document = WordDocument(["앞⟦CODE⟧뒤 문단", "둘째"])
    edit_word(document, word_plan("앞뒤 문단\n\n둘째", "앞X뒤 문장\n\n둘째"), threading.Event())
    assert document.Content.Text == "앞X뒤 문장\r둘째\r" and document.chars[1:3] == [("X", True), ("C", False)]
    # A change across the hidden code would take it along: it is refused, the document as it was.
    document = WordDocument(["앞⟦CODE⟧뒤 문단", "둘째"])
    with pytest.raises(Missing, match="Word에서 고칠 곳의 글이 가져온 때와 달라"):
        edit_word(document, word_plan("앞뒤 문단\n\n둘째", "AB 문단\n\n둘째"), threading.Event())
    assert document.Content.Text == "앞뒤 문단\r둘째\r"


def test_word_blank_cells_at_a_table_end_are_checked_by_their_row_and_column():
    document = WordDocument(["앞 문단", [["담당", "홍길동"], ["기한", ""]], ""])
    body = "<p>앞 문단</p><table><tr><td>담당</td><td>홍길동</td></tr><tr><td>기한</td><td></td></tr></table>"
    old = index_markdown(page("t", body, "word"))
    edit_word(document, word_plan(old, old.replace("| 기한 |  |", "| 기한 | 10월 |")), threading.Event())
    assert document.Content.Text == "앞 문단\r담당\r\x07홍길동\r\x07\r\x07기한\r\x0710월\r\x07\r\x07\r"
    # Merged in Word, the page's blank column is another cell there: refused.
    document = WordDocument(["앞 문단", [["담당", "홍길동", ""], ["기한", ""]], ""])
    document.cells[-1][3] = 3  # the second row's last cell spans two columns
    body = (
        "<p>앞 문단</p><table><tr><td>담당</td><td>홍길동</td><td></td></tr>"
        '<tr><td>기한</td><td colspan="2"></td></tr></table>'
    )
    old = index_markdown(page("t", body, "word"))
    with pytest.raises(Missing, match="빈 칸의 자리를 찾지 못했습니다"):
        edit_word(document, word_plan(old, old.replace("| 기한 |  | |", "| 기한 | 10월 | |")), threading.Event())
    # A first row Word repeats as the table's header is the Markdown table's header row, not an empty one: the row is
    # counted from the cell it is reached from.
    document = WordDocument(["앞 문단", [["단계", "기한"], ["설계", ""]], ""])
    body = (
        "<p>앞 문단</p><table><thead><tr><td>단계</td><td>기한</td></tr></thead><tr><td>설계</td><td></td></tr></table>"
    )
    old = index_markdown(page("t", body, "word"))
    assert old.startswith("앞 문단\n\n| 단계 | 기한 |\n| --- | --- |\n| 설계 |  |")
    edit_word(document, word_plan(old, old.replace("| 설계 |  |", "| 설계 | 11월 |")), threading.Event())
    assert document.Content.Text == "앞 문단\r단계\r\x07기한\r\x07\r\x07설계\r\x0711월\r\x07\r\x07\r"
    # Blank cells reached from the same cell are all filled.
    document = WordDocument(["앞 문단", [["담당", "", ""], ["기한", "", ""]], ""])
    body = (
        "<p>앞 문단</p><table><tr><td>담당</td><td></td><td></td></tr><tr><td>기한</td><td></td><td></td></tr></table>"
    )
    old = index_markdown(page("t", body, "word"))
    assert "| 담당 |  |  |" in old
    edit_word(document, word_plan(old, old.replace("| 담당 |  |  |", "| 담당 | 홍길동 | 김철수 |")), threading.Event())
    assert "담당\r\x07홍길동\r\x07김철수\r\x07\r\x07기한\r\x07\r\x07\r\x07" in document.Content.Text


def test_word_result_that_differs_is_not_saved_and_a_stop_is_heard():
    document = word_document()
    old = word_markdown()
    plan = word_plan(old, old.replace("탭 중간", "탭 사이"))

    original = WordRange.Text.fset

    def autocorrect(self, value):  # Word changed more than it was told.
        original(self, value + "!")

    WordRange.Text = WordRange.Text.setter(autocorrect)
    try:
        with pytest.raises(UserError, match="Word에 고친 결과가 예상과 달라 저장하지 않았습니다"):
            edit_word(document, plan, threading.Event())
    finally:
        WordRange.Text = WordRange.Text.setter(original)
    stop = threading.Event()
    stop.set()
    with pytest.raises(UserError, match=CANCELLED):
        edit_word(word_document(), plan, stop)


class TrackedDocument(WordDocument):
    """A Word document with Track Changes on: text a change takes away stays in its text, marked deleted, before the
    new text."""

    def __init__(self, blocks):
        super().__init__(blocks)
        self.TrackRevisions = True

    def replace(self, start, end, new):
        self.chars[start:end] = self.chars[start:end] + [(char, True) for char in new]


def test_word_with_track_changes_is_read_back_with_the_text_it_keeps():
    old = word_markdown()
    removed = "첫 문단 **굵게** 그리고 *기울임*.\n\n"
    new = old.replace("탭 중간", "탭 사이").replace("마지막 문단", "마지막 문단\n\n새 문단").replace(removed, "")
    document = TrackedDocument(
        [
            "사업 계획",
            "첫 문단 굵게 그리고 기울임.",
            "목록 항목 하나",
            "목록 항목 둘",
            "탭\t중간 그리고   공백",
            "줄\v바꿈",
            [["담당자", "홍길동"], ["기한", ""]],
            "해당 없음",
            "마지막 문단",
            "해당 없음",
        ]
    )
    assert edit_word(document, word_plan(old, new), threading.Event()) == (3, 0)
    # The deleted paragraph and the replaced word are still there, as Word keeps them until the change is accepted.
    assert document.Content.Text == WORD_TEXT.replace("탭\t중간", "탭\t중간사이").replace(
        "마지막 문단\r", "마지막 문단\r새 문단\r"
    )
    # What Word wrote elsewhere than the changes say is still found, and not saved.
    original = WordRange.Text.fset

    def autocorrect(self, value):
        original(self, value + "!")

    WordRange.Text = WordRange.Text.setter(autocorrect)
    try:
        with pytest.raises(
            UserError, match="예상과 달라 저장하지 않았습니다. 원본 파일은 바뀌지 않았습니다. 변경 내용 추적이"
        ):
            edit_word(
                TrackedDocument(["사업 계획", "탭\t중간"]),
                word_plan("# 사업 계획\n\n탭 중간", "# 사업 계획\n\n탭 사이"),
                threading.Event(),
            )
    finally:
        WordRange.Text = WordRange.Text.setter(original)
    # Deleted text a tracked change keeps is in Word's text, not on the page: a line with it is not the one imported.
    document = TrackedDocument(["사업 계획", "탭\t중간 지운 글"])
    document.Revisions = NS(Count=1)
    with pytest.raises(
        Changed, match="변경 내용 추적으로 남은 글이 있습니다. 지운 글이 남은 줄이라면 Word에서 변경 내용을 적용한"
    ):
        edit_word(document, word_plan("# 사업 계획\n\n탭 중간", "# 사업 계획\n\n탭 사이"), threading.Event())


def test_lines_the_file_changed_since_the_import_are_not_written_over():
    old = "# 예산\n\n예산은 100만원입니다\n\n담당 홍길동\n\n비고 없음"
    imported = ["예산", "예산은 100만원입니다", "담당 홍길동", "비고 없음"]
    # Someone changed the line in the file meanwhile: written as the place says, 100 would become 250.
    document = WordDocument(["예산", "예산은 150만원입니다", "담당 홍길동", "비고 없음"])
    with pytest.raises(
        Changed,
        match=re.escape(
            "‘예산은 100만원입니다’(지금 원본: ‘예산은 150만원입니다’) 가져온 뒤 원본이 "
            "바뀌었다면 ‘원본에서 업데이트’ 후 다시 고쳐 주세요."
        ),
    ):
        edit_word(document, word_plan(old, old.replace("100만원", "200만원")), threading.Event())
    assert document.Content.Text == "예산\r예산은 150만원입니다\r담당 홍길동\r비고 없음\r"
    # Nor is a line deleted that holds more than it did.
    document = WordDocument(["예산", "예산은 100만원입니다", "담당 홍길동 외 1명", "비고 없음"])
    with pytest.raises(Changed, match="‘담당 홍길동’"):
        edit_word(document, word_plan(old, old.replace("담당 홍길동\n\n", "")), threading.Event())
    # A line added between two lines goes in only where the file still has nothing else between them.
    new = old.replace("담당 홍길동", "담당 홍길동\n\n참석: 김철수")
    document = WordDocument(["예산", "예산은 100만원입니다", "담당 홍길동", "참석: 이영희", "비고 없음"])
    with pytest.raises(
        Changed,
        match=re.escape(
            "새 줄을 넣을 곳에 가져온 때 없던 줄이 있어 반영하지 않았습니다: ‘참석: 김철수’(지금 원본: ‘참석: 이영희’)"
        ),
    ):
        edit_word(document, word_plan(old, new), threading.Event())
    # The line it goes beside must be as imported; the other side then holds it.
    document = WordDocument(["예산", "예산은 100만원입니다", "담당 홍길동 (변경)", "비고 없음"])
    assert edit_word(document, word_plan(old, new), threading.Event()) == (1, 0)
    assert document.Content.Text == "예산\r예산은 100만원입니다\r담당 홍길동 (변경)\r참석: 김철수\r비고 없음\r"
    document = WordDocument(["예산", "예산은 100만원입니다", "담당 홍길동 (변경)", "비고 (변경)"])
    with pytest.raises(Changed, match="새 줄 옆의 줄이 가져온 때와 달라"):
        edit_word(document, word_plan(old, new), threading.Event())
    # Lines changed elsewhere in the file do not stop the change, and the file as imported takes it all.
    for blocks in (["예산 (개정)", *imported[1:]], imported):
        document = WordDocument(blocks)
        assert edit_word(document, word_plan(old, new.replace("100만원", "200만원")), threading.Event()) == (2, 0)
        assert "예산은 200만원입니다\r담당 홍길동\r참석: 김철수\r" in document.Content.Text


def test_lines_are_as_imported_however_the_file_and_page_show_pictures_notes_and_numbers():
    # The page writes a trace of a picture, a note's number and a comment, Word's text has a mark or nothing; a list
    # number the file draws is on the page only. The lines are the same, and their changes go in.
    body = (
        '<p>앞 <span class="missing">[그림: 로고]</span> 뒤 문장</p>'
        '<p>본문<sup><a href="#footnote-1">1</a></sup> 이어서</p>'
        '<p>검토<sup class="comment"><a href="#comment-1">[메모 1]</a></sup> 필요</p>'
        "<p><span>1.\t</span>항목 하나</p><p>[그림 1] 구성도</p>"
    )
    old = index_markdown(page("t", body, "word"))
    new = (
        old.replace("뒤 문장", "뒤 문단")
        .replace("이어서", "계속")
        .replace("필요", "완료")
        .replace("항목 하나", "항목 첫째")
        .replace("구성도", "구성 도면")
    )
    document = WordDocument(["앞 \x01 뒤 문장", "본문\x02 이어서", "검토\x05 필요", "항목 하나", "[그림 1] 구성도"])
    assert edit_word(document, word_plan(old, new), threading.Event()) == (5, 0)
    assert document.Content.Text == "앞 \x01 뒤 문단\r본문\x02 계속\r검토\x05 완료\r항목 첫째\r[그림 1] 구성 도면\r"
    # Their own text still has to be the same: a note's number does not stand for other digits.
    document = WordDocument(["앞 \x01 뒤 문장", "본문\x02 이어서 2", "검토\x05 필요", "항목 하나", "[그림 1] 구성도"])
    with pytest.raises(Changed, match="‘본문1 이어서’"):
        edit_word(document, word_plan(old, new), threading.Event())
    # Nor a typed number another one.
    with pytest.raises(Changed, match="‘가. 항목’"):
        edit_word(
            WordDocument(["나. 항목", "끝"]), word_plan("가\\. 항목\n\n끝", "가\\. 항목 수정\n\n끝"), threading.Event()
        )
    # Should Word hand over its fields' codes, a line still reads as their results.
    line = "자세한 내용은 사이트 안내를 보세요."
    field = '자세한 내용은 \x13 HYPERLINK "https://x.test/" \x14사이트\x15 안내를 보세요.'
    assert source_edits.same(line, [field]) and not source_edits.same(line, [field.replace("안내", "소개")])
    assert source_edits.same("1쪽 중 1쪽", ["\x13 PAGE \x141\x15쪽 중 \x13 IF \x13 NUMPAGES \x141\x15 \x141\x15쪽"])
    # Numbers Word draws in other shapes: of several levels, a chapter's, Roman numerals, Korean letters.
    labels = [("1.1", "배경"), ("1.1.2", "목적"), ("제1장", "개요"), ("Ⅱ.", "현황"), ("ㄱ.", "항목"), ("(가)", "세부")]
    old = index_markdown(page("t", "".join(f"<h2><span>{label}\t</span>{text}</h2>" for label, text in labels), "word"))
    new = old
    for _, text in labels:
        new = new.replace(f" {text}", f" 주요 {text}")
    document = WordDocument([text for _, text in labels])
    assert edit_word(document, word_plan(old, new), threading.Event()) == (6, 0)
    assert document.Content.Text == "".join(f"주요 {text}\r" for _, text in labels)


def test_unchanged_files_take_every_change_where_the_text_says():
    """Random changes of files as they were imported: none refused, each written where the page has it."""
    random = Random(20261007)
    words = ["예산", "검토", "완료", "일정", "담당", "회의", "결과", "보고", "10월", "2026년", "예정", "홍길동"]

    def phrase():
        return " ".join(random.choice(words) for _ in range(random.randint(1, 4)))

    def markdown(paragraphs):
        return index_markdown(page("t", "".join(f"<p>{'<br>'.join(lines)}</p>" for lines in paragraphs), "word"))

    for _ in range(300):
        paragraphs = [[phrase() for _ in range(random.choice((1, 1, 1, 2, 3)))] for _ in range(random.randint(1, 7))]
        changed = [list(lines) for lines in paragraphs]
        for _ in range(random.randint(1, 4)):
            number = random.randrange(len(changed))
            lines = changed[number]
            action = random.choice(("edit", "edit", "add", "line", "delete", "drop"))
            if action == "edit":
                place = random.randrange(len(lines))
                lines[place] = random.choice((phrase() + " " + lines[place], lines[place] + " " + phrase(), phrase()))
            elif action == "add":
                changed.insert(random.randint(0, len(changed)), [phrase()])
            elif action == "line":
                lines.insert(random.randint(0, len(lines)), phrase())
            elif action == "delete" and len(lines) > 1:
                lines.pop(random.randrange(len(lines)))
            elif action == "drop" and len(changed) > 1:
                changed.pop(number)
        try:
            plan = word_plan(markdown(paragraphs), markdown(changed))
        except UserError:
            continue  # Nothing changed as the text reads.
        document = WordDocument(["\v".join(lines) for lines in paragraphs])
        edit_word(document, plan, threading.Event())
        # Line by line: a line that became a paragraph of its own (or the reverse) keeps the break it had, as other
        # formatting does.
        written = [line for line in re.split("[\r\v]", document.Content.Text) if line]
        assert written == [line for lines in changed for line in lines], (paragraphs, changed)


# --- PowerPoint ------------------------------------------------------------------------------------------------------


def collection(items):
    return NS(Count=len(items), Item=lambda index: items[index - 1])


class Frame:
    """A PowerPoint TextRange: its text, paragraphs and runs as the reader reads them, and parts of it to change."""

    def __init__(self, text, bullet=False):
        self.Text, self.bullet = text, bullet

    def Paragraphs(self, index=None):
        texts = self.Text.split("\r")
        if index is None:
            return NS(Count=len(texts))
        text = texts[index - 1] + ("\r" if index < len(texts) else "")
        form = NS(Bullet=NS(Visible=-1, Type=1, Character=8226, Font=NS(Name="Arial"))) if self.bullet else None
        return NS(
            Runs=lambda number=None: NS(Count=1) if number is None else NS(Text=text),
            ParagraphFormat=form,
            IndentLevel=1,
        )

    def Characters(self, start, length):
        return Part(self, start - 1, length)


class Part:
    def __init__(self, frame, start, length):
        self.frame, self.start, self.length = frame, start, length

    @property
    def Text(self):
        return self.frame.Text[self.start : self.start + self.length]

    @Text.setter
    def Text(self, value):
        self.frame.Text = self.frame.Text[: self.start] + value + self.frame.Text[self.start + self.length :]

    def InsertAfter(self, value):
        end = self.start + self.length
        self.frame.Text = self.frame.Text[:end] + value + self.frame.Text[end:]

    def InsertBefore(self, value):
        self.frame.Text = self.frame.Text[: self.start] + value + self.frame.Text[self.start :]


def text_shape(text, bullet=False):
    return NS(
        Type=17,
        HasTable=False,
        HasChart=0,
        HasSmartArt=False,
        HasTextFrame=True,
        TextFrame=NS(HasText=bool(text), TextRange=Frame(text, bullet)),
    )


def title_shape(text):
    shape = text_shape(text)
    shape.Type, shape.PlaceholderFormat = 14, NS(Type=1)
    return shape


def table_shape(rows):
    frames = [[Frame(text) for text in row] for row in rows]
    return NS(
        Type=19,
        HasTable=True,
        Table=NS(
            Rows=NS(Count=len(rows)),
            Columns=NS(Count=len(rows[0])),
            FirstRow=True,
            Cell=lambda row, column: NS(
                Shape=NS(TextFrame=NS(TextRange=frames[row - 1][column - 1], HasText=True), Fill=None)
            ),
        ),
    )


def presentation():
    notes = NS(
        Type=14,
        PlaceholderFormat=NS(Type=2),
        HasTable=False,
        HasChart=0,
        HasSmartArt=False,
        HasTextFrame=True,
        TextFrame=NS(HasText=True, TextRange=Frame("발표할 내용")),
    )
    first = NS(
        Shapes=collection(
            [
                text_shape("일정 확정\r담당 배정", bullet=True),
                title_shape("개발 계획\v2026"),
                table_shape([["단계", "기한"], ["설계", ""]]),
                NS(Type=6, GroupItems=collection([text_shape("그룹 안 글")])),
            ]
        ),
        NotesPage=NS(Shapes=collection([notes])),
    )
    second = NS(Shapes=collection([title_shape("검토"), text_shape("검토 항목")]), NotesPage=NS(Shapes=collection([])))
    return NS(Slides=collection([first, second]))


def application_plan(application, old, new):
    return plan_of(application, [("doc", old, new, None)])


def test_powerpoint_changes_titles_bullets_cells_and_notes_in_their_frames():
    from agent.office_html import powerpoint_html

    deck = presentation()
    old = index_markdown(page("발표", powerpoint_html(deck), "powerpoint"))
    assert old.startswith("## 슬라이드 1 · 개발 계획 2026\n\n• 일정 확정\n\n• 담당 배정\n\n| 단계 | 기한 |")
    new = (
        old.replace("개발 계획 2026", "개발 계획 2027")
        .replace("• 담당 배정", "• 담당 재배정\n\n• 새 항목")
        .replace("| 설계 |  |", "| 설계 | 11월 |")
        .replace("발표할 내용", "발표할 새 내용")
        .replace("검토 항목", "검토 대상")
    )
    assert edit_powerpoint(deck, application_plan("PowerPoint", old, new), threading.Event()) == (6, 0)
    slide, second = deck.Slides.Item(1), deck.Slides.Item(2)
    assert slide.Shapes.Item(2).TextFrame.TextRange.Text == "개발 계획\v2027"
    assert slide.Shapes.Item(1).TextFrame.TextRange.Text == "일정 확정\r담당 재배정\r새 항목"
    assert slide.Shapes.Item(3).Table.Cell(2, 2).Shape.TextFrame.TextRange.Text == "11월"
    assert slide.NotesPage.Shapes.Item(1).TextFrame.TextRange.Text == "발표할 새 내용"
    assert second.Shapes.Item(2).TextFrame.TextRange.Text == "검토 대상"
    # A slide is matched with its own heading: the same text on another slide stays.
    deck = presentation()
    deck.Slides.Item(2).Shapes.Item(2).TextFrame.TextRange.Text = "그룹 안 글"
    old = index_markdown(page("발표", powerpoint_html(deck), "powerpoint"))
    edit_powerpoint(
        deck, application_plan("PowerPoint", old, old.replace("그룹 안 글", "그룹 안 말", 1)), threading.Event()
    )
    assert deck.Slides.Item(1).Shapes.Item(4).GroupItems.Item(1).TextFrame.TextRange.Text == "그룹 안 말"
    assert deck.Slides.Item(2).Shapes.Item(2).TextFrame.TextRange.Text == "그룹 안 글"


# --- Visio -----------------------------------------------------------------------------------------------------------


class VisioShape:
    def __init__(self, ident, text, kind=3, children=(), one_d=False, connects=()):
        self.ID, self.Name, self.text, self.Type, self.OneD = ident, f"도형 {ident}", text, kind, one_d
        self.Shapes, self.Connects = collection(list(children)), collection(list(connects))

    @property
    def Characters(self):
        return VisioCharacters(self)

    def SectionExists(self, *_):
        return False


class VisioCharacters:
    def __init__(self, shape):
        self.shape, self.Begin, self.End = shape, 0, len(shape.text)

    @property
    def Text(self):
        return self.shape.text[self.Begin : self.End]

    @Text.setter
    def Text(self, value):
        self.shape.text = self.shape.text[: self.Begin] + value + self.shape.text[self.End :]
        self.End = self.Begin + len(value)


def drawing():
    server, database = VisioShape(1, "웹 서버\n8080"), VisioShape(2, "DB")
    link = VisioShape(
        3, "SQL", one_d=True, connects=[NS(FromPart=9, ToSheet=server), NS(FromPart=12, ToSheet=database)]
    )
    group = VisioShape(4, "", kind=2, children=[VisioShape(5, "캐시")])
    return NS(Pages=collection([NS(Name="구성", Background=0, Shapes=collection([server, database, link, group]))]))


def test_visio_changes_the_text_of_its_shapes_only():
    from agent.office_html import visio_html

    document = drawing()
    old = index_markdown(page("구성", visio_html(document), "visio"))
    assert "- 웹 서버  \n  8080" in old and "웹 서버 8080 → DB: SQL" in old
    new = old.replace("8080", "8443", 1).replace("- 캐시", "- 분산 캐시")
    assert edit_visio(document, application_plan("Visio", old, new), threading.Event()) == (2, 0)
    shapes = document.Pages.Item(1).Shapes
    assert (shapes.Item(1).text, shapes.Item(4).Shapes.Item(1).text, shapes.Item(3).text) == (
        "웹 서버\n8443",
        "분산 캐시",
        "SQL",
    )
    with pytest.raises(UserError, match="앱이 만든 줄"):
        application_plan("Visio", old, old.replace("DB: SQL", "DB: HTTP"))


# --- Excel -----------------------------------------------------------------------------------------------------------


class Cell:
    """An Excel cell: the text it shows, its value and its formula. A value typed into it is read as Excel reads it (a
    number as a number), and the characters of its text change in place."""

    def __init__(self, text, value, formula=""):
        self.Text, self.Value2, self.Formula = text, value, formula or (value if isinstance(value, str) else "")
        self.written = []

    @property
    def HasFormula(self):
        return str(self.Formula).startswith("=")

    @property
    def FormulaLocal(self):
        return self.Formula

    @FormulaLocal.setter
    def FormulaLocal(self, value):
        from agent.office_write import number

        self.written.append(("typed", value))
        self.Value2 = value if number(value) is None else number(value)
        self.Text = self.Formula = value

    def ClearContents(self):
        self.written.append(("cleared",))
        self.Text, self.Value2, self.Formula = "", None, ""

    def Characters(self, start, length):
        return CellCharacters(self, start - 1, length)


class CellCharacters:
    """Characters of a cell's text, from 1 as Excel counts them: setting their text changes only them."""

    def __init__(self, cell, start, length):
        self.cell, self.start, self.length = cell, start, length

    @property
    def Text(self):
        return self.cell.Value2[self.start : self.start + self.length]

    @Text.setter
    def Text(self, value):
        cell = self.cell
        cell.written.append(("characters", self.start + 1, self.length, value))
        cell.Value2 = cell.Value2[: self.start] + value + cell.Value2[self.start + self.length :]
        cell.Text = cell.Formula = cell.Value2


class Sheets:
    def __init__(self, cells):
        self.cells = cells

    def Item(self, name):
        if name not in self.cells:
            raise KeyError(name)  # Excel: the sheet does not exist
        return NS(Range=lambda address: self.cells[name][address])


def workbook():
    cells = {
        "개발 & 운영": {
            "A1": Cell("<담당자>", "<담당자>"),
            "C1": Cell("홍길동", "홍길동"),
            "A3": Cell("0.3", 0.3),
            "C3": Cell("42", 42.0, "=SUM(A1:A2)"),
        },
        "요약": {"D5": Cell("FALSE", False)},
    }
    return NS(Worksheets=Sheets(cells)), cells


def test_excel_writes_the_changed_cells_by_address_after_checking_them():
    from agent.office_html import excel_html
    from test_office_import import workbook_fixture

    html = page("보고서", excel_html(workbook_fixture()), "excel")
    old = index_markdown(html)
    new = (
        old.replace("| 홍길동 |", "| 김철수 |")
        .replace("| 0.3 |", "| 1,250 |")
        .replace("수식: `=SUM(A1:A2)`", "수식: `=SUM(A1:A3)`")
        .replace("| 5 | FALSE |", "| 5 |  |")
    )
    found = excel_changes([(old, new, html)])
    assert [
        (change["sheet"][-1], change["address"], change["old"], change["new"], change["formula"]) for change in found
    ] == [
        ("개발 & 운영", "C1", "홍길동", "김철수", None),
        ("개발 & 운영", "A3", "0.3", "1,250", None),
        ("개발 & 운영", "C3", "42", "42", ["=SUM(A1:A2)", "=SUM(A1:A3)"]),
        ("요약", "D5", "FALSE", "", None),
    ]
    assert found[3]["sheet"] == ["요약 (숨긴 시트)", "요약"]
    book, sheets = workbook()
    assert edit_excel(book, {"cells": found}, threading.Event()) == (4, 0)
    # Text is changed in the cell's own text, a number typed as Excel reads it.
    assert sheets["개발 & 운영"]["C1"].written == [("characters", 1, 3, "김철수")]
    assert sheets["개발 & 운영"]["A3"].written == [("typed", "1,250")] and sheets["개발 & 운영"]["A3"].Value2 == 1250
    assert sheets["개발 & 운영"]["C3"].Formula == "=SUM(A1:A3)" and sheets["요약"]["D5"].written == [("cleared",)]
    # A cell that no longer holds what the page showed is not written over, nor one that holds a formula now.
    book, sheets = workbook()
    sheets["개발 & 운영"]["C1"].Text = sheets["개발 & 운영"]["C1"].Value2 = "다른 사람"
    with pytest.raises(
        Missing, match="원본 파일의 셀이 가져온 때와 달라 쓰지 않았습니다: ‘개발 & 운영 시트 C1 셀: 홍길동’"
    ):
        edit_excel(book, {"cells": found}, threading.Event())
    book, sheets = workbook()
    sheets["개발 & 운영"]["C1"].Formula = '="홍"&"길동"'
    with pytest.raises(
        Missing, match="원본 파일의 셀이 가져온 때와 달라 쓰지 않았습니다: ‘개발 & 운영 시트 C1 셀: 홍길동’"
    ):
        edit_excel(book, {"cells": found}, threading.Event())
    assert sheets["개발 & 운영"]["C1"].written == []

    # Every cell written is read back before the workbook is saved: a value Excel read otherwise is not saved.
    class Comma(Cell):  # Excel reads "1,250" with a decimal comma.
        @Cell.FormulaLocal.setter
        def FormulaLocal(self, value):
            self.Value2, self.Text = 1.25, "1.25"

    book, sheets = workbook()
    sheets["개발 & 운영"]["A3"] = Comma("0.3", 0.3)
    with pytest.raises(UserError, match="Excel에 고친 결과가 예상과 달라 저장하지 않았습니다"):
        edit_excel(book, {"cells": found}, threading.Event())
    # Only cells: a value computed by a formula, rows, and the text around the cells stay as they are.
    for changed, message in [
        (old.replace("| 42 수식", "| 43 수식"), "수식이 계산한 값이라"),
        (old.replace("| 3 | 0.3 |", "| 4 | 0.3 |"), "행 번호나 열을 바꾼 것은"),
        (old + "\n| 9 | 새 행 |  |", "행을 더하거나 지운 것은"),
        (old.replace("## 요약", "## 개요"), "셀 밖의 글"),
    ]:
        with pytest.raises(UserError, match=message):
            excel_changes([(old, changed, html)])


def test_excel_text_cells_keep_their_line_breaks_spacing_and_formatting():
    # The page shows the cell's text on one line, single-spaced; the cell keeps its own.
    raw = "1차 검토 완료\n2차 검토 예정  (10/15)"
    change = {
        "sheet": ["일정"],
        "address": "B2",
        "old": "1차 검토 완료 2차 검토 예정 (10/15)",
        "new": "1차 검토 완료 2차 검토 완료 (10/15)",
        "formula": None,
    }
    cell = Cell(raw, raw)
    assert edit_excel(NS(Worksheets=Sheets({"일정": {"B2": cell}})), {"cells": [change]}, threading.Event()) == (1, 0)
    assert cell.Value2 == "1차 검토 완료\n2차 검토 완료  (10/15)"
    # Only the characters that changed are written, so the others keep their formatting; text added is written with a
    # character beside it.
    assert cell.written == [("characters", 16, 2, "완료")]
    cell = Cell(raw, raw)
    added = {**change, "new": "확인: 1차 검토 완료 2차 검토 예정 (10/15) 끝"}
    assert edit_excel(NS(Worksheets=Sheets({"일정": {"B2": cell}})), {"cells": [added]}, threading.Event()) == (1, 0)
    assert cell.Value2 == "확인: 1차 검토 완료\n2차 검토 예정  (10/15) 끝"
    assert cell.written == [("characters", 26, 1, ") 끝"), ("characters", 1, 1, "확인: 1")]

    # What Excel wrote is read back: text written otherwise (white space as the page has it) is not saved.
    class Flattening(CellCharacters):
        @CellCharacters.Text.setter
        def Text(self, value):
            CellCharacters.Text.fset(self, value)
            self.cell.Value2 = " ".join(self.cell.Value2.split())

    cell = Cell(raw, raw)
    cell.Characters = lambda start, length: Flattening(cell, start - 1, length)
    with pytest.raises(UserError, match="Excel에 고친 결과가 예상과 달라 저장하지 않았습니다"):
        edit_excel(NS(Worksheets=Sheets({"일정": {"B2": cell}})), {"cells": [change]}, threading.Event())
    # A cell whose own text is not the text it shows (a number format around it) is not written.
    cell = Cell("검토 예정 님", "검토 예정")
    shown = {**change, "old": "검토 예정 님", "new": "검토 완료 님"}
    with pytest.raises(Missing, match="원본 파일의 셀 글이 가져온 때와 달라 쓰지 않았습니다"):
        edit_excel(NS(Worksheets=Sheets({"일정": {"B2": cell}})), {"cells": [shown]}, threading.Event())
    assert cell.written == []


def test_excel_takes_the_cells_of_the_text_the_workbook_holds():
    from agent.office_html import excel_html
    from test_office_import import workbook_fixture

    html = page("보고서", excel_html(workbook_fixture()), "excel")
    imported = index_markdown(html)
    written = imported.replace("| 홍길동 |", "| 김철수 |")
    now = written.replace("| 0.3 |", "| 1,250 |")
    plan = plan_of("Excel", [("doc", imported, now, html)], [[written]])
    # The workbook holds what the last write put there: only the cell changed since goes in.
    book, sheets = workbook()
    sheets["개발 & 운영"]["C1"] = Cell("김철수", "김철수")
    assert edit_excel(book, plan, threading.Event()) == (1, 0)
    assert sheets["개발 & 운영"]["C1"].written == [] and sheets["개발 & 운영"]["A3"].Value2 == 1250
    # It still holds the imported cells: both go in.
    book, sheets = workbook()
    assert edit_excel(book, plan, threading.Event()) == (2, 1)
    assert sheets["개발 & 운영"]["C1"].Value2 == "김철수" and sheets["개발 & 운영"]["A3"].Value2 == 1250
    # Neither: nothing is written.
    book, sheets = workbook()
    sheets["개발 & 운영"]["C1"] = Cell("박영희", "박영희")
    with pytest.raises(UserError, match="원본이 가져온 때의 내용과도, 지난번 원본에 반영한 내용과도 달라"):
        edit_excel(book, plan, threading.Event())
    # Nothing changed since that write, which the workbook holds: nothing to write.
    book, sheets = workbook()
    sheets["개발 & 운영"]["C1"] = Cell("김철수", "김철수")
    with pytest.raises(UserError, match="원본에 이미 지금 문서의 내용이 들어 있어"):
        edit_excel(book, plan_of("Excel", [("doc", imported, written, html)], [[written]]), threading.Event())


# --- Sessions --------------------------------------------------------------------------------------------------------


def word_application(calls, document):
    options = NS(UpdateLinksAtOpen=True)
    word = NS(Options=options, Visible=False, DisplayAlerts=-1, AutomationSecurity=1)
    word.Quit = lambda **kw: calls.append(
        ("quit", kw, (word.DisplayAlerts, word.AutomationSecurity, options.UpdateLinksAtOpen))
    )

    def open_document(**kwargs):
        calls.append(("open", kwargs))
        assert (word.Visible, word.DisplayAlerts, word.AutomationSecurity, options.UpdateLinksAtOpen) == (
            False,
            0,
            3,
            False,
        )
        return document

    word.Documents = NS(Open=open_document)
    return word


def test_a_file_on_this_pc_is_written_by_a_hidden_word_that_saves_and_quits(monkeypatch, tmp_path):
    calls, document = [], word_document()
    fake_modules(monkeypatch, word_application(calls, document), calls)
    old = word_markdown()
    source = tmp_path / "보고서.docx"
    plan = word_plan(old, old.replace("탭 중간", "탭 사이"))
    assert write_word(source, plan, threading.Event()) == {"changes": 1, "base": 0}
    opened_with = next(call[1] for call in calls if call[0] == "open")
    assert opened_with["FileName"] == str(source) and opened_with["ReadOnly"] is False
    assert opened_with["WritePasswordDocument"] == "" and opened_with["Visible"] is False
    # Saved, then closed; Word's settings are the user's again when it quits.
    assert document.events == ["save", ("close", {"SaveChanges": 0})]
    assert [call[0] for call in calls] == ["dispatch", "open", "quit"] and calls[-1] == (
        "quit",
        {"SaveChanges": 0},
        (0, 1, True),
    )
    assert "탭\t사이" in document.Content.Text

    # A file Word opened read-only, or holding another line than the import read: closed unsaved, Word quits.
    for prepare, message in [
        (lambda doc: setattr(doc, "ReadOnly", True), "읽기 전용으로 열어"),
        (lambda doc: doc.replace(0, 4, "다른 글"), "이 줄이 가져온 때와 달라"),
    ]:
        calls, document = [], word_document()
        prepare(document)
        fake_modules(monkeypatch, word_application(calls, document), calls)
        with pytest.raises(UserError, match=message):
            write_word(source, word_plan(old, old.replace("사업 계획", "사업 개요")), threading.Event())
        assert document.events == [("close", {"SaveChanges": 0})] and calls[-1][0] == "quit"


# --- The job ----------------------------------------------------------------------------------------------------------


def lines_page(file):
    """The page a stand-in Word file (its paragraphs as lines of text) is read as."""
    return page(file.stem, "".join(f"<p>{line}</p>" for line in file.read_text(encoding="utf-8").splitlines()), "word")


class LinesWriter:
    """The Office writer, on a stand-in Word file: its paragraphs are written as a Word document, changed by the real
    edit_word and saved back."""

    def __init__(self):
        self.calls, self.installed = [], True

    def ready(self, name):
        if not self.installed:
            raise UserError(f"{name} 데스크톱 앱이 설치된 Windows에서 원본에 반영할 수 있습니다.")

    def __call__(self, path, payload, stop):
        self.calls.append({"path": path, "payload": payload})
        document = WordDocument(path.read_text(encoding="utf-8").splitlines())
        changed, base = edit_word(document, payload["plan"], stop)
        path.write_text(document.Content.Text.rstrip("\r").replace("\r", "\n"), encoding="utf-8")
        return {"changes": changed, "base": base}


@pytest.fixture
def written(tmp_path):
    service = Service(Database(tmp_path / "wiki" / "wiki.db"), FakeLLM())
    service.office = OfficeImporter(
        service, converter=lambda file, *_: lines_page(file), capability_check=lambda: {"available": True}
    )
    service.writeback.writer = LinesWriter()
    service.writeback.reader = lambda file, *_: lines_page(file)
    source = tmp_path / "보고서.docx"
    source.write_text("사업 계획\n일정은 3월 15일까지\n담당 홍길동", encoding="utf-8")
    service.office.start({"path": str(source), "mode": "file"})
    service.office.thread.join(5)
    ident = service.office.status()["results"][0]["doc_id"]
    return service, source, ident


def wait_for(service):
    deadline = time.monotonic() + 30
    while service.writeback.status()["status"] in ("scanning", "login", "running", "cancelling"):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    return service.writeback.status()


def test_a_written_file_is_backed_up_and_the_document_read_again(written, tmp_path):
    service, source, ident = written
    doc = service.wiki_get({"id": ident})
    original = source.read_bytes()
    service.wiki_save(
        {"id": ident, "title": doc["title"], "content": doc["content"].replace("3월 15일", "4월 1일") + "\n\n검토 완료"}
    )
    job = service.writeback.start({"id": ident})
    assert (job["kind"], job["application"], job["file"], job["changes"]) == ("office", "Word", "보고서.docx", 2)
    job = wait_for(service)
    assert job["status"] == "completed", str(job)
    assert job["message"] == "원본 파일에 고친 곳 2개를 저장했습니다. 문서는 저장한 파일의 내용으로 다시 읽었습니다."
    assert source.read_text(encoding="utf-8") == "사업 계획\n일정은 4월 1일까지\n담당 홍길동\n검토 완료"
    # The file as it was is kept in the Wiki folder.
    backups = list((tmp_path / "wiki" / BACKUPS).iterdir())
    assert len(backups) == 1 and backups[0].name.endswith(" 보고서.docx") and backups[0].read_bytes() == original
    assert job["backup"] == str(backups[0])
    # The document is the file's page again, no longer edited, with the same ID.
    doc = service.wiki_get({"id": ident})
    assert doc["format"] == "html" and "검토 완료" in doc["html"] and doc["source"]["edited"] is False
    assert service.wiki_source_status({"id": ident})["state"] == "same"
    assert (
        service.writeback.status({"id": "other"}) is None
        and service.writeback.status({"id": job["id"]})["id"] == job["id"]
    )
    # Nothing left to write.
    with pytest.raises(UserError, match="고친 내용이 없어"):
        service.writeback.start({"id": ident})


def test_writes_that_cannot_start_say_why(written, tmp_path):
    service, source, ident = written
    doc = service.wiki_get({"id": ident})
    service.wiki_save({"id": ident, "title": doc["title"], "content": doc["content"].replace("홍길동", "김철수")})
    note = service.wiki_save({"title": "메모", "content": "직접 쓴 글"})
    for params, message in [
        ({"id": "없는 문서"}, "문서를 찾을 수 없습니다"),
        ({"id": note["id"]}, "Office 파일"),
        ({}, "문서 ID"),
    ]:
        with pytest.raises(UserError, match=message):
            service.writeback.start(params)
    service.writeback.writer.installed = False
    with pytest.raises(UserError, match="Word 데스크톱 앱이 설치된"):
        service.writeback.start({"id": ident})
    service.writeback.writer.installed = True
    # Office imports and writes take turns.
    service.office.job = {"status": "running"}
    with pytest.raises(UserError, match="가져오기가 진행 중입니다"):
        service.writeback.start({"id": ident})
    service.office.job = None
    gate = threading.Event()
    writer = service.writeback.writer
    service.writeback.writer = lambda *args: gate.wait(10) and writer(*args)
    service.writeback.writer.ready = writer.ready
    service.writeback.start({"id": ident})
    with pytest.raises(UserError, match="원본에 반영하는 중입니다"):
        service.writeback.start({"id": ident})
    with pytest.raises(UserError, match="원본에 반영하는 중입니다"):
        service.office.start({"path": str(source), "mode": "file"})
    gate.set()
    assert wait_for(service)["status"] == "completed"
    # A file that is gone cannot be written.
    service.wiki_save({"id": ident, "title": doc["title"], "content": doc["content"].replace("홍길동", "이영희")})
    source.unlink()
    with pytest.raises(UserError, match="원본 파일을 찾을 수 없습니다"):
        service.writeback.start({"id": ident})


def test_backups_are_kept_by_file_the_newest_five_of_each(written, tmp_path, monkeypatch):
    service, source, ident = written
    folder, writeback = tmp_path / "wiki" / BACKUPS, service.writeback
    folder.mkdir()
    # A copy an earlier version made, which no record names, and a file the user put there stay.
    (folder / "20260101-000000 예전.docx").write_bytes(b"old")
    (folder / "메모.txt").write_text("사용자가 둔 파일", encoding="utf-8")
    other = tmp_path / "다른 파일.docx"
    other.write_text("다른 내용", encoding="utf-8")
    theirs = writeback.backup(other, "other")
    copies = []
    for number in range(7):
        source.write_text(f"판 {number}", encoding="utf-8")
        copies.append(writeback.backup(source, ident))
        writeback.prune(source)
    # The two oldest copies of the file went; another file's copy, and what no record names, stay.
    kept = [copy["name"] for copy in copies[2:]]
    assert {item.name for item in folder.iterdir()} == {*kept, theirs["name"], "20260101-000000 예전.docx", "메모.txt"}
    listed = service.dispatch("writeback.backups", {"doc_id": ident})
    assert [item["id"] for item in listed["backups"]] == [copy["id"] for copy in copies[2:]][::-1]
    assert ([item["name"] for item in listed["backups"]], listed["keep"]) == (kept[::-1], 5)
    assert [item["current"] for item in listed["backups"]] == [True, False, False, False, False]
    # A copy another program holds open stays recorded until it can go; one removed by hand loses its record.
    stuck, unlink = folder / kept[0], Path.unlink

    def held(path, *args, **kwargs):
        if path == stuck:
            raise PermissionError("open in Word")
        return unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", held)
    (folder / kept[1]).unlink()
    for number in (7, 8):
        source.write_text(f"판 {number}", encoding="utf-8")
        writeback.backup(source, ident)
        writeback.prune(source)
    listed = [item["id"] for item in service.dispatch("writeback.backups", {"doc_id": ident})["backups"]]
    assert stuck.exists() and listed[-1] == copies[2]["id"] and copies[3]["id"] not in listed and len(listed) == 6
    monkeypatch.undo()
    writeback.prune(source)
    assert not stuck.exists() and len(service.dispatch("writeback.backups", {"doc_id": ident})["backups"]) == 5


def test_a_backup_of_the_documents_file_is_put_back_in_its_place(written, tmp_path):
    service, source, ident = written
    original = source.read_bytes()
    doc = service.wiki_get({"id": ident})
    service.wiki_save({"id": ident, "title": doc["title"], "content": doc["content"].replace("홍길동", "김철수")})
    service.writeback.start({"id": ident})
    assert wait_for(service)["status"] == "completed"
    written_bytes = source.read_bytes()
    listed = service.dispatch("writeback.backups", {"doc_id": ident})
    assert (listed["file"], listed["interrupted"]) == ("보고서.docx", None)
    [copy] = listed["backups"]
    assert (copy["kind"], copy["state"], copy["current"]) == ("write", "written", False)
    # Only a copy the app made of this document's file is put back: named by its id, never by a path.
    note = service.wiki_save({"title": "메모", "content": "직접 쓴 글"})
    for params, message in [
        ({"doc_id": ident, "backup": f"../{BACKUPS}/{copy['name']}"}, "백업한 것이 아닙니다"),
        ({"doc_id": ident}, "백업"),
        ({"doc_id": note["id"], "backup": copy["id"]}, "Office 파일"),
    ]:
        with pytest.raises(UserError, match=message):
            service.dispatch("writeback.restore_backup", params)
    service.writeback.job = {"status": "running"}
    with pytest.raises(UserError, match="원본에 반영하는 중입니다"):
        service.dispatch("writeback.restore_backup", {"doc_id": ident, "backup": copy["id"]})
    service.writeback.job = None
    restored = service.dispatch("writeback.restore_backup", {"doc_id": ident, "backup": copy["id"]})
    assert restored["restored"] is True and source.read_bytes() == original
    assert restored["message"] == (
        f"백업 {copy['name']}을(를) 원본 파일 자리에 복원했습니다. 복원하기 전의 파일은 "
        f"‘{BACKUPS}’에 {restored['backup']}(으)로 남겼습니다. 문서에 반영하려면 문서 창에서 "
        "‘원본에서 업데이트’를 눌러 주세요."
    )
    # The file it replaced was copied first, so the restore can be undone too.
    listed = service.dispatch("writeback.backups", {"doc_id": ident})["backups"]
    assert [(item["kind"], item["current"]) for item in listed] == [("restore", False), ("write", True)]
    assert (tmp_path / "wiki" / BACKUPS / restored["backup"]).read_bytes() == written_bytes
    # Restoring what the file holds already changes nothing; the document reads the file with an update from it.
    assert service.dispatch("writeback.restore_backup", {"doc_id": ident, "backup": copy["id"]})["restored"] is False
    assert service.wiki_source_status({"id": ident})["state"] == "changed"
    # A copy changed after it was made is not put back.
    (tmp_path / "wiki" / BACKUPS / restored["backup"]).write_text("바뀐 백업", encoding="utf-8")
    with pytest.raises(UserError, match="바뀌어 복원하지 않았습니다"):
        service.dispatch("writeback.restore_backup", {"doc_id": ident, "backup": listed[0]["id"]})
    assert source.read_bytes() == original


def office_package(path, text, compression=zipfile.ZIP_DEFLATED):
    """A file in the ZIP package format of Office since 2007, holding `text` in its one part."""
    with zipfile.ZipFile(path, "w", compression) as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("word/document.xml", f"<w:document>{text}</w:document>")


def test_what_a_cut_write_left_of_a_file_is_told_from_the_file_alone(tmp_path):
    docx = tmp_path / "보고서.docx"
    office_package(docx, "처음")
    before = checksum(docx)
    assert (classify(docx, before), whole(docx)) == ("intact", True)
    office_package(docx, "반영한 글")  # Another whole file: saved in full, or changed by another program.
    assert classify(docx, before) == "unknown"
    data = docx.read_bytes()
    docx.write_bytes(data[: len(data) // 2])  # Cut short: no central directory.
    assert (classify(docx, before), whole(docx)) == ("partial", False)
    office_package(docx, "가" * 200, zipfile.ZIP_STORED)
    data = bytearray(docx.read_bytes())
    data[data.index("가".encode()) + 1] ^= 0xFF  # A part that does not read back with its checksum.
    docx.write_bytes(bytes(data))
    assert classify(docx, before) == "partial"
    docx.write_bytes(b"")
    assert classify(docx, before) == "partial"
    docx.unlink()
    assert classify(docx, before) == "partial"
    # The older formats are compound files, told by their signature.
    doc = tmp_path / "옛 보고서.doc"
    doc.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"\0" * 1024)
    before = checksum(doc)
    doc.write_bytes(bytes.fromhex("d0cf11e0a1b11ae1") + b"\1" * 1024)
    assert classify(doc, before) == "unknown"
    doc.write_bytes(b"\1" * 100)
    assert classify(doc, before) == "partial"


def test_a_write_cut_short_by_a_crash_is_told_before_the_next_write(written, tmp_path):
    service, source, ident = written
    writeback = service.writeback
    doc = service.wiki_get({"id": ident})
    service.wiki_save({"id": ident, "title": doc["title"], "content": doc["content"].replace("홍길동", "김철수")})
    # A write began and the app was closed by force before its file was saved: its copy's record says "writing".
    writeback.backup(source, ident)
    with pytest.raises(UserError, match="저장하기 전에 중단되었습니다.*원본 파일은 반영 전 그대로입니다"):
        writeback.start({"id": ident})
    assert writeback.status() is None
    # Told once: the next write goes on.
    writeback.start({"id": ident})
    assert wait_for(service)["status"] == "completed"
    assert "김철수" in source.read_text(encoding="utf-8")

    # Cut while the file was being replaced: none is left. It is told until the file is whole again.
    doc = service.wiki_get({"id": ident})
    service.wiki_save({"id": ident, "title": doc["title"], "content": doc["content"].replace("김철수", "이영희")})
    cut = writeback.backup(source, ident)
    source.unlink()
    for _ in range(2):
        with pytest.raises(UserError, match="일부만 저장된 것으로 보입니다") as told:
            writeback.start({"id": ident})
        assert f"‘{BACKUPS}’에 있는 {cut['name']}을(를) 복원한 뒤" in str(told.value)
    listed = service.dispatch("writeback.backups", {"doc_id": ident})
    assert {key: listed["interrupted"][key] for key in ("state", "backup")} == {"state": "partial", "backup": cut["id"]}
    restored = service.dispatch("writeback.restore_backup", {"doc_id": ident, "backup": cut["id"]})
    assert restored["restored"] is True and restored["backup"] == ""  # No file was left to copy first.
    assert service.dispatch("writeback.backups", {"doc_id": ident})["interrupted"] is None
    writeback.start({"id": ident})
    assert wait_for(service)["status"] == "completed"
    assert "이영희" in source.read_text(encoding="utf-8")


def test_how_far_a_cut_write_saved_its_file_is_told(written, tmp_path):
    service, _, _ = written
    writeback = service.writeback
    docx = tmp_path / "발표.docx"
    office_package(docx, "처음")
    cut = writeback.backup(docx, "doc")
    office_package(docx, "반영한 글")  # Saved whole...
    cut["journal"].write_text("saved", encoding="utf-8")  # ...as far as its save noted, then the app was closed.
    with writeback.lock:
        message = writeback.interrupted(docx)
    assert message.startswith("지난번 원본 반영은 파일 저장까지 마쳤지만") and cut["name"] in message
    assert not cut["journal"].exists() and writeback.interrupted(docx) is None
    cut = writeback.backup(docx, "doc")
    office_package(docx, "다른 글")
    with writeback.lock:
        assert "끝까지 저장했는지는 알 수 없습니다" in writeback.interrupted(docx)


def test_a_failed_write_that_changed_the_file_says_so(written):
    service, source, ident = written
    doc = service.wiki_get({"id": ident})
    service.wiki_save({"id": ident, "title": doc["title"], "content": doc["content"].replace("홍길동", "김철수")})
    writer = service.writeback.writer

    def breaking(path, payload, stop):
        path.write_text("반쯤 쓴 내용", encoding="utf-8")
        raise UserError("Word 파일 저장 실패: 디스크가 가득 찼습니다.")

    breaking.ready = writer.ready
    service.writeback.writer = breaking
    service.writeback.start({"id": ident})
    job = wait_for(service)
    copy = Path(job["backup"]).name
    # The stand-in file is not a package: what is left is no whole file of its format.
    assert (job["status"], job["error"]) == (
        "error",
        (
            "Word 파일 저장 실패: 디스크가 가득 찼습니다. "
            "확인해 보니 원본 파일이 없거나 일부만 저장된 것으로 보입니다. "
            f"Wiki 폴더의 ‘{BACKUPS}’에 있는 {copy}을(를) 복원해 주세요."
        ),
    )
    listed = service.dispatch("writeback.backups", {"doc_id": ident})
    assert (listed["backups"][0]["state"], listed["interrupted"]) == ("failed", None)


def test_the_save_notes_in_its_journal_when_it_begins_and_ends(tmp_path, monkeypatch):
    journal, phases = tmp_path / "copy.writing", []

    class Office:
        @contextlib.contextmanager
        def writing(self):
            yield

        @contextlib.contextmanager
        def saving(self):
            yield

    class Document:
        def Save(self):
            phases.append(journal.read_text(encoding="utf-8") if journal.exists() else "")

    class Failing:
        def Save(self):
            raise OSError("disk full")

    monkeypatch.setitem(office_write.EDITORS, "Word", lambda document, plan, stop: (2, 0))
    assert office_write.edit(Office(), Document(), "Word", {}, None, str(journal)) == {"changes": 2, "base": 0}
    assert phases == ["saving"] and journal.read_text(encoding="utf-8") == "saved"
    with pytest.raises(OSError):
        office_write.edit(Office(), Failing(), "Word", {}, None, str(journal))
    assert journal.read_text(encoding="utf-8") == "saving"
    journal.unlink()
    assert office_write.edit(Office(), Document(), "Word", {}, None) == {"changes": 2, "base": 0}
    assert not journal.exists()


def test_a_document_changed_while_its_file_is_written_keeps_that_change(written):
    service, source, ident = written
    doc = service.wiki_get({"id": ident})
    service.wiki_save({"id": ident, "title": doc["title"], "content": doc["content"].replace("3월 15일", "4월 1일")})
    entered, gate, writer = threading.Event(), threading.Event(), service.writeback.writer

    def gated(path, payload, stop):
        entered.set()
        gate.wait(10)
        return writer(path, payload, stop)

    gated.ready = writer.ready
    service.writeback.writer = gated
    service.writeback.start({"id": ident})
    assert entered.wait(5)
    doc = service.wiki_get({"id": ident})
    service.wiki_save({"id": ident, "title": doc["title"], "content": doc["content"] + "\n\n검토 완료"})
    gate.set()
    job = wait_for(service)
    # The file has what was written; the document keeps what the user wrote meanwhile, and the job says so.
    assert (job["status"], job["reread"]) == ("completed", False), str(job)
    assert job["message"] == "원본 파일에 고친 곳 1개를 저장했습니다. " + EDITED_MEANWHILE
    assert source.read_text(encoding="utf-8") == "사업 계획\n일정은 4월 1일까지\n담당 홍길동"
    doc = service.wiki_get({"id": ident})
    assert doc["content"].endswith("검토 완료") and doc["source"]["edited"] is True
    # The next write starts from what the file holds now: only the line added meanwhile goes in, and the document is
    # read again.
    service.writeback.writer = writer
    service.writeback.start({"id": ident})
    job = wait_for(service)
    assert job["status"] == "completed" and job["reread"] is True, str(job)
    assert [choice["edits"][0]["op"] for choice in writer.calls[-1]["payload"]["plan"]["choices"]] == ["insert", "edit"]
    assert source.read_text(encoding="utf-8") == "사업 계획\n일정은 4월 1일까지\n담당 홍길동\n검토 완료"
    assert service.wiki_get({"id": ident})["source"]["edited"] is False
    with service.db.connect() as db:
        assert db.execute("SELECT written FROM office_sources WHERE doc_id=?", (ident,)).fetchone()[0] == ""


def test_a_stopped_or_failed_write_leaves_the_file_and_document(written):
    service, source, ident = written
    doc = service.wiki_get({"id": ident})
    service.wiki_save({"id": ident, "title": doc["title"], "content": doc["content"].replace("홍길동", "김철수")})
    before = source.read_bytes()
    started, writer = threading.Event(), service.writeback.writer

    def slow(path, payload, stop):
        started.set()
        stop.wait(10)
        raise UserError(CANCELLED)

    service.writeback.writer = slow
    slow.ready = writer.ready
    service.writeback.start({"id": ident})
    started.wait(5)
    assert service.writeback.cancel()["status"] == "cancelling"
    job = wait_for(service)
    assert (job["status"], job["message"], job["error"]) == ("cancelled", CANCELLED, "")
    # The writer finds the file changed: an error, the document still edited.
    source.write_text("전혀 다른 내용", encoding="utf-8")
    service.writeback.writer = writer
    service.writeback.start({"id": ident})
    job = wait_for(service)
    assert job["status"] == "error" and "고칠 곳을 찾지 못했습니다" in job["error"], str(job)
    assert service.wiki_get({"id": ident})["source"]["edited"] is True
    assert before != source.read_bytes() == "전혀 다른 내용".encode()


# --- Real Office (Windows) -------------------------------------------------------------------------------------------


def make_office_file(name, source):
    """A file with formatting a write must keep: Word's bold run, numbered list, link field and table; Excel's number
    format and formula; PowerPoint's title, bullets, table and notes; Visio's shapes."""
    import pythoncom
    import win32com.client

    from agent.office_converter import PROGIDS

    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx(PROGIDS[name])
    try:
        if name == "Word":
            document = app.Documents.Add(Visible=False)
            document.Content.Text = (
                "사업 계획\r일정은 3월 15일까지 마칩니다.\r첫째 항목\r둘째 항목\r탭\t중간 문단\r"
                "자세한 내용은 사이트 안내를 보세요.\r지울 문단\r마지막 문단"
            )
            paragraphs = document.Paragraphs
            paragraphs.Item(1).Style = -2  # wdStyleHeading1
            second = paragraphs.Item(2).Range.Start
            document.Range(second + 4, second + 6).Font.Bold = True
            document.Range(paragraphs.Item(3).Range.Start, paragraphs.Item(4).Range.End).ListFormat.ApplyNumberDefault()
            link = paragraphs.Item(6).Range.Start + 8
            document.Hyperlinks.Add(Anchor=document.Range(link, link + 3), Address="https://x.test/")
            document.Content.InsertParagraphAfter()
            table = document.Tables.Add(document.Paragraphs.Item(document.Paragraphs.Count).Range, 2, 2)
            for (row, column), text in {(1, 1): "담당", (1, 2): "홍길동", (2, 1): "기한"}.items():
                table.Cell(row, column).Range.Text = text
            document.SaveAs2(str(source), FileFormat=12)
            document.Close(SaveChanges=0)
        elif name == "Excel":
            book = app.Workbooks.Add()
            sheet = book.Worksheets.Item(1)
            sheet.Name = "보고"
            sheet.Range("A1").Value, sheet.Range("B1").Value = "담당자", "홍길동"
            sheet.Range("A2").Value = 1234.5
            sheet.Range("A2").NumberFormat = "#,##0.00"
            sheet.Range("A3").Formula = "=A2*2"
            sheet.Range("A3").NumberFormat = "#,##0.00"
            sheet.Range("B2").Value = "1차 검토 완료\n2차 검토 예정  (10/15)"
            sheet.Range("B3").Value = "메모"
            book.SaveAs(str(source), FileFormat=51)
            book.Close(SaveChanges=False)
        elif name == "PowerPoint":
            deck = app.Presentations.Add(WithWindow=False)
            slide = deck.Slides.Add(1, 2)  # ppLayoutText: a title and a bulleted body
            slide.Shapes.Item(1).TextFrame.TextRange.Text = "개발 계획"
            slide.Shapes.Item(2).TextFrame.TextRange.Text = "일정 확정\r담당 배정"
            slide.NotesPage.Shapes.Placeholders(2).TextFrame.TextRange.Text = "발표할 내용"
            table = slide.Shapes.AddTable(2, 2, 100, 300, 300, 80).Table
            for (row, column), text in {(1, 1): "단계", (1, 2): "기한", (2, 1): "설계"}.items():
                table.Cell(row, column).Shape.TextFrame.TextRange.Text = text
            deck.SaveAs(str(source), FileFormat=24)
            deck.Close()
        else:
            drawing = app.Documents.Add("")
            page_ = drawing.Pages.Item(1)
            first, second = page_.DrawRectangle(1, 1, 3, 2), page_.DrawRectangle(5, 1, 7, 2)
            first.Text, second.Text = "웹 서버", "DB"
            drawing.SaveAs(str(source))
            drawing.Close()
    finally:
        if name != "PowerPoint" or not (app.Visible or app.Presentations.Count):
            app.Quit()
        pythoncom.CoUninitialize()


def excel_value(path, sheet, address):
    """The value of a cell of a workbook, as a hidden Excel reads it."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("Excel.Application")
    try:
        book = app.Workbooks.Open(str(path), ReadOnly=True)
        try:
            return book.Worksheets.Item(sheet).Range(address).Value
        finally:
            book.Close(SaveChanges=False)
    finally:
        app.Quit()
        pythoncom.CoUninitialize()


EDITS = {
    "Word": [
        ("일정은 3월", "일정은 4월"),
        ("2\\. 둘째 항목", "2\\. 둘째 항목 수정"),
        ("안내를", "소개를"),
        ("| 기한 |  |", "| 기한 | 10월 |"),
        ("탭 중간 문단", "탭 중간 문단 끝\n\n새 문단"),
        ("지울 문단\n\n", ""),
    ],
    "Excel": [
        ("| 홍길동 |", "| 김철수 |"),
        ("| 1,234.50 |", "| 2,000.00 |"),
        ("`=A2*2`", "`=A2*3`"),
        ("| 메모 |", "|  |"),
        ("2차 검토 예정", "2차 검토 완료"),
    ],
    "PowerPoint": [
        ("· 개발 계획", "· 개발 계획 2027"),
        ("담당 배정", "담당 재배정\n\n새 항목"),
        ("발표할 내용", "발표할 새 내용"),
        ("| 설계 |  |", "| 설계 | 11월 |"),
    ],
    "Visio": [("- 웹 서버", "- 웹 서버 2")],
}
WRITTEN = {
    "Word": [
        "일정은 4월 15일까지",
        "2\\. 둘째 항목 수정",
        "[사이트](https://x.test/) 소개를",
        "| 기한 | 10월 |",
        "탭 중간 문단 끝\n\n새 문단\n\n자세한",
    ],
    "Excel": ["| 김철수 |", "| 2,000.00 |", "6,000.00 수식: `=A2*3`", "2차 검토 완료 (10/15)"],
    "PowerPoint": ["## 슬라이드 1 · 개발 계획 2027", "• 담당 재배정\n\n• 새 항목", "발표할 새 내용", "| 설계 | 11월 |"],
    "Visio": ["- 웹 서버 2"],
}


@pytest.mark.skipif(
    sys.platform != "win32" or os.getenv("PRIVATE_AGENT_OFFICE_TEST") != "1",
    reason="Requires Windows Office; opt in with PRIVATE_AGENT_OFFICE_TEST=1",
)
@pytest.mark.parametrize(
    "name,extension", [("Word", ".docx"), ("Excel", ".xlsx"), ("PowerPoint", ".pptx"), ("Visio", ".vsdx")]
)
def test_real_office_write(tmp_path, name, extension):
    from agent.office_converter import OfficeConverter, capabilities
    from agent.office_write import OfficeWriter

    if not capabilities()["applications"][name]:
        pytest.skip(f"{name} is not installed")
    source = tmp_path / ("반영 테스트" + extension)
    make_office_file(name, source)
    html = OfficeConverter()(source, threading.Event())
    old = index_markdown(html)
    new = old
    for before, after in EDITS[name]:
        assert before in new, (before, old)
        new = new.replace(before, after, 1)
    plan = plan_of(name, [("doc", old, new, html)])
    summary = OfficeWriter()(source, {"application": name, "plan": plan}, threading.Event())
    assert summary == {"changes": count(plan), "base": 0}
    again = OfficeConverter()(source, threading.Event())
    text = index_markdown(again)
    for expected in WRITTEN[name]:
        assert expected in text, (expected, text)
    if name == "Word":
        # The rest kept its formatting: the heading, the list's numbers, the link, the tab.
        assert text.startswith("# 사업 계획") and "1\\. 첫째 항목" in text and "지울 문단" not in text
        assert "탭\t중간" in again
        # The new 4 is bold, as the 3 it took the place of: Word keeps the formatting of what it replaces.
        assert re.search(
            r'일정은 <span style="font-weight:bold">4</span><span style="font-weight:bold">월</span> 15일', again
        )
    if name == "Excel":
        # The text cell kept its line break and spacing: only the word changed was written.
        assert excel_value(source, "보고", "B2") == "1차 검토 완료\n2차 검토 완료  (10/15)"
    # The same changes again find nothing left to change.
    with pytest.raises(UserError, match="고친"):
        plan_of(name, [("doc", text, text, again)])
