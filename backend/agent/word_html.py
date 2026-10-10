"""Word's in-memory Flat OPC XML as one HTML page, read by Python after Word has quit. No file I/O.

The main document part is read as Word lays it out on the page. Paragraph and character formatting is
resolved through the document defaults, the style hierarchy and direct formatting; lists carry the
numbers and bullets Word draws; tables keep their grid, merged cells, borders and shading. Pictures come
from the package's own media parts (TIFF and metafiles as PNG). Shapes, groups, drawing canvases, SmartArt
and the VML shapes of older documents are painted as SVG pictures (drawing.py, vml.py); the floating
drawings of a paragraph are painted together where Word placed them, and a text box without a drawn
outline keeps its text on the page. Charts become tables of their data and equations become MathML.
Links, bookmarks, footnotes, endnotes, comments, and the headers and footers the package carries are kept.

Content controls, smart tags and tracked insertions are looked into; tracked deletions, the old place of
moved text, hidden text and field codes are left out, and one branch of each mc:AlternateContent is
read so nothing appears twice: the Word 2010 shapes, groups and canvases that are painted, else the
fallback. The answers of legacy form fields (check boxes, drop-down lists) are written where they are.

Pictures go into the page once its text is known, within a budget like that of the pictures Office exports: the
first use of each picture before a picture shown again, and a marker where one no longer fits, which the top of the
page explains."""

import base64
import hashlib
import json
import posixpath
import re
from html import escape
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from . import drawing, images
from .drawing import Canvas, Matrix, Painter, Palette
from .html_document import MAX_HTML_BYTES, attribute, chart_table, css, font_family, hex_color, page, points
from .llm import UserError
from .office_pictures import FULL, MAX_PICTURE_BYTES
from .vml import (
    SHAPES as VML_SHAPES,
    Vml,
    box_css as vml_box_css,
    fraction as vml_fraction,
    length as vml_length,
    style_of,
)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
M = "{http://schemas.openxmlformats.org/officeDocument/2006/math}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
O = "{urn:schemas-microsoft-com:office:office}"  # noqa: E741 - named by its prefix (o:), as the others are
V = "{urn:schemas-microsoft-com:vml}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
PIC = "{http://schemas.openxmlformats.org/drawingml/2006/picture}"
C = "{http://schemas.openxmlformats.org/drawingml/2006/chart}"
DGM = "{http://schemas.openxmlformats.org/drawingml/2006/diagram}"
DSP = "{http://schemas.microsoft.com/office/drawing/2008/diagram}"
WPS = "{http://schemas.microsoft.com/office/word/2010/wordprocessingShape}"
PKG = "{http://schemas.microsoft.com/office/2006/xmlPackage}"
REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
WRAPPERS = {W + name for name in ("sdt", "sdtContent", "customXml", "smartTag", "ins", "moveTo")}
INLINE = WRAPPERS | {W + name for name in ("dir", "bdo")}
BLOCKS = {W + "p", W + "tbl", W + "altChunk"}
OFF = {"0", "false", "off", "none"}
EMPTY = "Word 문서에서 표시할 내용을 찾지 못했습니다."
EMU_PX, EMU_PT = 9525, 12700
# graphicData of the drawings painted as pictures: Word 2010's shapes, groups and drawing canvases. The
# mc:Choice branches that require them are read rather than their VML fallback.
SHAPE_DATA = {
    "http://schemas.microsoft.com/office/word/2010/wordprocessingShape",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup",
    "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas",
}
PAINTED = {"wps", "wpg", "wpc"}
# Where a floating drawing is placed from (wp:positionH/V relativeFrom, VML mso-position-*-relative).
HORIZONTAL_FRAMES = {
    "text": "column",
    "char": "column",
    "character": "column",
    "left-margin-area": "leftMargin",
    "right-margin-area": "rightMargin",
    "inner-margin-area": "insideMargin",
    "outer-margin-area": "outsideMargin",
}
VERTICAL_FRAMES = {
    "text": "paragraph",
    "top-margin-area": "topMargin",
    "bottom-margin-area": "bottomMargin",
    "inner-margin-area": "insideMargin",
    "outer-margin-area": "outsideMargin",
}
TOGGLES = ("b", "i", "caps", "smallCaps", "strike", "dstrike", "vanish")
FONT_SLOTS = (("ascii", "asciiTheme"), ("hAnsi", "hAnsiTheme"), ("eastAsia", "eastAsiaTheme"), ("cs", "cstheme"))
HIGHLIGHTS = {
    "yellow": "#FFFF00",
    "green": "#00FF00",
    "cyan": "#00FFFF",
    "magenta": "#FF00FF",
    "blue": "#0000FF",
    "red": "#FF0000",
    "darkBlue": "#000080",
    "darkCyan": "#008080",
    "darkGreen": "#008000",
    "darkMagenta": "#800080",
    "darkRed": "#800000",
    "darkYellow": "#808000",
    "darkGray": "#808080",
    "lightGray": "#C0C0C0",
    "black": "#000000",
    "white": "#FFFFFF",
}
ALIGN = {
    "left": "left",
    "start": "left",
    "center": "center",
    "right": "right",
    "end": "right",
    "both": "justify",
    "distribute": "justify",
    "lowKashida": "justify",
    "mediumKashida": "justify",
    "highKashida": "justify",
    "thaiDistribute": "justify",
}
UNDERLINES = {
    "double": "double",
    "dotted": "dotted",
    "dottedHeavy": "dotted",
    "dash": "dashed",
    "dashedHeavy": "dashed",
    "dashLong": "dashed",
    "dashLongHeavy": "dashed",
    "dotDash": "dashed",
    "dotDotDash": "dashed",
    "wave": "wavy",
    "wavyHeavy": "wavy",
    "wavyDouble": "wavy",
}
BORDERS = {
    "single": "solid",
    "thick": "solid",
    "double": "double",
    "triple": "double",
    "dotted": "dotted",
    "dashed": "dashed",
    "dashSmallGap": "dashed",
    "dotDash": "dashed",
    "dotDotDash": "dashed",
    "thinThickSmallGap": "double",
    "thickThinSmallGap": "double",
    "thinThickMediumGap": "double",
    "thickThinMediumGap": "double",
    "thinThickLargeGap": "double",
    "thickThinLargeGap": "double",
    "thinThickThinSmallGap": "double",
    "thinThickThinMediumGap": "double",
    "thinThickThinLargeGap": "double",
    "wave": "solid",
    "doubleWave": "double",
    "dashDotStroked": "dashed",
    "threeDEmboss": "ridge",
    "threeDEngrave": "groove",
    "outset": "outset",
    "inset": "inset",
}
# Symbol-font characters Word uses for bullets and inserted symbols, as Unicode. Others keep their font,
# which Windows draws.
SYMBOLS = {
    **{
        ("symbol", ord(latin)): greek
        for latin, greek in zip(
            "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "αβχδεφγηιϕκλμνοπθρστυϖωξψζΑΒΧΔΕΦΓΗΙϑΚΛΜΝΟΠΘΡΣΤΥςΩΞΨΖ",
            strict=True,
        )
    },
    **{
        ("symbol", code): text
        for code, text in (
            (0xB7, "•"),
            (0xB0, "°"),
            (0xB1, "±"),
            (0xB4, "×"),
            (0xB8, "÷"),
            (0xA3, "≤"),
            (0xB3, "≥"),
            (0xB9, "≠"),
            (0xBB, "≈"),
            (0xA5, "∞"),
            (0xB6, "∂"),
            (0xD6, "√"),
            (0xE5, "∑"),
            (0xF2, "∫"),
            (0xA2, "′"),
            (0xB2, "″"),
            (0xBC, "…"),
            (0xD7, "⋅"),
            (0xD8, "¬"),
            (0xAC, "←"),
            (0xAD, "↑"),
            (0xAE, "→"),
            (0xAF, "↓"),
            (0xAB, "↔"),
            (0xDB, "⇔"),
            (0xDC, "⇐"),
            (0xDE, "⇒"),
            (0xC5, "⊕"),
            (0xC4, "⊗"),
            (0xCE, "∈"),
            (0xC7, "∩"),
            (0xC8, "∪"),
        )
    },
    **{
        ("wingdings", code): text
        for code, text in (
            (0x4A, "☺"),
            (0x4C, "☹"),
            (0x6C, "●"),
            (0x6E, "■"),
            (0x6F, "□"),
            (0x71, "❑"),
            (0x75, "◆"),
            (0x76, "❖"),
            (0xA7, "▪"),
            (0xD8, "➢"),
            (0xE8, "➔"),
            (0xF0, "⇨"),
            (0xFB, "✗"),
            (0xFC, "✓"),
            (0xFD, "☒"),
            (0xFE, "☑"),
        )
    },
}
LOOK = {"firstRow": 0x20, "lastRow": 0x40, "firstColumn": 0x80, "lastColumn": 0x100, "noHBand": 0x200, "noVBand": 0x400}
CHART_KINDS = {
    "barChart": "막대형",
    "bar3DChart": "3차원 막대형",
    "lineChart": "꺾은선형",
    "line3DChart": "3차원 꺾은선형",
    "pieChart": "원형",
    "pie3DChart": "3차원 원형",
    "ofPieChart": "원형",
    "doughnutChart": "도넛형",
    "areaChart": "영역형",
    "area3DChart": "3차원 영역형",
    "scatterChart": "분산형",
    "bubbleChart": "거품형",
    "radarChart": "방사형",
    "stockChart": "주식형",
    "surfaceChart": "표면형",
    "surface3DChart": "표면형",
}
# Where a picture goes in the page, until the page's text is known (Reader.embedded).
PICTURE = re.compile(r'<img data-picture="(\d+)">')
# The page around its body and the notices at its top, which the pictures leave room for.
PAGE_SLACK = 64 * 1024
CROWDED = "문서의 글이 많아 HTML 문서(80MB)에 담기지 않는 나머지 그림은 넣지 않았습니다."
LONG_ALT = (
    f"도형 속 글이 길어 그림의 대체 텍스트(검색·AI가 읽는 글)에는 그림마다 앞 {drawing.MAX_TEXT:,}자만 넣었습니다."
)


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def branch(node):
    """The one branch of mc:AlternateContent that is read: a choice of shapes that are painted, else the
    fallback, else the first choice."""
    for choice in node.findall(MC + "Choice"):
        needs = set((choice.get("Requires") or "").split())
        if needs and needs <= PAINTED:
            return choice
    chosen = node.find(MC + "Fallback")
    return node.find(MC + "Choice") if chosen is None else chosen


def items(parent, tags):
    """The children with these tags, looking into content controls and similar wrappers."""
    for child in parent:
        if child.tag in tags:
            yield child
        elif child.tag in WRAPPERS:
            yield from items(child, tags)
        elif child.tag == MC + "AlternateContent":
            chosen = branch(child)
            if chosen is not None:
                yield from items(chosen, tags)


def value(node, path, default=""):
    item = None if node is None else node.find("/".join(W + part for part in path.split("/")))
    return default if item is None else item.get(W + "val", default)


def on(element):
    return element.get(W + "val", "true").lower() not in OFF


def attrs(element):
    return {local(name): text for name, text in element.attrib.items()}


def number(text, default=None):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def twips(text):
    """Points of a measure Word writes in twentieths of a point, or with a unit such as "12pt"."""
    match = re.fullmatch(r"\s*(-?[\d.]+)\s*(pt|in|cm|mm|pc|pi)?\s*", str(text or ""))
    if not match:
        return None
    size = number(match.group(1), 0)
    unit = match.group(2)
    return (
        size / 20
        if not unit
        else size * {"pt": 1, "in": 72, "cm": 72 / 2.54, "mm": 72 / 25.4, "pc": 12, "pi": 12}[unit]
    )


def count(text, default):
    """A column count Word wrote, kept within what a table can hold."""
    return min(int(text), 1000) if text.isdigit() else default


VISIBLE = re.compile(r'<(img|math|hr)\b|class="pagebreak"')


def visible(html):
    return bool(VISIBLE.search(html)) or bool(re.sub(r"<[^>]*>", "", html).replace("&nbsp;", " ").strip())


def safe_href(target):
    """A link the page may follow: http(s), mailto or a bookmark in the document."""
    target = (target or "").strip()
    if target.startswith("#"):
        return target if len(target) > 1 else None
    try:
        return target if urlsplit(target).scheme.lower() in ("http", "https", "mailto") else None
    except ValueError:
        return None


def link(content, target, tip=""):
    """`content` linked to `target`; a target the page cannot follow, such as a file share, stays its title."""
    href = safe_href(target)
    title = f' title="{attribute(tip)}"' if tip else ""
    if href:
        return f'<a href="{attribute(href)}"{title}>{content}</a>'
    return f'<span title="{attribute(tip or target)}">{content}</span>' if target or tip else content


def field_link(instruction):
    """(target, tip) of a HYPERLINK field, or None for any other field."""
    match = re.match(r"\s*HYPERLINK\b(.*)", instruction, re.I | re.S)
    if not match:
        return None
    rest = match.group(1)
    target = re.match(r'\s*"([^"]*)"', rest) or re.match(r'\s*([^\s\\"]+)', rest)
    anchor = re.search(r'\\l\s+"([^"]*)"', rest)
    tip = re.search(r'\\o\s+"([^"]*)"', rest)
    url = (target.group(1) if target else "") + (f"#{anchor.group(1)}" if anchor else "")
    return url, tip.group(1) if tip else ""


def symbol(font, code):
    """A symbol-font character as Unicode, or as the font's own glyph where no Unicode match is known."""
    low = code - 0xF000 if 0xF000 <= code <= 0xF0FF else code
    text = SYMBOLS.get(((font or "").strip().lower(), low))
    if text:
        return escape(text)
    if font and low <= 0xFF:
        return f'<span style="{attribute(css([("font-family", font_family([font]))]))}">{escape(chr(low))}</span>'
    return escape(chr(code)) if 0x20 <= code < 0x110000 and not 0xD800 <= code < 0xE000 else ""


# --- Numbers of lists -----------------------------------------------------------------------------


def roman(value):
    result = ""
    for size, letters in (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    ):
        while value >= size:
            result += letters
            value -= size
    return result


def sino_korean(value):
    digits = "영일이삼사오육칠팔구"
    if value <= 0 or value >= 10000:
        return str(value)
    result = ""
    for size, unit in ((1000, "천"), (100, "백"), (10, "십")):
        if value >= size:
            result += ("" if value // size == 1 else digits[value // size]) + unit
            value %= size
    return result + (digits[value] if value else "")


def hanja(value):
    digits = "〇一二三四五六七八九"
    if value <= 0 or value >= 10000:
        return str(value)
    result = ""
    for size, unit in ((1000, "千"), (100, "百"), (10, "十")):
        if value >= size:
            result += ("" if value // size == 1 else digits[value // size]) + unit
            value %= size
    return result + (digits[value] if value else "")


NATIVE_KOREAN = ["하나", "둘", "셋", "넷", "다섯", "여섯", "일곱", "여덟", "아홉", "열"]


def list_number(value, kind):
    """The number `value` written in a Word number format."""
    if kind in ("upperRoman", "lowerRoman") and value > 0:
        text = roman(value)
        return text if kind == "upperRoman" else text.lower()
    if kind in ("upperLetter", "lowerLetter") and value > 0:
        text = chr(65 + (value - 1) % 26) * ((value - 1) // 26 + 1)
        return text if kind == "upperLetter" else text.lower()
    if kind == "decimalZero":
        return f"{value:02d}"
    if kind == "ordinal":
        return f"{value}{'th' if 11 <= value % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(value % 10, 'th')}"
    if kind == "ganada" and value > 0:
        return "가나다라마바사아자차카타파하"[(value - 1) % 14]
    if kind == "chosung" and value > 0:
        return "ㄱㄴㄷㄹㅁㅂㅅㅇㅈㅊㅋㅌㅍㅎ"[(value - 1) % 14]
    if kind in ("decimalEnclosedCircle", "decimalEnclosedCircleChinese") and 1 <= value <= 20:
        return chr(0x2460 + value - 1)
    if kind == "decimalEnclosedParen" and 1 <= value <= 20:
        return chr(0x2474 + value - 1)
    if kind == "decimalEnclosedFullstop" and 1 <= value <= 20:
        return chr(0x2488 + value - 1)
    if kind in ("decimalFullWidth", "decimalFullWidth2"):
        return str(value).translate({48 + i: 0xFF10 + i for i in range(10)})
    if kind in ("koreanDigital", "koreanLegal"):
        return sino_korean(value)
    if kind == "koreanCounting":
        return NATIVE_KOREAN[value - 1] if 1 <= value <= 10 else sino_korean(value)
    if kind in (
        "koreanDigital2",
        "chineseCounting",
        "chineseCountingThousand",
        "chineseLegalSimplified",
        "ideographDigital",
        "japaneseCounting",
        "japaneseDigitalTenThousand",
        "taiwaneseCounting",
        "taiwaneseCountingThousand",
        "ideographLegalTraditional",
    ):
        return hanja(value)
    if kind == "ideographTraditional" and value > 0:
        return "甲乙丙丁戊己庚辛壬癸"[(value - 1) % 10]
    if kind == "ideographZodiac" and value > 0:
        return "子丑寅卯辰巳午未申酉戌亥"[(value - 1) % 12]
    if kind == "none":
        return ""
    return str(value)


# --- The package ----------------------------------------------------------------------------------


class Package:
    """The parts of a Flat OPC package: XML parts as elements, binary parts (pictures) as base64."""

    def __init__(self, root):
        self.parts = {part.get(PKG + "name"): part for part in root.findall(PKG + "part")}
        self.cache = {}

    def xml(self, name):
        part = self.parts.get(name)
        data = None if part is None else part.find(PKG + "xmlData")
        return None if data is None or not len(data) else data[0]

    def binary(self, name):
        part = self.parts.get(name)
        data = None if part is None else part.find(PKG + "binaryData")
        if data is None or not data.text:
            return None
        try:
            return base64.b64decode("".join(data.text.split()))
        except ValueError:
            return None

    def content_type(self, name):
        part = self.parts.get(name)
        return "" if part is None else (part.get(PKG + "contentType") or "").split(";")[0].strip().lower()

    def relations(self, name):
        """{id: (target part or URL, external, type)} of a part."""
        if name not in self.cache:
            folder, file = posixpath.split(name)
            rels = self.xml(f"{folder}/_rels/{file}.rels")
            found = {}
            for rel in [] if rels is None else rels.iter(REL + "Relationship"):
                target, external = rel.get("Target", ""), rel.get("TargetMode") == "External"
                if not external:
                    target = posixpath.normpath(target if target.startswith("/") else posixpath.join(folder, target))
                found[rel.get("Id")] = (target, external, rel.get("Type", ""))
            self.cache[name] = found
        return self.cache[name]

    def related(self, name, kind):
        """The target of the first relationship of `name` whose type ends with /kind."""
        return next(
            (
                target
                for target, external, rel in self.relations(name).values()
                if not external and rel.endswith("/" + kind)
            ),
            None,
        )


class Styles:
    def __init__(self, package):
        root = package.xml("/word/styles.xml")
        root = ET.Element("styles") if root is None else root
        self.styles = {style.get(W + "styleId"): style for style in root.iter(W + "style")}
        defaults = root.find(W + "docDefaults")
        self.run_default = None if defaults is None else defaults.find(f"{W}rPrDefault/{W}rPr")
        self.paragraph_default = None if defaults is None else defaults.find(f"{W}pPrDefault/{W}pPr")
        self.defaults = {}
        for ident, style in self.styles.items():
            if style.get(W + "default", "0").lower() in ("1", "true", "on"):
                self.defaults.setdefault(style.get(W + "type", "paragraph"), ident)
        self.chains = {}

    def chain(self, ident):
        """The style and the styles it is based on, the most basic first."""
        if ident not in self.chains:
            found, seen, current = [], set(), ident
            while current and current not in seen and len(found) < 16:  # styles can be based on each other in a loop
                style = self.styles.get(current)
                if style is None:
                    break
                seen.add(current)
                found.append(style)
                current = value(style, "basedOn")
            self.chains[ident] = found[::-1]
        return self.chains[ident]

    def name(self, ident):
        return value(self.styles.get(ident), "name")


class Theme:
    def __init__(self, package):
        root = package.xml(package.related("/word/document.xml", "theme") or "/word/theme/theme1.xml")
        self.root = root
        scheme = None if root is None else root.find(f"{A}themeElements/{A}fontScheme")
        self.fonts = {}
        for kind in ("major", "minor"):
            fonts = None if scheme is None else scheme.find(f"{A}{kind}Font")
            if fonts is None:
                continue

            def face(tag, fonts=fonts):
                return fonts.find(A + tag).get("typeface", "") if fonts.find(A + tag) is not None else ""

            # Korean documents name their East Asian font by script (Hang) rather than as ea.
            hangul = next(
                (font.get("typeface", "") for font in fonts.findall(A + "font") if font.get("script") == "Hang"), ""
            )
            self.fonts.update(
                {
                    kind + "Ascii": face("latin"),
                    kind + "HAnsi": face("latin"),
                    kind + "EastAsia": face("ea") or hangul,
                    kind + "Bidi": face("cs"),
                }
            )

    def font(self, name):
        return self.fonts.get(name, "")


def read_run(rpr, props):
    """Run properties of `rpr` over `props`: a later level overrides an earlier one."""
    if rpr is None:
        return props
    for child in rpr:
        name = local(child.tag)
        if name == "rFonts":
            for slot, theme in FONT_SLOTS:
                if child.get(W + theme):
                    props[slot] = ("theme", child.get(W + theme))
                elif child.get(W + slot):
                    props[slot] = ("font", child.get(W + slot))
        elif name in TOGGLES:
            props[name] = on(child)
        elif name == "u":
            props["u"] = child.get(W + "val", "single")
            props["u_color"] = hex_color(child.get(W + "color"))
        elif name in ("color", "sz", "highlight", "vertAlign", "spacing", "position"):
            props[name] = child.get(W + "val")
        elif name == "shd":
            props["shd"] = child.get(W + "fill")
    return props


def read_paragraph(ppr, props):
    """Paragraph properties of `ppr` over `props`."""
    if ppr is None:
        return props
    for child in ppr:
        name = local(child.tag)
        if name in ("jc", "outlineLvl"):
            props[name] = child.get(W + "val")
        elif name == "spacing":
            props["spacing"] = {**props.get("spacing", {}), **attrs(child)}
        elif name == "ind":
            new = {
                {"start": "left", "end": "right", "startChars": "leftChars", "endChars": "rightChars"}.get(
                    key, key
                ): text
                for key, text in attrs(child).items()
            }
            ind = dict(props.get("ind", {}))
            # A first-line indent replaces an inherited hanging one, and the other way round.
            if new.keys() & {"firstLine", "firstLineChars"}:
                ind.pop("hanging", None), ind.pop("hangingChars", None)
            if new.keys() & {"hanging", "hangingChars"}:
                ind.pop("firstLine", None), ind.pop("firstLineChars", None)
            props["ind"] = {**ind, **new}
        elif name == "shd":
            props["shd"] = child.get(W + "fill")
        elif name == "pBdr":
            props["pBdr"] = {**props.get("pBdr", {}), **{local(side.tag): attrs(side) for side in child}}
        elif name == "numPr":
            ident, level = value(child, "numId", None), value(child, "ilvl", None)
            old = props.get("numPr", (None, None))
            props["numPr"] = (old[0] if ident is None else ident, old[1] if level is None else level)
        elif name in ("bidi", "contextualSpacing"):
            props[name] = on(child)
    return props


def layered(props, extra):
    """Paragraph properties of a table style over `props`, both already read."""
    for key, item in extra.items():
        props[key] = {**props.get(key, {}), **item} if isinstance(item, dict) else item
    return props


def border(side):
    """The CSS border of a Word border element's attributes, or "none"."""
    kind = (side or {}).get("val", "none")
    if kind in ("nil", "none"):
        return "none"
    width = max(number(side.get("sz"), 4) / 8, 0.25)
    if kind == "thick":
        width = max(width, 1.5)
    style = BORDERS.get(kind, "solid")
    if style == "double":
        width = max(width * 3, 2.25)
    return f"{points(width)} {style} {hex_color(side.get('color')) or '#000000'}"


class Numbering:
    """The numbers and bullets Word draws before list paragraphs, counted in document order."""

    def __init__(self, package, styles):
        root = package.xml("/word/numbering.xml")
        root = ET.Element("numbering") if root is None else root
        self.styles = styles
        self.abstracts = {node.get(W + "abstractNumId"): node for node in root.iter(W + "abstractNum")}
        self.nums = {}
        for node in root.iter(W + "num"):
            overrides = {item.get(W + "ilvl"): item for item in node.findall(W + "lvlOverride")}
            self.nums[node.get(W + "numId")] = (value(node, "abstractNumId"), overrides)
        self.counters, self.restarted = {}, set()

    def abstract(self, ident):
        abstract_id = self.nums.get(ident, (None, {}))[0]
        node = self.abstracts.get(abstract_id)
        # A list defined by a numbering style: the style names the num that holds the definition.
        link = value(node, "numStyleLink") if node is not None else ""
        if link:
            linked = value(self.styles.styles.get(link), "pPr/numPr/numId")
            abstract_id = self.nums.get(linked, (abstract_id, {}))[0]
            node = self.abstracts.get(abstract_id, node)
        return abstract_id, node

    def level(self, ident, level):
        _, overrides = self.nums.get(ident, (None, {}))
        override = overrides.get(str(level))
        found = None if override is None else override.find(W + "lvl")
        if found is None:
            node = self.abstract(ident)[1]
            found = (
                None
                if node is None
                else next((item for item in node.findall(W + "lvl") if item.get(W + "ilvl") == str(level)), None)
            )
        return found

    def level_of_style(self, ident, style):
        """The level whose definition names the paragraph's style, for headings numbered by their style."""
        node = self.abstract(ident)[1]
        for item in [] if node is None else node.findall(W + "lvl"):
            if style and value(item, "pStyle") == style and (item.get(W + "ilvl") or "").isdigit():
                return int(item.get(W + "ilvl"))
        return None

    def start(self, ident, level):
        text = value(self.level(ident, level), "start", "0")
        return int(text) if text.lstrip("-").isdigit() else 0

    def next(self, ident, level):
        """(label, suffix, label run properties) of the next paragraph of list `ident` at `level`."""
        definition = self.level(ident, level)
        abstract_id, node = self.abstract(ident)
        if definition is None or node is None:
            return None
        counters = self.counters.setdefault(abstract_id, {})
        override = self.nums[ident][1].get(str(level))
        restart = value(override, "startOverride") if override is not None else ""
        if restart.lstrip("-").isdigit() and (ident, level) not in self.restarted:
            # "Restart at": a list instance that starts again, the first time it is used.
            self.restarted.add((ident, level))
            counters[level] = int(restart) - 1
        counters[level] = counters[level] + 1 if level in counters else self.start(ident, level)
        for deeper in range(level + 1, 9):
            limit = value(self.level(ident, deeper), "lvlRestart")
            if limit != "0" and (not limit.isdigit() or level <= int(limit) - 1):
                counters.pop(deeper, None)
        kind = value(definition, "numFmt", "decimal")
        text = value(definition, "lvlText", None)
        if text is None:
            text = "" if kind == "bullet" else f"%{level + 1}."
        legal = definition.find(W + "isLgl") is not None and on(definition.find(W + "isLgl"))

        def written(match):
            index = int(match.group(1)) - 1
            if index > level:
                return ""
            current = counters.get(index, self.start(ident, index))
            return list_number(current, "decimal" if legal else value(self.level(ident, index), "numFmt", "decimal"))

        label = text if kind == "bullet" else re.sub(r"%([1-9])", written, text)
        return label, value(definition, "suff", "tab"), definition.find(W + "rPr")


# --- The reader -----------------------------------------------------------------------------------


class Context:
    """Where inline content is rendered: the paragraph's style and the objects anchored in it. A paragraph
    that anchors shapes collects its floating drawings (`pieces`) to paint them together, and marks its
    inline pictures (`inline`), which join them when the paragraph shows nothing else."""

    __slots__ = ("style", "base", "floats", "props", "composing", "pieces", "inline")

    def __init__(self, style, base, props=None, composing=False):
        self.style, self.base, self.floats = style, base, []
        self.props, self.composing, self.pieces, self.inline = props or {}, composing, [], []


class Piece:
    """A floating drawing of a paragraph: its paint order, the frame its place is measured in ("paragraph"
    or "page"), how it is painted where Word placed it, and the HTML a text box without a drawn outline
    keeps instead. Such a text box that holds more than a label is `kept` on the page rather than painted
    with the drawings beside it."""

    __slots__ = ("z", "frame", "paint", "html", "kept")

    def __init__(self, z, frame, paint, html=None, kept=False):
        self.z, self.frame, self.paint, self.html, self.kept = z, frame, paint, html, kept


def wordy(content):
    """Whether a text box holds more than a label: a table, or more than 300 characters."""
    return content is not None and (
        content.find(f".//{W}tbl") is not None or len("".join(item.text or "" for item in content.iter(W + "t"))) > 300
    )


class Reader:
    def __init__(self, package):
        self.package = package
        self.styles = Styles(package)
        self.theme = Theme(package)
        self.numbering = Numbering(package, self.styles)
        self.part = "/word/document.xml"
        self.found = False
        self.fields = []
        self.notes = {"footnote": [], "endnote": [], "comment": []}
        # What each footnote and endnote is called: Word's number, which a note with a mark of its own (a custom mark,
        # such as *) does not take, or that mark.
        self.marks, self.numbered = {"footnote": {}, "endnote": {}}, {"footnote": 0, "endnote": 0}
        self.custom = None  # The note whose custom mark the run being read writes.
        self.linking = 0  # Inside a link: a mark or bookmark there has no link of its own, as links do not nest.
        # Pictures and their uses (use_picture(), embedded()); `direct` while the text inside a painted picture is read,
        # whose pictures go into that picture at once. A package's picture is converted once however often it is used.
        self.pictures, self.direct, self.converted = [], 0, {}
        self.notices = []  # What the top of the page says was left out.
        self.cache = {}
        # The paragraph properties of the table style around the cell being read, if any.
        self.table_paragraph = {}
        normal = self.styles.defaults.get("paragraph")
        self.root_paragraph = self.paragraph_base(normal)
        self.root_run = self.run_base(normal)
        fonts = {
            (kind, script): self.theme.font(kind + slot)
            for kind in ("minor", "major")
            for script, slot in (("latin", "Ascii"), ("ea", "EastAsia"))
        }
        self.palette = Palette(self.theme.root, fonts)
        self.painter = Painter(self.palette, self.picture_source, self.box_text, self.text_css())
        # VML shape types are written once, where Word first used them, and named by later shapes.
        types = {}
        for name in package.parts:
            root = package.xml(name)
            for item in [] if root is None else root.iter(V + "shapetype"):
                types.setdefault(item.get("id", ""), item)
        self.vml_painter = Vml(self.picture_source, self.box_text, types, self.text_css())
        # The page and its margins in px, which floating drawings are placed from.
        self.sheet = {"width": 794.0, "height": 1123.0, "left": 96.0, "right": 96.0, "top": 96.0, "bottom": 96.0}

    # Properties -------------------------------------------------------------------------------

    def paragraph_base(self, style):
        """Document defaults, then the table style of the cell (such as Table Grid's single spacing without
        space after), then the paragraph style and the styles it is based on."""
        key = ("p", style, json.dumps(self.table_paragraph, sort_keys=True))
        if key not in self.cache:
            props = layered(read_paragraph(self.styles.paragraph_default, {}), self.table_paragraph)
            for item in self.styles.chain(style):
                read_paragraph(item.find(W + "pPr"), props)
            self.cache[key] = props
        return dict(self.cache[key])

    def run_base(self, style, character=None):
        key = ("r", style, character)
        if key not in self.cache:
            props = read_run(self.styles.run_default, {})
            for item in self.styles.chain(style) + self.styles.chain(character):
                read_run(item.find(W + "rPr"), props)
            self.cache[key] = props
        return dict(self.cache[key])

    def paragraph_style(self, paragraph):
        return value(paragraph, "pPr/pStyle") or self.styles.defaults.get("paragraph")

    def paragraph_props(self, paragraph, style):
        direct = paragraph.find(W + "pPr")
        props = read_paragraph(direct, self.paragraph_base(style))
        ident, level = props.get("numPr", (None, None))
        if ident and ident != "0":
            if level is None:
                found = self.numbering.level_of_style(ident, style)
                level = str(found) if found is not None else "0"
            level = min(int(level), 8) if level.isdigit() else 0
            definition = self.numbering.level(ident, level)
            if definition is not None:
                # The list level's indents sit between the style and direct formatting.
                props = read_paragraph(direct, read_paragraph(definition.find(W + "pPr"), self.paragraph_base(style)))
            props["list"] = (ident, level)
        return props

    def heading(self, props, style):
        """1–6 for a heading, from the outline level or the heading style; 0 for any other paragraph."""
        outline = props.get("outlineLvl") or ""
        if outline.isdigit():
            return int(outline) + 1 if int(outline) < 6 else 0
        for name in (style or "", self.styles.name(style)):
            match = re.fullmatch(r"(?:heading|제목)\s*([1-6])", name, re.I)
            if match:
                return int(match[1])
        return 0

    def family(self, props):
        names = []
        for slot in ("ascii", "eastAsia"):
            kind, name = props.get(slot, (None, None))
            names.append(self.theme.font(name) if kind == "theme" else name)
        return font_family(names)

    def run_css(self, props, base):
        """The CSS of run properties that differ from those the run inherits (`base`)."""
        pairs = []
        family = self.family(props)
        if family and family != self.family(base):
            pairs.append(("font-family", family))
        if props.get("sz") != base.get("sz") and number(props.get("sz")):
            pairs.append(("font-size", points(number(props["sz"]) / 2)))
        for key, name, yes, no in (("b", "font-weight", "bold", "normal"), ("i", "font-style", "italic", "normal")):
            if bool(props.get(key)) != bool(base.get(key)):
                pairs.append((name, yes if props.get(key) else no))
        decoration = self.decoration(props)
        if decoration != self.decoration(base):
            pairs.append(("text-decoration", decoration or "none"))
        color, inherited = hex_color(props.get("color")), hex_color(base.get("color"))
        if color != inherited:
            pairs.append(("color", color or "#000000"))
        shade = HIGHLIGHTS.get(props.get("highlight")) or hex_color(props.get("shd"))
        if shade and shade != (HIGHLIGHTS.get(base.get("highlight")) or hex_color(base.get("shd"))):
            pairs.append(("background-color", shade))
        if props.get("caps") and not base.get("caps"):
            pairs.append(("text-transform", "uppercase"))
        if props.get("smallCaps") and not base.get("smallCaps"):
            pairs.append(("font-variant", "small-caps"))
        if props.get("spacing") != base.get("spacing") and twips(props.get("spacing")):
            pairs.append(("letter-spacing", points(twips(props["spacing"]))))
        if props.get("position") != base.get("position") and number(props.get("position")):
            pairs.append(("vertical-align", points(number(props["position"]) / 2)))
        return css(pairs)

    @staticmethod
    def decoration(props):
        lines = []
        kind = props.get("u")
        if kind and kind != "none":
            lines.append("underline")
        if props.get("strike") or props.get("dstrike"):
            lines.append("line-through")
        if not lines:
            return ""
        style = "double" if props.get("dstrike") else UNDERLINES.get(kind, "")
        color = props.get("u_color") if "underline" in lines else None
        return " ".join(lines + [part for part in (style, color) if part])

    def paragraph_css(self, props):
        pairs = []
        align = ALIGN.get(props.get("jc"))
        if align != ALIGN.get(self.root_paragraph.get("jc")):
            pairs.append(("text-align", align or "left"))
        spacing = props.get("spacing", {})
        for side, auto, lines in (
            ("before", "beforeAutospacing", "beforeLines"),
            ("after", "afterAutospacing", "afterLines"),
        ):
            if spacing.get(auto, "0").lower() in ("1", "true", "on"):
                size = 14  # Word's automatic spacing, as for paragraphs of a web page
            elif number(spacing.get(lines)):
                size = number(spacing[lines]) / 100 * 12
            else:
                size = twips(spacing.get(side)) or 0
            if size:
                pairs.append(("margin-top" if side == "before" else "margin-bottom", points(size)))
        height = self.line_height(spacing)
        if height != self.line_height(self.root_paragraph.get("spacing", {})):
            pairs.append(("line-height", height or "normal"))
        ind = props.get("ind", {})
        for side, chars, name in (("left", "leftChars", "margin-left"), ("right", "rightChars", "margin-right")):
            if number(ind.get(chars)):
                pairs.append((name, f"{number(ind[chars]) / 100:g}em"))
            elif twips(ind.get(side)):
                pairs.append((name, points(twips(ind[side]))))
        indent = self.hanging(props)
        if indent:
            pairs.append(("text-indent", f"-{indent}"))
        elif number(ind.get("firstLineChars")):
            pairs.append(("text-indent", f"{number(ind['firstLineChars']) / 100:g}em"))
        elif twips(ind.get("firstLine")):
            pairs.append(("text-indent", points(twips(ind["firstLine"]))))
        shade = hex_color(props.get("shd"))
        if shade:
            pairs.append(("background-color", shade))
        for side, edge in (
            ("top", "top"),
            ("bottom", "bottom"),
            ("left", "left"),
            ("right", "right"),
            ("start", "left"),
            ("end", "right"),
        ):
            if side in props.get("pBdr", {}) and border(props["pBdr"][side]) != "none":
                pairs.append((f"border-{edge}", border(props["pBdr"][side])))
                pairs.append((f"padding-{edge}", points(number(props["pBdr"][side].get("space"), 0) or 1)))
        if props.get("bidi"):
            pairs.append(("direction", "rtl"))
        return pairs

    @staticmethod
    def hanging(props):
        """The hanging indent as a CSS length, or ""."""
        ind = props.get("ind", {})
        if number(ind.get("hangingChars")):
            return f"{number(ind['hangingChars']) / 100:g}em"
        size = twips(ind.get("hanging"))
        return points(size) if size else ""

    @staticmethod
    def line_height(spacing):
        line, rule = number(spacing.get("line")), spacing.get("lineRule", "auto")
        if not line:
            return ""
        if rule in ("exact", "atLeast"):
            return points(line / 20)
        return "" if line == 240 else f"{round(line / 240 * 1.2, 3):g}"

    def root_style(self, body):
        pairs = []
        family = self.family(self.root_run)
        if family:
            pairs.append(("font-family", family))
        size = number(self.root_run.get("sz"))
        pairs.append(("font-size", points(size / 2 if size else 10)))
        if hex_color(self.root_run.get("color")):
            pairs.append(("color", hex_color(self.root_run["color"])))
        if self.root_run.get("b"):
            pairs.append(("font-weight", "bold"))
        if self.root_run.get("i"):
            pairs.append(("font-style", "italic"))
        if ALIGN.get(self.root_paragraph.get("jc")):
            pairs.append(("text-align", ALIGN[self.root_paragraph["jc"]]))
        if self.line_height(self.root_paragraph.get("spacing", {})):
            pairs.append(("line-height", self.line_height(self.root_paragraph["spacing"])))
        # The page's text width, so lines and tables wrap where they do in Word.
        section = body.find(W + "sectPr")
        width = (
            twips(section.find(W + "pgSz").get(W + "w"))
            if section is not None and section.find(W + "pgSz") is not None
            else None
        )
        margins = section.find(W + "pgMar") if section is not None else None
        if width and margins is not None:
            text = width - (twips(margins.get(W + "left")) or 0) - (twips(margins.get(W + "right")) or 0)
            if text > 72:
                pairs.append(("max-width", points(text)))
        return css(pairs)

    def text_css(self):
        """The CSS of the document's default text, for the text inside painted shapes."""
        size = number(self.root_run.get("sz"))
        return css(
            [
                ("font-family", self.family(self.root_run) or None),
                ("font-size", points(size / 2 if size else 10)),
                ("color", hex_color(self.root_run.get("color"))),
                ("line-height", self.line_height(self.root_paragraph.get("spacing", {})) or "1.2"),
            ]
        )

    def measure(self, body):
        """The page size and margins of the document's last section, in px."""
        section = body.find(W + "sectPr")
        size = None if section is None else section.find(W + "pgSz")
        margins = None if section is None else section.find(W + "pgMar")
        for name, element, attribute_ in (
            ("width", size, "w"),
            ("height", size, "h"),
            ("left", margins, "left"),
            ("right", margins, "right"),
            ("top", margins, "top"),
            ("bottom", margins, "bottom"),
        ):
            found = twips(None if element is None else element.get(W + attribute_))
            if found is not None:
                self.sheet[name] = abs(found) * 4 / 3

    # Blocks -----------------------------------------------------------------------------------

    def blocks(self, parent):
        html, nodes = [], list(items(parent, BLOCKS))
        for index, node in enumerate(nodes):
            if node.tag == W + "tbl":
                html.append(self.table(node))
            elif node.tag == W + "altChunk":
                html.append(f"<p>{images.missing('[외부 삽입 콘텐츠]')}</p>")
            else:
                neighbours = [
                    self.paragraph_style(other) if other is not None and other.tag == W + "p" else None
                    for other in (
                        nodes[index - 1] if index else None,
                        nodes[index + 1] if index + 1 < len(nodes) else None,
                    )
                ]
                html.append(self.paragraph(node, *neighbours))
        return "".join(html)

    def paragraph(self, node, before=None, after=None):
        """A paragraph; `before` and `after` are the styles of the paragraphs beside it."""
        style = self.paragraph_style(node)
        props = self.paragraph_props(node, style)
        if props.get("contextualSpacing"):
            # "Don't add space between paragraphs of the same style", as in Word's List Paragraph.
            spacing = dict(props.get("spacing", {}))
            for side, neighbour in (("before", before), ("after", after)):
                if neighbour == style:
                    spacing.update({side: "0", f"{side}Autospacing": "0", f"{side}Lines": "0"})
            props["spacing"] = spacing
        context = Context(style, self.run_base(style), props, self.composes(node))
        label = self.label(props, context) if "list" in props else ""
        content = self.inline(node, context)
        # A field's code ends within its paragraph (only results, such as a table of contents, go on);
        # one left open would hide the rest of the document.
        while self.fields and not self.fields[-1]["result"]:
            self.fields.pop()
        tail = "".join(context.floats)
        if context.pieces or context.inline:
            painted, content = self.compose(context, content)
            tail = painted + tail
        if not visible(content) and not label:
            if tail:
                return tail  # the paragraph only anchors objects
            content += "<br>"  # an empty paragraph keeps its line, as in Word
        level = self.heading(props, style)
        tag = f"h{level}" if level else "p"
        pairs = self.paragraph_css(props)
        run = self.run_css(context.base, self.root_run)
        style_attribute = css(pairs) + (";" if pairs and run else "") + run
        attribute_text = f' style="{attribute(style_attribute)}"' if style_attribute else ""
        return f"<{tag}{attribute_text}>{label}{content}</{tag}>{tail}"

    def label(self, props, context):
        ident, level = props["list"]
        found = self.numbering.next(ident, level)
        if not found or not found[0]:
            return ""
        text, suffix, rpr = found
        own = read_run(rpr, dict(context.base))
        face = next(
            (name for kind, name in (own.get(slot, (None, None)) for slot, _ in FONT_SLOTS) if kind == "font" and name),
            "",
        )
        if face.lower().startswith(("symbol", "wingdings")) or any(0xF000 <= ord(char) <= 0xF0FF for char in text):
            # Bullets of symbol fonts become Unicode; symbol() keeps the font only for unknown ones.
            rendered = "".join(symbol(face, ord(char)) for char in text)
            for slot, _ in FONT_SLOTS:
                own.pop(slot, None)
                if slot in context.base:
                    own[slot] = context.base[slot]
        else:
            rendered = escape(text)
        pairs = [part.split(":", 1) for part in self.run_css(own, context.base).split(";") if part]
        hanging = self.hanging(props)
        if suffix == "tab" and hanging:
            # The text starts at the indent, as at Word's tab stop after a hanging number. An inline block
            # inherits the paragraph's negative text-indent, so it gets its own.
            pairs += [("display", "inline-block"), ("min-width", hanging), ("text-indent", "0")]
            rendered += " "
        elif suffix == "tab":
            rendered += "\t"
        elif suffix == "space":
            rendered += " "
        return f'<span style="{attribute(css(pairs))}">{rendered}</span>' if pairs else rendered

    def table(self, node):
        props = node.find(W + "tblPr")
        style = value(node, "tblPr/tblStyle") or self.styles.defaults.get("table")
        table = {"borders": {}, "margins": {}, "shading": None, "conditions": {}}
        for item in self.styles.chain(style):
            self.read_table(item.find(W + "tblPr"), table)
            self.read_cell_style(
                item.find(W + "tcPr"),
                item.find(W + "rPr"),
                item.find(W + "pPr"),
                table["conditions"].setdefault("wholeTable", {}),
            )
            for condition in item.findall(W + "tblStylePr"):
                self.read_cell_style(
                    condition.find(W + "tcPr"),
                    condition.find(W + "rPr"),
                    condition.find(W + "pPr"),
                    table["conditions"].setdefault(condition.get(W + "type"), {}),
                )
                self.read_table(condition.find(W + "tblPr"), table["conditions"][condition.get(W + "type")])
        self.read_table(props, table)
        look = self.look(props)
        grid = [twips(column.get(W + "w")) or 0 for column in node.findall(f"{W}tblGrid/{W}gridCol")]
        rows = []
        for row in items(node, {W + "tr"}):
            column = count(value(row, "trPr/gridBefore"), 0)
            cells = []
            for item in items(row, {W + "tc"}):
                cell_props = item.find(W + "tcPr")
                span = count(value(cell_props, "gridSpan"), 1) or 1
                merge = cell_props.find(W + "vMerge") if cell_props is not None else None
                state = None if merge is None else ("restart" if merge.get(W + "val") == "restart" else "continue")
                horizontal = value(cell_props, "hMerge")
                if horizontal == "continue" and cells:
                    cells[-1]["span"] += span  # a cell merged into the one before it (older Word)
                    column += span
                    continue
                cells.append(
                    {"start": column, "span": span, "merge": state, "node": item, "props": cell_props, "rows": 1}
                )
                column += span
            rows.append((row, cells))
        columns = max(
            [len(grid)]
            + [cell["start"] + cell["span"] for _, cells in rows for cell in cells]
            + [count(value(row, "trPr/gridBefore"), 0) for row, _ in rows]
        )
        # Vertically merged cells: a restart spans the continuing cells below it, which are left out.
        for index, (_, cells) in enumerate(rows):
            for cell in cells:
                if cell["merge"] != "restart":
                    continue
                for _, below in rows[index + 1 :]:
                    match = next((other for other in below if other["start"] == cell["start"]), None)
                    if match is None or match["merge"] != "continue":
                        break
                    match["covered"] = True
                    cell["rows"] += 1
        # Rows repeated as the header on every page lead the table.
        header_rows = 0
        for row, _ in rows:
            repeat = row.find(f"{W}trPr/{W}tblHeader")
            if repeat is None or not on(repeat):
                break
            header_rows += 1
        look_css = self.table_css(table, props, grid)
        html = [f'<table style="{attribute(look_css)}">' if look_css else "<table>"]
        if grid:
            html.append(
                "<colgroup>" + "".join(f'<col style="width:{points(width)}">' for width in grid) + "</colgroup>"
            )
        for index, (row, cells) in enumerate(rows):
            if index == 0 and header_rows:
                html.append("<thead>")
            height = row.find(f"{W}trPr/{W}trHeight")
            size = twips(height.get(W + "val")) if height is not None and height.get(W + "hRule") != "auto" else None
            html.append(f'<tr style="height:{points(size)}">' if size else "<tr>")
            # The grid columns a row leaves empty before and after its cells (w:gridBefore, w:gridAfter) keep their
            # place, so each cell stays under the columns it spans.
            before = count(value(row, "trPr/gridBefore"), 0)
            after = min(
                count(value(row, "trPr/gridAfter"), 0),
                columns - max([before] + [cell["start"] + cell["span"] for cell in cells]),
            )
            html.append(empty_cells(before))
            for cell in cells:
                if cell.get("covered"):
                    continue
                html.append(self.cell(cell, index, len(rows), columns, table, look, header_rows))
            html.append(empty_cells(after) + "</tr>")
            if index + 1 == header_rows:
                html.append("</thead>")
        html.append("</table>")
        return "".join(html)

    @staticmethod
    def read_table(props, table):
        if props is None:
            return
        for child in props:
            name = local(child.tag)
            if name == "tblBorders":
                table["borders"] = {**table.get("borders", {}), **{local(side.tag): attrs(side) for side in child}}
            elif name == "tblCellMar":
                table["margins"] = {
                    **table.get("margins", {}),
                    **{
                        local(side.tag): twips(side.get(W + "w"))
                        for side in child
                        if side.get(W + "type", "dxa") == "dxa"
                    },
                }
            elif name == "shd":
                table["shading"] = hex_color(child.get(W + "fill"))
            elif name in ("tblW", "jc", "tblInd", "tblLayout"):
                table[name] = attrs(child)

    @staticmethod
    def read_cell_style(tcpr, rpr, ppr, target):
        if tcpr is not None:
            shade = tcpr.find(W + "shd")
            if shade is not None:
                target["shading"] = hex_color(shade.get(W + "fill"))
            borders = tcpr.find(W + "tcBorders")
            if borders is not None:
                target["borders"] = {**target.get("borders", {}), **{local(side.tag): attrs(side) for side in borders}}
        if rpr is not None:
            target["run"] = read_run(rpr, dict(target.get("run", {})))
        if ppr is not None:
            target["paragraph"] = read_paragraph(ppr, dict(target.get("paragraph", {})))

    @staticmethod
    def look(props):
        """Which parts of the table its style formats: firstRow, lastRow, firstColumn, … (w:tblLook)."""
        look = None if props is None else props.find(W + "tblLook")
        if look is None:
            return set()
        raw = look.get(W + "val") or ""
        bits = int(raw, 16) if re.fullmatch(r"[0-9A-Fa-f]{1,4}", raw) else 0
        found = set()
        for name, bit in LOOK.items():
            text = look.get(W + name)
            if (text.lower() in ("1", "true", "on")) if text is not None else bits & bit:
                found.add(name)
        return found

    @staticmethod
    def table_css(table, props, grid):
        pairs = []
        width = table.get("tblW", {})
        if width.get("type") == "pct" and width.get("w"):
            share = width["w"].rstrip("%")
            share = number(share, 0) / (1 if width["w"].endswith("%") else 50)
            if share:
                pairs.append(("width", f"{round(share, 2):g}%"))
        elif width.get("type") == "dxa" and twips(width.get("w")):
            pairs.append(("width", points(twips(width["w"]))))
        elif grid:
            pairs.append(("width", points(sum(grid))))
        align = table.get("jc", {}).get("val")
        if align == "center":
            pairs += [("margin-left", "auto"), ("margin-right", "auto")]
        elif align in ("right", "end"):
            pairs.append(("margin-left", "auto"))
        elif twips(table.get("tblInd", {}).get("w")):
            pairs.append(("margin-left", points(twips(table["tblInd"]["w"]))))
        if table.get("tblLayout", {}).get("type") == "fixed":
            pairs.append(("table-layout", "fixed"))
        if table.get("shading"):
            pairs.append(("background-color", table["shading"]))
        return css(pairs)

    def cell(self, cell, row, rows, columns, table, look, header_rows):
        """One <td>, with the table style's conditional formatting for its place in the table."""
        last_row = row + cell["rows"] - 1 == rows - 1
        conditions = ["wholeTable"]
        # Banding starts after the header row and the first column when the table style formats them.
        first_column = 1 if "firstColumn" in look else 0
        if "noVBand" not in look and cell["start"] >= first_column:
            conditions.append("band1Vert" if (cell["start"] - first_column) % 2 == 0 else "band2Vert")
        first_row = max(1, header_rows) if "firstRow" in look else 0
        if "noHBand" not in look and row >= first_row:
            conditions.append("band1Horz" if (row - first_row) % 2 == 0 else "band2Horz")
        if "firstColumn" in look and cell["start"] == 0:
            conditions.append("firstCol")
        if "lastColumn" in look and cell["start"] + cell["span"] == columns:
            conditions.append("lastCol")
        if "firstRow" in look and row < max(1, header_rows):
            conditions.append("firstRow")
        if "lastRow" in look and last_row:
            conditions.append("lastRow")
        applied = {"borders": {}, "run": {}, "paragraph": {}, "shading": None}
        for name in conditions:
            condition = table["conditions"].get(name, {})
            applied["borders"].update(condition.get("borders", {}))
            applied["run"].update(condition.get("run", {}))
            applied["paragraph"] = layered(applied["paragraph"], condition.get("paragraph", {}))
            if condition.get("shading"):
                applied["shading"] = condition["shading"]
        own = {"borders": {}, "shading": None}
        self.read_cell_style(cell["props"], None, None, own)
        pairs = []
        for side, outer, inner, first in (
            ("top", "top", "insideH", row == 0),
            ("bottom", "bottom", "insideH", last_row),
            ("left", "left", "insideV", cell["start"] == 0),
            ("right", "right", "insideV", cell["start"] + cell["span"] == columns),
        ):
            alias = {"left": "start", "right": "end"}.get(side)
            chosen = (
                own["borders"].get(side)
                or own["borders"].get(alias)
                or applied["borders"].get(side)
                or applied["borders"].get(alias)
            )
            if chosen is None:
                borders = table["borders"]
                chosen = (
                    borders.get(outer) or borders.get({"left": "start", "right": "end"}.get(outer))
                    if first
                    else borders.get(inner)
                )
            if chosen is not None:
                pairs.append((f"border-{side}", border(chosen)))
        shade = own["shading"] or applied["shading"]
        if shade:
            pairs.append(("background-color", shade))
        margins = dict(table["margins"])
        own_margins = cell["props"].find(W + "tcMar") if cell["props"] is not None else None
        for side in [] if own_margins is None else own_margins:
            margins[local(side.tag)] = twips(side.get(W + "w"))
        for side, alias, default in (
            ("top", None, 0),
            ("bottom", None, 0),
            ("left", "start", 5.4),
            ("right", "end", 5.4),
        ):
            size = margins.get(side, margins.get(alias, default))
            pairs.append((f"padding-{side}", points(size or 0)))
        align = value(cell["props"], "vAlign")
        if align in ("center", "bottom"):
            pairs.append(("vertical-align", "middle" if align == "center" else "bottom"))
        width = cell["props"].find(W + "tcW") if cell["props"] is not None else None
        if width is not None and width.get(W + "type", "dxa") == "dxa" and twips(width.get(W + "w")):
            pairs.append(("width", points(twips(width.get(W + "w")))))
        direction = value(cell["props"], "textDirection")
        if direction in ("btLr", "tbRl", "tbRlV", "tbLrV"):
            pairs.append(("writing-mode", "vertical-rl"))
            if direction == "btLr":
                pairs.append(("transform", "rotate(180deg)"))
        if cell["props"] is not None and cell["props"].find(W + "noWrap") is not None:
            pairs.append(("white-space", "nowrap"))
        run = self.run_css({**self.root_run, **applied["run"]}, self.root_run) if applied["run"] else ""
        spans = (f' colspan="{cell["span"]}"' if cell["span"] > 1 else "") + (
            f' rowspan="{cell["rows"]}"' if cell["rows"] > 1 else ""
        )
        style = css(pairs) + (";" + run if run else "")
        # The table style's paragraph properties (its spacing, alignment) apply to the cell's paragraphs.
        outer, self.table_paragraph = self.table_paragraph, applied["paragraph"]
        try:
            content = self.blocks(cell["node"]) or "<p><br></p>"
        finally:
            self.table_paragraph = outer
        return f'<td{spans} style="{attribute(style)}">{content}</td>'

    # Inline content ---------------------------------------------------------------------------

    def inline(self, node, context):
        return "".join(self.part_of(child, context) for child in node)

    def linked(self, node, context):
        """The content of a link, whose marks of notes and comments have no link of their own."""
        self.linking += 1
        try:
            return self.inline(node, context)
        finally:
            self.linking -= 1

    def in_link(self):
        return bool(self.linking) or any(field["link"] for field in self.fields)

    def part_of(self, node, context):
        """What one child of a paragraph or a hyperlink shows."""
        tag = node.tag
        if tag == W + "r":
            return self.run(node, context)
        if tag in INLINE:
            return self.inline(node, context)
        if tag == W + "fldSimple":
            found = field_link(node.get(W + "instr", ""))
            content = self.linked(node, context) if found else self.inline(node, context)
            return link(content, *found) if found and content else content
        if tag == W + "hyperlink":
            content = self.linked(node, context)
            if not content:
                return ""
            target = (
                self.package.relations(self.part).get(node.get(R + "id"), ("", True, ""))[0]
                if node.get(R + "id")
                else ""
            )
            if node.get(W + "anchor"):
                target += "#" + node.get(W + "anchor")
            return link(content, target, node.get(W + "tooltip", ""))
        if tag == W + "bookmarkStart":
            # A place links lead to, not a link: inside a link's text an <a> would end that link.
            name = node.get(W + "name", "")
            return f'<span id="{attribute(name)}"></span>' if name and name != "_GoBack" else ""
        if tag == MC + "AlternateContent":
            chosen = branch(node)
            return "" if chosen is None else self.inline(chosen, context)
        if tag in (M + "oMath", M + "oMathPara"):
            self.found = True
            return math(node)
        # Properties, comments ranges, tracked deletions and the old place of moved text.
        return ""

    def run(self, node, context):
        rpr = node.find(W + "rPr")
        props = read_run(rpr, self.run_base(context.style, value(node, "rPr/rStyle") or None))
        if props.get("vanish"):
            return ""
        outer, self.custom = self.custom, None
        try:
            text = "".join(self.content(child, context) for child in node)
            custom = self.custom
        finally:
            self.custom = outer
        if custom is not None:
            # The mark of a note that has one of its own is the text of its run, such as *.
            kind, index, ident = custom
            self.marks[kind][ident] = "".join(item.text or "" for item in node.iter(W + "t")).strip()
        if not visible(text):
            return text
        align = props.get("vertAlign")
        if align in ("superscript", "subscript"):
            tag = "sup" if align == "superscript" else "sub"
            text = f"<{tag}>{text}</{tag}>"
        style = self.run_css(props, context.base)
        if style:
            text = f'<span style="{attribute(style)}">{text}</span>'
        target = next((field["link"] for field in reversed(self.fields) if field["link"]), None)
        if custom is not None and not target and not self.linking:
            return f'<a href="#{kind}-{index}" id="{kind}-ref-{index}">{text}</a>'
        return link(text, *target) if target else text

    def showing(self):
        """Whether text is shown: not inside the code of a field."""
        return all(field["result"] for field in self.fields)

    def form_field(self, begin):
        """The answer of a legacy form field, which Word draws from its w:ffData rather than a result of its own: ☒ or ☐
        for a check box (FORMCHECKBOX), the chosen entry of a drop-down list (FORMDROPDOWN); "" for any other field. A
        text field (FORMTEXT) shows its result, as other fields do."""
        data = begin.find(W + "ffData")
        box = None if data is None else data.find(W + "checkBox")
        if box is not None:
            state = box.find(W + "checked")
            state = box.find(W + "default") if state is None else state
            self.found = True
            return "☒" if state is not None and on(state) else "☐"
        choices = None if data is None else data.find(W + "ddList")
        if choices is None:
            return ""
        entries = [item.get(W + "val", "") for item in choices.findall(W + "listEntry")]
        chosen = value(choices, "result") or value(choices, "default") or "0"
        index = int(chosen) if chosen.isdigit() else 0
        answer = entries[index] if index < len(entries) else ""
        self.found = self.found or bool(answer.strip())
        return escape(answer)

    def content(self, node, context):
        """What one child of a run shows."""
        tag = node.tag
        if tag == W + "fldChar":
            kind = node.get(W + "fldCharType")
            if kind == "begin":
                answer = self.form_field(node) if self.showing() else ""
                self.fields.append({"code": [], "result": False, "link": None, "answered": bool(answer)})
                return answer
            elif kind == "separate" and self.fields:
                # A form field whose answer is shown already keeps any result Word wrote from showing it twice.
                field = self.fields[-1]
                field.update(result=not field["answered"], link=field_link("".join(field["code"])))
            elif kind == "end" and self.fields:
                self.fields.pop()
            return ""
        if tag == W + "instrText":
            if self.fields and not self.fields[-1]["result"]:
                self.fields[-1]["code"].append(node.text or "")
            return ""
        if not self.showing():
            return ""
        if tag == W + "t":
            self.found = self.found or bool((node.text or "").strip())
            return escape(node.text or "")
        if tag == W + "br":
            return '<span class="pagebreak"></span>' if node.get(W + "type") == "page" else "<br>"
        if tag == W + "cr":
            return "<br>"
        if tag in (W + "tab", W + "ptab"):
            return "\t"
        if tag == W + "noBreakHyphen":
            return "\u2011"
        if tag == W + "sym":
            code = (
                int(node.get(W + "char", "0"), 16) if re.fullmatch(r"[0-9A-Fa-f]{1,4}", node.get(W + "char", "")) else 0
            )
            return symbol(node.get(W + "font", ""), code) if code else ""
        if tag == W + "ruby":
            base, text = node.find(W + "rubyBase"), node.find(W + "rt")
            base_html = "" if base is None else self.inline(base, context)
            return f"<ruby>{base_html}<rt>{self.inline(text, context)}</rt></ruby>" if text is not None else base_html
        if tag == W + "drawing":
            return self.drawing(node, context)
        if tag in (W + "pict", W + "object"):
            return self.vml(node, context)
        if tag in (W + "footnoteReference", W + "endnoteReference"):
            return self.reference("footnote" if tag == W + "footnoteReference" else "endnote", node)
        if tag == W + "commentReference":
            return self.reference("comment", node)
        if tag == MC + "AlternateContent":
            chosen = branch(node)
            return "" if chosen is None else "".join(self.content(child, context) for child in chosen)
        # Run properties, deleted text, note marks, soft hyphens and rendered page breaks.
        return ""

    def reference(self, kind, node):
        ident = node.get(W + "id", "")
        notes = self.notes[kind]
        if ident not in notes:
            notes.append(ident)
        index = notes.index(ident) + 1
        if kind == "comment":
            mark = f"[메모 {index}]"
            return (
                f'<sup class="comment">{mark}</sup>'
                if self.in_link()
                else f'<sup class="comment"><a href="#comment-{index}">{mark}</a></sup>'
            )
        marks = self.marks[kind]
        if node.get(W + "customMarkFollows", "0").lower() in ("1", "true", "on"):
            # The mark is the text that follows in the run, such as *: run() links it to the note.
            marks.setdefault(ident, "")
            self.custom = (kind, index, ident)
            return ""
        if ident not in marks:
            # Word numbers the notes without a mark of their own.
            self.numbered[kind] += 1
            marks[ident] = self.numbered[kind]
        number = marks[ident]
        mark = (roman(number).lower() if kind == "endnote" else str(number)) if isinstance(number, int) else number
        if self.in_link():
            return f'<sup><span id="{kind}-ref-{index}">{escape(mark)}</span></sup>'
        return f'<sup><a href="#{kind}-{index}" id="{kind}-ref-{index}">{escape(mark)}</a></sup>'

    # Objects ----------------------------------------------------------------------------------

    def drawing(self, node, context):
        html = []
        for anchor in node:
            kind = local(anchor.tag)
            if kind not in ("inline", "anchor"):
                continue
            extent = anchor.find(WP + "extent")
            size = (number(extent.get("cx"), 0), number(extent.get("cy"), 0)) if extent is not None else (0, 0)
            props = anchor.find(WP + "docPr")
            if props is not None and props.get("hidden", "0").lower() in ("1", "true"):
                continue
            label = " ".join(((props.get("descr") or props.get("title") or "") if props is not None else "").split())
            name = props.get("name", "") if props is not None else ""
            data = anchor.find(f"{A}graphic/{A}graphicData")
            if data is None:
                continue
            w, h = size[0] / EMU_PX, size[1] / EMU_PX
            element = next((item for item in data if local(item.tag) in ("wsp", "wgp", "wpc", "pic")), None)
            floating = kind == "anchor" and context.composing
            if element is not None and (data.get("uri") in SHAPE_DATA or (floating and local(element.tag) == "pic")):
                try:
                    if floating:
                        context.pieces.append(self.anchored(anchor, element, w, h, label))
                    else:
                        html.append(self.shape_picture(element, w, h, label, context))
                except Exception:
                    # A drawing this reader does not understand must not cost the document.
                    html.append(images.missing("[그리지 못한 도형]"))
                continue
            found = self.graphic(data, context, size, label, name)
            if context.composing and element is not None and local(element.tag) == "pic" and found.startswith("<img"):
                # A picture in the line may join the paragraph's floating drawings: see compose().
                token = f'<img data-inline="{len(context.inline)}">'
                context.inline.append((token, found, element, w, h))
                found = token
            html.append(found)
        return "".join(html)

    def graphic(self, data, context, size, label, name):
        """Pictures inline; text boxes, charts and SmartArt after the paragraph."""
        html, floats = [], len(context.floats)

        def walk(element, own_size):
            for child in element:
                tag = child.tag
                if tag == W + "txbxContent":
                    context.floats.append(f'<div class="textbox">{self.blocks(child)}</div>')
                elif tag in (PIC + "pic", A + "blipFill"):
                    extent = child.find(f"{PIC}spPr/{A}xfrm/{A}ext")
                    found = (
                        (number(extent.get("cx"), 0), number(extent.get("cy"), 0)) if extent is not None else own_size
                    )
                    html.append(self.picture(child, found, label or name))
                elif tag == C + "chart":
                    context.floats.append(self.chart(child.get(R + "id"), label or name))
                elif tag == DGM + "relIds":
                    picture = self.smartart_picture(child, own_size[0] / EMU_PX, own_size[1] / EMU_PX, label)
                    context.floats.append(
                        f"<figure>{picture}</figure>" if picture else self.smartart(child.get(R + "dm"), label or name)
                    )
                elif tag == MC + "AlternateContent":
                    chosen = branch(child)
                    if chosen is not None:
                        walk(chosen, own_size)
                else:
                    walk(child, own_size)

        walk(data, size)
        if (
            not html
            and len(context.floats) == floats
            and data.get("uri", "").endswith(("/picture", "/chart", "/diagram"))
        ):
            html.append(images.missing(f"[개체: {label or name or '그림'}]"))
        return "".join(html)

    def picture(self, element, size, label):
        blip = element.find(f".//{A}blip")
        if blip is None:
            return images.missing(f"[그림: {label}]" if label else "[그림]")
        width, height = (size[0] / EMU_PX, size[1] / EMU_PX) if size[0] and size[1] else (None, None)
        # The part of the picture Word shows of it, as painted pictures are cropped (drawing.Painter.image).
        crop = element.find(f".//{A}srcRect")
        sides = [0.0] * 4 if crop is None else [number(crop.get(side), 0) / 100000 for side in ("t", "r", "b", "l")]
        return self.image(blip.get(R + "embed") or blip.get(R + "link"), label, width, height, images.crop_css(*sides))

    def fetch(self, ident):
        """(picture, reason, detail): the package's picture `ident` of the current part as (content type,
        bytes, size) the page can show, or why there is none: "linked" (detail: its address), "missing"
        or "format" (detail: its format). Each picture of the package is converted once."""
        target, external, _ = self.package.relations(self.part).get(ident, ("", False, ""))
        if external:
            return None, "linked", target
        if target not in self.converted:
            self.converted[target] = self.convert(target)
        found = self.converted[target]
        self.found = self.found or found[0] is not None
        return found

    def convert(self, target):
        data = self.package.binary(target) if target else None
        if not data:
            return None, "missing", ""
        try:
            # Word shows the first page of a TIFF.
            pictures = images.for_web(data, self.package.content_type(target), pages=1)
        except Exception:
            return None, "format", images.kind(images.sniff(data) or self.package.content_type(target))
        return pictures[0], "", ""

    def image(self, ident, label, width=None, height=None, style=""):
        """An <img> of the package's picture `ident`, or a marker saying why there is none."""
        found, reason, detail = self.fetch(ident)
        if reason == "linked":
            return images.missing(f"[연결된 그림: {label or detail}]")
        if reason == "missing":
            return images.missing(f"[그림: {label}]" if label else "[그림]")
        if reason == "format":
            return images.missing(f"[그림 ({detail}, 표시할 수 없는 형식){': ' + label if label else ''}]")
        kind, data, own = found
        if not (width and height) and own:
            width, height = own
        key = ("part", self.package.relations(self.part).get(ident, ("",))[0])
        return self.use_picture(key, kind, data, label or "그림", width, height, style)

    def use_picture(self, key, kind, data, alt, width=None, height=None, style=""):
        """Where a picture goes in the page: a token embedded() puts the picture in for, or the <img> itself inside a
        painted picture. `key` tells a picture shown again."""
        if self.direct:
            return images.img(kind, data, alt, width, height, style)
        self.pictures.append((key, kind, data, alt, width, height, style))
        return f'<img data-picture="{len(self.pictures) - 1}">'

    def svg_picture(self, canvas, label):
        """The token of a painted picture, or "" when nothing was painted."""
        found = canvas.svg()
        if found is None:
            return ""
        svg, width, height = found
        alt = canvas.alt(label)
        if canvas.cut and LONG_ALT not in self.notices:
            self.notices.append(LONG_ALT)
        data = svg.encode("utf-8")
        return self.use_picture(("svg", hashlib.sha256(data).hexdigest()), "image/svg+xml", data, alt, width, height)

    def embedded(self, html):
        """`html` with its pictures, while they fit: in the budget of the pictures of a page (MAX_PICTURE_BYTES) and in
        what an HTML page holds beside its text (MAX_HTML_BYTES). The first use of each picture goes before the uses of
        a picture shown again, as one more copy of a picture shows least; one that does not fit is a marker."""
        uses, seen, again = [], set(), []
        for match in PICTURE.finditer(html):
            index = int(match.group(1))
            (again if self.pictures[index][0] in seen else uses).append(index)
            seen.add(self.pictures[index][0])
        room = MAX_HTML_BYTES - len(html.encode("utf-8")) - PAGE_SLACK
        used = size = 0
        kept, reasons = set(), []
        for index in uses + again:
            _, kind, data, alt, *_ = self.pictures[index]
            cost = 4 * ((len(data) + 2) // 3) + 3 * len(alt.encode("utf-8")) + 200  # base64, the escaped alt and tag
            if used + len(data) > MAX_PICTURE_BYTES:
                reasons.append(FULL)
            elif size + cost > room:
                reasons.append(CROWDED)
            else:
                kept.add(index)
                used, size = used + len(data), size + cost
        if reasons:
            self.notices.insert(0, "그림 일부를 넣지 못했습니다: " + " ".join(dict.fromkeys(reasons)))

        def picture(match):
            index = int(match.group(1))
            _, kind, data, alt, width, height, style = self.pictures[index]
            if index not in kept:
                return images.missing(f"[그림: {alt}]")
            return images.img(kind, data, alt, width, height, style)

        return PICTURE.sub(picture, html)

    def picture_source(self, ident):
        """A picture for the painters: (content type, bytes, size), or the text of a marker."""
        found, reason, detail = self.fetch(ident)
        if found is not None:
            return found
        return {"linked": f"[연결된 그림: {detail}]", "format": f"[그림 ({detail}, 표시할 수 없는 형식)]"}.get(
            reason, "[그림]"
        )

    def box_text(self, content):
        """(XHTML, plain text) of a text box's content (w:txbxContent), for the text inside a painted shape: its
        pictures go into that picture."""
        self.direct += 1
        try:
            html = self.blocks(content)
        finally:
            self.direct -= 1
        plain = " ".join(
            "".join(item.text or "" for item in paragraph.iter(W + "t")) for paragraph in content.iter(W + "p")
        )
        return drawing.xhtml(html), plain

    # Painted drawings -------------------------------------------------------------------------

    def composes(self, paragraph):
        """Whether a paragraph anchors shapes: its floating drawings are then painted together, where Word
        placed them, rather than one by one."""
        for item in paragraph.iter():
            if item.tag == WP + "anchor":
                data = item.find(f"{A}graphic/{A}graphicData")
                if data is not None and data.get("uri") in SHAPE_DATA:
                    return True
            elif (
                item.tag in VML_SHAPES
                and style_of(item.get("style")).get("position") == "absolute"
                and (item.find(V + "imagedata") is None or item.find(V + "textbox") is not None)
            ):
                return True
        return False

    def place(self, horizontal, x_offset, x_align, vertical, y_offset, y_align, w, h):
        """(x, y, frame) of a floating drawing w by h px: x from the text column's left edge; y from the
        top of its paragraph (frame "paragraph") or of the page (frame "page")."""
        sheet = self.sheet
        column = sheet["width"] - sheet["left"] - sheet["right"]
        horizontal = HORIZONTAL_FRAMES.get(horizontal, horizontal) or "column"
        start, span = {
            "page": (-sheet["left"], sheet["width"]),
            "leftMargin": (-sheet["left"], sheet["left"]),
            "insideMargin": (-sheet["left"], sheet["left"]),
            "rightMargin": (column, sheet["right"]),
            "outsideMargin": (column, sheet["right"]),
        }.get(horizontal, (0.0, column))
        aligned = {"left": 0.0, "inside": 0.0, "center": (span - w) / 2, "right": span - w, "outside": span - w}
        x = start + (aligned.get(x_align, 0.0) if x_align else (x_offset or 0.0))
        vertical = VERTICAL_FRAMES.get(vertical, vertical) or "paragraph"
        if vertical in ("paragraph", "line"):
            return x, y_offset or 0.0, "paragraph"
        height = sheet["height"]
        start, span = {
            "page": (0.0, height),
            "topMargin": (0.0, sheet["top"]),
            "insideMargin": (0.0, sheet["top"]),
            "bottomMargin": (height - sheet["bottom"], sheet["bottom"]),
            "outsideMargin": (height - sheet["bottom"], sheet["bottom"]),
        }.get(vertical, (sheet["top"], height - sheet["top"] - sheet["bottom"]))
        aligned = {"top": 0.0, "inside": 0.0, "center": (span - h) / 2, "bottom": span - h, "outside": span - h}
        return x, start + (aligned.get(y_align, 0.0) if y_align else (y_offset or 0.0)), "page"

    def anchored(self, anchor, element, w, h, label):
        """The Piece of a floating drawing (wp:anchor), placed as Word places it."""
        spot = anchor.find(WP + "simplePos")
        if anchor.get("simplePos", "0").lower() in ("1", "true") and spot is not None:
            x, y, frame = (
                number(spot.get("x"), 0) / EMU_PX - self.sheet["left"],
                number(spot.get("y"), 0) / EMU_PX,
                "page",
            )
        else:

            def position(name):
                found = anchor.find(WP + name)
                offset = None if found is None else found.find(WP + "posOffset")
                align = None if found is None else found.find(WP + "align")
                return (
                    None if found is None else found.get("relativeFrom"),
                    None if offset is None else number(offset.text, 0) / EMU_PX,
                    None if align is None else (align.text or "").strip() or None,
                )

            x, y, frame = self.place(*position("positionH"), *position("positionV"), w, h)
        # Paint order: behind the text first, then by Word's own order.
        z = number(anchor.get("relativeHeight"), 0) - (
            2**32 if anchor.get("behindDoc", "0").lower() in ("1", "true") else 0
        )

        def paint(canvas):
            if label:
                canvas.labels.append(label)
            self.painter.top(canvas, element, x, y, w, h)

        box = self.plain_box(element, w, h)
        return Piece(z, frame, paint, box, box is not None and wordy(element.find(f"{WPS}txbx/{W}txbxContent")))

    def plain_box(self, element, w, h):
        """For a text box the page draws itself, what makes its HTML; None for a drawing to paint."""
        content = element.find(f"{WPS}txbx/{W}txbxContent") if local(element.tag) == "wsp" else None
        look = None if content is None else drawing.box_css(element, self.palette, w, h)
        if look is None:
            return None
        return lambda: f'<div class="textbox" style="{attribute(look)}">{self.blocks(content)}</div>'

    def shape_picture(self, element, w, h, label, context):
        """A drawing in the line of text: its picture, or a text box the page draws, after the paragraph."""
        box = self.plain_box(element, w, h)
        if box is not None:
            context.floats.append(box())
            return ""

        def paint(canvas):
            if label:
                canvas.labels.append(label)
            self.painter.top(canvas, element, 0.0, 0.0, w, h)

        return self.painted([Piece(0.0, "paragraph", paint)])

    def painted(self, pieces, placed=False):
        """One picture of drawings painted in their order; one that cannot be painted leaves its text box
        or a marker instead, and never fails the document. A `placed` picture keeps its distance from the
        text column's left edge, as Word placed it."""
        canvas, failed = Canvas(), []
        for piece in sorted(pieces, key=lambda item: item.z):
            try:
                piece.paint(canvas)
            except Exception:
                failed.append(piece.html() if piece.html is not None else images.missing("[그리지 못한 도형]"))
        found = self.svg_picture(canvas, "도형")
        if found:
            self.found = True
            left = canvas.bounds()[0]
            if placed and left > 0:
                found = f'<div style="margin-left:{left}px">{found}</div>'
        return found + "".join(failed)

    def compose(self, context, content):
        """(pictures, content): the paragraph's floating drawings painted together where Word placed them.
        A picture in the line that is all the paragraph shows joins drawings placed from the paragraph,
        as screenshots marked with boxes and arrows are; it starts where the paragraph's alignment puts it."""
        pieces = list(context.pieces)
        if len(context.inline) == 1 and any(piece.frame == "paragraph" for piece in pieces):
            token, _, element, w, h = context.inline[0]
            if not visible(content.replace(token, "")):
                x = self.aligned(context.props, w)
                pieces.append(
                    Piece(
                        0.0, "paragraph", lambda canvas: self.painter.picture(canvas, element, Matrix(), (x, 0.0, w, h))
                    )
                )
                content = content.replace(token, "")
        for token, html, *_ in context.inline:
            content = content.replace(token, html)
        found = []
        for frame in ("paragraph", "page"):
            group = sorted((piece for piece in pieces if piece.frame == frame), key=lambda piece: piece.z)
            kept = [piece for piece in group if piece.kept]
            group = [piece for piece in group if not piece.kept]
            if group and all(piece.html is not None for piece in group):
                # Text boxes the page draws itself keep their text on the page.
                found.extend(piece.html() for piece in group)
            elif group:
                found.append(self.painted(group, placed=True))
            found.extend(piece.html() for piece in kept)
        return "".join(found), content

    def aligned(self, props, w):
        """Where a picture alone in its paragraph starts, from the text column's left edge."""
        column = self.sheet["width"] - self.sheet["left"] - self.sheet["right"]
        ind = props.get("ind", {})
        left, right = ((twips(ind.get(side)) or 0) * 4 / 3 for side in ("left", "right"))
        align = ALIGN.get(props.get("jc"), "left")
        if align == "center":
            return left + (column - left - right - w) / 2
        if align == "right":
            return column - right - w
        return left + ((twips(ind.get("firstLine")) or 0) - (twips(ind.get("hanging")) or 0)) * 4 / 3

    def smartart_picture(self, relations, w, h, label):
        """SmartArt as Office laid it out (its drawing part), painted; "" when the package has no layout."""
        own = self.package.relations(self.part)
        data = self.package.xml(own.get(relations.get(R + "dm"), ("", True, ""))[0] or "")
        extension = None if data is None else next(data.iter(DSP + "dataModelExt"), None)
        target = own.get(extension.get("relId"), ("", True, ""))[0] if extension is not None else ""
        root = self.package.xml(target) if target else None
        tree = None if root is None else root.find(DSP + "spTree")
        if tree is None:
            return ""
        canvas = Canvas()
        if label:
            canvas.labels.append(label)

        def paint():
            for item in tree:
                self.painter.item(canvas, item, Matrix(), (0.0, 0.0, 1.0, 1.0, 0.0, 0.0), None, 0)

        try:
            # Pictures in SmartArt belong to its drawing part.
            self.in_part(target, paint)
        except Exception:
            return ""
        if canvas.parts:
            canvas.cover(Matrix(), 0, 0, w, h)
        found = self.svg_picture(canvas, "SmartArt")
        if found:
            self.found = True
        return found

    def vml(self, node, context):
        """VML pictures, shapes, groups and text boxes (older Word, web pages), or an embedded object."""
        if any(item.get(O + "hr") == "t" for item in node.iter()):
            context.floats.append("<hr>")
            return ""
        program = next((item.get("ProgID", "") for item in node.iter(O + "OLEObject")), "")
        html = []
        for shape in node:
            if shape.tag not in VML_SHAPES:
                continue
            try:
                html.append(self.vml_shape(shape, context, program))
            except Exception:
                html.append(images.missing("[그리지 못한 도형]"))
        if program and not "".join(html):
            html.append(images.missing(f"[삽입된 개체: {program}]"))
        return "".join(html)

    def vml_shape(self, shape, context, program):
        style = style_of(shape.get("style"))
        picture, content = shape.find(V + "imagedata"), shape.find(f"{V}textbox/{W}txbxContent")
        w, h = Vml.size(shape)
        look = None if content is None else vml_box_css(shape, self.vml_painter.types, w, h)
        box = (
            None
            if look is None
            else (lambda: f'<div class="textbox" style="{attribute(look)}">{self.blocks(content)}</div>')
        )
        floating = style.get("position") == "absolute"
        if floating and context.composing:

            def position(axis, edge):
                align = (style.get(f"mso-position-{axis}") or "").strip()
                return (
                    style.get(f"mso-position-{axis}-relative", "text"),
                    vml_length(style.get(f"margin-{edge}")) + vml_length(style.get(edge)),
                    None if align in ("", "absolute") else align,
                )

            x, y, frame = self.place(*position("horizontal", "left"), *position("vertical", "top"), w, h)
            context.pieces.append(
                Piece(
                    number(style.get("z-index"), 0),
                    frame,
                    lambda canvas: self.vml_painter.top(canvas, shape, x, y),
                    box,
                    box is not None and wordy(content),
                )
            )
            return ""
        if picture is not None and content is None:
            label = picture.get(O + "title") or (f"삽입된 개체: {program}" if program else "")
            width, height = vml_size(shape.get("style"))
            # VML crops a picture by shares of its sides, as fixed-point (5243f) or plain fractions.
            crop = images.crop_css(
                *(vml_fraction(picture.get(f"crop{side}")) for side in ("top", "right", "bottom", "left"))
            )
            return self.image(picture.get(R + "id") or picture.get(O + "relid"), label, width, height, crop)
        if box is not None:
            context.floats.append(box())
            return ""
        found = self.painted(
            [Piece(0.0, "paragraph", lambda canvas: self.vml_painter.top(canvas, shape, 0.0, 0.0), box)]
        )
        if floating:
            context.floats.append(found)
            return ""
        return found

    def chart(self, ident, label):
        target = self.package.relations(self.part).get(ident, ("", True, ""))[0]
        root = self.package.xml(target) if target else None
        chart = None if root is None else root.find(C + "chart")
        if chart is None:
            return f"<p>{images.missing(f'[차트: {label}]' if label else '[차트]')}</p>"
        caption = chart.find(C + "title")
        title = (
            " ".join("".join(item.text or "" for item in caption.iter(A + "t")).split()) if caption is not None else ""
        )
        plot = chart.find(C + "plotArea")
        groups = [group for group in ([] if plot is None else plot) if local(group.tag) in CHART_KINDS]
        series, labels = [], {}
        for group in groups:
            for item in group.findall(C + "ser"):
                caption = item.find(C + "tx")
                name = (
                    " ".join("".join(point.text or "" for point in caption.iter(C + "v")).split())
                    if caption is not None
                    else ""
                )
                categories = item.find(C + "cat") if item.find(C + "cat") is not None else item.find(C + "xVal")
                amounts = item.find(C + "val") if item.find(C + "val") is not None else item.find(C + "yVal")
                for index, text in chart_points(categories).items():
                    labels.setdefault(index, text)
                series.append((name or f"계열 {len(series) + 1}", chart_points(amounts)))
        kinds = ", ".join(dict.fromkeys(CHART_KINDS[local(group.tag)] for group in groups))
        self.found = True
        return chart_table(title or label, kinds, labels, series)

    def smartart(self, ident, label):
        target = self.package.relations(self.part).get(ident, ("", True, ""))[0]
        root = self.package.xml(target) if target else None
        if root is None:
            return f"<p>{images.missing(f'[SmartArt: {label}]' if label else '[SmartArt]')}</p>"
        points_ = {item.get("modelId"): item for item in root.iter(DGM + "pt")}
        children = {}
        for link_ in root.iter(DGM + "cxn"):
            if link_.get("type", "parOf") == "parOf":
                children.setdefault(link_.get("srcId"), []).append(
                    (number(link_.get("srcOrd"), 0), link_.get("destId"))
                )
        top = next((ident_ for ident_, item in points_.items() if item.get("type") == "doc"), None)

        def text(item):
            return " ".join("".join(run.text or "" for run in item.iter(A + "t")).split())

        def tree(ident_, depth=0):
            if depth > 32:
                return ""
            entries = []
            for _, child in sorted(children.get(ident_, [])):
                item = points_.get(child)
                if item is None or item.get("type", "node") not in ("node", "asst"):
                    continue
                entries.append(f"<li>{escape(text(item))}{tree(child, depth + 1)}</li>")
            return f"<ul>{''.join(entries)}</ul>" if entries else ""

        body = tree(top) if top else ""
        if not body:
            return ""
        self.found = True
        caption = f"SmartArt: {label}" if label else "SmartArt"
        return f'<figure class="smartart"><figcaption>{escape(caption)}</figcaption>{body}</figure>'

    # Other parts ------------------------------------------------------------------------------

    def in_part(self, name, render):
        """Render with the relationships (pictures, links) of another part, such as footnotes.xml."""
        previous, self.part = self.part, name
        fields, self.fields = self.fields, []
        try:
            return render()
        finally:
            self.part, self.fields = previous, fields

    def notes_html(self):
        html = []
        for kind, part_kind, title, tag in (
            ("footnote", "footnotes", "각주", "footnote"),
            ("endnote", "endnotes", "미주", "endnote"),
            ("comment", "comments", "메모", "comment"),
        ):
            name = self.package.related("/word/document.xml", part_kind) or f"/word/{part_kind}.xml"
            root = self.package.xml(name)
            if root is None or not self.notes[kind]:
                continue
            found = {item.get(W + "id"): item for item in root.findall(W + tag)}
            entries = []
            # Notes can refer to more notes, which join the end of the list while it is read.
            index = 0
            while index < len(self.notes[kind]):
                ident = self.notes[kind][index]
                index += 1
                item = found.get(ident)
                if item is None:
                    continue
                content = self.in_part(name, lambda item=item: self.blocks(item))
                if kind == "comment":
                    author = " · ".join(
                        part for part in (item.get(W + "author", ""), (item.get(W + "date") or "")[:10]) if part
                    )
                    content = (f'<p class="caption">{escape(author)}</p>' if author else "") + content
                entries.append(
                    f'<li id="{kind}-{index}"{note_mark(self.marks.get(kind, {}).get(ident), index)}>{content}</li>'
                )
            if entries:
                kind_attribute = ' type="i"' if kind == "endnote" else ""
                html.append(
                    f'<div class="notes"><p class="caption">{title}</p><ol{kind_attribute}>{"".join(entries)}</ol>'
                    "</div>"
                )
        return "".join(html)

    def headers_footers(self, body):
        """The headers and footers of the sections, as far as the package carries them."""
        sections = [paragraph.find(f"{W}pPr/{W}sectPr") for paragraph in body.iter(W + "p")] + [body.find(W + "sectPr")]
        found = {"header": [], "footer": []}
        seen = set()
        for section in sections:
            if section is None:
                continue
            for reference in section:
                kind = {"headerReference": "header", "footerReference": "footer"}.get(local(reference.tag))
                if not kind or reference.get(W + "type", "default") == "even":
                    continue
                target = self.package.relations("/word/document.xml").get(reference.get(R + "id"), ("", True, ""))[0]
                root = self.package.xml(target) if target and target not in seen else None
                if root is None:
                    continue
                seen.add(target)
                content = self.in_part(target, lambda root=root: self.blocks(root))
                if visible(content):
                    found[kind].append(content)
        return (
            "".join(f'<div class="header">{item}</div>' for item in found["header"]),
            "".join(f'<div class="footer">{item}</div>' for item in found["footer"]),
        )


def empty_cells(span):
    """The grid columns a table row leaves empty, without borders or text of their own."""
    if span <= 0:
        return ""
    return f'<td{f" colspan={chr(34)}{span}{chr(34)}" if span > 1 else ""} style="border:none;padding:0"></td>'


def note_mark(mark, index):
    """The attributes of the list item of the note that is `index`th in its list, showing the note's mark: Word's
    number where it differs from the item's place, or the mark the note has of its own (such as *)."""
    if isinstance(mark, int):
        return f' value="{mark}"' if mark != index else ""
    if mark and re.fullmatch(r"[^\"'\\<>;{}\n]{1,16}", mark):
        return f' style="{attribute(f"list-style-type:{chr(34)}{mark} {chr(34)}")}"'
    return ""


def chart_points(element):
    """{index: text} of a chart's cached categories or values."""
    if element is None:
        return {}
    found = {}
    for point in element.iter(C + "pt"):
        index = point.get("idx", "")
        text = point.find(C + "v")
        if index.isdigit() and text is not None and int(index) not in found:
            found[int(index)] = (text.text or "").strip()
    return found


def vml_size(style):
    """(width, height) in pixels of a VML shape's style, or (None, None)."""
    sizes = {}
    for name, amount, unit in re.findall(r"(width|height)\s*:\s*([\d.]+)\s*(pt|px|in|cm|mm)?", style or ""):
        sizes[name] = (
            number(amount, 0) * {"pt": 4 / 3, "px": 1, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4, "": 1}[unit]
        )
    return sizes.get("width"), sizes.get("height")


# --- Equations ------------------------------------------------------------------------------------


def math(node):
    """An Office equation as MathML, with its linear form as alttext for search."""
    if node.tag == M + "oMathPara":
        return "".join(math(item) for item in node.iter(M + "oMath"))
    markup, linear = equation(node)
    return f'<math alttext="{attribute(" ".join(linear.split()))}">{markup}</math>'


def equation(node, depth=0):
    """(MathML, linear text) of an OMML element and its children."""
    if depth > 64:
        return "", ""
    tag = local(node.tag)

    def part(name):
        child = node.find(M + name)
        return equation(child, depth + 1) if child is not None else ("<mrow></mrow>", "")

    def prop(name, default=""):
        item = node.find(f"{M}{tag}Pr/{M}{name}")
        return default if item is None else item.get(M + "val", default)

    if tag == "r":
        text = "".join(item.text or "" for item in node.iter(M + "t")) or "".join(
            item.text or "" for item in node.iter(W + "t")
        )
        normal = node.find(f"{M}rPr/{M}nor") is not None
        return tokens(text, normal), text
    if tag == "f":
        (top, top_text), (bottom, bottom_text) = part("num"), part("den")
        bar = ' linethickness="0"' if prop("type") == "noBar" else ""
        return f"<mfrac{bar}>{top}{bottom}</mfrac>", f"({top_text})/({bottom_text})"
    if tag in ("sSup", "sSub", "sSubSup", "sPre"):
        (base, base_text), (low, low_text), (high, high_text) = part("e"), part("sub"), part("sup")
        if tag == "sSup":
            return f"<msup>{base}{high}</msup>", f"{base_text}^({high_text})"
        if tag == "sSub":
            return f"<msub>{base}{low}</msub>", f"{base_text}_({low_text})"
        if tag == "sPre":
            return (
                f"<mmultiscripts>{base}<mprescripts></mprescripts>{low}{high}</mmultiscripts>",
                f"_({low_text})^({high_text}){base_text}",
            )
        return f"<msubsup>{base}{low}{high}</msubsup>", f"{base_text}_({low_text})^({high_text})"
    if tag == "rad":
        (base, base_text), (degree, degree_text) = part("e"), part("deg")
        if prop("degHide") in ("1", "on", "true") or not degree_text:
            return f"<msqrt>{base}</msqrt>", f"√({base_text})"
        return f"<mroot>{base}{degree}</mroot>", f"√[{degree_text}]({base_text})"
    if tag == "nary":
        char = prop("chr", "∫")
        (base, base_text), (low, low_text), (high, high_text) = part("e"), part("sub"), part("sup")
        operator = f"<mo>{escape(char)}</mo>"
        limits = "munderover" if prop("limLoc") == "undOvr" or char in "∑∏∐⋃⋂⋁⋀" else "msubsup"
        return (
            f"<mrow><{limits}>{operator}{low}{high}</{limits}>{base}</mrow>",
            f"{char}_({low_text})^({high_text}) {base_text}",
        )
    if tag == "d":
        opening, closing, separator = prop("begChr", "("), prop("endChr", ")"), prop("sepChr", "|")
        found = [equation(child, depth + 1) for child in node.findall(M + "e")]
        middle = f"<mo>{escape(separator)}</mo>".join(markup for markup, _ in found)
        return (
            f"<mrow><mo>{escape(opening)}</mo>{middle}<mo>{escape(closing)}</mo></mrow>",
            opening + separator.join(text for _, text in found) + closing,
        )
    if tag == "func":
        (name, name_text), (base, base_text) = part("fName"), part("e")
        return f"<mrow>{name}<mo>&#x2061;</mo>{base}</mrow>", f"{name_text} {base_text}"
    if tag in ("acc", "groupChr", "bar"):
        (base, base_text) = part("e")
        char = prop("chr", {"acc": "\u0302", "groupChr": "\u23df", "bar": "\u00af"}[tag])
        under = prop("pos", "bot" if tag == "groupChr" else "top") == "bot"
        element = "munder" if under else "mover"
        accent = ' accent="true"' if tag == "acc" else ""
        return f"<{element}{accent}>{base}<mo>{escape(char)}</mo></{element}>", f"{base_text}{char}"
    if tag in ("limLow", "limUpp"):
        (base, base_text), (limit, limit_text) = part("e"), part("lim")
        element = "munder" if tag == "limLow" else "mover"
        return f"<{element}>{base}{limit}</{element}>", f"{base_text}_({limit_text})"
    if tag == "m":
        rows = []
        texts = []
        for item in node.findall(M + "mr"):
            cells = [equation(child, depth + 1) for child in item.findall(M + "e")]
            rows.append("<mtr>" + "".join(f"<mtd>{markup}</mtd>" for markup, _ in cells) + "</mtr>")
            texts.append(", ".join(text for _, text in cells))
        return f"<mtable>{''.join(rows)}</mtable>", "[" + "; ".join(texts) + "]"
    if tag == "eqArr":
        cells = [equation(child, depth + 1) for child in node.findall(M + "e")]
        return (
            "<mtable>" + "".join(f"<mtr><mtd>{markup}</mtd></mtr>" for markup, _ in cells) + "</mtable>",
            "; ".join(text for _, text in cells),
        )
    if tag in ("phant", "t"):
        return "", ""
    # e, num, den, sub, sup, deg, lim, fName, box, borderBox, oMath and the like: a row of their children.
    found = [equation(child, depth + 1) for child in node if not local(child.tag).endswith("Pr")]
    return f"<mrow>{''.join(markup for markup, _ in found)}</mrow>", "".join(text for _, text in found)


def tokens(text, normal=False):
    """MathML tokens of equation text: numbers, identifiers and operators."""
    if normal:
        return f"<mtext>{escape(text)}</mtext>" if text else ""
    markup = []
    for match in re.finditer(r"\d+(?:\.\d+)?|[^\W\d_]+|\s+|.", text):
        token = match.group(0)
        if token.isspace():
            continue
        if token[0].isdigit():
            markup.append(f"<mn>{escape(token)}</mn>")
        elif token[0].isalpha():
            markup.append(
                "".join(f"<mi>{escape(char)}</mi>" for char in token)
                if len(token) <= 3
                else f"<mi>{escape(token)}</mi>"
            )
        else:
            markup.append(f"<mo>{escape(token)}</mo>")
    return "".join(markup)


def word_xml_to_html(xml, title):
    """The HTML page of Word's Flat OPC XML (or a bare w:document)."""
    if "<!DOCTYPE" in xml or "<!ENTITY" in xml:
        raise UserError("DTD나 ENTITY가 있는 Word XML은 읽지 않습니다.")
    root = ET.fromstring(xml)
    if root.tag == W + "document":
        package, document = Package(ET.Element(PKG + "package")), root
    else:
        # WordOpenXML is a Flat OPC package; its main document part holds the body.
        package = Package(root)
        document = package.xml("/word/document.xml")
    body = None if document is None or document.tag != W + "document" else document.find(W + "body")
    if body is None:
        raise UserError("Word 문서에서 본문을 읽지 못했습니다.")
    reader = Reader(package)
    reader.measure(body)
    content = reader.blocks(body)
    notes = reader.notes_html()
    header, footer = reader.headers_footers(body)
    if not reader.found:
        raise UserError(EMPTY)
    shown = reader.embedded(header + content + notes + footer)
    notice = "".join(f'<p class="caption">{escape(text)}</p>' for text in reader.notices)
    return page(title, notice + shown, "word", reader.root_style(body))
