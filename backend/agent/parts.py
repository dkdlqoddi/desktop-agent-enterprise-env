"""An import is one Wiki document, however long, and the documents earlier versions cut a long one into are one again.

A Wiki document holds at most MAX_DOCUMENT_CHARS characters of text, which the editor edits and search, PageIndex and
the LLM read. A page, ticket or file with more text keeps its beginning (fit_markdown, fit_page): the cut falls
between blocks, and a heading or a table's header is not left without what it introduces; a block too long by itself
is cut between its lines or rows. What is kept ends with a note saying that the rest was left out.

Earlier versions cut a text longer than 80,000 characters into several documents titled "<title> (2/5)", each with a
source row numbered `part` of `parts`. merge() makes the documents of such an import one: the first of them takes the
text and the page of all, and the others are deleted with a note of where they went (merged_documents), so that a
link, a citation or a task that names one of them finds the whole document. The pages of a file's parts become the
page the file had, without what the earlier version repeated where it cut them (join_pages). Database.merge_parts does
so for every such import at the backend's start, for as long as its time allows and then beside the requests;
sources.py and Service.document do it sooner for the import they are asked about."""

import re
import time
from html import escape
from typing import NamedTuple

from bs4 import BeautifulSoup, NavigableString, Tag

from .html_document import index_markdown, page_text
from .llm import UserError
from .markdown_import import MAX_DOCUMENT_CHARS

CUT = "문서가 길어 여기까지만 가져왔습니다. 나머지는 원본에서 확인해 주세요."
# What is kept of a longer text leaves room for that note.
NOTE_ROOM = len(CUT) + 8
PIECE = 4000  # characters of a text too long to keep whole, per piece it is cut into
# What an import whose end was left out tells the user.
CUT_MESSAGE = f"글이 {MAX_DOCUMENT_CHARS:,}자를 넘어 앞부분만 저장했습니다. 나머지는 원본에서 확인해 주세요."


class Fitted(NamedTuple):
    page: str | None  # the HTML page of a file; None for a Markdown text
    text: str  # the document's text
    cut: bool  # whether the end of a text longer than a document holds was left out


def cut_text(text, size):
    """Text in pieces of at most `size` characters, cut after a line break or a space where there is one."""
    found = []
    while len(text) > size:
        end = text.rfind("\n", size // 2, size) + 1 or text.rfind(" ", size // 2, size) + 1 or size
        found.append(text[:end])
        text = text[end:]
    return found + [text] if text else found


# --- Markdown --------------------------------------------------------------------------------------------------------

FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
ROW = re.compile(r" {0,3}\|")
RULE = re.compile(r" {0,3}\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)*\|?\s*")
HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t]|$)")


def blocks(text):
    """The text as [(kind, lines)]: a fenced code block, a table or the lines up to a blank line, each with the blank
    lines after it. What is kept of a long text ends with one of them."""
    lines, found, index = text.splitlines(keepends=True), [], 0
    while index < len(lines):
        first, end = lines[index], index + 1
        fence = FENCE.match(first)
        if fence:
            mark = fence.group(1)
            closing = re.compile(rf" {{0,3}}{re.escape(mark[0])}{{{len(mark)},}}[ \t]*\r?\n?")
            while end < len(lines) and not closing.fullmatch(lines[end]):
                end += 1
            end, kind = min(end + 1, len(lines)), "code"
        elif ROW.match(first):
            while end < len(lines) and ROW.match(lines[end]):
                end += 1
            kind = "table"
        else:
            while end < len(lines) and lines[end].strip() and not FENCE.match(lines[end]):
                end += 1
            kind = "heading" if end == index + 1 and HEADING.match(first) else "text"
        while end < len(lines) and not lines[end].strip():
            end += 1
        found.append((kind, lines[index:end]))
        index = end
    return found


def first_piece(kind, lines, room):
    """The beginning of a block longer than `room`, at most that long and ending in a blank line: whole lines or rows,
    a code block with its closing fence. A line longer than the room is cut after a space where there is one."""
    body = [line if line.endswith("\n") else line + "\n" for line in lines]
    tail = ""
    if kind == "code":
        mark = FENCE.match(body[0]).group(1)
        tail = mark + "\n"
        if len(body[0]) + len(tail) > room // 2:
            tail = ""  # a fence that long is not closed: the note follows the text as it is
    size = max(1, room - len(tail) - 1)
    kept, used = [], 0
    for line in body:
        segment = cut_text(line, size)[0] if len(line) > size else line
        if kept and used + len(segment) > size:
            break
        kept.append(segment)
        used += len(segment)
        if segment is not line:
            break
    return "".join(kept) + tail + "\n"


def first_markdown(text, limit):
    """(the beginning of a Markdown text, whether its end was left out): the blocks that fit `limit` characters with
    the note that says so. A heading does not end what is kept; a block too long by itself gives its first lines."""
    room = limit - NOTE_ROOM
    kept, size, cut = [], 0, False
    for kind, lines in blocks(text.strip()):
        block = "".join(lines)
        if size + len(block) > room:
            cut = True
            if not kept:
                kept.append(("text", first_piece(kind, lines, room)))
            elif kept[-1][0] == "heading":
                if len(kept) > 1:
                    kept.pop()
                elif room - size >= PIECE:  # the only heading kept so far: the beginning of what it introduces
                    kept.append(("text", first_piece(kind, lines, room - size)))
            break
        kept.append((kind, block))
        size += len(block)
    found = "".join(block for _, block in kept).strip()
    return (found + "\n\n> " + CUT if cut else found), cut


def fit_markdown(text):
    """A Markdown page as the document it is saved as: itself, or its beginning when it is longer than one holds."""
    if len(text) <= MAX_DOCUMENT_CHARS:
        return Fitted(None, text, False)
    found, cut = first_markdown(text, MAX_DOCUMENT_CHARS)
    return Fitted(None, found, cut)


# --- HTML pages ------------------------------------------------------------------------------------------------------

# Pictures stay out of the parser: a token stands for each until the page is assembled.
PICTURE = re.compile(r'(<img\b[^>]*?\ssrc=")(data:[^"]*)"', re.I)
TOKEN = re.compile(r"data:,private-agent-picture-(\d+)")
ROOT = re.compile(r'<div class="doc\b[^>]*>')
END = "</div></body></html>"
TITLE = re.compile(r"<title>(.*?)</title>", re.S)
BODY = re.compile(r"<body\b[^>]*>(.*)</body>", re.S | re.I)
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
# For the size estimate: characters Markdown escapes, and the Markdown elements add around their text.
ESCAPED = re.compile(r"[\\*_`\[\]&<>~=+|#]")
MARKUP = {
    "td": 3,
    "th": 3,
    "tr": 2,
    "li": 4,
    "p": 2,
    "div": 2,
    "section": 2,
    "figure": 2,
    "table": 2,
    "pre": 10,
    "br": 3,
    "blockquote": 4,
    "strong": 4,
    "b": 4,
    "em": 2,
    "i": 2,
    "code": 2,
    **dict.fromkeys(HEADINGS, 9),
}


def estimate(node):
    """About how many characters of Markdown a node becomes, at least 1: an element without text may still become some
    (<hr> is ---). What is kept is measured exactly before it is saved."""
    if isinstance(node, Tag):
        text = node.get_text()
        size = len(text) + len(ESCAPED.findall(text))
        for tag in (node, *node.descendants):
            if isinstance(tag, Tag):
                size += MARKUP.get(tag.name, 0)
                if tag.name == "a":
                    size += len(tag.get("href") or "") + 4
                elif tag.name == "img":
                    size += len(tag.get("alt") or "") + 6
                if tag.has_attr("data-formula"):
                    size += len(tag["data-formula"]) + 7
        return max(1, size)
    if type(node) in (str, NavigableString):
        return max(1, len(node) + len(ESCAPED.findall(node)))
    return 1  # comments and other markup without text


class Unit:
    """A share of a page that is kept or left out as one: an element, a text or a piece of one, below the elements in
    `path`."""

    __slots__ = ("path", "node", "size")

    def __init__(self, path, node):
        self.path, self.node, self.size = path, node, estimate(node)


def divide(unit):
    """The unit as smaller ones: an element's children or a text's pieces. None for what cannot be cut further."""
    node = unit.node
    if isinstance(node, Tag):
        return [Unit(unit.path + (node,), child) for child in node.children] or None
    if type(node) in (str, NavigableString) and len(node) > 1:
        return [Unit(unit.path, piece) for piece in cut_text(str(node), min(PIECE, len(node) // 2))]
    return None


def header_row(row):
    """Whether a table row is the header of the rows below it: only header cells, and the table's first row."""
    cells = row.find_all(["td", "th"], recursive=False)
    return bool(cells) and all(cell.name == "th" for cell in cells) and row.find_previous_sibling("tr") is None


def leads(node):
    """Whether a node introduces what follows it, so that what is kept does not end with it."""
    if not isinstance(node, Tag):
        return False
    return (
        node.name in HEADINGS
        or node.name in ("colgroup", "caption", "thead")
        or (node.name == "tr" and header_row(node))
    )


def start_tag(tag):
    return (
        f"<{tag.name}"
        + "".join(
            f' {name}="{escape(" ".join(value) if isinstance(value, list) else str(value))}"'
            for name, value in tag.attrs.items()
        )
        + ">"
    )


def markup(node):
    if isinstance(node, Tag):
        return str(node)
    if isinstance(node, NavigableString):
        return node.output_ready(formatter="minimal")
    return escape(node, quote=False)


def render(units):
    """The HTML of the units with the elements around them, each closed where the units leave it."""
    out, stack = [], []
    for unit in units:
        same = 0
        while same < len(stack) and same < len(unit.path) and stack[same] is unit.path[same]:
            same += 1
        while len(stack) > same:
            out.append(f"</{stack.pop().name}>")
        for tag in unit.path[same:]:
            out.append(start_tag(tag))
            stack.append(tag)
        out.append(markup(unit.node))
    while stack:
        out.append(f"</{stack.pop().name}>")
    return "".join(out)


def first_page(html, limit):
    """(the beginning of a page whose text is longer than `limit`, its text, whether its end was left out): the page
    with as many of its elements as hold at most that much text, and the note that says so after them."""
    pictures = []

    def keep(match):
        pictures.append(match.group(2))
        return f'{match.group(1)}data:,private-agent-picture-{len(pictures) - 1}"'

    html = PICTURE.sub(keep, html)
    root, end = ROOT.search(html), html.rfind(END)
    if root is None or end < root.end():
        raise UserError("변환된 HTML 문서가 길어 줄이려 했으나 구조를 읽지 못했습니다.")
    head, tail = html[: root.end()], html[end:]
    units = [
        Unit((), node)
        for node in BeautifulSoup(html[root.end() : end], "html.parser").contents
        if not (type(node) is NavigableString and not node.strip())
    ]
    room = limit - NOTE_ROOM
    # Characters of text per estimated one: a cautious guess at first, then what the try before had. What is kept is
    # measured before it is saved, so a guess that runs short only costs another try.
    ratio = 1.1
    budget = int(room * 0.97 / ratio)
    while True:
        stop, size = 0, 0
        while stop < len(units):
            unit = units[stop]
            if unit.size > budget:  # too long to keep whole: its first pieces are kept
                smaller = divide(unit)
                if smaller:
                    units[stop : stop + 1] = smaller
                    continue
            if stop and size + unit.size > budget:
                break
            size += unit.size
            stop += 1
        if stop < len(units):  # cut short: a heading or table header goes with what it introduces
            while stop > 1 and leads(units[stop - 1].node):
                stop -= 1
        body = render(units[:stop])
        text = page_text(head + body + tail)
        size = sum(unit.size for unit in units[:stop])
        if size and text:
            ratio = max(len(text) / size, 0.2)
        if len(text) <= room:
            break
        if stop > 1:
            # The next try takes fewer units, whatever the estimate says, so the cutting always ends.
            budget = max(1, min(int(room * 0.95 / ratio), size - 1))
            continue
        smaller = divide(units[0])
        if not smaller:
            raise UserError(f"한 문서에 담을 수 있는 {limit:,}자보다 긴 항목이 있어 문서를 줄이지 못했습니다.")
        units[0:1] = smaller
    cut = stop < len(units)
    if cut:
        body += f'<p class="caption">{escape(CUT)}</p>'
        text = page_text(head + body + tail)
    return TOKEN.sub(lambda match: pictures[int(match.group(1))], head + body + tail), text, cut


def fit_page(html):
    """A converted page as the document it is saved as: itself, or its beginning when its text is longer than a
    document holds."""
    text = index_markdown(html)
    if len(text) <= MAX_DOCUMENT_CHARS:
        return Fitted(html, text, False)
    return Fitted(*first_page(html, MAX_DOCUMENT_CHARS))


# --- The parts of earlier versions -----------------------------------------------------------------------------------


def whole_title(title, part, parts):
    """The title of an import, from the title an earlier version gave its part `part` of `parts`: "<title> (2/5)"."""
    suffix = f" ({part}/{parts})"
    return title[: -len(suffix)] if parts > 1 and title.endswith(suffix) else title


def join_markdown(texts):
    """The texts of a Markdown page's parts as one text again: without what a part repeated of the one before it (the
    header of a table and the fences of a code block that went on into it), and with a blank line where a part ended
    with a block."""
    lines, previous = [], None
    for text in texts:
        own = text.splitlines()
        if previous is not None:
            found = blocks(previous)
            kind, block = found[-1] if found else ("text", [])
            block = [line.rstrip("\r\n") for line in block]
            while block and not block[-1].strip():
                block.pop()
            if kind == "table" and len(block) > 2 and RULE.fullmatch(block[1]) and own[:2] == block[:2]:
                own = own[2:]
            elif (
                kind == "code"
                and len(block) > 1
                and own[:1] == block[:1]
                and lines[-1:] == block[-1:]
                and FENCE.fullmatch(block[-1].rstrip(" \t"))
            ):
                lines.pop()
                own = own[1:]
            else:
                lines.append("")
        lines += own
        previous = text
    return "\n".join(lines)


# What earlier versions repeated where they cut an element between two parts: the element was opened again in the
# next part, a table with its columns and header, a section (a sheet, a slide, a page) with its heading followed by
# GOES_ON, and a numbered list numbered on from its items before.
SECTIONS = {"section", "div", "article"}
GOES_ON = " (계속)"


def elements(node):
    """The children of `node` that are elements."""
    return [child for child in node.children if isinstance(child, Tag)]


def edge(nodes):
    """The first of `nodes` when it is an element, past white space; None when text comes first."""
    for node in nodes:
        if isinstance(node, Tag):
            return node
        if str(node).strip():
            return None
    return None


def numbered(tag):
    """The number of a numbered list's first item, as earlier versions read it."""
    return int(tag["start"]) if str(tag.get("start", "")).isdigit() else 1


def repeated(before, after, within=None):
    """How many first children of `after` an earlier version repeated, when `after`, the first element of a part, is
    `before`, the last element of the part before it, opened again where the version cut it: the same tag with the same
    attributes, beginning with what it repeated there (a table's columns and header, a section's heading with GOES_ON)
    or numbered on from it (a numbered list, whose start is then that of its next item). Inside a table that went on
    (`within`), its body went on too. None when it is not that: another element that follows, or one whose beginning
    it did not repeat, which cannot be told apart from another."""
    if not isinstance(before, Tag) or not isinstance(after, Tag) or before.name != after.name:
        return None
    if before.name == "ol":
        kept = {name: value for name, value in after.attrs.items() if name != "start"}
        items = sum(child.name == "li" for child in elements(before))
        same = kept == {name: value for name, value in before.attrs.items() if name != "start"}
        return 0 if same and numbered(after) == numbered(before) + items else None
    if before.attrs != after.attrs:
        return None
    if before.name == "tbody" and within == "table":
        return 0
    began, begins = elements(before), elements(after)
    if before.name == "table":
        head = [child for child in began if child.name in ("colgroup", "thead")]
        row = before.find("tr")
        if not any(child.name == "thead" for child in head) and row is not None and header_row(row):
            head.append(row)
        found = [str(child) for child in begins[: len(head)]] == [str(child) for child in head]
        return len(head) if head and found else None
    heading = began[0] if began else None
    if before.name in SECTIONS and heading is not None and heading.name in HEADINGS and begins:
        again = begins[0]
        if (again.name, again.attrs) == (heading.name, heading.attrs) and again.get_text() == (
            " ".join(heading.get_text().split()) + GOES_ON
        ):
            return 1
    return None


def stitch(before, after, within=None):
    """Make `after` go on in `before` where an earlier version cut it into two parts (repeated): without what it
    repeated, its children follow those of `before`, and where the two meet, an element that went on inside it (a
    sheet's table in its section, a table's body) goes on as well. Whether it did."""
    count = repeated(before, after, within)
    if count is None:
        return False
    for child in elements(after)[:count]:
        child.decompose()
    inner = edge(reversed(before.contents)), edge(after.contents)
    for child in list(after.contents):
        before.append(child.extract())
    after.decompose()
    if None not in inner:
        stitch(*inner, within=before.name)
    return True


def stitched(bodies):
    """The bodies of a page's parts as one body with each seam stitched (stitch), or None when no part goes on from the
    one before it. Pictures stay out of the parser."""
    pictures = []

    def keep(match):
        pictures.append(match.group(2))
        return f'{match.group(1)}data:,private-agent-picture-{len(pictures) - 1}"'

    whole, seams = BeautifulSoup("", "html.parser"), 0
    for body in bodies:
        part = BeautifulSoup(PICTURE.sub(keep, body), "html.parser")
        before, after = edge(reversed(whole.contents)), edge(part.contents)
        seams += before is not None and after is not None and stitch(before, after)
        for node in list(part.contents):
            whole.append(node.extract())
    return TOKEN.sub(lambda match: pictures[int(match.group(1))], str(whole)) if seams else None


def join_pages(pages, title):
    """The HTML pages of a file's parts as one page titled `title`: the first page's head around what each part shows.
    Where an earlier version cut a table, a section or a numbered list between two parts, the second part opened it
    again, repeating the table's columns and header or the section's heading with " (계속)", or numbering the list on:
    it goes on in one element again, without what was repeated (stitched)."""
    bodies, head = [], None
    for page in pages:
        root, end = ROOT.search(page), page.rfind(END)
        if root is None or end < root.end():
            whole = BODY.search(page)
            bodies.append(whole.group(1) if whole else page)
            continue
        bodies.append(page[root.end() : end])
        head = head or page[: root.end()]
    body = stitched(bodies) if len(bodies) > 1 else None
    body = "".join(bodies) if body is None else body
    if head is None:
        from .html_document import page as new_page

        return new_page(title, body, "")
    named = TITLE.search(head)
    if named:
        head = head[: named.start(1)] + escape(title) + head[named.end(1) :]
    return head + body + END


def parted(db, table):
    """The key (values of its columns) of an import in the source table `table` that an earlier version saved in
    parts, or None when there is none left."""
    from .db import SOURCE_TABLES

    columns = SOURCE_TABLES[table][1]
    # By the index of such rows (db.py), which is empty once all are merged: no start reads the table for it.
    row = db.execute(f"SELECT {columns} FROM {table} INDEXED BY idx_{table}_parted WHERE parts>1 LIMIT 1").fetchone()
    return tuple(row) if row else None


def merge(db, table, key):
    """Make the documents of the import with `key` (values of the source table's key) one document, within the
    caller's transaction, and return its id (None when the import has no document).

    The document of the lowest part takes the texts of all, in the order of the parts, its page the pages of all, and
    the title without " (1/5)" unless the user gave it another. Parts in the trash stay there as documents of their
    own when others are live: the user deleted them, and they are not brought back; when all are in the trash, they
    are one document there. What the user changed in a part stays changed (the text imported is kept beside it as
    before), and a document with a changed part is the Markdown the user wrote, its page the original. The AI analysis
    was of each part and is taken back: the document is analyzed again as one. Tasks and the copies of write-backs
    that named another part name the document, and merged_documents says where each of those went."""
    from .db import SOURCE_TABLES, index_text

    columns = [column.strip() for column in SOURCE_TABLES[table][1].split(",")]
    where = " AND ".join(f"s.{column}=?" for column in columns)
    rows = db.execute(
        f"""SELECT s.doc_id, s.part, s.parts, s.title AS given, s.markdown, d.title, d.content, d.format,
        d.deleted_at, d.created_at, d.updated_at FROM {table} s JOIN documents d ON d.id=s.doc_id
        WHERE {where} ORDER BY s.part, s.doc_id""",
        list(key),
    ).fetchall()
    if not rows:
        return None
    live = [row for row in rows if not row["deleted_at"]]
    used = live or rows
    first, others = used[0], [row["doc_id"] for row in used[1:]]
    # The parts left in the trash are documents without a source from here on: the import is the document.
    kept = {row["doc_id"] for row in used}
    for row in rows:
        if row["doc_id"] not in kept:
            db.execute(f"DELETE FROM {table} WHERE doc_id=?", (row["doc_id"],))
    ident = first["doc_id"]
    given = first["given"] or first["title"]
    title = whole_title(given, first["part"], first["parts"])
    named = title if first["title"] == given else first["title"]
    values = {"part": 1, "parts": 1, "title": title}
    if len(used) > 1:
        pages = {
            row["doc_id"]: row["html"]
            for row in db.execute(
                f"SELECT doc_id, html FROM document_html WHERE doc_id IN ({','.join('?' * len(used))})",
                [row["doc_id"] for row in used],
            )
        }
        page = None
        if pages:
            page = join_pages([pages[row["doc_id"]] for row in used if row["doc_id"] in pages], title)
        if page is not None and len(pages) == len(used):
            # The text of the page made one (join_pages), which leaves out the headers and headings the parts repeated;
            # a document with a part whose text the user changed keeps the texts of its parts, theirs included.
            values["markdown"] = page_text(page)
            mine = any(row["content"] != row["markdown"] for row in used)
            content = "\n\n".join(row["content"] for row in used) if mine else values["markdown"]
        else:
            join = join_markdown if not pages else "\n\n".join
            content = join([row["content"] for row in used])
            values["markdown"] = join([row["markdown"] for row in used])
        if "written" in {column[1] for column in db.execute(f"PRAGMA table_info({table})")}:
            # What write-backs put into the file was kept by part: the next write reads the file again instead.
            values["written"] = ""
        shown = "html" if pages and all(row["format"] == "html" for row in used) else "markdown"
        db.execute(
            """UPDATE documents SET title=?,content=?,format=?,summary='',status='pending',error='',index_mode='',
            revision=revision+1,created_at=?,updated_at=?,deleted_at=? WHERE id=?""",
            (
                named,
                content,
                shown,
                min(row["created_at"] for row in used),
                max(row["updated_at"] for row in used),
                None if live else max(row["deleted_at"] for row in used),
                ident,
            ),
        )
        for name in ("keywords", "relations", "doc_nodes"):
            db.execute(f"DELETE FROM {name} WHERE doc_id=?", (ident,))
        if page is not None:
            db.execute(
                "INSERT INTO document_html(doc_id,html) VALUES(?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET html=excluded.html",
                (ident, page),
            )
        marks = ",".join("?" * len(others))
        db.execute(f"UPDATE tasks SET source_id=? WHERE source_id IN ({marks})", [ident, *others])
        db.execute(f"UPDATE writeback_backups SET doc_id=? WHERE doc_id IN ({marks})", [ident, *others])
        db.execute(f"UPDATE merged_documents SET doc_id=? WHERE doc_id IN ({marks})", [ident, *others])
        db.executemany(
            "INSERT OR REPLACE INTO merged_documents(id,doc_id) VALUES(?,?)", [(old, ident) for old in others]
        )
        # Their source rows, pages, search text and analysis go with them (ON DELETE CASCADE).
        db.execute(f"DELETE FROM documents WHERE id IN ({marks})", others)
    elif named != first["title"]:
        # The one part left only loses the number in its title: a new revision, which its table of contents follows
        # as after any change of the title alone (Service.wiki_save).
        db.execute("UPDATE documents SET title=?,revision=revision+1 WHERE id=?", (named, ident))
        db.execute("UPDATE doc_nodes SET revision=revision+1 WHERE doc_id=?", (ident,))
    db.execute(
        f"UPDATE {table} SET {','.join(f'{column}=?' for column in values)} WHERE doc_id=?", [*values.values(), ident]
    )
    index_text(db, [ident])
    return ident


def merge_all(connect, budget=None):
    """Merge every import an earlier version saved in parts, one a transaction, for as long as `budget` seconds last
    (None: until done). `connect` opens the Wiki (Database.connect). Returns whether none is left."""
    from .db import SOURCE_TABLES

    deadline = None if budget is None else time.monotonic() + budget
    for table in SOURCE_TABLES:
        while True:
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                key = parted(db, table)
                if key is None:
                    break
                merge(db, table, key)
            if deadline is not None and time.monotonic() > deadline:
                return False
    return True
