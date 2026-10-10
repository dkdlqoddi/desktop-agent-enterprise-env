"""Imports too long for one Wiki document, saved as several.

A Wiki document holds at most MAX_MARKDOWN_CHARS characters of text: the editor edits it whole, and search, PageIndex
and the LLM read it. A longer page, ticket or file is cut into parts that each hold at most that much, and every
part becomes a document of its own titled "<title> (2/5)". Cuts fall between blocks, and a heading stays with what
follows it; a block too long for one part is cut between its lines or rows, a table repeating its header and a code
block its fence in the next part. Confluence and JIRA pages are cut as Markdown text. The HTML page of a file is cut
between its elements, so each part is a page of its own whose text is that of its share. At most MAX_PARTS parts are
saved; the last one then says that the rest was left out."""

import re
import uuid
from html import escape, unescape
from typing import NamedTuple

from bs4 import BeautifulSoup, NavigableString, Tag

from .html_document import index_markdown, page_text
from .llm import UserError
from .markdown_import import MAX_MARKDOWN_CHARS

MAX_PARTS = 100
CUT = "문서가 길어 여기까지만 가져왔습니다. 나머지는 원본에서 확인해 주세요."
# Every part leaves room for that note, so the last one can always take it.
NOTE_ROOM = len(CUT) + 8
PIECE = 4000  # characters of a text too long for one part, per piece the parts are filled with


class Split(NamedTuple):
    parts: list  # [(page, text)] of an HTML page, [(None, text)] of Markdown
    text: str  # the whole text, which tells a repeated import of the same content apart
    cut: bool  # whether parts beyond MAX_PARTS were left out


def part_title(title, number, count):
    """The title of part `number` of `count`, within the 200 characters of a title."""
    if count == 1:
        return title[:200]
    suffix = f" ({number}/{count})"
    return title[: 200 - len(suffix)].rstrip() + suffix


def saved_message(split, kind):
    """What an import that was cut into parts tells the user; `kind` is the kind of document (HTML, Markdown)."""
    message = f"글이 {MAX_MARKDOWN_CHARS:,}자를 넘어 {kind} 문서 {len(split.parts)}개로 나누어 Wiki에 저장했습니다."
    if split.cut:
        message += f" 문서 {MAX_PARTS}개를 넘는 뒷부분은 저장하지 않았습니다."
    return message


def imported(split, ids, message, kind):
    """The result of an import saved as the documents `ids`: `message` for one, how it was cut for several."""
    return {
        "status": "imported",
        "doc_id": ids[0],
        "doc_ids": ids,
        "message": message if len(ids) == 1 else saved_message(split, kind),
    }


def insert(db, split, title, timestamp, source, first=1):
    """Add the parts from number `first` on as new documents and return their ids; `source(ident, number, text)` adds
    each one's source."""
    ids = []
    for number, (page, text) in enumerate(split.parts[first - 1 :], first):
        ident = str(uuid.uuid4())
        db.execute(
            "INSERT INTO documents(id,title,content,format,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (
                ident,
                part_title(title, number, len(split.parts)),
                text,
                "markdown" if page is None else "html",
                timestamp,
                timestamp,
            ),
        )
        source(ident, number, text)
        if page is not None:
            db.execute("INSERT INTO document_html(doc_id,html) VALUES(?,?)", (ident, page))
        ids.append(ident)
    return ids


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
    lines after it. A part may begin with any of them."""
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


def pieces(kind, lines, room):
    """A block longer than `room` in pieces that fit it, each ending in a blank line: a code block keeps its fences in
    every piece and a table its header. A fence or header too long to repeat stays where it is, cut like any line."""
    body = [line if line.endswith("\n") else line + "\n" for line in lines]
    while body and not body[-1].strip():
        body.pop()
    head = tail = ""
    whole = body
    if kind == "code":
        head, body = body[0], body[1:]
        mark = FENCE.match(head).group(1)
        if body and re.fullmatch(rf" {{0,3}}{re.escape(mark[0])}{{{len(mark)},}}\s*", body[-1]):
            tail = body.pop()
        else:
            tail = mark + "\n"
    elif kind == "table" and len(body) > 2 and RULE.fullmatch(body[1]):
        head, body = body[0] + body[1], body[2:]
    if len(head) + len(tail) > room // 2:
        head, tail, body = "", "", whole  # a header that long is not repeated
    size = room - len(head) - len(tail) - 1
    found, chunk, used = [], [], 0
    for line in body:
        for segment in cut_text(line, size) if len(line) > size else [line]:
            if chunk and used + len(segment) > size:
                found.append(head + "".join(chunk) + tail + "\n")
                chunk, used = [], 0
            chunk.append(segment)
            used += len(segment)
    if chunk:
        found.append(head + "".join(chunk) + tail + "\n")
    return found


def split_markdown(text, limit, most):
    """Markdown longer than `limit` as parts of at most that many characters, and whether parts beyond `most` were left
    out (the last part then ends with a note that says so)."""
    room = limit - NOTE_ROOM
    units = []
    for kind, lines in blocks(text.strip()):
        block = "".join(lines)
        units += [(kind, block)] if len(block) <= room else [("text", piece) for piece in pieces(kind, lines, room)]
    parts, current, size = [], [], 0
    for unit in units:
        if current and size + len(unit[1]) > room:
            # A heading goes on to the next part with the text it introduces.
            carried = (
                [current.pop()]
                if len(current) > 1 and current[-1][0] == "heading" and len(current[-1][1]) + len(unit[1]) <= room
                else []
            )
            parts.append(current)
            current, size = carried, sum(len(block) for _, block in carried)
        current.append(unit)
        size += len(unit[1])
    if current:
        parts.append(current)
    texts = ["".join(block for _, block in part).strip() for part in parts]
    cut = len(texts) > most
    if cut:
        texts = texts[:most]
        texts[-1] += "\n\n> " + CUT
    return texts, cut


def joined(texts):
    """The texts of a Markdown page's parts as one text again, without what a part repeats of the one before it
    (pieces): the header of a table and the fences of a code block that go on into it. Two imports of a page compared
    so (changes.compare) show what changed in it, not where it was cut into parts."""
    lines, previous = [], None
    for text in texts:
        own = text.splitlines()
        if previous is not None:
            kind, block = blocks(previous)[-1]
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
        lines += own
        previous = text
    return "\n".join(lines)


def markdown_parts(text):
    """The documents a Markdown page is saved as: itself when it fits one."""
    if len(text) <= MAX_MARKDOWN_CHARS:
        return Split([(None, text)], text, False)
    texts, cut = split_markdown(text, MAX_MARKDOWN_CHARS, MAX_PARTS)
    return Split([(None, part) for part in texts], text, cut)


# --- HTML pages ------------------------------------------------------------------------------------------------------

# Pictures stay out of the parser: a token stands for each until its part is assembled.
PICTURE = re.compile(r'(<img\b[^>]*?\ssrc=")(data:[^"]*)"', re.I)
TOKEN = re.compile(r"data:,private-agent-picture-(\d+)")
ROOT = re.compile(r'<div class="doc\b[^>]*>')
END = "</div></body></html>"
TITLE = re.compile(r"<title>(.*?)</title>", re.S)
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
SECTIONS = {"section", "div", "article"}
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
    (<hr> is ---), and a part must not take any number of them. Each part is measured exactly before it is kept."""
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
    """A share of a page that goes to one part: an element, a text or a piece of one, below the elements in `path`."""

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
    """Whether a node introduces what follows it, so that a part does not end with it."""
    if not isinstance(node, Tag):
        return False
    return (
        node.name in HEADINGS
        or node.name in ("colgroup", "caption", "thead")
        or (node.name == "tr" and header_row(node))
    )


def start_tag(tag, **extra):
    attributes = {**tag.attrs, **extra}
    return (
        f"<{tag.name}"
        + "".join(
            f' {name}="{escape(" ".join(value) if isinstance(value, list) else str(value))}"'
            for name, value in attributes.items()
        )
        + ">"
    )


def repeated(tag):
    """What a part that goes on inside `tag` repeats: a table's columns and header, a section's heading."""
    if tag.name == "table":
        head = [child for child in tag.children if isinstance(child, Tag) and child.name in ("colgroup", "thead")]
        row = tag.find("tr")
        if not any(child.name == "thead" for child in head) and row is not None and header_row(row):
            head.append(row)
        return "".join(str(item) for item in head)
    first = next((child for child in tag.children if isinstance(child, Tag)), None)
    if tag.name in SECTIONS and first is not None and first.name in HEADINGS:
        return start_tag(first) + escape(" ".join(first.get_text().split()) + " (계속)") + f"</{first.name}>"
    return ""


def markup(node):
    if isinstance(node, Tag):
        return str(node)
    if isinstance(node, NavigableString):
        return node.output_ready(formatter="minimal")
    return escape(node, quote=False)


def render(units, opened):
    """The HTML of a part's units with the elements around them, and those elements. An element a former part opened
    (in `opened`, by id) goes on: it repeats its header, and a numbered list its numbers."""
    out, stack, now = [], [], []
    for unit in units:
        same = 0
        while same < len(stack) and same < len(unit.path) and stack[same] is unit.path[same]:
            same += 1
        while len(stack) > same:
            out.append(f"</{stack.pop().name}>")
        for depth in range(same, len(unit.path)):
            tag = unit.path[depth]
            extra = {}
            if id(tag) in opened and tag.name == "ol":
                item = next(
                    (
                        outer
                        for outer in (*unit.path[depth + 1 :], unit.node)
                        if isinstance(outer, Tag) and outer.name == "li"
                    ),
                    None,
                )
                if item is not None:
                    start = int(tag["start"]) if str(tag.get("start", "")).isdigit() else 1
                    extra["start"] = str(start + len(item.find_previous_siblings("li")))
            out.append(start_tag(tag, **extra))
            if id(tag) in opened:
                out.append(repeated(tag))
            stack.append(tag)
            now.append(tag)
        out.append(markup(unit.node))
    while stack:
        out.append(f"</{stack.pop().name}>")
    return "".join(out), now


def split_page(html, limit, most):
    """A page whose text is longer than `limit` as pages that each hold at most that much: [(page, text)], and whether
    pages beyond `most` were left out (the last page then ends with a note that says so)."""
    pictures = []

    def keep(match):
        pictures.append(match.group(2))
        return f'{match.group(1)}data:,private-agent-picture-{len(pictures) - 1}"'

    html = PICTURE.sub(keep, html)
    root, end = ROOT.search(html), html.rfind(END)
    if root is None or end < root.end():
        raise UserError("변환된 HTML 문서를 나누지 못했습니다.")
    head, tail = html[: root.end()], html[end:]
    units = [
        Unit((), node)
        for node in BeautifulSoup(html[root.end() : end], "html.parser").contents
        if not (type(node) is NavigableString and not node.strip())
    ]
    room = limit - NOTE_ROOM
    # Characters of text per estimated one: a cautious guess at first, then what the part measured last had. Every
    # part is measured before it is kept, so a guess that runs short only costs another try.
    ratio = 1.1
    budget = int(room * 0.97 / ratio)
    parts, opened, index, cut = [], set(), 0, False
    while index < len(units):
        if len(parts) == most:
            cut = True
            break
        stop, size = index, 0
        while stop < len(units):
            unit = units[stop]
            if unit.size > budget:  # too long for any part: its first pieces fill this one
                smaller = divide(unit)
                if smaller:
                    units[stop : stop + 1] = smaller
                    continue
            if stop > index and size + unit.size > budget:
                break
            size += unit.size
            stop += 1
        if stop < len(units):  # cut short: a heading or table header goes on with what it introduces
            while stop - index > 1 and leads(units[stop - 1].node):
                stop -= 1
        body, now = render(units[index:stop], opened)
        text = page_text(head + body + tail)
        size = sum(unit.size for unit in units[index:stop])
        if size and text:
            ratio = max(len(text) / size, 0.2)
        if len(text) > room:
            if stop - index > 1:
                # The next try takes fewer units, whatever the estimate says, so the cutting always ends.
                budget = max(1, min(int(room * 0.95 / ratio), size - 1))
                continue
            smaller = divide(units[index])
            if not smaller:
                raise UserError(f"한 문서에 담을 수 있는 {limit:,}자보다 긴 항목이 있어 문서를 나누지 못했습니다.")
            units[index : index + 1] = smaller
            continue
        if text or not parts:
            parts.append([body, text])
        else:
            parts[-1][0] += body  # only markup without text: it stays with the part before
        opened.update(map(id, now))
        index, budget = stop, max(1, int(room * 0.97 / ratio))
    title = TITLE.search(head)
    pages = []
    for number, (body, text) in enumerate(parts, 1):
        named = head
        if title:
            name = escape(part_title(unescape(title.group(1)), number, len(parts)))
            named = head[: title.start(1)] + name + head[title.end(1) :]
        if cut and number == len(parts):
            body += f'<p class="caption">{escape(CUT)}</p>'
            text = page_text(named + body + tail)
        pages.append((TOKEN.sub(lambda match: pictures[int(match.group(1))], named + body + tail), text))
    return pages, cut


def page_parts(html):
    """The documents a converted page is saved as: itself when its text fits one."""
    text = index_markdown(html)
    if len(text) <= MAX_MARKDOWN_CHARS:
        return Split([(html, text)], text, False)
    pages, cut = split_page(html, MAX_MARKDOWN_CHARS, MAX_PARTS)
    return Split(pages, text, cut)
