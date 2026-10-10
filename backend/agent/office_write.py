"""Write the changes of a document's text (source_edits.py) into its Office file, through the Office application.

A file is written as the import reads it, in a disposable worker: DispatchEx starts a hidden application,
settings that could show a dialog, run a macro or update a link are restored afterwards.

Every change is found in the file's own text before anything is written, and the text is read back afterwards: when a
place cannot be found, holds other text than the import read, or the result differs from what the changes make, the
file closes without being saved. Word's character positions count field codes and marks its text leaves out, so a
place in Word is found by Word's own search for the text around it; PowerPoint and Visio take positions in a text frame
or shape. Excel cells are written by their address, after their value is checked against the one imported, and read
back before the workbook is saved."""
import contextlib
import math
import re
from datetime import datetime
from pathlib import Path

from .llm import UserError
from .office_converter import PROGIDS, capabilities, close_drawing, same_file
from .office_html import VISIO_GROUP, VISIO_SHAPES, cell_text, connector_ends, one_line, read, slide_title
from .office_session import OfficeSession, run_worker
from .source_edits import (ALREADY, ELSEWHERE, Missing, Text, choose, missing, norm, normalized, raw_span, spans,
                           split_units)
from .word_converter import WordConverter, close_document

# visOpenDontList | visOpenMacrosDisabled | visOpenNoWorkspace, without visOpenRO.
VISIO_WRITE = 0x8 | 0x80 | 0x100
WORD_SEPARATORS = re.compile(r"\r\x07|\r|\v|\x07|\x0c|\x0e")
# A non-breaking hyphen as the page shows it; an optional hyphen does not show.
WORD_CHARACTERS = {"\x1e": "\u2011", "\x1f": ""}
POWERPOINT_SEPARATORS = re.compile(r"\r|\v")
VISIO_SEPARATORS = re.compile(r"\r\n|\r|\n|\u2028|\u2029|\v")
# Field placeholders in a Visio shape's text, which the page leaves out.
VISIO_CHARACTERS = {"\ufffc": ""}
# What Word's search is told to do: exactly the characters given.
WORD_FIND = (("MatchByte", True), ("MatchFuzzy", False), ("IgnoreSpace", False), ("IgnorePunct", False),
             ("MatchPrefix", False), ("MatchSuffix", False), ("MatchPhrase", False), ("MatchDiacritics", True),
             ("MatchKashida", True), ("MatchAlefHamza", True), ("MatchControl", True))
FIND_LIMIT = 255  # characters Word searches for at once
CONTEXT = 24  # characters around a place that tell it apart
CANCELLED = "원본 반영을 취소했습니다. 원본 파일은 바뀌지 않았습니다."
HINT = "해당 앱에서 파일을 직접 열고 저장할 수 있는지, 암호·DRM·보안 정책을 확인해 주세요."
READ_ONLY = ("{name} 파일을 읽기 전용으로 열어 고칠 수 없습니다. 파일의 읽기 전용 속성, 다른 사람이나 다른 프로그램이 "
             "쓰고 있는지, 쓰기 암호를 확인해 주세요.")
CHANGED = "{name}에 고친 결과가 예상과 달라 저장하지 않았습니다. 원본 파일은 바뀌지 않았습니다."
TRACKED = " 변경 내용 추적이 켜져 있으니 Word에서 끈 뒤 다시 반영해 보세요."
REVISED = ("이 문서에는 변경 내용 추적으로 남은 글이 있습니다. 지운 글이 남은 줄이라면 Word에서 변경 내용을 적용한 뒤 "
           "다시 반영해 주세요.")


def subject(name):
    """The application's name with the subject particle Korean puts after it: Word가, Excel이."""
    return name + ("이" if name == "Excel" else "가")


def width(text):
    """Office counts characters in UTF-16 units: a character beyond the BMP takes two."""
    return len(text.encode("utf-16-le")) // 2


def check(stop):
    if stop is not None and stop.is_set():
        raise UserError(CANCELLED)


def applied(raw, changes):
    """`raw` with the replacements of one container made, in order from the end."""
    for start, end, text in changes:
        raw = raw[:start] + text + raw[end:]
    return raw


def writable(document, name):
    """Refuse a document its application opened read-only: it could not be saved where it is."""
    if read(document, "ReadOnly") in (True, -1, 1):
        raise UserError(READ_ONLY.format(name=subject(name)))


# --- Word ----------------------------------------------------------------------------------------------------------


def searchable(char):
    return char >= " " or char in "\t\v\x1e\x1f"


def find_text(text):
    """`text` as Word's search takes it: its special characters written as ^ codes."""
    return (text.replace("^", "^^").replace("\t", "^t").replace("\v", "^l").replace("\r", "^p").replace("\xa0", "^s")
            .replace("\x1e", "^~").replace("\x1f", "^-"))


def bounds(text, start, end, limit):
    """The stretch of the paragraph (or table cell) that holds text[start:end], up to `limit`: a search never reaches
    past a paragraph, nor into a place already written."""
    low = start
    while low > 0 and text[low - 1] not in "\r\x07" and searchable(text[low - 1]):
        low -= 1
    high = end
    while high < min(len(text), limit) and text[high] not in "\r\x07" and searchable(text[high]):
        high += 1
    return low, high


def anchor(text, start, end, limit):
    """(first, last, count): the stretch text[first:last] that Word searches for to find the place text[start:end] (an
    insertion point when they are equal), and how many times it occurs before `first`, which the search passes. It
    starts at the place, or for an insertion ends there, so a field code before it does not move the place, and grows
    until it occurs once where it can, within FIND_LIMIT characters around the place, in its paragraphs. None when the
    place cannot be searched for: a picture, footnote or field mark in it."""
    # A deleted paragraph takes its paragraph mark along; a table cell's end never goes.
    place = text[start:min(end, start + FIND_LIMIT)]
    if any(not searchable(char) and char != "\r" for char in place) or "\r\x07" in text[start:end + 1]:
        return None
    low, high = bounds(text, start, min(end, start + FIND_LIMIT), limit)
    leftward = start == end and start > low
    chosen = None
    for around in (CONTEXT // 2, CONTEXT, CONTEXT * 2, CONTEXT * 4, FIND_LIMIT):
        for wider in (0, around):
            if leftward:
                first, last = max(low, start - around), min(high, start + wider)
            else:
                first, last = max(low, start - wider), min(high, max(end, start + around))
            last = min(last, first + FIND_LIMIT)
            if last <= first or last < min(end, start + FIND_LIMIT) or first > start:
                continue
            needle = text[first:last]
            starts = [match.start() for match in re.finditer(re.escape(needle), text)]
            if first not in starts:
                continue
            chosen = (first, last, starts.index(first))
            if len(starts) == 1:
                return chosen
    return chosen


def find_nth(document, needle, count):
    """The Range of the `count`th (from 0) occurrence of `needle` in Word's main text, found by Word's search, or
    None."""
    found = document.Content
    search = found.Find
    with contextlib.suppress(Exception):
        search.ClearFormatting()
    for name, value in WORD_FIND:
        with contextlib.suppress(Exception):  # Options of languages this Word does not have.
            setattr(search, name, value)
    seen = 0
    while search.Execute(FindText=find_text(needle), MatchCase=True, MatchWholeWord=False, MatchWildcards=False,
                         MatchSoundsLike=False, MatchAllWordForms=False, Forward=True, Wrap=0, Format=False,
                         Replace=0):
        if found.Text == needle:
            if seen == count:
                return found
            seen += 1
        found.Collapse(0)  # wdCollapseEnd: search on after this one
    return None


def word_range(document, text, start, end, limit):
    """The Range of text[start:end] of Word's main text (`text` as Word handed it over), checked to hold those
    characters. `limit` is where text already written begins."""
    found = anchor(text, start, end, limit)
    if found is None:
        raise missing(text[start:end] or text[max(0, start - 20):start + 20],
                      "그림·각주·필드 표시가 있는 곳이라 Word에서 찾지 못했습니다")
    first, last, count = found
    match = find_nth(document, text[first:last], count)
    if match is None:
        raise missing(text[first:last])
    if start == end == last:
        point = match.End  # Right after what was found: nothing between it and the place.
        return document.Range(point, point)
    origin = match.Start
    target = document.Range(origin + width(text[first:start]), origin + width(text[first:end]))
    # What lies between the start of what was found and the place, then the place itself, must be what the text says:
    # a field's code, which positions count, would show here as a difference.
    if (first < start and (document.Range(origin, target.Start).Text or "") != text[first:start]) or \
            (target.Text or "") != text[start:end]:
        raise missing(text[start:end] or text[first:last], "Word에서 고칠 곳의 글이 가져온 때와 달라 쓰지 않았습니다")
    return target


def blank_cell(document, text, via, limit):
    """The Range of a blank table cell, reached from the nearest cell of its table that has text: `via` is that cell's
    (start, end) in the text, how many cells on (or back) the blank one is, how many rows below that cell's it is and
    the column it is in. A cell merged with another changes the count, and Word then names another row or column:
    nothing is written."""
    start, end, steps, rows, column = via
    if end > limit:
        raise missing(text[start:end], "Word에서 빈 칸의 자리를 찾지 못했습니다")
    cell = word_range(document, text, start, end, limit).Cells.Item(1)
    row = cell.RowIndex + rows
    for _ in range(abs(steps)):
        cell = cell.Next if steps > 0 else cell.Previous
        if cell is None:
            break
    if cell is None or (cell.Range.Text or "").rstrip("\r\x07") or (cell.RowIndex, cell.ColumnIndex) != (row, column):
        raise missing(text[start:end], "Word에서 빈 칸의 자리를 찾지 못했습니다")
    return cell.Range


def word_units(text):
    return split_units(text, 0, 0, WORD_SEPARATORS, WORD_CHARACTERS)


def edit_word(document, plan, stop):
    """Make the plan's changes in a Word document's main text. (The number of places changed: lines edited, added or
    deleted; the number of the plan's choice taken.)"""
    text = document.Content.Text or ""
    try:
        number, choice, changes = choose(plan, word_units(text), ("\r", "\v"), marked_rows=True, searched=True)
    except Missing as error:
        # Text a tracked change deleted is still in Word's text, which the page leaves out.
        if read(read(document, "Revisions"), "Count", 0):
            raise type(error)(f"{error} {REVISED}") from None
        raise
    limit = len(text)
    for _, start, end, new, via in changes:
        check(stop)
        target = blank_cell(document, text, via, limit) if via else word_range(document, text, start, end, limit)
        target.Text = new
        # A blank cell is written where it starts: the cell before it that it was reached from may reach another.
        limit = start
    tracked = read(document, "TrackRevisions")
    if not written_back(document.Content.Text or "", text, changes, tracked):
        raise UserError(CHANGED.format(name="Word") + (TRACKED if tracked else ""))
    return len(choice["edits"]), number


def written_back(now, text, changes, tracked):
    """Whether Word's text `now` is its text before, `text`, with the replacements made. With Track Changes on
    (`tracked`), Word keeps the text a replacement takes away, marked deleted, in its text beside the new text: the text
    read back may have it, on either side, or not."""
    if not tracked:
        return now == applied(text, [(start, end, new) for _, start, end, new, _ in changes])
    pattern, position = [], 0
    for _, start, end, new, _ in sorted(changes, key=lambda item: (item[1], item[2])):
        gone, added = re.escape(text[start:end]), re.escape(new)
        pattern += [re.escape(text[position:start]), f"(?:{gone}{added}|{added}{gone}|{added})" if gone else added]
        position = end
    pattern.append(re.escape(text[position:]))
    return re.fullmatch("".join(pattern), now) is not None


# --- PowerPoint ----------------------------------------------------------------------------------------------------


def shape_frames(shape, tables, depth=0):
    """[(text frame, table cell or None)] of a shape in the order the reader reads them: a group's items, a table's
    cells row by row, or its own text. Charts, SmartArt, pictures and media have none that the page shows as the
    shape's text. `tables` counts the tables so far, so each cell knows its (table, row, column)."""
    if depth > 32:
        return []
    kind = shape.Type
    if kind == 6:  # msoGroup
        items = shape.GroupItems
        return [frame for index in range(1, items.Count + 1)
                for frame in shape_frames(items.Item(index), tables, depth + 1)]
    if shape.HasTable:
        table = shape.Table
        tables[0] += 1
        return [(table.Cell(row, column).Shape.TextFrame.TextRange, (tables[0], row, column))
                for row in range(1, table.Rows.Count + 1) for column in range(1, table.Columns.Count + 1)]
    if read(shape, "HasChart") == -1 or shape.HasSmartArt or kind in (7, 10, 11, 13, 16):
        return []
    if shape.HasTextFrame and shape.TextFrame.HasText:
        return [(shape.TextFrame.TextRange, None)]
    return []


def powerpoint_frames(presentation):
    """[(slide, TextRange, whole, table cell)] of a presentation as the reader reads it: each slide's title (compared
    whole, as the slide's heading shows it on one line), its shapes' text, then its speaker notes."""
    found, tables = [], [0]
    slides = presentation.Slides
    for number in range(1, slides.Count + 1):
        slide = slides.Item(number)
        shapes = slide.Shapes
        title, heading, body = "", [], []
        for index in range(1, shapes.Count + 1):
            shape = shapes.Item(index)
            text = slide_title(shape)
            if text is not None and not title:
                title = one_line(text)
                if title:
                    heading.append((number, shape.TextFrame.TextRange, True, None))
            else:
                body.extend((number, frame, False, cell) for frame, cell in shape_frames(shape, tables))
        notes = slide.NotesPage.Shapes
        for index in range(1, notes.Count + 1):
            shape = notes.Item(index)
            if shape.Type == 14 and shape.PlaceholderFormat.Type == 2 and shape.HasTextFrame and shape.TextFrame.HasText:
                body.append((number, shape.TextFrame.TextRange, False, None))
        found.extend(heading + body)
    return found


def frame_units(frames, read_text, separators, characters=None):
    """(raw texts, units) of the containers `frames` [(scope, container, whole, table cell)]."""
    raws, units = [], []
    for container, (scope, frame, whole, cell) in enumerate(frames):
        raw = read_text(frame) or ""
        raws.append(raw)
        if whole:
            text, index = normalized(raw, 0, characters)
            units.append(Text(container, 0, len(raw), text, index, "", scope))
        else:
            units.extend(split_units(raw, container, scope, separators, characters, cell))
    return raws, units


def edit_powerpoint(presentation, plan, stop):
    """Make the plan's changes in a presentation's slides, titles and speaker notes. (The number of places changed, the
    number of the plan's choice taken.)"""
    frames = powerpoint_frames(presentation)
    raws, units = frame_units(frames, lambda frame: frame.Text, POWERPOINT_SEPARATORS)
    number, choice, changes = choose(plan, units, ("\r", "\v"))
    for container, start, end, new, _ in changes:
        check(stop)
        frame, raw = frames[container][1], raws[container]
        if start < end:
            part = frame.Characters(width(raw[:start]) + 1, width(raw[start:end]))
            if (part.Text or "") != raw[start:end]:
                raise missing(raw[start:end], "PowerPoint에서 고칠 곳의 글이 가져온 때와 달라 쓰지 않았습니다")
            part.Text = new
        elif not raw:
            frame.Text = new
        elif start > 0:
            frame.Characters(width(raw[:start - 1]) + 1, width(raw[start - 1])).InsertAfter(new)
        else:
            frame.Characters(1, width(raw[0])).InsertBefore(new)
    for container in {item[0] for item in changes}:
        expected = applied(raws[container], [item[1:4] for item in changes if item[0] == container])
        if (frames[container][1].Text or "") != expected:
            raise UserError(CHANGED.format(name="PowerPoint"))
    return len(choice["edits"]), number


# --- Visio ---------------------------------------------------------------------------------------------------------


def visio_shapes(document):
    """[(page, shape, False, None)] of a drawing whose text the page lists, in its order: a group's own text before its
    shapes'. Connectors glued at an end are listed as connections, not as text."""
    found = []

    def walk(shapes, number, depth):
        if depth > 32:
            return
        for position in range(1, shapes.Count + 1):
            shape = shapes.Item(position)
            if shape.Type not in VISIO_SHAPES:
                continue
            if shape.OneD and connector_ends(shape) != (None, None):
                continue
            found.append((number, shape, False, None))
            if len(found) > 20000:
                raise UserError("Visio 도형이 20,000개를 초과합니다.")
            if shape.Type == VISIO_GROUP:
                walk(shape.Shapes, number, depth + 1)
    pages = document.Pages
    for number in range(1, pages.Count + 1):
        walk(pages.Item(number).Shapes, number, 0)
    return found


def edit_visio(document, plan, stop):
    """Make the plan's changes in the text of a drawing's shapes. (The number of places changed, the number of the
    plan's choice taken.)"""
    shapes = visio_shapes(document)
    raws, units = frame_units(shapes, lambda shape: shape.Characters.Text, VISIO_SEPARATORS, VISIO_CHARACTERS)
    number, choice, changes = choose(plan, units, ("\n", "\u2028"))
    for container, start, end, new, _ in changes:
        check(stop)
        raw = raws[container]
        characters = shapes[container][1].Characters
        characters.Begin = width(raw[:start])
        characters.End = width(raw[:end])
        if start < end and (characters.Text or "") != raw[start:end]:
            raise missing(raw[start:end], "Visio에서 고칠 곳의 글이 가져온 때와 달라 쓰지 않았습니다")
        characters.Text = new
    for container in {item[0] for item in changes}:
        expected = applied(raws[container], [item[1:4] for item in changes if item[0] == container])
        if (shapes[container][1].Characters.Text or "") != expected:
            raise UserError(CHANGED.format(name="Visio"))
    return len(choice["edits"]), number


# --- Excel ---------------------------------------------------------------------------------------------------------


def number(text):
    try:
        return float(text.replace(",", "").removesuffix("%")) / (100 if text.endswith("%") else 1)
    except ValueError:
        return None


DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?: (\d{2}):(\d{2})(?::(\d{2}))?)?")


def serial(text):
    """The Excel serial number of a date as the page writes it (2026-09-28, 2026-09-28 13:45), or None."""
    match = DATE.fullmatch(text)
    if not match:
        return None
    try:
        moment = datetime(*(int(part or 0) for part in match.groups()))
    except ValueError:
        return None
    return (moment - datetime(1899, 12, 30)).total_seconds() / 86400


def shows(cell, expected):
    """Whether a cell still holds the value the page showed, `expected`: as Excel shows it, or its value in the General
    format, or the same number or date."""
    shown = " ".join(str(read(cell, "Text", "") or "").split())
    if shown == expected:
        return True
    value = read(cell, "Value2")
    if value is None or value == "":
        return expected == ""
    if " ".join(cell_text(value).split()) == expected:
        return True
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        for candidate in (number(expected), serial(expected)):
            if candidate is not None and math.isclose(candidate, value, rel_tol=1e-9, abs_tol=1e-9):
                return True
    return False


def holds(cell, shown, formula):
    """Whether a cell still holds what the page showed: the formula `formula`, or when None a value shown as `shown`
    and no formula."""
    if formula is not None:
        return str(read(cell, "Formula", "") or "") == formula
    return not read(cell, "HasFormula") and shows(cell, shown)


def same_formula(written, wanted):
    """Whether the formula a cell reads back is the one written, which Excel may give with its names in capitals."""
    return norm(str(written or "")).upper() == norm(wanted).upper()


def worksheet_cell(sheets, change):
    """(the cell, how to name it) of a change of a workbook's cell: its sheet by the first name it may have there."""
    sheet = None
    for name in change["sheet"]:
        with contextlib.suppress(Exception):
            sheet = sheets.Item(name)
            break
    if sheet is None:
        raise missing(change["sheet"][-1], "원본 파일에서 시트를 찾지 못했습니다")
    return sheet.Range(change["address"]), f"{change['sheet'][-1]} 시트 {change['address']} 셀"


def proven_cells(sheets, witness):
    """Whether the cells of a choice's `witness` hold what the text the choice was made against has there."""
    for change in witness:
        try:
            cell, _ = worksheet_cell(sheets, change)
        except UserError:
            return False
        if not holds(cell, change["old"], change["formula"]):
            return False
    return True


def retype(cell, raw, change, where):
    """Make the change of a cell's shown text (change["old"] to change["new"]) in the text `raw` it holds, only the
    characters that changed, each in its place: the rest keeps its line breaks, spacing and formatting, and the cell
    stays text. Text added between two characters is written with the one before it (or after it, at the start), as
    the characters of a cell are only ever replaced. The text it then holds."""
    text, index = normalized(raw)
    if text != change["old"]:
        raise missing(f"{where}: {change['old']}", "원본 파일의 셀 글이 가져온 때와 달라 쓰지 않았습니다")
    unit = Text(0, 0, len(raw), text, index, "", 0)
    places = []
    for start, end, new in spans(text, change["new"]):
        start, end = raw_span(unit, start, end)
        if start == end:
            start, end, new = (start - 1, end, raw[start - 1] + new) if start else (start, end + 1, new + raw[start])
        places.insert(0, (start, end, new))
    for start, end, new in places:
        cell.Characters(width(raw[:start]) + 1, width(raw[start:end])).Text = new
    return applied(raw, places)


def edit_excel(workbook, plan, stop):
    """Write the plan's cells of a workbook: a formula as Excel writes formulas; a value in a cell that holds text by
    changing only the characters that changed in its text, so its line breaks, spacing and the formatting of the rest
    stay; another value as typed into the cell. Each cell is checked to hold what the page showed first, and every
    cell written is read back before the workbook is saved. (The number of cells written, the number of the plan's
    choice taken.)"""
    sheets = workbook.Worksheets
    choices = plan.get("choices") or [plan]
    number = next((number for number, choice in enumerate(choices) if proven_cells(sheets, choice.get("witness", ()))),
                  None)
    if number is None:
        raise UserError(ELSEWHERE)
    cells = choices[number]["cells"]
    if not cells:
        raise UserError(ALREADY)
    written = []  # for each cell written, whether it reads back as written
    for change in cells:
        check(stop)
        cell, where = worksheet_cell(sheets, change)
        formula = change["formula"]
        if not holds(cell, change["old"], formula[0] if formula else None):
            raise missing(where if formula else f"{where}: {change['old']}",
                          "원본 파일의 셀이 가져온 때와 달라 쓰지 않았습니다")
        raw = read(cell, "Value2")
        if formula:
            cell.Formula = formula[1]
            written.append(lambda cell=cell, new=formula[1]: same_formula(read(cell, "Formula", ""), new))
        elif not change["new"]:
            cell.ClearContents()
            written.append(lambda cell=cell: read(cell, "Value2") in (None, ""))
        elif isinstance(raw, str) and raw:
            text = retype(cell, raw, change, where)
            written.append(lambda cell=cell, text=text: read(cell, "Value2") == text)
        else:
            cell.FormulaLocal = change["new"]
            written.append(lambda cell=cell, new=change["new"]: shows(cell, new))
    if not all(back() for back in written):
        raise UserError(CHANGED.format(name="Excel"))
    return len(cells), number


# --- Sessions ------------------------------------------------------------------------------------------------------


EDITORS = {"Word": edit_word, "Excel": edit_excel, "PowerPoint": edit_powerpoint, "Visio": edit_visio}


def discard_presentation(presentation):
    presentation.Saved = -1  # msoTrue: its changes go without a question, also in the PowerPoint the user runs
    presentation.Close()


DISCARD = {"Word": close_document, "Excel": lambda book: book.Close(SaveChanges=False),
           "PowerPoint": discard_presentation, "Visio": close_drawing}


def note(journal, phase):
    """Write how far the save got ("saving", then "saved") into the `journal` file the backend named, if any: after a
    crash or a kill, the next write tells from it whether the save began or ended (writeback.Writeback.interrupted).
    A journal that cannot be written changes nothing in the write."""
    if journal:
        with contextlib.suppress(OSError):
            Path(journal).write_text(phase, encoding="utf-8")


def edit(office, document, name, plan, stop, journal=None):
    """Make the changes and save, in an open session; the document closes when the session ends. {"changes": how many
    were made, "base": the number of the plan's choice taken, the text the file was found to hold}. The `journal` file
    notes when the save begins and ends."""
    with office.writing():
        count, base = EDITORS[name](document, plan, stop)
    check(stop)
    note(journal, "saving")
    with office.saving():
        document.Save()
    note(journal, "saved")
    return {"changes": count, "base": base}


def write_word(path, plan, stop, dispatch=None, journal=None):
    with OfficeSession("Word", PROGIDS["Word"], quit_args={"SaveChanges": 0}, dispatch=dispatch) as office:
        word = office.application
        office.hide()
        office.quiet(DisplayAlerts=0)  # wdAlertsNone
        office.change(word, AutomationSecurity=3)  # msoAutomationSecurityForceDisable
        office.change(word.Options, UpdateLinksAtOpen=False)
        with office.opening():
            document = office.track(word.Documents.Open(
                FileName=str(path), ReadOnly=False, AddToRecentFiles=False, ConfirmConversions=False, Visible=False,
                PasswordDocument="", WritePasswordDocument="", OpenAndRepair=False, NoEncodingDialog=True,
            ), close_document)
        writable(document, "Word")
        return edit(office, document, "Word", plan, stop, journal)


def write_excel(path, plan, stop, dispatch=None, journal=None):
    with OfficeSession("Excel", PROGIDS["Excel"], dispatch=dispatch) as office:
        excel = office.application
        office.hide()
        office.quiet(DisplayAlerts=False, ScreenUpdating=False)
        office.change(excel, EnableEvents=False, AskToUpdateLinks=False, AutomationSecurity=3)
        with office.opening():
            workbook = office.track(excel.Workbooks.Open(
                Filename=str(path), UpdateLinks=0, ReadOnly=False, Password="", WriteResPassword="",
                IgnoreReadOnlyRecommended=True, Notify=False, AddToMru=False,
            ), DISCARD["Excel"])
        writable(workbook, "Excel")
        return edit(office, workbook, "Excel", plan, stop, journal)


def write_powerpoint(path, plan, stop, dispatch=None, journal=None):
    in_use = "PowerPoint에 이 파일이 열려 있습니다. PowerPoint에서 파일을 닫은 뒤 다시 반영해 주세요."
    with OfficeSession("PowerPoint", PROGIDS["PowerPoint"], dispatch=dispatch) as office:
        powerpoint = office.application
        with office.starting():
            # PowerPoint runs one process per user: the one the user runs is only borrowed.
            office.owned = not (powerpoint.Visible or powerpoint.Presentations.Count)
        if office.owned:
            office.quiet(DisplayAlerts=1)  # ppAlertsNone
        office.change(powerpoint, AutomationSecurity=3)
        with office.opening():
            presentations = powerpoint.Presentations
            if any(same_file(presentations.Item(i).FullName, path) for i in range(1, presentations.Count + 1)):
                raise UserError(in_use)
            presentation = presentations.Open(FileName=str(path), ReadOnly=False, Untitled=False, WithWindow=False)
            if presentation.Windows.Count:
                raise UserError(in_use)
            office.track(presentation, discard_presentation)
        writable(presentation, "PowerPoint")
        return edit(office, presentation, "PowerPoint", plan, stop, journal)


def write_visio(path, plan, stop, dispatch=None, journal=None):
    with OfficeSession("Visio", PROGIDS["Visio"], dispatch=dispatch) as office:
        visio = office.application
        office.quiet(AlertResponse=7, ScreenUpdating=False)
        office.change(visio, EventsEnabled=False)
        with office.opening():
            drawing = office.track(visio.Documents.OpenEx(str(path), VISIO_WRITE), close_drawing)
        writable(drawing, "Visio")
        return edit(office, drawing, "Visio", plan, stop, journal)


WRITERS = {"Word": write_word, "Excel": write_excel, "PowerPoint": write_powerpoint, "Visio": write_visio}


def write_file(source, payload, stop):
    """Runs only in the disposable worker: the changes `payload` holds, written into `source`, and how far the save got
    noted in the journal file it names."""
    path = Path(source)
    return WRITERS[payload["application"]](path.resolve(), payload["plan"], stop, journal=payload.get("journal"))


def _worker(source, sender, stop, payload):
    run_worker(lambda: write_file(source, payload, stop), sender, "원본 반영", HINT, key="summary")


class OfficeWriter(WordConverter):
    """Writes the changes into a file in a disposable worker, as the converters read files: one that hangs in a
    dialog is killed after the time limit, and a hidden application it started is ended (word_converter.Watch)."""
    worker = staticmethod(_worker)
    stage = "원본 반영"
    cancelled = CANCELLED

    def __init__(self, timeout=300, grace=15):
        super().__init__(timeout, grace)
        self.payload = {"application": "Word"}

    def __call__(self, source, payload, cancel):
        self.payload = payload
        self.application_label = payload["application"]
        return super().__call__(source, cancel, payload)

    def answer(self, result):
        return result.get("summary") or {}

    def cancellable(self, result):
        return False  # A file the worker saved stays saved: its answer is awaited.

    def stopped(self):
        return (f"원본 반영을 멈췄지만 {subject(self.application_label)} 제때 끝내지 않아 원본 파일이 바뀌었는지 알 수 "
                "없습니다. 원본 파일을 열어 확인해 주세요.")

    def password(self, name):
        # The prompt shows as the file opens, before anything is written.
        return (f"암호가 걸린 {name} 파일이라 원본에 반영하지 않았습니다. {name}이(가) 띄운 암호 입력 창은 앱이 취소했고, "
                "원본 파일은 바뀌지 않았습니다.")

    @staticmethod
    def ready(name):
        """Refuse a write that the application `name` is not installed for."""
        if not capabilities()["applications"].get(name):
            raise UserError(f"{name} 데스크톱 앱이 설치된 Windows에서 원본에 반영할 수 있습니다.")

    def check(self, source):
        self.ready(self.payload["application"])

    def applications(self, source):
        return (self.payload["application"],)
