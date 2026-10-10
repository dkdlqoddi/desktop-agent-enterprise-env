"""What the user changed in the text of a document imported from an Office file, as edits of the file's own text.

An imported file's document holds the Markdown text its HTML page became (html_document.page_text). The source keeps
the text the import saved (sources.py); the user edits the document's. The difference between the two is what goes back
into the file (writeback.py), and only that: the file keeps everything else as it is, formatting included.

The texts are compared line by line as units: a line of a paragraph, heading or list item, a code line or a table cell,
with the Markdown taken off and runs of white space read as one space, as a person reads them. A changed unit becomes
the characters that changed in it, so the formatting of the rest stays; added lines become new paragraphs beside the
line they follow, and deleted lines go with their paragraph or line break. Formatting changed in Markdown (bold, a
heading's level) is not carried over, and Markdown the user wrote goes in as the text it shows.

Each place is then found in the text Office hands over (office_write.py): the file's units are aligned with the
imported ones, the lines that are the same pinning those between, so a list number Word draws, a tab or a picture
marker the page shows differently do not get in the way, and a phrase that occurs more than once is told apart by where
it is. A line that changes, goes or has lines added beside it must hold in the file the text the import read there,
and nothing may stand between the lines a new one goes between that was not there: what someone changed in the file
since is never written over. Excel is compared cell by cell, as the page keeps each cell's address.

A write-back that left the file with other text than the import read (a document changed while a write was in
progress) makes the next plan a choice per text the file may hold, each with what tells it from the others (witness):
the writer takes the one the file is found to hold."""

import difflib
import re
from typing import NamedTuple

from bs4 import BeautifulSoup

from .llm import UserError

# Headings are read as PageIndex reads them, without the regular expression whose backtracking took seconds.
from .pageindex import heading as atx_heading

# Markdown punctuation a backslash escapes (CommonMark).
ESCAPABLE = frozenset("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
RULE = re.compile(r" {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*")
ROW = re.compile(r" {0,3}\|")
TABLE_RULE = re.compile(r" {0,3}\|?(?:[ \t]*:?-+:?[ \t]*\|)*[ \t]*:?-+:?[ \t]*\|?[ \t]*")
LIST = re.compile(r"[ \t]*(?:[-+*]|\d{1,9}[.)])(?:[ \t]+(.*))?")
QUOTE = re.compile(r" {0,3}>[ ]?")
# The number or bullet Word or PowerPoint draws before a paragraph: the page shows it as text, the paragraph's own text
# does not hold it. Lines are compared without it, so a list item still meets its paragraph. Word's numbers: 1. a) (가)
# ㄱ. 1.1 1.1.1 제1장 Ⅱ. and the like.
LABEL = re.compile(
    r"(?:[(（]?(?:\d{1,4}(?:\.\d{1,4})*|[a-zA-Z]{1,3}|[가-힣ㄱ-ㅎ]|[０-９]{1,4}|[Ⅰ-ⅿ]{1,4})[.)）]"
    r"|[(（](?:\d{1,4}|[a-zA-Z]{1,3}|[가-힣ㄱ-ㅎ])[)）]|\d{1,4}(?:\.\d{1,4})+|제 ?\d{1,4} ?[장절조항관편부]"
    r"|[Ⅰ-ⅿ]{1,4}|[①-⑳⑴-⒇⒈-⒛❶-❿➀-➓]|[•◦▪▫■□●○◆◇◈➢➤►▶▷✓✔☐☑※\-–—·∙・*+>→])\s+"
)
SLIDE = re.compile(r"슬라이드 (\d+)(?: · (.*?))?(?: \(숨긴 슬라이드\))?")
PAGE = re.compile(r"페이지 (\d+)(?: · .*?)?(?: \(배경\))?")
CONTINUED = " (계속)"
# Visio's page sections: the shapes' text is the drawing's; connections and Shape Data are what the reader made of it.
VISIO_TEXT = "도형 텍스트"
# What the file's text and the page show differently for what is not text: Word's text holds a control character for a
# picture or a note's or comment's mark, the page a trace in brackets for a picture or comment, a note's number or
# nothing (a floating picture).
MARKS = re.compile(r"[\x00-\x08\x0e-\x1b]+")
TRACE = re.compile(
    r"\[(?:그림|개체|연결된 그림|그리지 못한 도형|삽입된 개체|차트|SmartArt|외부 삽입 콘텐츠|메모)\b[^\[\]]*\]"
)
NOTE = r"(?:\d{1,4}|[ivxlcdm]{1,8})?"
UPDATE = "가져온 뒤 원본이 바뀌었다면 ‘원본에서 업데이트’ 후 다시 고쳐 주세요."
ELSEWHERE = (
    "원본이 가져온 때의 내용과도, 지난번 원본에 반영한 내용과도 달라 반영하지 않았습니다. 그 뒤 원본이 바뀌었다면 "
    "‘원본에서 업데이트’로 원본의 지금 내용을 가져온 뒤 다시 고쳐 주세요."
)
ALREADY = (
    "원본에 이미 지금 문서의 내용이 들어 있어 반영할 것이 없습니다. ‘원본에서 업데이트’로 문서를 원본에 맞출 수 "
    "있습니다."
)
# The most characters (old times new) the units between two lines that are the same may hold for their characters to
# be compared; more takes too long, and their changes are then not found.
ALIGN_LIMIT = 2_000_000
PAIR_LIMIT = 2500
ALIKE = 0.3  # how alike two lines must be, as difflib's ratio, to be paired across a line added or deleted beside them


class Unit(NamedTuple):
    text: str  # what a person reads, white space as single spaces
    kind: str  # "p" a paragraph's line, "h" a heading, "li" a list item, "cell", "code", "title" a slide's title
    scope: int  # the slide or page it is on (PowerPoint, Visio) from 1; 0 in Word and before the first
    joined: bool  # whether it continues the line before it in the same paragraph, after a line break
    editable: bool = True  # False for what the reader wrote rather than read from the file, such as a page's heading
    table: int = -1  # a cell's table, row and column
    row: int = -1
    column: int = -1


def norm(text):
    return " ".join(text.split())


class Unchanged(UserError):
    """Nothing to write: the text now is the one the changes would be made against."""


# --- Markdown as text ------------------------------------------------------------------------------------------------


def code_span(text, index):
    """(end, content) of the code span whose backticks start at `index`, or None when no run of as many closes it."""
    size = len(text) - index - len(text[index:].lstrip("`"))
    search = index + size
    while True:
        found = text.find("`" * size, search)
        if found < 0:
            return None
        end = found
        while end < len(text) and text[end] == "`":
            end += 1
        if end - found == size:
            content = text[index + size : found]
            if len(content) > 2 and content[0] == content[-1] == " " and content.strip():
                content = content[1:-1]
            return end, content
        search = end


def bracket(text, index, opening, closing):
    """The index after the bracket that closes the one at `index`, past escaped and nested ones, or None."""
    depth, position = 0, index
    while position < len(text):
        char = text[position]
        if char == "\\":
            position += 2
            continue
        if char == "`" and (span := code_span(text, position)):
            position = span[0]
            continue
        if char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return position + 1
        position += 1
    return None


def link_at(text, index):
    """(end, label) of the link [label](destination) whose bracket is at `index`, or None."""
    close = bracket(text, index, "[", "]")
    if close is None or close >= len(text) or text[close] != "(":
        return None
    end = bracket(text, close, "(", ")")
    return None if end is None else (end, text[index + 1 : close - 1])


def punctuation(char):
    return not char.isalnum() and not char.isspace()


def flanking(text, start, end):
    """Whether the delimiter run text[start:end] may open and close emphasis, by CommonMark's flanking rules."""
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    left = not after.isspace() and (not punctuation(after) or before.isspace() or punctuation(before))
    right = not before.isspace() and (not punctuation(before) or after.isspace() or punctuation(after))
    if text[start] == "_":
        return left and (not right or punctuation(before)), right and (not left or punctuation(after))
    return left, right


def inline(text):
    """The text Markdown inline markup shows: escapes resolved, code spans as they are, links and pictures as their
    text, and the marks of emphasis and strikethrough taken off where they pair up."""
    pieces = []  # (text, whether it is a run of *, _ or ~ that may be emphasis)
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text) and text[index + 1] in ESCAPABLE:
            pieces.append((text[index + 1], False))
            index += 2
            continue
        if char == "`":
            span = code_span(text, index)
            if span:
                pieces.append((span[1], False))
                index = span[0]
            else:
                end = len(text) - len(text[index:].lstrip("`"))
                pieces.append((text[index:end], False))
                index = end
            continue
        link = (
            link_at(text, index + 1)
            if char == "!" and text.startswith("[", index + 1)
            else link_at(text, index)
            if char == "["
            else None
        )
        if link:
            pieces.append((inline(link[1]), False))
            index = link[0]
            continue
        if char == "<":
            match = re.match(r"<([a-zA-Z][a-zA-Z0-9+.-]{1,31}:[^\s<>]*|[^\s<>@]+@[^\s<>@]+)>", text[index:])
            if match:
                pieces.append((match.group(1), False))
                index += match.end()
                continue
        if char in "*_~":
            end = index
            while end < len(text) and text[end] == char:
                end += 1
            pieces.append((text[index:end], True))
            index = end
            continue
        pieces.append((char, False))
        index += 1
    return emphasis(pieces)


def emphasis(pieces):
    """The pieces' text without the delimiter runs that open and close emphasis or strikethrough."""
    flat, runs = "", []
    for value, delimiter in pieces:
        if delimiter:
            runs.append((len(flat), len(flat) + len(value), value[0]))
        flat += value
    removed, openers = set(), []
    for start, end, char in runs:
        if char == "~" and end - start < 2:
            continue
        can_open, can_close = flanking(flat, start, end)
        if can_close:
            for position in range(len(openers) - 1, -1, -1):
                opener = openers[position]
                if opener[2] != char:
                    continue
                size = min(opener[1] - opener[0], end - start, 2)
                removed.update(range(opener[1] - size, opener[1]))
                removed.update(range(start, start + size))
                opener[1] -= size
                start += size
                del openers[position + (opener[1] > opener[0]) :]
                break
        if can_open and start < end:
            openers.append([start, end, char])
    return "".join(char for position, char in enumerate(flat) if position not in removed)


def plain(text):
    return norm(inline(text))


def cells(line):
    """The cells of a table row, split at the pipes Markdown did not escape, as Markdown."""
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    found, current, index = [], [], 0
    while index < len(body):
        char = body[index]
        if char == "\\" and index + 1 < len(body):
            current.append(body[index : index + 2])
            index += 2
            continue
        if char == "|":
            found.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    found.append("".join(current))
    return found


def units(markdown, application):
    """The units of a document's Markdown text, in order. `application` (Word, PowerPoint, Visio) tells which lines the
    reader wrote itself: a slide's or page's heading, the speaker notes' heading, Visio's connections and Shape Data."""
    found = []
    scope, section = 0, True  # section: whether the lines here are the file's text
    fence = None
    table, row, in_table = -1, -1, False
    joined = False  # the line before ended with a hard line break
    for line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if fence:
            if re.fullmatch(rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*", line):
                fence = None
            else:
                found.append(Unit(norm(line), "code", scope, False, section))
            continue
        if opening := FENCE.match(line):
            fence, in_table, joined = opening.group(1), False, False
            continue
        if not line.strip():
            in_table = joined = False
            continue
        if ROW.match(line):
            if not in_table:
                table, row, in_table = table + 1, -1, True
            joined = False
            if not TABLE_RULE.fullmatch(line):
                row += 1
                found.extend(
                    Unit(plain(cell), "cell", scope, False, section, table, row, column)
                    for column, cell in enumerate(cells(line))
                )
            continue
        in_table = False
        if heading := atx_heading(line):
            joined = False
            unit, scope, section = heading_unit(heading[0], plain(heading[1]), application, scope, section)
            found.append(unit)
            continue
        if RULE.fullmatch(line):
            joined = False
            continue
        hard = line.endswith("  ") or (line.endswith("\\") and not line.endswith("\\\\"))
        text = line[:-1] if hard and line.endswith("\\") else line
        while quote := QUOTE.match(text):
            text = text[quote.end() :]
        kind = "li" if joined and found and found[-1].kind == "li" else "p"
        if not joined and (item := LIST.fullmatch(text)):
            kind, text = "li", item.group(1) or ""
        found.append(Unit(plain(text), kind, scope, joined, section))
        joined = hard
    return found


def heading_unit(level, text, application, scope, section):
    """(unit, slide or page, whether the file's text follows) of a heading. Word's headings are the file's. A slide's
    heading in PowerPoint carries the slide's title; the other headings of slides and pages, and one a later part
    repeats, the reader wrote. In Visio only the section of the shapes' text is the drawing's."""
    if application == "Word":
        return Unit(text, "h", scope, False, section and not text.endswith(CONTINUED)), scope, section
    if level == 2:
        slide = SLIDE.fullmatch(text) if application == "PowerPoint" else None
        if slide:
            return Unit(slide.group(2) or "", "title", int(slide.group(1)), False, True), int(slide.group(1)), True
        continued = (
            SLIDE.fullmatch(text.removesuffix(CONTINUED))
            if application == "PowerPoint"
            else PAGE.fullmatch(text.removesuffix(CONTINUED))
        )
        if continued:
            scope = int(continued.group(1))
            section = application == "PowerPoint" or (text.endswith(CONTINUED) and section)
            return Unit(text, "h", scope, False, False), scope, section
    if application == "Visio" and level == 3:
        section = text.removesuffix(CONTINUED) == VISIO_TEXT
    return Unit(text, "h", scope, False, False), scope, section


# --- What changed --------------------------------------------------------------------------------------------------


def spans(old, new):
    """[[start, end, text]] that replace old[start:end] to make `new`: only the characters that changed, so the
    formatting of the others stays."""
    matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
    return [[i1, i2, new[j1:j2]] for tag, i1, i2, j1, j2 in matcher.get_opcodes() if tag != "equal"]


def similarity(first, second):
    score = (
        difflib.SequenceMatcher(None, first.text, second.text, autojunk=False).ratio()
        if first.text or second.text
        else 1.0
    )
    return score + (first.kind == second.kind) * 0.01


def pairing(old, new):
    """The old lines of a changed stretch paired with the new ones in order: [(i, j)], i or j None for a line deleted
    or added. Lines are paired when they are alike, so a line added before one that changed does not take its place;
    a line deleted and one added where it was are paired too, so a rewritten paragraph keeps its formatting."""
    n, m = len(old), len(new)
    if n * m > PAIR_LIMIT:
        pairs = [(i, i) for i in range(min(n, m))]
        return pairs + [(i, None) for i in range(m, n)] + [(None, j) for j in range(n, m)]
    # The most alike pairs, in order: each pair is worth how alike its lines are beyond ALIKE.
    worth = [[similarity(old[i], new[j]) - ALIKE for j in range(m)] for i in range(n)]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            best[i][j] = max(worth[i][j] + best[i + 1][j + 1], best[i + 1][j], best[i][j + 1])
    steps, i, j = [], 0, 0
    while i < n or j < m:
        if i < n and j < m and worth[i][j] > 0 and best[i][j] == worth[i][j] + best[i + 1][j + 1]:
            steps.append((i, j))
            i, j = i + 1, j + 1
        elif i < n and (j == m or best[i][j] == best[i + 1][j]):
            steps.append((i, None))
            i += 1
        else:
            steps.append((None, j))
            j += 1
    # Between two pairs, what was deleted and what was added stand in the same place: paired in order.
    pairs, gap = [], []

    def close():
        deleted = [i for i, j in gap if j is None]
        added = [j for i, j in gap if i is None]
        pairs.extend(zip(deleted, added))
        pairs.extend((i, None) for i in deleted[len(added) :])
        pairs.extend((None, j) for j in added[len(deleted) :])
        gap.clear()

    for step in steps:
        if step[0] is not None and step[1] is not None:
            close()
            pairs.append(step)
        else:
            gap.append(step)
    close()
    return pairs


def unsupported(unit, what):
    shown = unit.text if len(unit.text) <= 40 else unit.text[:40] + "…"
    return UserError(f"{what} 원본 파일에 반영할 수 없습니다: ‘{shown}’ 원래대로 되돌린 뒤 다시 반영해 주세요.")


def among_text(old, after):
    """Whether lines added after the old unit `after` go among the file's own text: a unit beside the place is."""
    beside = [old[i] for i in (after, after + 1) if 0 <= i < len(old)]
    return not beside or any(unit.editable for unit in beside)


class Line(NamedTuple):
    """Units compared together: a table row's cells, or one other unit."""

    start: int  # the index of its first unit
    units: list
    row: bool

    @property
    def text(self):
        return " | ".join(unit.text for unit in self.units)

    @property
    def kind(self):
        return "row" if self.row else self.units[0].kind


def lines(units):
    found = []
    for index, unit in enumerate(units):
        last = found[-1] if found else None
        if (
            unit.kind == "cell"
            and last
            and last.row
            and (last.units[0].table, last.units[0].row) == (unit.table, unit.row)
        ):
            last.units.append(unit)
        else:
            found.append(Line(index, [unit], unit.kind == "cell"))
    return found


def changes(old, new, application):
    """The edits that make the units `old` the units `new`, as the dicts the Office writer takes:
    {"op": "edit", "unit": i, "spans": [[start, end, text]]}, {"op": "delete", "unit": i} and
    {"op": "insert", "after": i, "before": i, "lines": [[text, kind, joined]]} for lines added between two units that
    stay (-1: none on that side). Table rows are compared cell by cell. A change the file cannot take raises
    UserError: one of a line the reader wrote, of a table's rows and columns, or a new shape in Visio."""
    found, kept, added = [], set(), []
    olds, news = lines(old), lines(new)

    def edit(i, line):
        if old[i].text == line.text:
            return
        if not old[i].editable:
            raise unsupported(old[i], "앱이 만든 줄(슬라이드·페이지 제목, 연결, 도형 데이터 등)을 고친 것은")
        found.append({"op": "edit", "unit": i, "spans": spans(old[i].text, line.text)})

    matcher = difflib.SequenceMatcher(
        None, [(line.row, line.text) for line in olds], [(line.row, line.text) for line in news], autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            kept.update(line.start + offset for line in olds[i1:i2] for offset in range(len(line.units)))
            continue
        after = olds[i1].start - 1 if i1 < len(olds) else len(old) - 1
        for a, b in pairing(olds[i1:i2], news[j1:j2]):
            was = None if a is None else olds[i1 + a]
            now = None if b is None else news[j1 + b]
            if was is not None and now is not None:
                if was.row or now.row:
                    if not (was.row and now.row and len(was.units) == len(now.units)):
                        raise unsupported(now.units[0] if now.row else was.units[0], "표의 행이나 열을 바꾼 것은")
                for offset, line in enumerate(now.units):
                    kept.add(was.start + offset)
                    edit(was.start + offset, line)
                after = was.start + len(was.units) - 1
            elif was is not None:
                after = was.start + len(was.units) - 1
                unit = was.units[0]
                if not unit.editable:
                    raise unsupported(unit, "앱이 만든 줄(슬라이드·페이지 제목, 연결, 도형 데이터 등)을 지운 것은")
                if was.row:
                    raise unsupported(unit, "표의 행이나 열을 지운 것은")
                if unit.text:
                    found.append({"op": "delete", "unit": was.start})
            else:
                line = now.units[0]
                if now.row:
                    raise unsupported(line, "표에 행이나 열을 더한 것은")
                if not among_text(old, after):
                    raise unsupported(line, "앱이 만든 줄(연결, 도형 데이터 등) 사이에 쓴 것은")
                if application == "Visio" and not line.joined and line.text:
                    raise unsupported(line, "Visio에 새 도형을 만드는 것은")
                if line.text:
                    added.append((after, line))
    groups = {}
    for after, line in added:
        groups.setdefault(after, []).append([line.text, line.kind, line.joined])
    for after, items in groups.items():
        anchor = next((i for i in range(after, -1, -1) if i in kept), -1)
        before = next((i for i in range(after + 1, len(old)) if i in kept), -1)
        found.append({"op": "insert", "after": anchor, "before": before, "lines": items})
    if not found:
        raise Unchanged("가져온 뒤 고친 내용이 없어 원본에 반영할 것이 없습니다.")
    return found


def witness(old, other):
    """What tells a file holding the units `old` from one holding the units `other`: {"units": [i], "gaps": [[after,
    before]]}, the old units `other` does not have as they are, and the places between two old units (-1: the start or
    the end) where `other` has lines that `old` does not."""
    olds, others = lines(old), lines(other)
    matcher = difflib.SequenceMatcher(
        None, [(line.row, line.text) for line in olds], [(line.row, line.text) for line in others], autojunk=False
    )
    found, gaps = [], []
    for tag, i1, i2, _, _ in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            found.extend(index for line in olds[i1:i2] for index in range(line.start, line.start + len(line.units)))
        elif tag == "insert":
            after = olds[i1 - 1].start + len(olds[i1 - 1].units) - 1 if i1 else -1
            gaps.append([after, olds[i1].start if i1 < len(olds) else -1])
    return {"units": found, "gaps": gaps}


# --- Excel ---------------------------------------------------------------------------------------------------------


COLUMN = re.compile(r"([A-Z]{1,3})(?: \(숨김\))?")
FORMULA = re.compile(r"(.*?)[ ]*수식: (`+)(.+?)\2", re.S)
SHEET_SUFFIXES = (CONTINUED, " (숨긴 시트)", " (차트 시트)")


def sheet_names(heading):
    """The sheet names a sheet's heading may stand for, most likely first: as it is, then without what the reader and
    the parts added after it."""
    names, name, changed = [heading], heading, True
    while changed:
        changed = False
        for suffix in SHEET_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                names.append(name)
                changed = True
    return list(dict.fromkeys(names))


def letters_row(row):
    """Whether a table row is the cell table's header: an empty corner and column letters."""
    return len(row) > 1 and not plain(row[0]) and all(COLUMN.fullmatch(plain(cell)) for cell in row[1:])


def sheet_tables(markdown):
    """(tables, other lines) of an Excel page's Markdown: [(sheet heading, [row as Markdown cells])] with each sheet's
    cell table, rows starting with their number, and the page's lines outside those tables."""
    tables, others, heading = [], [], ""
    rows = None  # the rows of the cell table being read
    for line in markdown.replace("\r\n", "\n").split("\n"):
        found = atx_heading(line)
        if found and found[0] == 2:
            heading = plain(found[1])
        if ROW.match(line):
            row = cells(line)
            if rows is None and letters_row(row):
                rows = []
                tables.append((heading, rows))
                continue
            if rows is not None:
                if not TABLE_RULE.fullmatch(line):
                    rows.append(row)
                continue
        else:
            rows = None
        if line.strip():
            others.append(norm(line))
    return tables, others


def grid(html):
    """The cells of each sheet's table on an Excel page, as the Markdown text has them: [(sheet heading, [[address or
    None per cell after the row number]])], a row per table row after the column letters. A merged cell has its first
    address, then None for each further column it spans."""
    found = []
    for section in BeautifulSoup(html, "html.parser").select("section.sheet"):
        heading = section.find("h2")
        name = norm(heading.get_text()) if heading else ""
        for table in section.find_all("table", class_="cells"):
            rows = table.find_all("tr", recursive=False) or table.find_all("tr")
            if not rows:
                continue
            letters = [
                match.group(1) if (match := COLUMN.fullmatch(norm(cell.get_text()))) else None
                for cell in rows[0].find_all(["th", "td"])[1:]
            ]
            covered, mapped = set(), []
            for index, tr in enumerate(rows[1:]):
                number = tr.find("th")
                row = norm(number.get_text()) if number else ""
                addresses, column = [], 0
                for cell in tr.find_all("td"):
                    while (index, column) in covered:
                        column += 1
                    across = int(cell["colspan"]) if str(cell.get("colspan", "")).isdigit() else 1
                    down = int(cell["rowspan"]) if str(cell.get("rowspan", "")).isdigit() else 1
                    across, down = max(across, 1), max(down, 1)
                    letter = letters[column] if column < len(letters) else None
                    addresses.append(f"{letter}{row}" if letter and row.isdigit() else None)
                    addresses.extend([None] * (across - 1))
                    covered.update(
                        (index + below, column + beside) for below in range(down) for beside in range(across)
                    )
                    column += across
                mapped.append(addresses)
            found.append((name, mapped))
    return found


def excel_value(cell):
    """(text shown, formula or None) of a Markdown cell of an Excel page: the value as Excel shows it, and the formula
    the page writes after it."""
    match = FORMULA.fullmatch(cell.strip())
    if match:
        return plain(match.group(1)), match.group(3).strip()
    return plain(cell), None


def excel_changes(parts):
    """The cells the user changed on an Excel page: [{"sheet": [names], "address", "old", "new", "formula": [old, new]
    or None}]. `parts` are (Markdown imported, Markdown now, HTML page) of the document's parts. Only values and formulas
    of cells go back; a change of a sheet's rows or columns or of anything outside the cells raises UserError."""
    found = []
    for heading, row, address, (old_shown, old_formula), (new_shown, new_formula) in excel_cells(parts):
        if address is None:
            raise UserError(
                f"‘{heading}’ 시트 {row}행에서 병합된 셀이 덮은 칸에 쓴 것은 원본에 반영할 수 없습니다. "
                "병합된 셀의 첫 칸에 써 주세요."
            )
        if old_formula is not None and new_formula is None:
            raise UserError(f"{address} 셀의 수식을 지운 것은 원본에 반영할 수 없습니다. 수식을 고쳐 주세요.")
        if old_formula is not None and old_formula == new_formula:
            raise UserError(f"{address} 셀은 수식이 계산한 값이라 값만 바꿀 수 없습니다. 수식을 고쳐 주세요.")
        found.append(
            {
                "sheet": sheet_names(heading),
                "address": address,
                "old": old_shown,
                "new": new_shown,
                "formula": None if old_formula is None and new_formula is None else [old_formula, new_formula],
            }
        )
    if not found:
        raise Unchanged("가져온 뒤 고친 셀이 없어 원본에 반영할 것이 없습니다.")
    return found


def excel_witness(parts):
    """What tells a workbook holding the first texts of `parts` [(Markdown, Markdown, HTML page)] from one holding the
    second: [{"sheet": [names], "address", "old", "formula"}], the cells that differ, as the first text has them."""
    return [
        {"sheet": sheet_names(heading), "address": address, "old": shown, "formula": formula}
        for heading, _, address, (shown, formula), _ in excel_cells(parts)
        if address
    ]


def excel_cells(parts):
    """The cells that differ between two texts of an Excel page's parts [(Markdown before, Markdown after, HTML page)]:
    [(sheet heading, row number, address or None, (shown, formula) before, (shown, formula) after)], None for a column
    a merged cell covers. A change of a sheet's rows or columns or of anything outside the cells raises UserError."""
    found = []
    for old_text, new_text, html in parts:
        if old_text == new_text:
            continue
        if not html:
            raise UserError(
                "이 문서에는 가져올 때 만든 HTML 문서가 없어 셀 위치를 알 수 없습니다. ‘원본에서 업데이트’로 "
                "다시 가져온 뒤 고쳐 주세요."
            )
        (old_tables, old_others), (new_tables, new_others) = sheet_tables(old_text), sheet_tables(new_text)
        layout = grid(html)
        if old_others != new_others:
            changed = next((line for line in new_others if line not in old_others), "") or next(
                (line for line in old_others if line not in new_others), ""
            )
            raise UserError(
                "셀 밖의 글(시트 이름·차트·메모·도형 등)을 고친 것은 원본에 반영할 수 없습니다"
                + (f": ‘{changed[:40]}’" if changed else "")
                + ". 원래대로 되돌린 뒤 다시 반영해 주세요."
            )
        if [(name, len(rows)) for name, rows in old_tables] != [(name, len(rows)) for name, rows in new_tables]:
            raise UserError("시트의 행을 더하거나 지운 것은 원본에 반영할 수 없습니다. 셀의 값만 고칠 수 있습니다.")
        if [len(rows) for _, rows in layout] != [len(rows) for _, rows in old_tables]:
            raise UserError(
                "가져온 HTML 문서와 글의 시트가 맞지 않아 셀 위치를 알 수 없습니다. ‘원본에서 업데이트’로 다시 "
                "가져온 뒤 고쳐 주세요."
            )
        for (heading, old_rows), (_, new_rows), (_, addresses) in zip(old_tables, new_tables, layout):
            for old_row, new_row, row_addresses in zip(old_rows, new_rows, addresses):
                if plain(old_row[0]) != plain(new_row[0]) or len(old_row) != len(new_row):
                    raise UserError(
                        f"‘{heading}’ 시트의 행 번호나 열을 바꾼 것은 원본에 반영할 수 없습니다. 셀의 값만 "
                        "고칠 수 있습니다."
                    )
                for position, (old_cell, new_cell) in enumerate(zip(old_row[1:], new_row[1:])):
                    before, after = excel_value(old_cell), excel_value(new_cell)
                    if before != after:
                        address = row_addresses[position] if position < len(row_addresses) else None
                        found.append((heading, plain(old_row[0]), address, before, after))
    return found


# --- Finding the units in the file ---------------------------------------------------------------------------------


class Text(NamedTuple):
    """A unit of the file's own text: characters start:end of a container (Word's main text, a PowerPoint text frame,
    a Visio shape), compared as `text`, each character of which stands for the raw ones at `index`."""

    container: int
    start: int
    end: int
    text: str
    index: list  # [(start, end)] in the container, a pair per character of text
    separator: str  # what ends it in the container: "\r", "\v", "\r\x07" (a Word table cell), ..., "" at the end
    scope: int
    cell: tuple = None  # (table, row, column) of a table cell's text frame (PowerPoint), counted from 1


def normalized(raw, start=0, translate=None):
    """(text, index): `raw` with runs of white space as one space and none at either end, and for each character of
    text the [start, end) of the raw characters it stands for. `translate` maps raw characters, or drops them ("")."""
    chars, index = [], []
    position, size = 0, len(raw)

    def mapped(char):
        return translate.get(char, char) if translate else char

    while position < size:
        char = mapped(raw[position])
        if not char:
            position += 1
            continue
        if char.isspace():
            end = position
            while end < size and (mapped(raw[end]).isspace() or not mapped(raw[end])):
                end += 1
            if chars and end < size:
                chars.append(" ")
                index.append((start + position, start + end))
            position = end
            continue
        chars.append(char)
        index.append((start + position, start + position + 1))
        position += 1
    return "".join(chars), index


def split_units(raw, container, scope, separators, translate=None, cell=None):
    """The units of a container's raw text, split at `separators` (a compiled pattern, longest alternative first)."""
    found, start = [], 0
    for match in separators.finditer(raw):
        text, index = normalized(raw[start : match.start()], start, translate)
        found.append(Text(container, start, match.start(), text, index, match.group(0), scope, cell))
        start = match.end()
    text, index = normalized(raw[start:], start, translate)
    found.append(Text(container, start, len(raw), text, index, "", scope, cell))
    return found


def key(text):
    """(what a unit is compared as, where that starts in it): without a list number or bullet before it."""
    match = LABEL.match(text)
    if match and match.end() < len(text):
        return text[match.end() :], match.end()
    return text, 0


def same(text, pieces):
    """Whether the texts of file units `pieces` (a table cell's paragraphs and lines one after another) hold the text
    of an imported unit as the import read it: the same but for white space, a list number or bullet the page shows
    before a paragraph and the file draws, and what stands for a picture, a note or a comment. A number or bullet both
    have must be the same."""
    label, patterns = f"(?:{LABEL.pattern})?", []
    for piece in pieces:
        bits = MARKS.split(norm(TRACE.sub("", results(piece))))
        patterns.append(label + rf" ?{NOTE} ?".join(re.escape(norm(bit)) for bit in bits))
    return re.fullmatch(" ?".join(patterns), norm(TRACE.sub("", text))) is not None


def results(text):
    """A unit of Word's text as its fields show, should it hold their codes (character 19, the code, 20, the result,
    21): the results only, as the page has them."""
    if "\x13" not in text:
        return text
    found, fields = [], []  # for each field the character is in, whether its result has begun
    for char in text:
        if char == "\x13":
            fields.append(False)
        elif char == "\x14" and fields:
            fields[-1] = True
        elif char == "\x15" and fields:
            fields.pop()
        elif all(fields):
            found.append(char)
    return "".join(found)


def visible(text):
    """Whether a unit of the file holds text, not only white space and marks of pictures, notes and comments."""
    return bool(MARKS.sub("", results(text)).strip())


def align(old, doc):
    """How the characters of the old units meet those of the file's: for each old unit, [(start, file unit, start
    there, size)] in order. Units the same but for a list number or bullet are paired whole; between them the characters
    of the units that differ are compared."""
    found = [[] for _ in old]
    old_keys, doc_keys = [key(text) for text in old], [key(text) for text in doc]
    olds = [i for i, text in enumerate(old) if text]
    docs = [j for j, text in enumerate(doc) if text]
    matcher = difflib.SequenceMatcher(
        None, [old_keys[i][0] for i in olds], [doc_keys[j][0] for j in docs], autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for i, j in zip(olds[i1:i2], docs[j1:j2]):
                (text, start), (_, doc_start) = old_keys[i], doc_keys[j]
                if start and old[i][:start] == doc[j][:doc_start]:
                    found[i].append((0, j, 0, start))
                found[i].append((start, j, doc_start, len(text)))
        elif tag == "replace":
            compare(old, doc, olds[i1:i2], docs[j1:j2], found)
    return found


def compare(old, doc, olds, docs, found):
    """The characters of the old units `olds` matched with those of the file's units `docs`, added to `found`."""
    first, first_at = joined(old, olds)
    second, second_at = joined(doc, docs)
    if len(first) * len(second) > ALIGN_LIMIT:
        return
    runs = []
    for a, b, size in difflib.SequenceMatcher(None, first, second, autojunk=False).get_matching_blocks():
        run = None
        for offset in range(size):
            left, right = first_at[a + offset], second_at[b + offset]
            if left is None or right is None:
                run = None
            elif (
                run
                and run[0] == left[0]
                and run[2] == right[0]
                and run[1] + run[4] == left[1]
                and run[3] + run[4] == right[1]
            ):
                run[4] += 1
            else:
                run = [left[0], left[1], right[0], right[1], 1]
                runs.append(run)
    for i, start, j, doc_start, size in runs:
        found[i].append((start, j, doc_start, size))
    for i in olds:
        found[i].sort()


def joined(texts, chosen):
    """The texts `chosen` joined by line ends, and for each character the (text, position) it is, None for the ends."""
    text, positions = [], []
    for number, i in enumerate(chosen):
        if number:
            text.append("\n")
            positions.append(None)
        text.append(texts[i])
        positions.extend((i, offset) for offset in range(len(texts[i])))
    return "".join(text), positions


def position(blocks, at):
    """(file unit, position there) of the old unit's character `at`, or None."""
    for start, unit, doc_start, size in blocks:
        if start <= at < start + size:
            return unit, doc_start + at - start
    return None


def place(blocks, start, end, size):
    """(file unit, start, end) in its compared text for the old unit's characters start:end, an insertion when they
    are equal, or None when they are not all in one unit of the file, in order and side by side."""
    if start < end:
        covered, unit, first, at = start, None, None, None
        for block_start, block_unit, block_doc, block_size in blocks:
            if block_start + block_size <= covered:
                continue
            if block_start > covered:
                return None
            here = block_doc + covered - block_start
            if unit is None:
                unit, first = block_unit, here
            elif block_unit != unit or here != at:
                return None
            taken = min(block_start + block_size, end) - covered
            covered, at = covered + taken, here + taken
            if covered >= end:
                return unit, first, at
        return None
    left = position(blocks, start - 1) if start > 0 else None
    right = position(blocks, start) if start < size else None
    if left and right:
        return (left[0], left[1] + 1, left[1] + 1) if right[0] == left[0] and right[1] == left[1] + 1 else None
    # Beside a character the file does not have (a list number it draws), the one it has tells the place.
    if left:
        return left[0], left[1] + 1, left[1] + 1
    if right:
        return right[0], right[1], right[1]
    return None


def raw_span(unit, start, end):
    """The container's raw [start, end) of a file unit's compared characters start:end."""
    if start < end:
        return unit.index[start][0], unit.index[end - 1][1]
    if start < len(unit.index):
        return unit.index[start][0], unit.index[start][0]
    point = unit.index[-1][1] if unit.index else unit.start
    return point, point


def primary(blocks):
    """The file unit most of an old unit's characters are in, or None."""
    sizes = {}
    for _, unit, _, size in blocks:
        sizes[unit] = sizes.get(unit, 0) + size
    return max(sizes, key=sizes.get) if sizes else None


class Missing(UserError):
    """A changed place the file does not have."""


class Changed(Missing):
    """A changed place the file has with other text than the import read there: someone changed the file since."""


class Elsewhere(Exception):
    """The file does not hold the text a choice of the plan was made against (choose)."""


def shown(text):
    return text if len(text) <= 40 else text[:40] + "…"


def missing(text, what="원본 파일에서 고칠 곳을 찾지 못했습니다"):
    return Missing(f"{what}: ‘{shown(text)}’ {UPDATE}")


def changed(text, now, what="원본 파일의 이 줄이 가져온 때와 달라 반영하지 않았습니다"):
    """Changed, saying what the file holds `now` where the imported `text` was."""
    return Changed(f"{what}: ‘{shown(text)}’(지금 원본: ‘{shown(norm(MARKS.sub(' ', results(now))))}’) {UPDATE}")


def holder(old, doc, blocks, i):
    """The file units the old unit `i` stands for, from the first to the last its characters were found in, or None
    when none was found: for a table cell the whole cell, whose paragraphs and lines the page shows as one line."""
    touched = sorted({unit for _, unit, _, _ in blocks[i]})
    if not touched:
        return None
    first, last = touched[0], touched[-1]
    if old[i].kind == "cell" and doc[first].cell is not None:
        # A PowerPoint cell is a text frame of its own.
        while first > 0 and doc[first - 1].container == doc[first].container:
            first -= 1
        while last + 1 < len(doc) and doc[last + 1].container == doc[last].container:
            last += 1
    elif old[i].kind == "cell":
        # In Word's text, the lines before it in its paragraph, and the cell's paragraphs up to the cell's end.
        container = doc[first].container
        while first > 0 and doc[first - 1].separator in LINE_BREAKS and doc[first - 1].container == container:
            first -= 1
        while last + 1 < len(doc) and doc[last].separator not in ("\r\x07", "\x07", ""):
            last += 1
    return range(first, last + 1)


def held(old, doc, homes, i, what):
    """Refuse a change of the old unit `i` the file does not have (Missing, saying `what` was not found) or has with
    other text than the import read (Changed). `homes` are the file units of each old unit (holder)."""
    if homes[i] is None:
        raise missing(old[i].text, what)
    pieces = [doc[k].text for k in homes[i]]
    if not same(old[i].text, pieces):
        raise changed(old[i].text, " ".join(pieces))


def intruder(old, doc, homes, after, before):
    """The first unit of the file with text between the old units `after` and `before` (-1: the start or the end) that
    none of the old units between them stands for, or None: a line written there since, which a line added between
    them would land beside."""
    low = next((homes[k][-1] for k in range(after, -1, -1) if homes[k]), -1)
    end = before if before >= 0 else len(old)
    high = next((homes[k][0] for k in range(end, len(old)) if homes[k]), len(doc))
    between = {unit for k in range(after + 1, end) if homes[k] for unit in homes[k]}
    return next((j for j in range(low + 1, high) if j not in between and visible(doc[j].text)), None)


def blank(old, doc, blocks, i, marked_rows):
    """The file unit of the empty old unit `i` (a blank table cell), found empty there, or None."""
    target = cell_place(old, doc, blocks, i)
    if target is None:
        found = empty_place(old, doc, blocks, i, marked_rows)
        target = found[0] if found else None
    return target


def proven(plan, old, doc, blocks, homes, marked_rows):
    """Raise Elsewhere unless the file holds the text the plan was made against where it differs from the other texts
    a write-back may have left the file with (plan["witness"], none for a plan made against the imported text only)."""
    proof = plan.get("witness")
    if not proof:
        return
    for i in proof["units"]:
        if old[i].text:
            if homes[i] is None or not same(old[i].text, [doc[k].text for k in homes[i]]):
                raise Elsewhere
        elif blank(old, doc, blocks, i, marked_rows) is None:
            raise Elsewhere
    if any(intruder(old, doc, homes, after, before) is not None for after, before in proof["gaps"]):
        raise Elsewhere


def cell_place(old, doc, blocks, i):
    """The file unit of the empty old cell `i` where the file's units know their table cell (PowerPoint): a cell of the
    same table that was found tells which table it is and how its rows are numbered there (a Markdown table may start
    with an empty header row). None when no cell of that table was found, or the cell there is not empty."""
    unit = old[i]
    if unit.kind != "cell":
        return None
    near = sorted(
        (
            k
            for k, other in enumerate(old)
            if other.kind == "cell"
            and other.table == unit.table
            and other.scope == unit.scope
            and other.text
            and primary(blocks[k]) is not None
        ),
        key=lambda k: abs(k - i),
    )
    for k in near:
        found = doc[primary(blocks[k])].cell
        if found is None:
            return None
        table, row, column = found
        wanted = (table, row + unit.row - old[k].row, column + unit.column - old[k].column)
        target = next((j for j, other in enumerate(doc) if other.cell == wanted), None)
        if target is None or doc[target].text or doc[target].scope != unit.scope:
            return None
        return target
    return None


def row_ends(old, start, end):
    """How many table rows end between the old units start and end: Word's text has a mark there that the Markdown
    table does not."""
    return sum(
        1
        for k in range(start + 1, end + 1)
        if old[k - 1].kind == "cell"
        and (old[k].kind != "cell" or old[k].table != old[k - 1].table or old[k].row != old[k - 1].row)
    )


def empty_place(old, doc, blocks, i, marked_rows):
    """(file unit, via) of the empty old unit `i` (a blank table cell), told by place: the units of the same slide or
    page nearest before and after it that were found have as many units between them in the file (less the lines the
    reader wrote, plus the ends of rows in Word: `marked_rows`). A merged cell makes them differ, and the cell is not
    written. In Word, a found cell of the same row also tells it, for the cells at a table's end. `via` is how Word
    reaches the cell, having no text there to search for: (start, end, steps, rows, column) of the nearest found cell of
    its table, how many cells on (or back) it is, and the row Word must say it is in, as rows below that cell's (a
    Markdown table starts with an empty header row unless Word repeats its first row as one), and the column. None when
    not found."""
    scope = old[i].scope
    before = next((k for k in range(i - 1, -1, -1) if old[k].scope == scope and primary(blocks[k]) is not None), None)
    after = next((k for k in range(i + 1, len(old)) if old[k].scope == scope and primary(blocks[k]) is not None), None)
    marks = (lambda start, end: row_ends(old, start, end)) if marked_rows else (lambda start, end: 0)
    # Lines the reader wrote (a heading of its own) are not in the file's text.
    absent = lambda start, end: sum(1 for k in range(start + 1, end) if not old[k].editable)
    target = None
    if before is not None and after is not None:
        first, last = primary(blocks[before]), primary(blocks[after])
        if last - first == after - before - absent(before, after) + marks(before, after):
            target = first + i - before - absent(before, i) + marks(before, i)
    cells = [k for k in (before, after) if k is not None and old[k].kind == "cell" and old[k].table == old[i].table]
    if target is None and marked_rows:
        row = [k for k in cells if old[k].row == old[i].row]
        target = primary(blocks[row[0]]) + i - row[0] if row else None
    if target is None or not 0 <= target < len(doc) or doc[target].text or doc[target].scope != scope:
        return None
    if not marked_rows:
        return target, None
    if not cells:
        return None
    unit = doc[primary(blocks[cells[0]])]
    return target, (unit.start, unit.end, i - cells[0], old[i].row - old[cells[0]].row, old[i].column + 1)


def choose(plan, doc, separators, marked_rows=False, searched=False):
    """(number, choice, replacements) of the first of the plan's choices (or the plan itself) whose text the file is
    found to hold where they differ, with its replacements (replacements). Raises UserError when the file holds none
    of them, or already holds the document's text."""
    choices = plan.get("choices") or [plan]
    for number, choice in enumerate(choices):
        try:
            found = replacements(choice, doc, separators, marked_rows, searched)
        except Elsewhere:
            continue
        if not choice["edits"]:
            raise UserError(ALREADY)
        return number, choice, found
    raise UserError(ELSEWHERE)


def replacements(plan, doc, separators, marked_rows=False, searched=False):
    """The raw replacements [(container, start, end, text, via)] that make the plan's edits in the file whose units are
    `doc`, in the order they apply: from the end. `via` is None, or how Word reaches a blank table cell (empty_place).
    `separators` are (paragraph, line break) for added lines; `marked_rows` says the file's text marks the end of a
    table row (Word); `searched` that a place is found by searching for the text around it (Word). Insertions at one
    point are one replacement. Raises Missing for a place the file does not have, Changed for one where the file holds
    other text than the import read, and Elsewhere when it does not hold the text of the plan's witness."""
    old = [Unit(*unit) for unit in plan["units"]]
    blocks = [[] for _ in old]
    for scope in sorted({unit.scope for unit in old}):
        olds = [i for i, unit in enumerate(old) if unit.scope == scope]
        docs = [j for j, unit in enumerate(doc) if unit.scope == scope]
        if docs:
            for i, items in zip(olds, align([old[i].text for i in olds], [doc[j].text for j in docs])):
                blocks[i] = [(start, docs[unit], doc_start, size) for start, unit, doc_start, size in items]
    homes = [holder(old, doc, blocks, i) for i in range(len(old))]
    proven(plan, old, doc, blocks, homes, marked_rows)
    found = []  # (container, start, end, text, via, order): order puts insertions at one point in the text's order
    whole = set()  # the file units of lines deleted whole
    for edit in plan["edits"]:
        if edit["op"] == "insert":
            found.append(insertion(edit, old, doc, blocks, homes, separators))
            continue
        i = edit["unit"]
        unit = old[i]
        if edit["op"] == "delete":
            held(old, doc, homes, i, "원본 파일에서 지울 곳을 찾지 못했습니다")
            spot = place(blocks[i], 0, len(unit.text), len(unit.text)) or place(
                blocks[i], key(unit.text)[1], len(unit.text), len(unit.text)
            )
            if spot is None:
                raise missing(unit.text, "원본 파일에서 지울 곳을 찾지 못했습니다")
            if spot[1] == 0 and spot[2] == len(doc[spot[0]].text):
                whole.add(spot[0])
            else:
                found.append((doc[spot[0]].container, *raw_span(doc[spot[0]], spot[1], spot[2]), "", None, LINE))
            continue
        if unit.text:
            held(old, doc, homes, i, "원본 파일에서 고칠 곳을 찾지 못했습니다")
        empty = None
        if not unit.text:
            target = cell_place(old, doc, blocks, i)
            empty = (target, None) if target is not None else empty_place(old, doc, blocks, i, marked_rows)
        for start, end, text in edit["spans"]:
            spot = (empty[0], 0, 0) if empty else place(blocks[i], start, end, len(unit.text))
            if spot is None:
                raise missing(
                    unit.text or text,
                    "원본 파일에서 고칠 곳을 찾지 못했습니다"
                    if unit.text
                    else "원본 파일에서 빈 칸의 자리를 찾지 못했습니다(병합된 셀 옆이나 표의 처음·끝 칸)",
                )
            found.append(
                (
                    doc[spot[0]].container,
                    *raw_span(doc[spot[0]], spot[1], spot[2]),
                    text,
                    empty[1] if empty else None,
                    LINE,
                )
            )
    found.extend(removals(doc, whole))
    found = joined_insertions(found, searched)
    found.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    for later, earlier in zip(found, found[1:]):
        if later[0] == earlier[0] and earlier[2] > later[1]:
            raise UserError("고친 곳이 원본에서 서로 겹쳐 반영하지 못했습니다. 나누어 고쳐 반영해 주세요.")
    return found


# Where an insertion goes among others at the same point: lines added before a line, then text added to the line
# itself, then lines added after it.
BEFORE, LINE, AFTER = 0, 1, 2


def joined_insertions(found, searched=False):
    """The replacements, with the insertions at one point of a container made one, their texts in the order the file
    will have them: text added at the end of a line and a paragraph added after it are both inserted where the line
    ends, and inserted one after the other the second would land before the first. Where a place is found by the text
    after it (`searched`, Word), insertions are also made one with a replacement that begins at their point: once that
    replacement is made, the text after the point is no longer the one to search for."""
    points, kept = {}, []
    for container, start, end, text, via, order in found:
        if start == end and via is None:
            points.setdefault((container, start), []).append((order, text))
        else:
            kept.append([container, start, end, text, via])
    begins = {(item[0], item[1]): item for item in kept if searched and item[4] is None}
    for (container, point), texts in points.items():
        text = "".join(text for _, text in sorted(texts, key=lambda item: item[0]))
        if (container, point) in begins:
            begins[container, point][3] = text + begins[container, point][3]
        else:
            kept.append([container, point, point, text, None])
    return [tuple(item) for item in kept]


LINE_BREAKS = ("\v", "\u2028")


def removals(doc, whole):
    """The removals of the file units `whole` (indices), lines deleted whole. Lines of one paragraph that follow one
    another (empty lines between them aside) go as one, so they do not both take the line break between them."""
    runs = []
    for unit in sorted(whole):
        last = runs[-1][1] if runs else None
        if (
            last is not None
            and doc[unit].container == doc[last].container
            and all(doc[k].separator in LINE_BREAKS for k in range(last, unit))
            and not any(doc[k].text for k in range(last + 1, unit))
        ):
            runs[-1][1] = unit
        else:
            runs.append([unit, unit])
    return [removal(doc, first, last) for first, last in runs]


def removal(doc, first, last):
    """Deleted lines, the file units first to last of one paragraph: their text and a break beside them, so no empty
    line is left. Lines after a line break take that break along, so the paragraph's own mark, which holds its
    formatting, stays; others take the break after them, else the one before. A table cell keeps its end, and Word's
    last paragraph its mark (its text goes, as the paragraph before would otherwise take that mark and its
    formatting)."""
    target, end = doc[first], doc[last]
    container = target.container
    if end.separator == "\r\x07":
        return container, target.start, end.end, "", None, LINE
    previous = doc[first - 1] if first > 0 and doc[first - 1].container == container else None
    if previous is not None and previous.separator in LINE_BREAKS:
        return container, previous.end, end.end, "", None, LINE
    following = doc[last + 1] if last + 1 < len(doc) and doc[last + 1].container == container else None
    final = (
        following is not None
        and not following.text
        and not following.separator
        and (last + 2 >= len(doc) or doc[last + 2].container != container)
    )
    if final:
        return container, target.start, end.end, "", None, LINE
    if end.separator:
        return container, target.start, end.end + len(end.separator), "", None, LINE
    if previous is not None and previous.separator and previous.separator != "\r\x07":
        return container, previous.end, end.end, "", None, LINE
    return container, target.start, end.end, "", None, LINE


def insertion(edit, old, doc, blocks, homes, separators):
    """Lines added beside a line of the file: after the line before them, or before the line after them, whichever is of
    the kind of the new line (a heading beside a heading), never into a table cell. A number or bullet the file draws
    beside the line is not written again. The line beside them must hold the text the import read, and the file must
    have nothing between the lines they go between that the import did not see there (Changed)."""
    paragraph, line_break = separators
    lines = edit["lines"]
    beside = intruder(old, doc, homes, edit["after"], edit["before"])
    if beside is not None:
        raise changed(
            lines[0][0],
            doc[beside].text,
            "원본 파일에서 새 줄을 넣을 곳에 가져온 때 없던 줄이 있어 반영하지 않았습니다",
        )
    options = [("after", edit["after"]), ("before", edit["before"])]
    options = [(side, i) for side, i in options if i >= 0]
    options.sort(
        key=lambda option: (
            old[option[1]].kind != (lines[0][1] if option[0] == "after" else lines[-1][1]),
            option[0] != "after",
        )
    )
    differs = None  # an old unit beside them the file holds with other text
    for side, i in options:
        if homes[i] is None or old[i].kind == "cell":
            continue
        unit = homes[i][-1] if side == "after" else homes[i][0]
        if doc[unit].separator == "\r\x07":
            continue
        if not same(old[i].text, [doc[k].text for k in homes[i]]):
            differs = i if differs is None else differs
            continue
        drawn = key(old[i].text)[1] and position(blocks[i], 0) is None
        values = [key(text)[0] if drawn else text for text, _, _ in lines]
        target = doc[unit]
        if side == "after":
            text = "".join(
                (line_break if joined else paragraph) + value for value, (_, _, joined) in zip(values, lines)
            )
            return target.container, target.end, target.end, text, None, AFTER
        breaks = [line_break if joined else paragraph for _, _, joined in lines[1:]]
        previous = doc[unit - 1] if unit > 0 and doc[unit - 1].container == target.container else None
        tail = line_break if previous is not None and previous.separator == line_break else paragraph
        text = "".join(value + separator for value, separator in zip(values, breaks + [tail]))
        return target.container, target.start, target.start, text, None, BEFORE
    if differs is not None:
        raise changed(
            old[differs].text,
            " ".join(doc[k].text for k in homes[differs]),
            "원본 파일에서 새 줄 옆의 줄이 가져온 때와 달라 반영하지 않았습니다",
        )
    raise missing(lines[0][0], "원본 파일에서 새 줄을 넣을 곳을 찾지 못했습니다")
