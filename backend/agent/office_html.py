"""Read an open Office document through its object model into the body of an HTML page.

The document is never saved or copied: every reader walks the COM objects of the document an Office
application opened and asks for anything else in memory (Excel's XML Spreadsheet of a range). Pictures of
slides, pages, charts and shapes are the exception: PowerPoint, Excel and Visio hand them over only as
files, which they write into a private scratch folder that is emptied at once (office_pictures.py).
`tick` raises when the import is stopped; it runs between sheets, slides and pages, so a stopped worker
still closes its document and its Office application itself.

Excel keeps each sheet's grid with cell addresses, the values as their number formats show them (in the
workbook's date system), fonts, fills, borders, alignment, merged cells, column widths, comments and links;
charts as Excel draws them with the tables of their data, pictures, shapes and text boxes as Excel draws
them (the cells under them published as a web page), and the answers of form controls, each with the cell
it sits on. PowerPoint keeps a picture of each slide as PowerPoint draws it,
with the slide's title, text with its formatting, bullets and numbers, tables, charts, SmartArt, speaker
notes and comments. Visio keeps a picture of each page with its shape text, connections and Shape Data.
Where Office cannot hand a picture over, its text or a marker stays."""

import math
import re
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from html import escape
from xml.etree import ElementTree as ET

from . import images
from .html_document import attribute, bgr_color, chart_table, css, font_family, hex_color, points
from .llm import UserError
from .office_session import property_with
from .word_html import list_number, symbol

MAX_CELLS = 100000
MAX_SHAPES = 20000
MAX_SHEET_SHAPES = 2000  # pictures, shapes and controls listed of one sheet
MAX_DEPTH = 32
# A sheet whose XML Spreadsheet Excel did not hand over keeps its values only, and says so.
PLAIN_CELLS = "Excel이 셀 서식을 넘겨주지 않아 표시 형식·글꼴·병합·메모·링크 없이 값만 가져왔습니다."
LINE_BREAKS = ("\r\n", "\r", "\v", "\u2028", "\u2029")
TRUE = -1  # msoTrue

# Excel error values arrive as VT_ERROR codes 0x800A0000 + xlErr…; numbers always arrive as floats.
EXCEL_ERRORS = {
    2000: "#NULL!",
    2007: "#DIV/0!",
    2015: "#VALUE!",
    2023: "#REF!",
    2029: "#NAME?",
    2036: "#NUM!",
    2042: "#N/A",
    2043: "#GETTING_DATA",
    2045: "#SPILL!",
    2046: "#CONNECT!",
    2047: "#BLOCKED!",
    2048: "#UNKNOWN!",
    2049: "#FIELD!",
    2050: "#CALC!",
}
XML_SPREADSHEET = 11  # xlRangeValueXMLSpreadsheet: values, formatting, formulas and names of a range
SS = "{urn:schemas-microsoft-com:office:spreadsheet}"
HTML40 = "{http://www.w3.org/TR/REC-html40}"
TITLE_PLACEHOLDERS = {1, 3, 5}  # ppPlaceholderTitle, ppPlaceholderCenterTitle, ppPlaceholderVerticalTitle
SLIDE_SIDE = 1280  # px of the long side of a slide's picture
NO_PICTURE = (3, 4, 8, 12)  # charts (drawn by sheet_charts), comments, form and ActiveX controls
CHECK_MARKS = {1: "☒", 2: "◩"}  # a check box on (xlOn) or mixed (xlMixed); off is ☐
VISIO_SHAPES = {2, 3, 4}  # visTypeGroup, visTypeShape, visTypeForeignObject; not guides or ink
VISIO_GROUP = 2
VISIO_DATA = 243  # visSectionProp, the Shape Data section
VISIO_VALUE, VISIO_LABEL, VISIO_HIDDEN = 0, 2, 6  # visCustPropsValue, visCustPropsLabel, visCustPropsInvis
VISIO_BEGIN, VISIO_END = {7, 8, 9}, {10, 11, 12}  # visBeginX/Y, visBegin; visEndX/Y, visEnd
# xlChartType values of the common chart families.
CHART_TYPES = {
    **dict.fromkeys((51, 52, 53, -4100, 54, 55, 56), "세로 막대형"),
    **dict.fromkeys((57, 58, 59, 60, 61, 62), "가로 막대형"),
    **dict.fromkeys((4, 63, 64, 65, 66, 67, -4101), "꺾은선형"),
    **dict.fromkeys((5, 69, -4102, 70, 68, 71), "원형"),
    **dict.fromkeys((1, 76, 77, -4098, 78, 79), "영역형"),
    **dict.fromkeys((-4169, 72, 73, 74, 75), "분산형"),
    -4120: "도넛형",
    80: "도넛형",
    -4151: "방사형",
    81: "방사형",
    82: "방사형",
    15: "거품형",
    87: "거품형",
    83: "표면형",
    84: "표면형",
    85: "표면형",
    86: "표면형",
    88: "주식형",
    89: "주식형",
    90: "주식형",
    91: "주식형",
}
EXCEL_BORDERS = {
    "Continuous": "solid",
    "Dash": "dashed",
    "Dot": "dotted",
    "DashDot": "dashed",
    "DashDotDot": "dashed",
    "SlantDashDot": "dashed",
    "Double": "double",
}
EXCEL_ALIGN = {
    "Left": "left",
    "Center": "center",
    "Right": "right",
    "Justify": "justify",
    "Distributed": "justify",
    "CenterAcrossSelection": "center",
    "Fill": "left",
}
COLORS = {
    "red": "#FF0000",
    "blue": "#0000FF",
    "green": "#00FF00",
    "yellow": "#FFFF00",
    "magenta": "#FF00FF",
    "cyan": "#00FFFF",
    "black": "#000000",
    "white": "#FFFFFF",
}
POWERPOINT_ALIGN = {2: "center", 3: "right", 4: "justify", 5: "justify"}
# PpNumberedBulletStyle: how a numbered paragraph writes its number.
NUMBER_STYLES = {
    0: ("lowerLetter", "{}."),
    1: ("upperLetter", "{}."),
    2: ("decimal", "{})"),
    3: ("decimal", "{}."),
    4: ("lowerRoman", "({})"),
    5: ("lowerRoman", "{})"),
    6: ("lowerRoman", "{}."),
    7: ("upperRoman", "{}."),
    8: ("lowerLetter", "({})"),
    9: ("lowerLetter", "{})"),
    10: ("upperLetter", "({})"),
    11: ("upperLetter", "{})"),
    12: ("decimal", "({})"),
    13: ("decimal", "{}"),
    14: ("upperRoman", "({})"),
    15: ("upperRoman", "{})"),
}


def plain(text):
    """Text with every Office line break as a newline and without field placeholders."""
    text = "" if text is None else str(text)
    for separator in LINE_BREAKS:
        text = text.replace(separator, "\n")
    return text.replace("\ufffc", "").strip()


def one_line(text):
    return " ".join(plain(text).split())


def html_text(value):
    return escape(plain(value)).replace("\n", "<br>")


def run_text(value):
    """The text of one run, its spaces kept: they separate it from the runs beside it."""
    text = "" if value is None else str(value)
    for separator in LINE_BREAKS:
        text = text.replace(separator, "\n")
    return escape(text.replace("\ufffc", "")).replace("\n", "<br>")


def read(target, name, default=None):
    """An optional property, such as formatting: one Office cannot give leaves the default."""
    try:
        return getattr(target, name)
    except Exception:
        return default


def as_tuple(value):
    if value is None:
        return ()
    return tuple(value) if isinstance(value, (tuple, list)) else (value,)


# --- Excel values and number formats --------------------------------------------------------------


def cell_text(value):
    """A cell value in the General format, as Excel shows it near enough for a Wiki page."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, datetime):
        # COM dates carry UTC as their zone although Excel dates have none; 1899-12-30 is a time of day.
        value = value.replace(tzinfo=None)
        precision = "seconds" if value.second else "minutes"
        if value.date() == date(1899, 12, 30):
            return value.time().isoformat(precision)
        return value.date().isoformat() if value.time() == time() else value.isoformat(" ", precision)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, int):
        return EXCEL_ERRORS.get((value & 0xFFFFFFFF) - 0x800A0000, str(value))
    if isinstance(value, float):
        return format(value, ".15g")
    return str(value)


# Excel's named formats of the XML Spreadsheet format, written as the codes a Korean Excel shows.
NAMED_FORMATS = {
    "General Number": "General",
    "General Date": "yyyy-mm-dd h:mm",
    "Short Date": "yyyy-mm-dd",
    "Medium Date": "dd-mmm-yy",
    "Long Date": 'yyyy"년" m"월" d"일" dddd',
    "Short Time": "h:mm",
    "Medium Time": "h:mm AM/PM",
    "Long Time": "h:mm:ss",
    "Fixed": "0.00",
    "Standard": "#,##0.00",
    "Percent": "0.00%",
    "Scientific": "0.00E+00",
    "Currency": "₩#,##0;-₩#,##0",
    "Euro Currency": "€#,##0.00;-€#,##0.00",
    "Yes/No": '"Yes";"Yes";"No"',
    "True/False": '"True";"True";"False"',
    "On/Off": '"On";"On";"Off"',
}
EPOCH = datetime(1899, 12, 30)
EPOCH_1904 = datetime(1904, 1, 1)  # day 0 of a workbook in the 1904 date system (Workbook.Date1904)
KOREAN_DAYS = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
ENGLISH_DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ENGLISH_MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def format_sections(code):
    """The ;-separated sections of a number format; quoted, escaped and bracketed text stays whole."""
    found, current, index = [], "", 0
    while index < len(code):
        char = code[index]
        if char == '"':
            end = code.find('"', index + 1)
            end = len(code) if end < 0 else end
            current += code[index : end + 1]
            index = end + 1
        elif char in "\\_*" and index + 1 < len(code):
            current += code[index : index + 2]
            index += 2
        elif char == "[":
            end = code.find("]", index)
            end = len(code) if end < 0 else end
            current += code[index : end + 1]
            index = end + 1
        elif char == ";":
            found.append(current)
            current, index = "", index + 1
        else:
            current += char
            index += 1
    return found + [current]


def format_tokens(section):
    """[(kind, text)] of a number format section, its color and condition, and its locale."""
    found, color, condition, locale, index = [], None, None, "", 0
    while index < len(section):
        char, rest = section[index], section[index:]
        if char == '"':
            end = section.find('"', index + 1)
            end = len(section) if end < 0 else end
            found.append(("text", section[index + 1 : end]))
            index = end + 1
        elif char == "\\" and index + 1 < len(section):
            found.append(("text", section[index + 1]))
            index += 2
        elif char == "_" and index + 1 < len(section):
            found.append(("text", " "))
            index += 2
        elif char == "*" and index + 1 < len(section):
            index += 2  # a fill character repeats to the cell's width
        elif char == "[":
            end = section.find("]", index)
            end = len(section) if end < 0 else end
            inner = section[index + 1 : end]
            if re.fullmatch(r"[hms]+", inner, re.I):
                found.append(("elapsed", inner.lower()))
            elif inner.startswith("$"):
                symbol, _, locale = inner[1:].partition("-")
                if symbol:
                    found.append(("text", symbol))
            elif re.fullmatch(r"[<>=]{1,2}-?[\d.]+", inner):
                condition = inner
            elif inner.lower() in COLORS:
                color = COLORS[inner.lower()]
            index = end + 1
        elif rest[:7].lower() == "general":
            found.append(("general", ""))
            index += 7
        elif rest[:5].upper() == "AM/PM":
            found.append(("ampm", rest[:5]))
            index += 5
        elif rest[:3].upper() == "A/P":
            found.append(("ap", rest[:3]))
            index += 3
        elif char in "Ee" and rest[1:2] in ("+", "-"):
            found.append(("exp", rest[:2]))
            index += 2
        elif char.lower() in "ymdhsea":
            end = index
            while end < len(section) and section[end].lower() == char.lower():
                end += 1
            token = section[index:end].lower()
            if char.lower() == "a" and len(token) < 3:
                found.append(("text", section[index:end]))
            else:
                found.append(("date", token))
            index = end
        elif char in "0#?":
            found.append(("digit", char))
            index += 1
        elif char in ".,%@/":
            found.append(
                {
                    ".": ("point", "."),
                    ",": ("comma", ","),
                    "%": ("percent", "%"),
                    "@": ("at", "@"),
                    "/": ("slash", "/"),
                }[char]
            )
            index += 1
        else:
            found.append(("text", char))
            index += 1
    return found, color, condition, locale


def matches(condition, number):
    operator, limit = re.match(r"([<>=]{1,2})(.*)", condition).groups()
    limit = float(limit)
    return {
        "<": number < limit,
        ">": number > limit,
        "=": number == limit,
        "<=": number <= limit,
        ">=": number >= limit,
        "<>": number != limit,
    }.get(operator, False)


@lru_cache(maxsize=1024)
def parsed_format(code):
    """The tokens of each section of a number format, and of its text section; a sheet uses few formats."""
    sections = format_sections(code)
    text = next((section for section in sections if "@" in section), None)
    return tuple(format_tokens(section) for section in sections[:4]), None if text is None else format_tokens(text)


def format_value(value, code, date1904=False):
    """(text, color) of a value as Excel shows it in number format `code`, or None to show it in the
    General format: for General itself and for anything this reading of number formats does not know.
    `date1904`: the workbook counts dates from 1904 (Workbook.Date1904)."""
    code = NAMED_FORMATS.get(code, code)
    if not code or code in ("General", "@") or isinstance(value, bool) or value is None:
        return None
    parsed, text_section = parsed_format(code)
    if isinstance(value, str):
        if text_section is None:
            return None
        tokens, color, _, _ = text_section
        return "".join(value if kind == "at" else part for kind, part in tokens if kind in ("text", "at")), color
    if isinstance(value, int) or not isinstance(value, float) or math.isnan(value) or math.isinf(value):
        return None
    # A format code is written by whoever made the file: one this reading cannot follow is shown as General.
    try:
        number, negative_section = value, False
        if any(condition for _, _, condition, _ in parsed[:2]):
            chosen = next((item for item in parsed[:2] if item[2] and matches(item[2], value)), None)
            chosen = chosen or (parsed[2] if len(parsed) > 2 else parsed[-1] if not parsed[-1][2] else None)
            if chosen is None:
                return None
        elif value > 0 or len(parsed) == 1 or (value == 0 and len(parsed) < 3):
            chosen = parsed[0]
        elif value < 0:
            chosen, number, negative_section = parsed[1], -value, True
        else:
            chosen = parsed[2]
        tokens, color, _, locale = chosen
        if not tokens:
            return "", color
        if any(kind in ("date", "elapsed", "ampm", "ap") for kind, _ in tokens):
            text = date_text(tokens, number, locale, date1904)
        else:
            text = number_text(tokens, number if negative_section or len(parsed) > 1 else abs(number))
            if not negative_section and len(parsed) == 1 and number < 0 and text is not None:
                text = "-" + text
    except (ValueError, OverflowError, InvalidOperation, ArithmeticError, IndexError, KeyError, TypeError):
        return None
    return None if text is None else (text, color)


def calendar(offset, date1904=False):
    """(moment, (year, month, day), weekday) of a serial date, `offset` days after day 0, as Excel shows it.

    In the 1900 date system Excel counts 1900 as a leap year, as Lotus 1-2-3 did: day 1 is 1900-01-01, day 60 the 29
    February that never was and day 0 is shown as 1900-01-00, so the days before 1 March 1900 are one later than the
    calendar's. Their weekdays are Excel's as well, one before the calendar's: Excel counts back from day 61."""
    moment = (EPOCH_1904 if date1904 else EPOCH) + offset
    year, month, day = moment.year, moment.month, moment.day
    if not date1904 and offset < timedelta(days=61):
        if offset.days in (0, 60):
            year, month, day = (1900, 1, 0) if offset.days == 0 else (1900, 2, 29)
        else:
            later = moment + timedelta(days=1)
            year, month, day = later.year, later.month, later.day
    return moment, (year, month, day), moment.weekday()


def date_text(tokens, serial, locale, date1904=False):
    if serial < 0:
        return None
    # Fractions of a second: s or ss followed by a point and zeros (ss.00).
    fraction_at, decimals = None, 0
    for index, (kind, text) in enumerate(tokens):
        if kind == "point" and index and tokens[index - 1][0] == "date" and tokens[index - 1][1][0] == "s":
            fraction_at = index
            for kind_, text_ in tokens[index + 1 :]:
                if kind_ != "digit" or text_ != "0":
                    break
                decimals += 1
            break
    unit = timedelta(seconds=10**-decimals)
    offset = round(timedelta(days=serial) / unit) * unit
    moment, (year, month, day), weekday = calendar(offset, date1904)
    english = locale.lower() in ("409", "809", "en-us", "en-gb")
    korean = locale.lower() in ("412", "ko-kr")
    twelve = any(kind in ("ampm", "ap") for kind, _ in tokens)
    out = []
    for index, (kind, text) in enumerate(tokens):
        if fraction_at is not None and fraction_at < index <= fraction_at + decimals:
            continue  # written with the point before them
        if kind == "date":
            letter, width = text[0], len(text)
            if letter == "m" and width <= 2 and minute_context(tokens, index):
                out.append(f"{moment.minute:0{width}d}")
            elif letter == "m":
                out.append(month_text(month, width, english))
            elif letter == "y":
                out.append(f"{year % 100:02d}" if width <= 2 else f"{year:04d}")
            elif letter == "e":
                out.append(str(year))
            elif letter == "d" and width <= 2:
                out.append(f"{day:0{width}d}")
            elif letter == "d":
                name = (ENGLISH_DAYS if english else KOREAN_DAYS)[weekday]
                out.append(name if width >= 4 else name[:3] if english else name[0])
            elif letter == "a":
                name = KOREAN_DAYS[weekday]
                out.append(name if width >= 4 else name[0])
            elif letter == "h":
                out.append(f"{(moment.hour % 12 or 12) if twelve else moment.hour:0{min(width, 2)}d}")
            else:
                out.append(f"{moment.second:0{min(width, 2)}d}")
        elif kind == "elapsed":
            total = offset.total_seconds()
            out.append(str(int(total // {"h": 3600, "m": 60, "s": 1}[text[0]])).zfill(len(text)))
        elif kind == "ampm":
            morning = moment.hour < 12
            out.append(("오전" if morning else "오후") if korean else ("AM" if morning else "PM"))
        elif kind == "ap":
            out.append("A" if moment.hour < 12 else "P")
        elif index == fraction_at:
            out.append(f"{moment.microsecond / 1e6:.{decimals}f}"[1:] if decimals else ".")
        elif kind in ("text", "comma", "slash", "percent", "point"):
            out.append(text)
        else:
            return None  # digits, exponents or @ among date parts
    return "".join(out)


def minute_context(tokens, index):
    """Whether m or mm at `index` means minutes: right after hours or right before seconds."""
    for step in (-1, 1):
        position = index + step
        while 0 <= position < len(tokens) and tokens[position][0] == "text":
            position += step
        if 0 <= position < len(tokens) and tokens[position][0] in ("date", "elapsed"):
            letter = tokens[position][1][0]
            if (step < 0 and letter == "h") or (step > 0 and letter == "s"):
                return True
    return False


def month_text(month, width, english):
    if width <= 2:
        return f"{month:0{width}d}"
    if english:
        name = ENGLISH_MONTHS[month - 1]
        return name[0] if width == 5 else name if width == 4 else name[:3]
    return str(month) if width == 5 else f"{month}월"


def number_text(tokens, number):
    """The number written with digit placeholders (0 # ?), separators, percent, scaling by thousands and
    exponents; None for fractions such as # ?/?, which are left to the General format."""
    if any(kind == "slash" for kind, _ in tokens):
        return None
    if not any(kind == "digit" for kind, _ in tokens):
        return "".join(
            cell_text(number) if kind == "general" else text
            for kind, text in tokens
            if kind in ("text", "percent", "general")
        )
    value = Decimal(repr(float(number))) * 100 ** sum(1 for kind, _ in tokens if kind == "percent")
    exponent = next((index for index, (kind, _) in enumerate(tokens) if kind == "exp"), None)
    body = tokens if exponent is None else tokens[:exponent]
    point = next((index for index, (kind, _) in enumerate(body) if kind == "point"), None)
    whole_tokens = body if point is None else body[:point]
    part_tokens = [] if point is None else body[point + 1 :]
    places = sum(1 for kind, _ in part_tokens if kind == "digit")
    # A comma right after the last digit placeholder divides by 1,000; one between digits groups them.
    digits = [index for index, (kind, _) in enumerate(body) if kind == "digit"]
    if not digits:
        return None  # every placeholder after the exponent, as in E-#: nothing Excel writes a number with
    scale, position = 0, digits[-1] + 1
    while position < len(body) and body[position][0] == "comma":
        scale, position = scale + 1, position + 1
    value /= Decimal(1000) ** scale
    whole_digits = [index for index, (kind, _) in enumerate(whole_tokens) if kind == "digit"]
    grouping = bool(whole_digits) and any(
        kind == "comma" for kind, _ in whole_tokens[whole_digits[0] : whole_digits[-1]]
    )
    power = 0
    if exponent is not None and value:
        size = max(1, len(whole_digits))
        power = math.floor(math.log10(abs(float(value))))
        power = power - power % size if size > 1 else power - (size - 1)
        value /= Decimal(10) ** power
    rounded = abs(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    if exponent is not None and rounded >= Decimal(10) ** max(1, len(whole_digits)):
        # 9.999 in 0.00E+00 rounds to 10.00: Excel writes 1.00E+01.
        step = max(1, len(whole_digits)) if len(whole_digits) > 1 else 1
        power += step
        rounded = (abs(value) / Decimal(10) ** step).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    whole, _, part = f"{rounded:f}".partition(".")
    whole = whole.lstrip("0")
    whole = whole.rjust(sum(1 for index in whole_digits if whole_tokens[index][1] == "0"), "0")
    if grouping and whole:
        whole = f"{int(whole):,}"
    spaces = sum(1 for index in whole_digits if whole_tokens[index][1] == "?")
    out = []
    between = [kind for kind, _ in whole_tokens[whole_digits[0] : whole_digits[-1]]] if whole_digits else []
    if "text" in between and not grouping:
        # Placeholders fill from the right; text between them (as in 000-0000) stays where it is.
        fill, written = list(whole), []
        for kind, text in reversed(whole_tokens):
            if kind == "digit":
                written.append(fill.pop() if fill else "0" if text == "0" else " " if text == "?" else "")
            elif kind in ("text", "percent"):
                written.append(text)
        first = next(index for index, (kind, _) in enumerate(whole_tokens) if kind == "digit")
        lead = sum(1 for kind, _ in whole_tokens[:first] if kind in ("text", "percent"))
        written.reverse()
        out.append("".join(written[:lead]) + "".join(fill) + "".join(written[lead:]))
    else:
        placed = False
        for index, (kind, text) in enumerate(whole_tokens):
            if kind == "digit" and not placed:
                out.append(" " * max(0, spaces - len(whole)) + whole)
                placed = True
            elif kind in ("text", "percent"):
                out.append(text)
            elif kind == "general":
                out.append(cell_text(float(number)))
    if point is not None:
        written = list(part.ljust(places, "0"))
        holders = [text for kind, text in part_tokens if kind == "digit"]
        for offset in range(len(written) - 1, -1, -1):
            if holders[offset] == "0" or written[offset] != "0":
                break
            written[offset] = " " if holders[offset] == "?" else ""
        out.append(".")
        placed = False
        for kind, text in part_tokens:
            if kind == "digit" and not placed:
                out.append("".join(written))
                placed = True
            elif kind in ("text", "percent"):
                out.append(text)
    if exponent is not None:
        tail = tokens[exponent + 1 :]
        width = sum(1 for kind, _ in tail if kind == "digit")
        sign = "-" if power < 0 else "+" if tokens[exponent][1][1] == "+" else ""
        out.append(tokens[exponent][1][0].upper() + sign + str(abs(power)).zfill(max(1, width)))
        out.extend(text for kind, text in tail if kind in ("text", "percent"))
    return "".join(out)


# --- Excel ----------------------------------------------------------------------------------------


def column_name(index):
    """Excel's letters for a 1-based column index: 1 → A, 27 → AA."""
    name = ""
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def spreadsheet_xml(used):
    """The used range as XML Spreadsheet 2003 (formatting, merged cells, comments, links), or None
    when Excel does not give it."""
    try:
        xml = property_with(used, "Value", XML_SPREADSHEET)
    except Exception:
        return None
    if not isinstance(xml, str) or "<!DOCTYPE" in xml or "<!ENTITY" in xml or "Workbook" not in xml[:4000]:
        return None
    try:
        return ET.fromstring(xml)
    except ET.ParseError:
        return None


def style_props(element, props):
    for child in [] if element is None else element:
        name = child.tag[len(SS) :] if child.tag.startswith(SS) else ""
        get = lambda key: child.get(SS + key)
        if name == "Alignment":
            props.update(
                {
                    key: value
                    for key, value in (
                        ("horizontal", get("Horizontal")),
                        ("vertical", get("Vertical")),
                        ("wrap", get("WrapText")),
                        ("indent", get("Indent")),
                        ("rotate", get("Rotate")),
                    )
                    if value is not None
                }
            )
        elif name == "Font":
            props.update(
                {
                    key: value
                    for key, value in (
                        ("font", get("FontName")),
                        ("size", get("Size")),
                        ("color", get("Color")),
                        ("bold", get("Bold")),
                        ("italic", get("Italic")),
                        ("underline", get("Underline")),
                        ("strike", get("StrikeThrough")),
                        ("script", get("VerticalAlign")),
                    )
                    if value is not None
                }
            )
        elif name == "Interior":
            props["fill"] = get("Color") if get("Pattern") != "None" else None
        elif name == "Borders":
            props["borders"] = {side.get(SS + "Position"): side for side in child}
        elif name == "NumberFormat":
            props["format"] = get("Format") or "General"
    return props


class Sheet:
    """The XML Spreadsheet of one used range: styles, merged cells, comments, links, widths and heights."""

    def __init__(self, root):
        self.styles = {style.get(SS + "ID"): style for style in root.iter(SS + "Style")}
        self.resolved = {}
        table = root.find(f"{SS}Worksheet/{SS}Table")
        self.columns, self.rows, self.cells = {}, {}, {}
        if table is None:
            return
        column = 0
        for item in table.findall(SS + "Column"):
            column = int(item.get(SS + "Index")) - 1 if (item.get(SS + "Index") or "").isdigit() else column
            for _ in range(int(item.get(SS + "Span", "0") or 0) + 1):
                self.columns[column] = {
                    "width": item.get(SS + "Width"),
                    "hidden": item.get(SS + "Hidden") == "1",
                    "style": item.get(SS + "StyleID"),
                }
                column += 1
        row = 0
        for item in table.findall(SS + "Row"):
            row = int(item.get(SS + "Index")) - 1 if (item.get(SS + "Index") or "").isdigit() else row
            span = int(item.get(SS + "Span", "0") or 0) + 1
            for offset in range(span):
                self.rows[row + offset] = {
                    "height": item.get(SS + "Height"),
                    "hidden": item.get(SS + "Hidden") == "1",
                    "style": item.get(SS + "StyleID"),
                }
            column = 0
            for cell in item.findall(SS + "Cell"):
                column = int(cell.get(SS + "Index")) - 1 if (cell.get(SS + "Index") or "").isdigit() else column
                across, down = int(cell.get(SS + "MergeAcross", "0") or 0), int(cell.get(SS + "MergeDown", "0") or 0)
                data, note = cell.find(SS + "Data"), cell.find(SS + "Comment")
                self.cells[(row, column)] = {
                    "style": cell.get(SS + "StyleID")
                    or self.rows[row]["style"]
                    or self.columns.get(column, {}).get("style"),
                    "across": across,
                    "down": down,
                    "href": cell.get(SS + "HRef"),
                    "rich": rich(data) if data is not None and len(data) else None,
                    "comment": comment_text(note) if note is not None else None,
                    "author": note.get(SS + "Author", "") if note is not None else "",
                }
                column += across + 1
            row += span

    def style(self, ident):
        """A style's properties over its parents' and the Default style's."""
        if ident not in self.resolved:
            chain, current = [], ident
            while current and current not in chain and len(chain) < 16:
                chain.append(current)
                current = self.styles.get(current).get(SS + "Parent") if self.styles.get(current) is not None else None
            props = style_props(self.styles.get("Default"), {})
            for item in reversed(chain):
                if item != "Default":
                    style_props(self.styles.get(item), props)
            self.resolved[ident] = props
        return self.resolved[ident]


def rich(data):
    """The HTML of a cell's rich text (B, I, U, S, Sub, Sup, Font of HTML 4.0)."""
    tags = {"B": "strong", "I": "em", "U": "u", "S": "s", "Sub": "sub", "Sup": "sup"}

    def walk(element, depth=0):
        html = escape(element.text or "").replace("\n", "<br>")
        for child in element:
            name = child.tag[len(HTML40) :] if child.tag.startswith(HTML40) else ""
            inner = walk(child, depth + 1) if depth < MAX_DEPTH else ""
            if name in tags:
                html += f"<{tags[name]}>{inner}</{tags[name]}>"
            elif name == "Font":
                pairs = [
                    ("color", hex_color(child.get(HTML40 + "Color"))),
                    ("font-family", font_family([child.get(HTML40 + "Face")]) if child.get(HTML40 + "Face") else None),
                    (
                        "font-size",
                        points(float(child.get(HTML40 + "Size")))
                        if re.fullmatch(r"[\d.]+", child.get(HTML40 + "Size") or "")
                        else None,
                    ),
                ]
                style = css(pairs)
                html += f'<span style="{attribute(style)}">{inner}</span>' if style else inner
            else:
                html += inner
            html += escape(child.tail or "").replace("\n", "<br>")
        return html

    return walk(data)


def comment_text(note):
    data = note.find(SS + "Data")
    return plain("".join(data.itertext())) if data is not None else ""


def excel_border(side):
    if side is None:
        return None
    style = EXCEL_BORDERS.get(side.get(SS + "LineStyle"))
    if not style:
        return None
    weight = {"0": 0.5, "1": 0.75, "2": 1.5, "3": 2.25}.get(side.get(SS + "Weight", "1"), 0.75)
    if style == "double":
        weight = max(weight, 2.25)
    return f"{points(weight)} {style} {hex_color(side.get(SS + 'Color')) or '#000000'}"


def excel_cell_css(props, default, kind, color):
    """The CSS of a cell's style, compared with the sheet's Default style."""
    pairs = []
    if props.get("font") != default.get("font") and props.get("font"):
        pairs.append(("font-family", font_family([props["font"]])))
    if props.get("size") != default.get("size") and re.fullmatch(r"[\d.]+", props.get("size") or ""):
        pairs.append(("font-size", points(float(props["size"]))))
    if props.get("bold") == "1":
        pairs.append(("font-weight", "bold"))
    if props.get("italic") == "1":
        pairs.append(("font-style", "italic"))
    lines = (["underline"] if props.get("underline") not in (None, "None") else []) + (
        ["line-through"] if props.get("strike") == "1" else []
    )
    if lines:
        pairs.append(
            (
                "text-decoration",
                " ".join(lines) + (" double" if (props.get("underline") or "").startswith("Double") else ""),
            )
        )
    shown = color or hex_color(props.get("color"))
    if shown and shown != hex_color(default.get("color")):
        pairs.append(("color", shown))
    if hex_color(props.get("fill")):
        pairs.append(("background-color", hex_color(props["fill"])))
    align = EXCEL_ALIGN.get(props.get("horizontal"))
    if not align:
        # General alignment: numbers right, logical and error values centered, text left.
        align = {"number": "right", "logical": "center"}.get(kind)
    if align:
        pairs.append(("text-align", align))
    vertical = {"Top": "top", "Center": "middle", "Justify": "middle", "Distributed": "middle"}.get(
        props.get("vertical")
    )
    if vertical:
        pairs.append(("vertical-align", vertical))
    if props.get("wrap") == "1":
        pairs.append(("white-space", "pre-wrap"))
    if (props.get("indent") or "").isdigit() and int(props["indent"]):
        pairs.append(("padding-" + ("right" if align == "right" else "left"), points(int(props["indent"]) * 9)))
    for side, name in (("Top", "top"), ("Bottom", "bottom"), ("Left", "left"), ("Right", "right")):
        line = excel_border(props.get("borders", {}).get(side))
        if line:
            pairs.append((f"border-{name}", line))
    if props.get("rotate") in ("90", "-90", "255"):
        pairs.append(("writing-mode", "vertical-rl"))
    return css(pairs)


def excel_html(workbook, tick=lambda: None, exports=None):
    """The sheets of a workbook; `exports` (office_pictures.Exports) receives the pictures Excel draws."""
    parts, total = [], [0]
    sheets = workbook.Worksheets
    for index in range(1, sheets.Count + 1):
        tick()
        parts.append(sheet_html(sheets.Item(index), total, workbook, exports))
    # Chart sheets hold one chart each.
    charts = read(workbook, "Charts")
    for index in range(1, (read(charts, "Count", 0) or 0) + 1):
        tick()
        chart = charts.Item(index)
        name = one_line(read(chart, "Name", ""))
        parts.append(
            f'<section class="sheet"><h2>{html_text(name or f"차트 시트 {index}")} (차트 시트)</h2>'
            f"{chart_picture(chart, name, exports)}{chart_html(chart, name)}</section>"
        )
    return (exports.notice() if exports is not None else "") + "".join(parts)


def sheet_html(sheet, total, workbook=None, exports=None):
    used = sheet.UsedRange
    rows, columns = used.Rows.Count, used.Columns.Count
    top, left = used.Row, used.Column
    hidden = "" if sheet.Visible in (-1, True) else " (숨긴 시트)"
    date1904 = workbook is not None and read(workbook, "Date1904", False) in (True, -1)
    # An empty sheet's used range is A1 alone, without a value: it takes nothing of the workbook's cells.
    cells = 0 if rows == columns == 1 and used.Value2 is None else rows * columns
    # A workbook is read up to MAX_CELLS cells of its sheets' used ranges: the rows that still fit, and the page says
    # what was left out.
    cut = ""
    if total[0] + cells > MAX_CELLS:
        kept = (MAX_CELLS - total[0]) // columns
        if kept < 1:  # charts and pictures hold no cells: they still come
            return (
                f'<section class="sheet"><h2>{html_text(sheet.Name)}{hidden}</h2><p class="caption">통합 문서가 '
                f"커서 이 시트의 셀은 가져오지 않았습니다 (시트 사용 범위 합계 {MAX_CELLS:,}셀까지).</p>"
                f"{sheet_charts(sheet, exports)}{sheet_shapes(sheet, workbook, exports)}</section>"
            )
        used = sheet.Range(f"{column_name(left)}{top}:{column_name(left + columns - 1)}{top + kept - 1}")
        cut = (
            f'<p class="caption">시트가 커서 {rows:,}행 중 앞 {kept:,}행만 가져왔습니다 '
            f"(시트 사용 범위 합계 {MAX_CELLS:,}셀까지).</p>"
        )
        rows, columns = used.Rows.Count, used.Columns.Count
        cells = rows * columns
    total[0] += cells
    values, typed, formulas = used.Value2, used.Value, used.Formula
    # COM returns a scalar for a single cell and a tuple of rows otherwise.
    if rows == columns == 1:
        values, typed, formulas = ((values,),), ((typed,),), ((formulas,),)
    root = spreadsheet_xml(used)
    layout = Sheet(root) if root is not None else None
    default = layout.style("Default") if layout else {}
    threaded = threaded_comments(sheet)
    grid, notes = {}, []
    for r in range(rows):
        for c in range(columns):
            value, shown = values[r][c], typed[r][c]
            info = layout.cells.get((r, c), {}) if layout else {}
            props = layout.style(info.get("style")) if layout else {}
            formatted = format_value(value, props.get("format"), date1904) if layout else None
            color = None
            if formatted is not None:
                text, color = formatted
                content = escape(text)
            else:
                # Value2 keeps the precision of numbers; Value is read only to recognize dates.
                content = html_text(cell_text(shown if isinstance(shown, date) else value))
            if info.get("rich") and isinstance(value, str):
                content = info["rich"]
            address = f"{column_name(left + c)}{top + r}"
            comment = threaded.get(address) or ([(info["author"], info["comment"])] if info.get("comment") else [])
            formula = formulas[r][c] if isinstance(formulas[r][c], str) and formulas[r][c].startswith("=") else ""
            if not (content or comment):
                continue
            kind = "number" if isinstance(value, float) else "logical" if isinstance(value, (bool, int)) else "text"
            grid[(r, c)] = {
                "content": content,
                "formula": formula,
                "kind": kind,
                "color": color,
                "props": props,
                "info": info,
                "noted": bool(comment),
            }
            for author, text in comment:
                notes.append((address, author, text))
    # Formatting alone stretches the used range; rows and columns without a value are left out.
    kept_rows = sorted({r for r, _ in grid})
    kept_columns = sorted({c for _, c in grid})
    charts, shapes = sheet_charts(sheet, exports), sheet_shapes(sheet, workbook, exports)
    if not kept_rows and not charts and not shapes and not cut:
        return ""
    html = [f'<section class="sheet"><h2>{html_text(sheet.Name)}{hidden}</h2>{cut}']
    if kept_rows:
        if layout is None:
            html.append(f'<p class="caption">{PLAIN_CELLS}</p>')
        html.append(cells_table(grid, kept_rows, kept_columns, top, left, layout, default))
    if notes:
        html.append(
            '<div class="notes"><p class="caption">메모</p><ul>'
            + "".join(
                f"<li>{escape(address)}{' · ' + html_text(author) if author else ''}: {html_text(text)}</li>"
                for address, author, text in notes
            )
            + "</ul></div>"
        )
    html.append(charts + shapes + "</section>")
    return "".join(html)


def cells_table(grid, kept_rows, kept_columns, top, left, layout, default):
    """The grid with column letters and row numbers, so the cell addresses formulas refer to stay."""
    kept_row_set, kept_column_set = set(kept_rows), set(kept_columns)
    covered = set()
    style = css(
        [
            ("font-family", font_family([default["font"]]) if default.get("font") else None),
            (
                "font-size",
                points(float(default["size"])) if re.fullmatch(r"[\d.]+", default.get("size") or "") else None,
            ),
            ("color", hex_color(default.get("color"))),
        ]
    )
    html = [f'<table class="cells"{f" style={chr(34)}{attribute(style)}{chr(34)}" if style else ""}><colgroup><col>']
    for c in kept_columns:
        width = (layout.columns.get(c, {}).get("width") if layout else None) or ""
        html.append(f'<col style="width:{points(float(width))}">' if re.fullmatch(r"[\d.]+", width) else "<col>")
    html.append("</colgroup><tr><th></th>")
    for c in kept_columns:
        concealed = layout and layout.columns.get(c, {}).get("hidden")
        mark = ' title="숨긴 열"' if concealed else ""
        html.append(f"<th{mark}>{column_name(left + c)}{' (숨김)' if concealed else ''}</th>")
    html.append("</tr>")
    for r in kept_rows:
        row = layout.rows.get(r, {}) if layout else {}
        height = row.get("height") or ""
        attributes = (' class="hidden" title="숨긴 행"' if row.get("hidden") else "") + (
            f' style="height:{points(float(height))}"' if re.fullmatch(r"[\d.]+", height) else ""
        )
        html.append(f"<tr{attributes}><th>{top + r}</th>")
        for c in kept_columns:
            if (r, c) in covered:
                continue
            cell = grid.get((r, c))
            info = cell["info"] if cell else (layout.cells.get((r, c), {}) if layout else {})
            spans = ""
            across, down = info.get("across", 0), info.get("down", 0)
            if across or down:
                # A merged cell spans the kept rows and columns of its area.
                width = sum(1 for column in range(c, c + across + 1) if column in kept_column_set)
                height_ = sum(1 for line in range(r, r + down + 1) if line in kept_row_set)
                covered.update((line, column) for line in range(r, r + down + 1) for column in range(c, c + across + 1))
                spans = (f' colspan="{width}"' if width > 1 else "") + (f' rowspan="{height_}"' if height_ > 1 else "")
            if not cell:
                props = layout.style(info.get("style")) if layout and info else {}
                look = excel_cell_css(props, default, "text", None) if props else ""
                html.append(f"<td{spans}{f' style={chr(34)}{attribute(look)}{chr(34)}' if look else ''}></td>")
                continue
            look = (
                excel_cell_css(cell["props"], default, cell["kind"], cell["color"])
                if layout
                else ("text-align:right" if cell["kind"] == "number" else "")
            )
            formula = (
                f' data-formula="{attribute(cell["formula"])}" title="{attribute(cell["formula"])}"'
                if cell["formula"]
                else ""
            )
            content = cell["content"]
            href = info.get("href")
            if href and re.match(r"(https?|mailto):", href, re.I):
                content = f'<a href="{attribute(href)}">{content}</a>'
            elif href:
                content = f'<span title="{attribute(href)}">{content}</span>'
            if cell["noted"]:
                content += '<span class="note-mark" title="메모">◥</span>'
            html.append(
                f"<td{spans}{formula}{f' style={chr(34)}{attribute(look)}{chr(34)}' if look else ''}>{content}</td>"
            )
        html.append("</tr>")
    html.append("</table>")
    return "".join(html)


def threaded_comments(sheet):
    """{address: [(author, text)]} of Excel 365's threaded comments, with their replies."""
    found = {}
    comments = read(sheet, "CommentsThreaded")
    for index in range(1, (read(comments, "Count", 0) or 0) + 1):
        try:
            comment = comments.Item(index)
            address = str(comment.Parent.Address).replace("$", "")
            entries = [(one_line(read(read(comment, "Author"), "Name", "")), plain(comment.Text()))]
            replies = read(comment, "Replies")
            for reply_index in range(1, (read(replies, "Count", 0) or 0) + 1):
                reply = replies.Item(reply_index)
                entries.append((one_line(read(read(reply, "Author"), "Name", "")), plain(reply.Text())))
            found[address] = entries
        except Exception:
            continue
    return found


def chart_html(chart, name=""):
    """A chart of the Office chart object model (Excel or PowerPoint) as a table of its data."""
    try:
        title = one_line(chart.ChartTitle.Text) if read(chart, "HasTitle") else ""
        kind = CHART_TYPES.get(read(chart, "ChartType"), "")
        collection = chart.SeriesCollection()
        series, labels = [], {}
        for index in range(1, collection.Count + 1):
            item = collection.Item(index)
            for position, label in enumerate(as_tuple(read(item, "XValues"))):
                labels.setdefault(position, cell_text(label))
            series.append(
                (
                    one_line(read(item, "Name", "")) or f"계열 {index}",
                    {position: cell_text(value) for position, value in enumerate(as_tuple(read(item, "Values")))},
                )
            )
        return chart_table(title or name, kind, labels, series)
    except Exception:
        return f"<p>{images.missing(f'[차트: {name}] 데이터를 읽지 못했습니다' if name else '[차트] 데이터를 읽지 못했습니다')}</p>"


def chart_picture(chart, name, exports):
    """A figure of a chart as Excel draws it (Chart.Export), or ""."""
    found = None if exports is None else exports.picture(lambda path: chart.Export(path, "PNG"))
    if found is None:
        return ""
    kind, data, size = found
    return (
        f'<figure class="chart">{images.img(kind, data, f"차트: {name}" if name else "차트", *(size or ()))}</figure>'
    )


def sheet_charts(sheet, exports=None):
    try:
        objects = sheet.ChartObjects()
        count = objects.Count
    except Exception:
        return ""
    parts = []
    for index in range(1, count + 1):
        item = objects.Item(index)
        name = one_line(read(item, "Name", ""))
        parts.append(chart_picture(item.Chart, name, exports) + chart_html(item.Chart, name))
    return "".join(parts)


def publish_sheet(workbook, sheet, path, area=None):
    """Excel publishes the cells under a sheet's shapes (`area`), or the whole sheet, as a static web page, which comes
    with a picture of each shape on it; what was published, as its XlSourceType. The page holds the text of its cells
    too, so no more than the cells under the shapes is published when Excel says where they are; Excel writes it into
    the app's private scratch folder, which is emptied at once (office_pictures.Exports.web_page)."""
    options = read(workbook, "WebOptions")
    # PNG pictures, a page in UTF-8, and pictures for browsers without VML, which are the ones read.
    for name, value in (("AllowPNG", True), ("Encoding", 65001), ("RelyOnVML", False), ("OrganizeInFolder", True)):
        try:
            setattr(options, name, value)
        except Exception:
            pass
    if area:
        try:
            publish(workbook, path, SourceType=4, Sheet=sheet.Name, Source=area)  # xlSourceRange
            return 4
        except Exception:
            pass  # an Excel that cannot publish the range publishes the sheet, as before
    publish(workbook, path, SourceType=1, Sheet=sheet.Name)  # xlSourceSheet
    return 1


def publish(workbook, path, **source):
    published = workbook.PublishObjects.Add(Filename=path, HtmlType=0, **source)  # xlHtmlStatic
    try:
        published.Publish(True)
    finally:
        try:
            published.Delete()
        except Exception:
            pass


def shapes_area(items):
    """The address of the cells under these shapes, from the top left cell of one to the bottom right cell of another,
    or None when a shape does not say where it is."""
    rows, columns = [], []
    for item in items:
        for corner in (read(item, "TopLeftCell"), read(item, "BottomRightCell")):
            row, column = read(corner, "Row"), read(corner, "Column")
            if not (isinstance(row, int) and isinstance(column, int) and row > 0 and column > 0):
                return None
            rows.append(row)
            columns.append(column)
    return f"{column_name(min(columns))}{min(rows)}:{column_name(max(columns))}{max(rows)}" if rows else None


def sheet_shapes(sheet, workbook=None, exports=None):
    """Pictures, shapes, text boxes and other objects on a sheet, with the cell they sit on: as Excel
    draws them when it hands their pictures over, else their text or a marker."""
    shapes = read(sheet, "Shapes")
    count = read(shapes, "Count", 0) or 0
    items = [shapes.Item(index) for index in range(1, min(count, MAX_SHEET_SHAPES) + 1)]
    pictures = {}
    # A hidden sheet is not published: Excel may refuse it, and a failed export stops the rest.
    drawn = [item for item in items if read(item, "Type") not in NO_PICTURE]
    if exports is not None and workbook is not None and read(sheet, "Visible") in (-1, True) and drawn:
        area, published = shapes_area(drawn), []
        pictures = exports.web_page(lambda path: published.append(publish_sheet(workbook, sheet, path, area)))
        names = {one_line(read(item, "Name", "")) for item in drawn}
        if published == [4] and not exports.stopped and not names & set(pictures):
            # Excel left the shapes out of the page of their cells: the page of the sheet has them.
            pictures = exports.web_page(lambda path: publish_sheet(workbook, sheet, path))
    entries = []
    for item in items:
        entries.extend(shape_entries(item, 0, pictures))
    if not entries:
        return ""
    cut = (
        f'<p class="caption">도형이 많아 {count:,}개 중 앞 {MAX_SHEET_SHAPES:,}개만 적었습니다.</p>'
        if count > MAX_SHEET_SHAPES
        else ""
    )
    return f'<div class="notes"><p class="caption">그림·도형</p>{cut}<ul>' + "".join(entries) + "</ul></div>"


def shape_words(shape, depth=0):
    """The text of a shape, a group or SmartArt, for the alternative text of its picture."""
    try:
        kind = read(shape, "Type")
        if depth > MAX_DEPTH:
            return ""
        if kind == 6:  # msoGroup
            items = shape.GroupItems
            return " / ".join(
                filter(None, (shape_words(items.Item(index), depth + 1) for index in range(1, items.Count + 1)))
            )
        if kind == 24 and read(shape, "HasSmartArt") == TRUE:
            nodes = shape.SmartArt.AllNodes
            return " / ".join(
                filter(
                    None, (one_line(nodes.Item(index).TextFrame2.TextRange.Text) for index in range(1, nodes.Count + 1))
                )
            )
        frame = read(shape, "TextFrame2")
        return one_line(read(read(frame, "TextRange"), "Text", "")) if read(frame, "HasText") == TRUE else ""
    except Exception:
        return ""


def form_control(shape):
    """What a form control (msoFormControl) shows: its caption with the state of a check box or option button, the
    entry chosen in a list, or the value of a scroll bar or spinner; "" for one that shows nothing."""
    kind = read(shape, "FormControlType")
    control = read(shape, "ControlFormat")
    value = read(control, "Value")
    caption = one_line(read(read(read(shape, "OLEFormat"), "Object"), "Caption", "")) or one_line(
        read(shape, "AlternativeText", "")
    )
    if kind == 1:  # xlCheckBox: xlOn 1, xlMixed 2, xlOff -4146
        return f"{CHECK_MARKS.get(value, '☐')} {caption}".rstrip()
    if kind == 7:  # xlOptionButton
        return f"{'◉' if value == 1 else '○'} {caption}".rstrip()
    if kind in (2, 6):  # xlDropDown, xlListBox: Value is the chosen entry's number, 0 for none
        entries = as_tuple(read(control, "List"))
        try:
            index = int(value) if not isinstance(value, bool) and value == int(value) else 0
        except (TypeError, ValueError, OverflowError):
            index = 0
        chosen = one_line(entries[index - 1]) if 0 < index <= len(entries) else "(선택 없음)"
        return f"{caption + ': ' if caption else ''}{chosen}"
    if kind in (8, 9) and isinstance(value, (int, float)):  # xlScrollBar, xlSpinner
        return f"{caption + ': ' if caption else ''}{cell_text(value)}"
    return caption  # buttons, labels and group boxes


def shape_entries(shape, depth, pictures=None):
    kind = read(shape, "Type")
    if depth > MAX_DEPTH:
        return []
    if kind == 8:  # msoFormControl: an answer on the sheet, such as a check box of a form
        text = form_control(shape)
        where = str(read(read(shape, "TopLeftCell"), "Address", "") or "").replace("$", "")
        return [f"<li>{html_text(text)}{escape(f' ({where})' if where else '')}</li>"] if text else []
    if kind in NO_PICTURE:
        return []
    name, label = one_line(read(shape, "Name", "")), one_line(read(shape, "AlternativeText", ""))
    where = str(read(read(shape, "TopLeftCell"), "Address", "") or "").replace("$", "")
    place = f" ({where})" if where else ""
    picture = (pictures or {}).get(name)
    if picture is not None:
        # The shape as Excel draws it; its text stays in the alternative text, for search.
        found, data, size = picture
        words = shape_words(shape)
        alt = (label or name or "그림") + (f": {words}" if words else "")
        return [f"<li>{images.img(found, data, alt, *(size or ()))}{escape(place)}</li>"]
    if kind == 6:  # msoGroup
        items = shape.GroupItems
        return [entry for index in range(1, items.Count + 1) for entry in shape_entries(items.Item(index), depth + 1)]
    if kind in (11, 13):  # msoLinkedPicture, msoPicture
        return [f"<li>{images.missing('[그림: ' + (label or name) + ']')}{escape(place)}</li>"]
    if kind == 7:  # msoEmbeddedOLEObject
        return [f"<li>{images.missing('[삽입된 개체: ' + (label or name) + ']')}{escape(place)}</li>"]
    if kind == 24 and read(shape, "HasSmartArt") == TRUE:
        return [f"<li>{escape(name)}{escape(place)}{smartart_html(shape.SmartArt)}</li>"]
    frame = read(shape, "TextFrame2")
    text = plain(read(read(frame, "TextRange"), "Text", "")) if read(frame, "HasText") == TRUE else ""
    if text:
        return [f"<li>{html_text(text)}{escape(place)}</li>"]
    return []


# --- PowerPoint -----------------------------------------------------------------------------------


def run_css(font):
    """The CSS of a PowerPoint text run's font."""
    pairs = [("font-family", font_family([read(font, "Name", "")]) if read(font, "Name") else None)]
    size = read(font, "Size")
    if isinstance(size, (int, float)) and 0 < size < 4000:
        pairs.append(("font-size", points(size)))
    if read(font, "Bold") == TRUE:
        pairs.append(("font-weight", "bold"))
    if read(font, "Italic") == TRUE:
        pairs.append(("font-style", "italic"))
    if read(font, "Underline") == TRUE:
        pairs.append(("text-decoration", "underline"))
    color = bgr_color(read(read(font, "Color"), "RGB"))
    if color and color != "#000000":
        pairs.append(("color", color))
    return css(pairs)


def items_of(method):
    """The items of a TextRange method such as Paragraphs or Runs, or None when Office does not give them
    one by one; the caller then keeps the text as a whole."""
    try:
        count = method().Count
        return [method(index) for index in range(1, count + 1)]
    except Exception:
        return None


def bullet_label(form):
    """The bullet or number PowerPoint draws before a paragraph, as HTML."""
    bullet = read(form, "Bullet")
    if read(bullet, "Visible") != TRUE:
        return ""
    if read(bullet, "Type") == 2:  # ppBulletNumbered
        number = read(bullet, "Number", 1)
        kind, pattern = NUMBER_STYLES.get(read(bullet, "Style"), ("decimal", "{}."))
        text = escape(pattern.format(list_number(number, kind) if isinstance(number, int) else number))
    else:
        code = read(bullet, "Character")
        face = str(read(read(bullet, "Font"), "Name", "") or "")
        valid = isinstance(code, int) and 0x20 < code < 0x110000 and not 0xD800 <= code < 0xE000
        text = symbol(face, code) if valid else "•"
    return f'<span class="bullet">{text} </span>'


def text_html(text_range):
    """Paragraphs of a PowerPoint text range with their bullets, numbers, indent levels, alignment and
    the formatting of each run."""
    paragraphs = items_of(lambda *args: text_range.Paragraphs(*args))
    if not paragraphs:
        # Without paragraph access the text keeps its line breaks.
        text = plain(read(text_range, "Text", ""))
        return f"<p>{html_text(text)}</p>" if text else ""
    html = []
    for paragraph in paragraphs:
        runs = items_of(lambda *args: paragraph.Runs(*args)) or [paragraph]
        parts = []
        for position, run in enumerate(runs, 1):
            raw = str(read(run, "Text", "") or "")
            # The last run carries the paragraph's own break.
            text = run_text(raw.rstrip("\r\n") if position == len(runs) else raw)
            if not text:
                continue
            font = read(run, "Font")
            if read(font, "Superscript") == TRUE:
                text = f"<sup>{text}</sup>"
            elif read(font, "Subscript") == TRUE:
                text = f"<sub>{text}</sub>"
            style = run_css(font)
            parts.append(f'<span style="{attribute(style)}">{text}</span>' if style else text)
        content = "".join(parts)
        if not content.strip():
            continue
        form = read(paragraph, "ParagraphFormat")
        level = read(paragraph, "IndentLevel", 1)
        style = css(
            [
                ("margin-left", points((level - 1) * 18) if isinstance(level, int) and level > 1 else None),
                ("text-align", POWERPOINT_ALIGN.get(read(form, "Alignment"))),
            ]
        )
        html.append(
            f"<p{f' style={chr(34)}{attribute(style)}{chr(34)}' if style else ''}>{bullet_label(form)}{content}</p>"
        )
    return "".join(html)


def table_html(table):
    rows, columns = table.Rows.Count, table.Columns.Count
    if rows * columns > MAX_CELLS:
        raise UserError("PowerPoint 표가 100,000셀을 초과합니다.")
    # A table formatted with a header row keeps it as the header.
    header = bool(table.FirstRow)
    html = ["<table>"]
    for row in range(1, rows + 1):
        tag = "th" if header and row == 1 else "td"
        cells = []
        for column in range(1, columns + 1):
            shape = table.Cell(row, column).Shape
            fill = read(shape, "Fill")
            color = bgr_color(read(read(fill, "ForeColor"), "RGB")) if read(fill, "Visible") == TRUE else None
            content = text_html(shape.TextFrame.TextRange) if read(read(shape, "TextFrame"), "HasText", TRUE) else ""
            look = f' style="background-color:{color}"' if color else ""
            cells.append(f"<{tag}{look}>{content}</{tag}>")
        html.append("<tr>" + "".join(cells) + "</tr>")
    return "".join(html) + "</table>"


def smartart_html(smartart):
    """SmartArt nodes as nested lists by their levels."""
    nodes = smartart.AllNodes
    html, depth = [], 0
    for index in range(1, nodes.Count + 1):
        node = nodes.Item(index)
        level = read(node, "Level", 1)
        level = level if isinstance(level, int) and 1 <= level <= MAX_DEPTH else 1
        text = html_text(node.TextFrame2.TextRange.Text)
        if level > depth:
            html.append("<ul>" * (level - depth))
        elif level < depth:
            html.append("</li>" + "</ul></li>" * (depth - level))
        elif html:
            html.append("</li>")
        html.append(f"<li>{text}")
        depth = level
    if depth:
        html.append("</li>" + "</ul></li>" * (depth - 1) + "</ul>")
    return "".join(html)


def shape_html(shape, depth=0, pictured=False):
    """A shape's text, table, chart data or SmartArt outline. Pictures, media and embedded objects leave a
    marker, unless the slide's own picture (`pictured`) shows them."""
    if depth > MAX_DEPTH:
        raise UserError("PowerPoint 그룹 도형의 중첩이 너무 깊습니다.")
    kind = shape.Type
    if kind == 6:  # msoGroup
        items = shape.GroupItems
        return "".join(shape_html(items.Item(i), depth + 1, pictured) for i in range(1, items.Count + 1))
    if shape.HasTable:
        return f'<div class="shape">{table_html(shape.Table)}</div>'
    if read(shape, "HasChart") == TRUE:
        return f'<div class="shape">{chart_html(shape.Chart, one_line(read(shape, "Name", "")))}</div>'
    if shape.HasSmartArt:
        return f'<div class="shape">{smartart_html(shape.SmartArt)}</div>'
    contained = read(read(shape, "PlaceholderFormat"), "ContainedType") if kind == 14 else None
    label = one_line(read(shape, "AlternativeText", "")) or one_line(read(shape, "Name", ""))
    if kind in (11, 13) or contained == 13:  # msoLinkedPicture, msoPicture
        return "" if pictured else f'<p class="shape">{images.missing(f"[그림: {label}]" if label else "[그림]")}</p>'
    if kind == 16:  # msoMedia
        return (
            ""
            if pictured
            else f'<p class="shape">{images.missing(f"[동영상·소리: {label}]" if label else "[동영상·소리]")}</p>'
        )
    if kind in (7, 10):  # msoEmbeddedOLEObject, msoLinkedOLEObject
        return (
            ""
            if pictured
            else f'<p class="shape">{images.missing(f"[삽입된 개체: {label}]" if label else "[삽입된 개체]")}</p>'
        )
    if shape.HasTextFrame and shape.TextFrame.HasText:
        return f'<div class="shape">{text_html(shape.TextFrame.TextRange)}</div>'
    return ""


def slide_title(shape):
    """The text of a title placeholder, or None for any other shape."""
    if shape.Type != 14 or shape.PlaceholderFormat.Type not in TITLE_PLACEHOLDERS:  # msoPlaceholder
        return None
    return shape.TextFrame.TextRange.Text if shape.HasTextFrame and shape.TextFrame.HasText else ""


def slide_comments(slide):
    comments = read(slide, "Comments")
    entries = []
    for index in range(1, (read(comments, "Count", 0) or 0) + 1):
        comment = comments.Item(index)
        author = one_line(read(comment, "Author", ""))
        entries.append(f"<li>{html_text(author) + ': ' if author else ''}{html_text(read(comment, 'Text', ''))}</li>")
    return ('<div class="notes"><p class="caption">메모</p><ul>' + "".join(entries) + "</ul></div>") if entries else ""


def slide_links(slide):
    links = read(slide, "Hyperlinks")
    entries = []
    for index in range(1, (read(links, "Count", 0) or 0) + 1):
        item = links.Item(index)
        address = str(read(item, "Address", "") or "")
        text = one_line(read(item, "TextToDisplay", "")) or address
        if address and re.match(r"(https?|mailto):", address, re.I):
            entries.append(f'<li><a href="{attribute(address)}">{html_text(text)}</a></li>')
        elif address or read(item, "SubAddress"):
            entries.append(f"<li>{html_text(text or read(item, 'SubAddress', ''))}</li>")
    return ('<div class="notes"><p class="caption">링크</p><ul>' + "".join(entries) + "</ul></div>") if entries else ""


def slide_size(presentation):
    """((width, height) px a slide's picture is exported at, (width, height) px it is shown at), or None."""
    setup = read(presentation, "PageSetup")
    width, height = read(setup, "SlideWidth"), read(setup, "SlideHeight")
    if not all(
        isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value < 100000
        for value in (width, height)
    ):
        return None
    scale = SLIDE_SIDE / max(width, height)
    return (round(width * scale), round(height * scale)), (width * 4 / 3, height * 4 / 3)


def slide_picture(slide, index, size, exports):
    """A figure of the slide as PowerPoint draws it (Slide.Export), or ""."""
    if exports is None or size is None:
        return ""
    (width, height), shown = size
    found = exports.picture(lambda path: slide.Export(path, "PNG", width, height))
    if found is None:
        return ""
    kind, data, _ = found
    return f'<figure class="slide-picture">{images.img(kind, data, f"슬라이드 {index} 그림", *shown)}</figure>'


def powerpoint_html(presentation, tick=lambda: None, exports=None):
    """The slides of a presentation; `exports` (office_pictures.Exports) receives their pictures."""
    parts = []
    size = slide_size(presentation) if exports is not None else None
    slides = presentation.Slides
    for index in range(1, slides.Count + 1):
        tick()
        slide = slides.Item(index)
        figure = slide_picture(slide, index, size, exports)
        shapes = slide.Shapes
        title, body = "", []
        for shape_index in range(1, shapes.Count + 1):
            shape = shapes.Item(shape_index)
            text = slide_title(shape)
            if text is not None and not title:
                title = one_line(text)
            else:
                body.append(shape_html(shape, pictured=bool(figure)))
        hidden = read(read(slide, "SlideShowTransition"), "Hidden") == TRUE
        heading = f"슬라이드 {index} · {title}" if title else f"슬라이드 {index}"
        parts.append(
            f'<section class="slide{" hidden" if hidden else ""}">'
            f"<h2>{html_text(heading)}{' (숨긴 슬라이드)' if hidden else ''}</h2>{figure}{''.join(body)}"
        )
        notes = slide.NotesPage.Shapes
        for note_index in range(1, notes.Count + 1):
            shape = notes.Item(note_index)
            # Only the notes body, excluding slide thumbnails, date, header and footer.
            if shape.Type == 14 and shape.PlaceholderFormat.Type == 2:
                note = shape_html(shape)
                if note:
                    parts.append("<h3>발표자 노트</h3>" + note)
        parts.append(slide_comments(slide) + slide_links(slide) + "</section>")
    return (exports.notice() if exports is not None else "") + "".join(parts)


# --- Visio ----------------------------------------------------------------------------------------


def connector_ends(shape):
    """IDs of the shapes glued to a connector's begin and end; None for a free end."""
    begin = end = None
    connects = shape.Connects
    for index in range(1, connects.Count + 1):
        connect = connects.Item(index)
        if connect.FromPart in VISIO_BEGIN:
            begin = connect.ToSheet.ID
        elif connect.FromPart in VISIO_END:
            end = connect.ToSheet.ID
    return begin, end


def shape_data(shape):
    """Visible Shape Data rows as (label, value) pairs."""
    rows = []
    try:
        if shape.SectionExists(VISIO_DATA, 0):
            for row in range(shape.RowCount(VISIO_DATA)):
                if shape.CellsSRC(VISIO_DATA, row, VISIO_HIDDEN).ResultIU:
                    continue
                value = shape.CellsSRC(VISIO_DATA, row, VISIO_VALUE)
                text = one_line(value.ResultStr(""))
                if text:
                    rows.append(
                        (one_line(shape.CellsSRC(VISIO_DATA, row, VISIO_LABEL).ResultStr("")) or value.RowName, text)
                    )
    except Exception:
        # Shape Data only adds to a shape; one whose ShapeSheet cannot be read keeps its text and links.
        pass
    return rows


def page_picture(page, name, exports):
    """A figure of the page as Visio draws it, or "". Visio's picture in memory (Page.Picture) reaches only
    code inside Visio, as its IPictureDisp cannot be marshaled to this process, so Visio exports the page
    as PNG with the user's raster export settings (Page.Export, the filter chosen by the extension)."""
    if exports is None:
        return ""
    found = exports.picture(lambda path: page.Export(path))
    if found is None:
        return ""
    kind, data, size = found
    return f"<figure>{images.img(kind, data, f'{name} 그림' if name else '페이지 그림', *(size or ()))}</figure>"


def visio_page(page, number, tick, counted, exports):
    labels, links, data = {}, [], []

    def walk(shapes, depth):
        if depth > MAX_DEPTH:
            raise UserError("Visio 그룹 도형의 중첩이 너무 깊습니다.")
        items = []
        for position in range(1, shapes.Count + 1):
            counted[0] += 1
            if counted[0] > MAX_SHAPES:
                raise UserError("Visio 도형이 20,000개를 초과합니다. 파일을 나눠 주세요.")
            if counted[0] % 100 == 0:
                tick()
            shape = shapes.Item(position)
            kind = shape.Type
            if kind not in VISIO_SHAPES:
                continue
            text = plain(shape.Characters.Text)
            ident = shape.ID
            labels[ident] = one_line(text) or shape.Name
            if shape.OneD:
                ends = connector_ends(shape)
                if ends != (None, None):
                    links.append((ends, one_line(text)))
                    continue
            data.extend((ident, label, value) for label, value in shape_data(shape))
            children = walk(shape.Shapes, depth + 1) if kind == VISIO_GROUP else []
            if text:
                nested = f"<ul>{''.join(children)}</ul>" if children else ""
                items.append(f"<li>{html_text(text)}{nested}</li>")
            else:
                items.extend(children)
        return items

    def label(ident):
        return "(미연결)" if ident is None else labels.get(ident) or f"도형 {ident}"

    items = walk(page.Shapes, 0)
    name = one_line(page.Name)
    # A page of shapes without text still has its picture; a page without shapes is left out.
    picture = page_picture(page, name, exports) if labels else ""
    if not (items or links or data or picture):
        return ""
    heading = f"페이지 {number} · {name}" if name else f"페이지 {number}"
    html = [f'<section class="page"><h2>{html_text(heading)}{" (배경)" if page.Background else ""}</h2>', picture]
    if items:
        html.append(f"<h3>도형 텍스트</h3><ul>{''.join(items)}</ul>")
    if links:
        html.append("<h3>연결</h3><ul>")
        html.extend(
            f"<li>{html_text(f'{label(begin)} → {label(end)}' + (f': {text}' if text else ''))}</li>"
            for (begin, end), text in links
        )
        html.append("</ul>")
    if data:
        html.append('<h3>도형 데이터</h3><table class="grid"><tr><th>도형</th><th>항목</th><th>값</th></tr>')
        html.extend(
            f"<tr><td>{html_text(label(ident))}</td><td>{html_text(key)}</td><td>{html_text(value)}</td></tr>"
            for ident, key, value in data
        )
        html.append("</table>")
    return "".join(html) + "</section>"


def visio_html(document, tick=lambda: None, exports=None):
    """The pages of a drawing; `exports` (office_pictures.Exports) receives their pictures."""
    parts, counted = [], [0]
    pages = document.Pages
    for index in range(1, pages.Count + 1):
        tick()
        parts.append(visio_page(pages.Item(index), index, tick, counted, exports))
    return (exports.notice() if exports is not None else "") + "".join(parts)
