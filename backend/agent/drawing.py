"""Drawings of Word documents as SVG pictures, painted in Python from the XML Word handed over.

Word hands over shapes, groups, drawing canvases and SmartArt only as their description, so they are
painted here: the outline of each shape from the preset definitions of the standard (shape_presets.py) or
its own path; fills, outlines, dashes, arrowheads, theme colors and style references as Office resolves
them; pictures from the package; and the text of each shape as XHTML in a foreignObject. Shadows, glow,
bevels, 3-D and other effects are left out. A drawing becomes one <img> of an SVG whose alternative text
carries the text of its shapes, so search and the LLM still read it. vml.py paints the VML shapes of older
documents on the same canvas."""

import colorsys
import math
import re
from html import escape
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

from . import images
from .html_document import attribute, css, font_family, points
from .shape_presets import PRESETS

A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
SVG = "http://www.w3.org/2000/svg"
XHTML = "http://www.w3.org/1999/xhtml"
EMU_PX = 9525
MAX_DEPTH = 32
MAX_TEXT = 4000  # characters of shape text in a picture's alternative text
# The default theme of Office 2013 and later, for documents without one.
OFFICE_COLORS = {
    "dk1": "000000",
    "lt1": "FFFFFF",
    "dk2": "44546A",
    "lt2": "E7E6E6",
    "accent1": "4472C4",
    "accent2": "ED7D31",
    "accent3": "A5A5A5",
    "accent4": "FFC000",
    "accent5": "5B9BD5",
    "accent6": "70AD47",
    "hlink": "0563C1",
    "folHlink": "954F72",
}
_SOLID = f'<a:solidFill xmlns:a="{A[1:-1]}"><a:schemeClr val="phClr"/></a:solidFill>'
# The fill and line styles of that theme, which shapes refer to by index, flattened to solid colors.
OFFICE_STYLES = {
    "fillStyleLst": [ET.fromstring(_SOLID) for _ in range(3)],
    "bgFillStyleLst": [ET.fromstring(_SOLID) for _ in range(3)],
    "lnStyleLst": [
        ET.fromstring(f'<a:ln xmlns:a="{A[1:-1]}" w="{width}">{_SOLID}</a:ln>') for width in (6350, 12700, 19050)
    ],
}
SCHEME_NAMES = {"bg1": "lt1", "tx1": "dk1", "bg2": "lt2", "tx2": "dk2"}
SYSTEM_COLORS = {
    "windowText": "000000",
    "window": "FFFFFF",
    "btnFace": "F0F0F0",
    "btnText": "000000",
    "highlight": "0078D7",
    "highlightText": "FFFFFF",
    "grayText": "6D6D6D",
    "menu": "F0F0F0",
    "menuText": "000000",
    "windowFrame": "646464",
    "infoBk": "FFFFE1",
    "infoText": "000000",
}
# Named colors of DrawingML (a:prstClr) and VML; names are compared in lower case.
NAMED_COLORS = {
    "black": "000000",
    "white": "FFFFFF",
    "red": "FF0000",
    "green": "008000",
    "lime": "00FF00",
    "blue": "0000FF",
    "yellow": "FFFF00",
    "cyan": "00FFFF",
    "aqua": "00FFFF",
    "magenta": "FF00FF",
    "fuchsia": "FF00FF",
    "gray": "808080",
    "grey": "808080",
    "silver": "C0C0C0",
    "maroon": "800000",
    "olive": "808000",
    "navy": "000080",
    "purple": "800080",
    "teal": "008080",
    "orange": "FFA500",
    "brown": "A52A2A",
    "pink": "FFC0CB",
    "gold": "FFD700",
    "violet": "EE82EE",
    "indigo": "4B0082",
    "beige": "F5F5DC",
    "darkblue": "00008B",
    "dkblue": "00008B",
    "darkred": "8B0000",
    "dkred": "8B0000",
    "darkgreen": "006400",
    "dkgreen": "006400",
    "darkgray": "A9A9A9",
    "dkgray": "A9A9A9",
    "darkgrey": "A9A9A9",
    "lightgray": "D3D3D3",
    "ltgray": "D3D3D3",
    "lightgrey": "D3D3D3",
    "lightblue": "ADD8E6",
    "ltblue": "ADD8E6",
    "lightgreen": "90EE90",
    "ltgreen": "90EE90",
    "lightyellow": "FFFFE0",
    "ltyellow": "FFFFE0",
    "darkorange": "FF8C00",
    "dkorange": "FF8C00",
    "skyblue": "87CEEB",
    "royalblue": "4169E1",
    "steelblue": "4682B4",
    "tan": "D2B48C",
    "crimson": "DC143C",
    "coral": "FF7F50",
    "salmon": "FA8072",
    "khaki": "F0E68C",
    "lavender": "E6E6FA",
    "turquoise": "40E0D0",
    "dimgray": "696969",
    "slategray": "708090",
    "whitesmoke": "F5F5F5",
    "darkcyan": "008B8B",
    "dkcyan": "008B8B",
    "darkmagenta": "8B008B",
    "dkmagenta": "8B008B",
    "darkviolet": "9400D3",
    "dkviolet": "9400D3",
    "forestgreen": "228B22",
    "seagreen": "2E8B57",
    "midnightblue": "191970",
    "firebrick": "B22222",
    "chocolate": "D2691E",
    "sienna": "A0522D",
}
FILL_TAGS = {"noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill"}
COLOR_TAGS = {"srgbClr", "schemeClr", "sysClr", "prstClr", "scrgbClr", "hslClr"}
# Preset dashes as lengths of dash and gap in line widths.
DASHES = {
    "dot": (1, 1),
    "dash": (4, 3),
    "lgDash": (8, 3),
    "dashDot": (4, 3, 1, 3),
    "lgDashDot": (8, 3, 1, 3),
    "lgDashDotDot": (8, 3, 1, 3, 1, 3),
    "sysDash": (3, 1),
    "sysDot": (1, 1),
    "sysDashDot": (3, 1, 1, 1),
    "sysDashDotDot": (3, 1, 1, 1, 1, 1),
}
CAPS = {"rnd": "round", "sq": "square", "flat": "butt"}
ARROW_SIZES = {"sm": 2.0, "med": 3.0, "lg": 5.0}
# The fill modes of preset paths, such as the lit top of a can, as a tint or shade of the fill.
MODES = {"lighten": ("tint", 0.6), "lightenLess": ("tint", 0.8), "darken": ("shade", 0.6), "darkenLess": ("shade", 0.8)}
ANCHORS = {"ctr": "center", "b": "flex-end", "just": "center", "dist": "center"}
WRITING = {
    "vert": "vertical-rl",
    "eaVert": "vertical-rl",
    "wordArtVert": "vertical-rl",
    "vert270": "vertical-lr",
    "mongolianVert": "vertical-lr",
    "wordArtVertRtl": "vertical-rl",
}
ALIGNS = {"ctr": "center", "r": "right", "just": "justify", "dist": "justify"}
# Stylesheet of the text inside a picture, which the page's stylesheet does not reach.
TEXT_STYLE = (
    ".t p,.t h1,.t h2,.t h3,.t h4,.t h5,.t h6{margin:0;font-size:inherit;font-weight:inherit;"
    "white-space:pre-wrap}.t.nowrap p{white-space:pre}.t table{border-collapse:collapse}"
    ".t td,.t th{vertical-align:top}.t img{max-width:100%;height:auto}.t ul,.t ol{margin:0;"
    "padding-left:1.5em}.t sup,.t sub{line-height:0}.t a{color:#0563c1;text-decoration:underline}"
    ".t .missing{border:1px dashed #b4b4b4;color:#595959;font-size:9pt}"
)


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def child(element, name):
    """The first child named `name` in any namespace: wps, pic and dsp shapes share the DrawingML names."""
    return None if element is None else next((item for item in element if local(item.tag) == name), None)


def num(value):
    """A coordinate for SVG, without float noise."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def emu(text, default=0.0):
    try:
        return float(text) / EMU_PX
    except (TypeError, ValueError):
        return default


def number(text, default=0.0):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


def on(text, default=False):
    return default if text is None else str(text).lower() not in ("0", "false", "off", "f")


# --- Transforms -----------------------------------------------------------------------------------


class Matrix:
    """An affine transform of the plane, as SVG's matrix(a b c d e f)."""

    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, a=1.0, b=0.0, c=0.0, d=1.0, e=0.0, f=0.0):
        self.a, self.b, self.c, self.d, self.e, self.f = a, b, c, d, e, f

    def __matmul__(self, other):
        """This transform after `other`."""
        return Matrix(
            self.a * other.a + self.c * other.b,
            self.b * other.a + self.d * other.b,
            self.a * other.c + self.c * other.d,
            self.b * other.c + self.d * other.d,
            self.a * other.e + self.c * other.f + self.e,
            self.b * other.e + self.d * other.f + self.f,
        )

    def apply(self, x, y):
        return self.a * x + self.c * y + self.e, self.b * x + self.d * y + self.f

    def svg(self):
        return "matrix(" + " ".join(num(value) for value in (self.a, self.b, self.c, self.d, self.e, self.f)) + ")"


def moved(x, y):
    return Matrix(e=x, f=y)


def turned(degrees, cx, cy):
    """Rotation by `degrees` clockwise (y points down) about (cx, cy)."""
    if not degrees:
        return Matrix()
    angle = math.radians(degrees)
    cos, sin = math.cos(angle), math.sin(angle)
    return Matrix(cos, sin, -sin, cos, cx - cos * cx + sin * cy, cy - sin * cx - cos * cy)


def flipped(horizontal, vertical, cx, cy):
    sx, sy = -1.0 if horizontal else 1.0, -1.0 if vertical else 1.0
    return Matrix(sx, 0.0, 0.0, sy, cx - sx * cx, cy - sy * cy)


def placed(matrix, x, y, w, h, rotation=0.0, flip_h=False, flip_v=False):
    """The transform of a shape's own coordinates (0..w, 0..h) at (x, y), turned and flipped about its center."""
    cx, cy = x + w / 2, y + h / 2
    return matrix @ turned(rotation, cx, cy) @ flipped(flip_h, flip_v, cx, cy) @ moved(x, y)


# --- The picture ----------------------------------------------------------------------------------


class Canvas:
    """One SVG picture: shapes in paint order, their definitions, the area they cover and their text."""

    def __init__(self):
        self.parts, self.defs, self.texts, self.labels = [], [], [], []
        self.area = None
        self.count = 0
        self.cut = False  # whether the alternative text left out shape text beyond MAX_TEXT

    def ident(self):
        self.count += 1
        return f"d{self.count}"

    def cover(self, matrix, x0, y0, x1, y1, margin=0.0):
        corners = [matrix.apply(x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
        xs, ys = [x for x, _ in corners], [y for _, y in corners]
        box = (min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)
        if self.area is None:
            self.area = box
        else:
            self.area = (
                min(self.area[0], box[0]),
                min(self.area[1], box[1]),
                max(self.area[2], box[2]),
                max(self.area[3], box[3]),
            )

    def text(self, value):
        value = " ".join(str(value or "").split())
        if value:
            self.texts.append(value)

    def alt(self, label):
        """The alternative text: what the author called the drawing, then the text of its shapes."""
        names = list(dict.fromkeys(name for name in self.labels if name)) or [label]
        found = " / ".join(dict.fromkeys(self.texts))
        if len(found) > MAX_TEXT:
            found = found[:MAX_TEXT].rstrip() + "…"
            self.cut = True
        return " · ".join(names) + (f": {found}" if found else "")

    def bounds(self):
        """(x, y, width, height) of the picture in whole pixels, a pixel around what was painted."""
        x0, y0 = math.floor(self.area[0]) - 1, math.floor(self.area[1]) - 1
        x1, y1 = math.ceil(self.area[2]) + 1, math.ceil(self.area[3]) + 1
        return x0, y0, max(x1 - x0, 1), max(y1 - y0, 1)

    def svg(self):
        """(SVG text, width, height) of what was painted, or None for nothing."""
        if self.area is None or not self.parts:
            return None
        x0, y0, width, height = self.bounds()
        defs = f"<defs>{''.join(self.defs)}</defs>" if self.defs else ""
        return (
            (
                f'<svg xmlns="{SVG}" width="{width}" height="{height}" viewBox="{x0} {y0} {width} {height}">'
                f"<style>{TEXT_STYLE}</style>{defs}{''.join(self.parts)}</svg>"
            ),
            width,
            height,
        )

    def img(self, label):
        """An <img> of the picture, or "" when nothing was painted."""
        found = self.svg()
        if found is None:
            return ""
        svg, width, height = found
        return images.img("image/svg+xml", svg.encode("utf-8"), self.alt(label), width, height)


def xhtml(html):
    """HTML of the page as XHTML for a foreignObject: void elements closed, entities resolved."""
    markup = BeautifulSoup(html, "html.parser").decode(formatter="minimal")
    return markup.replace("<math", '<math xmlns="http://www.w3.org/1998/Math/MathML"')


def text_box(
    canvas, matrix, x, y, w, h, content, *, insets=(9.6, 4.8, 9.6, 4.8), anchor="t", vertical="horz", wrap=True, base=""
):
    """XHTML `content` laid out in the rectangle (x, y, w, h) of a shape's own coordinates."""
    left, top, right, bottom = insets
    pairs = [
        ("width", f"{num(max(w, 1))}px"),
        ("height", f"{num(max(h, 1))}px"),
        ("display", "flex"),
        ("flex-direction", "column"),
        ("justify-content", ANCHORS.get(anchor, "flex-start")),
        ("box-sizing", "border-box"),
        ("padding", " ".join(f"{num(max(side, 0))}px" for side in (top, right, bottom, left))),
        ("writing-mode", WRITING.get(vertical)),
        ("overflow-wrap", "break-word"),
    ]
    style = css(pairs) + (";" + base if base else "")
    kind = "t" if wrap else "t nowrap"
    canvas.parts.append(
        f'<foreignObject x="{num(x)}" y="{num(y)}" width="{num(max(w, 1))}" height="{num(max(h, 1))}" '
        f'overflow="visible" transform="{matrix.svg()}"><div xmlns="{XHTML}" class="{kind}" '
        f'style="{attribute(style)}">{content}</div></foreignObject>'
    )


def missing_picture(canvas, matrix, w, h, label):
    """Where a picture cannot be shown: a dashed frame with what it was."""
    canvas.parts.append(
        f'<rect x="0" y="0" width="{num(w)}" height="{num(h)}" fill="#F2F2F2" stroke="#B4B4B4" '
        f'stroke-dasharray="4 3" transform="{matrix.svg()}"/>'
    )
    text_box(
        canvas,
        matrix,
        0,
        0,
        w,
        h,
        escape(label),
        anchor="ctr",
        insets=(2, 2, 2, 2),
        base="text-align:center;color:#595959;font-size:9pt",
    )


# --- Colors ---------------------------------------------------------------------------------------


def rgb_hex(text):
    """(r, g, b) in 0..1 of RRGGBB or RGB, or None."""
    text = str(text or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9A-Fa-f]{3}", text):
        text = "".join(char * 2 for char in text)
    if not re.fullmatch(r"[0-9A-Fa-f]{6}", text):
        return None
    return tuple(int(text[index : index + 2], 16) / 255 for index in (0, 2, 4))


def linear(value):
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def gamma(value):
    value = min(max(value, 0.0), 1.0)
    return value * 12.92 if value <= 0.0031308 else 1.055 * value ** (1 / 2.4) - 0.055


class Color:
    """A color with its opacity, all in 0..1."""

    __slots__ = ("r", "g", "b", "alpha")

    def __init__(self, r, g, b, alpha=1.0):
        self.r, self.g, self.b, self.alpha = r, g, b, alpha

    def hex(self):
        return "#" + "".join(f"{round(min(max(value, 0.0), 1.0) * 255):02X}" for value in (self.r, self.g, self.b))

    def tinted(self, amount):
        """Toward white, as DrawingML's tint: `amount` of the color is kept, in linear light."""
        return Color(*(gamma(1 - (1 - linear(value)) * amount) for value in (self.r, self.g, self.b)), self.alpha)

    def shaded(self, amount):
        """Toward black, as DrawingML's shade."""
        return Color(*(gamma(linear(value) * amount) for value in (self.r, self.g, self.b)), self.alpha)

    def mixed(self, other, share):
        """`share` of `other` mixed into this color."""
        return Color(
            *(
                mine + (theirs - mine) * share
                for mine, theirs in ((self.r, other.r), (self.g, other.g), (self.b, other.b))
            ),
            self.alpha,
        )

    def fill(self, name="fill"):
        """SVG attributes painting with this color."""
        opacity = f' {name}-opacity="{num(self.alpha)}"' if self.alpha < 0.999 else ""
        return f'{name}="{self.hex()}"{opacity}'


def modified(color, element):
    """`color` changed by the modifiers inside a DrawingML color element, in their order."""
    r, g, b, alpha = color.r, color.g, color.b, color.alpha
    for item in element:
        name, amount = local(item.tag), number(item.get("val"), 0) / 100000
        if name == "alpha":
            alpha = amount
        elif name == "alphaMod":
            alpha *= amount
        elif name == "alphaOff":
            alpha += amount
        elif name in ("tint", "shade"):
            changed = Color(r, g, b).tinted(amount) if name == "tint" else Color(r, g, b).shaded(amount)
            r, g, b = changed.r, changed.g, changed.b
        elif name in ("lumMod", "lumOff", "lum", "satMod", "satOff", "sat", "hueMod", "hueOff", "hue", "comp"):
            hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
            if name == "lumMod":
                lightness *= amount
            elif name == "lumOff":
                lightness += amount
            elif name == "lum":
                lightness = amount
            elif name == "satMod":
                saturation *= amount
            elif name == "satOff":
                saturation += amount
            elif name == "sat":
                saturation = amount
            elif name == "hueMod":
                hue *= amount
            elif name == "hueOff":
                hue += number(item.get("val"), 0) / 60000 / 360
            elif name == "hue":
                hue = number(item.get("val"), 0) / 60000 / 360
            else:
                hue += 0.5
            r, g, b = colorsys.hls_to_rgb(hue % 1.0, min(max(lightness, 0.0), 1.0), min(max(saturation, 0.0), 1.0))
        elif name == "inv":
            r, g, b = 1 - r, 1 - g, 1 - b
        elif name == "gray":
            r = g = b = 0.299 * r + 0.587 * g + 0.114 * b
    return Color(min(max(r, 0.0), 1.0), min(max(g, 0.0), 1.0), min(max(b, 0.0), 1.0), min(max(alpha, 0.0), 1.0))


class Palette:
    """The theme's colors and its fill and line styles, which shapes refer to by index, and its fonts."""

    def __init__(self, theme=None, fonts=None):
        self.colors = dict(OFFICE_COLORS)
        self.fills, self.backgrounds, self.lines = [], [], []
        self.fonts = fonts or {}
        elements = None if theme is None else theme.find(A + "themeElements")
        scheme = None if elements is None else elements.find(A + "clrScheme")
        for item in [] if scheme is None else scheme:
            found = next(iter(item), None)
            if found is None:
                continue
            value = (
                (found.get("lastClr") or SYSTEM_COLORS.get(found.get("val")))
                if local(found.tag) == "sysClr"
                else found.get("val")
            )
            if rgb_hex(value):
                self.colors[local(item.tag)] = value
        formats = None if elements is None else elements.find(A + "fmtScheme")
        for name, target in (
            ("fillStyleLst", self.fills),
            ("bgFillStyleLst", self.backgrounds),
            ("lnStyleLst", self.lines),
        ):
            found = None if formats is None else formats.find(A + name)
            target.extend(list(found) if found is not None and len(found) else OFFICE_STYLES[name])

    def font(self, face):
        """A typeface of a run, the theme's fonts (+mn-lt, +mj-ea, …) resolved."""
        face = face or ""
        if face.startswith("+"):
            kind, _, script = face[1:].partition("-")
            return self.fonts.get(("minor" if kind == "mn" else "major", "ea" if script == "ea" else "latin"), "")
        return face

    def color(self, element, placeholder=None):
        """The Color of a DrawingML color element, or of the first one inside `element`; None without one.
        `placeholder` is the color a style's phClr stands for."""
        if element is None:
            return None
        if local(element.tag) not in COLOR_TAGS:
            element = next((item for item in element if local(item.tag) in COLOR_TAGS), None)
            if element is None:
                return None
        kind = local(element.tag)
        if kind == "schemeClr" and element.get("val") == "phClr":
            return modified(placeholder or Color(0, 0, 0), element)
        if kind == "srgbClr":
            rgb = rgb_hex(element.get("val"))
        elif kind == "schemeClr":
            name = element.get("val", "")
            rgb = rgb_hex(self.colors.get(SCHEME_NAMES.get(name, name)))
        elif kind == "sysClr":
            rgb = rgb_hex(element.get("lastClr") or SYSTEM_COLORS.get(element.get("val")))
        elif kind == "prstClr":
            rgb = rgb_hex(NAMED_COLORS.get((element.get("val") or "").lower()))
        elif kind == "scrgbClr":
            rgb = tuple(gamma(number(element.get(name), 0) / 100000) for name in ("r", "g", "b"))
        else:
            rgb = colorsys.hls_to_rgb(
                number(element.get("hue"), 0) / 60000 / 360 % 1.0,
                min(max(number(element.get("lum"), 0) / 100000, 0.0), 1.0),
                min(max(number(element.get("sat"), 0) / 100000, 0.0), 1.0),
            )
        return None if rgb is None else modified(Color(*rgb), element)

    def fill(self, properties, style, group=None):
        """(fill element, placeholder color) of a shape: its own fill, else its style's; None for no fill."""
        own = next((item for item in ([] if properties is None else properties) if local(item.tag) in FILL_TAGS), None)
        if own is not None:
            return group if local(own.tag) == "grpFill" else (own, None)
        reference = child(style, "fillRef")
        index = int(number(None if reference is None else reference.get("idx"), 0))
        styles, position = (self.backgrounds, index - 1001) if index >= 1001 else (self.fills, index - 1)
        if index <= 0 or not 0 <= position < len(styles):
            return None
        return styles[position], self.color(reference)

    def line(self, properties, style):
        """The Line of a shape: its own outline over its style's, or None for none."""
        own = child(properties, "ln")
        reference = child(style, "lnRef")
        index = int(number(None if reference is None else reference.get("idx"), 0))
        base = self.lines[index - 1] if 0 < index <= len(self.lines) else None
        placeholder = self.color(reference) if base is not None else None
        if own is None and base is None:
            return None

        def pick(names):
            for element, color in ((own, None), (base, placeholder)):
                found = next((item for item in ([] if element is None else element) if local(item.tag) in names), None)
                if found is not None:
                    return found, color
            return None, None

        paint, color = pick({"noFill", "solidFill", "gradFill", "pattFill"})
        if paint is None or local(paint.tag) == "noFill":
            return None
        if local(paint.tag) == "solidFill":
            found = self.color(paint, color)
        elif local(paint.tag) == "gradFill":
            found = self.color(paint.find(f"{A}gsLst/{A}gs"), color)
        else:
            found = self.color(paint.find(A + "fgClr"), color)
        if found is None:
            return None
        width = next(
            (emu(element.get("w")) for element in (own, base) if element is not None and element.get("w")), 0.75 * 4 / 3
        )
        dash, _ = pick({"prstDash", "custDash"})
        pattern = None
        if dash is not None and local(dash.tag) == "prstDash":
            pattern = DASHES.get(dash.get("val"))
        elif dash is not None:
            pattern = tuple(
                value
                for item in dash.findall(A + "ds")
                for value in (number(item.get("d"), 100000) / 100000, number(item.get("sp"), 100000) / 100000)
            )
        cap = next((element.get("cap") for element in (own, base) if element is not None and element.get("cap")), None)
        join, _ = pick({"round", "bevel", "miter"})
        head, _ = pick({"headEnd"})
        tail, _ = pick({"tailEnd"})
        return Line(
            found,
            max(width, 0.0),
            pattern,
            CAPS.get(cap),
            None if join is None else local(join.tag),
            ends(head),
            ends(tail),
        )


def ends(element):
    """(kind, width, length) of an arrowhead, or None."""
    if element is None or element.get("type", "none") == "none":
        return None
    return element.get("type"), element.get("w", "med"), element.get("len", "med")


class Line:
    """An outline: its color, width in px, dash pattern in widths, cap, join and arrowheads."""

    __slots__ = ("color", "width", "dash", "cap", "join", "head", "tail")

    def __init__(self, color, width, dash=None, cap=None, join=None, head=None, tail=None):
        self.color, self.width, self.dash, self.cap = color, width, dash, cap
        self.join, self.head, self.tail = join, head, tail

    def svg(self):
        width = max(self.width, 0.5)
        pairs = [self.color.fill("stroke"), f'stroke-width="{num(width)}"']
        if self.dash:
            pairs.append(f'stroke-dasharray="{" ".join(num(max(part * width, 0.5)) for part in self.dash)}"')
        if self.cap:
            pairs.append(f'stroke-linecap="{self.cap}"')
        if self.join in ("round", "bevel"):
            pairs.append(f'stroke-linejoin="{self.join}"')
        return " ".join(pairs)

    def reach(self):
        """How far the outline and its arrowheads reach beyond the shape's outline."""
        arrow = max(self.width, 2.65) * 5 if self.head or self.tail else 0
        return max(self.width / 2, arrow / 2) + 1


# --- Geometry -------------------------------------------------------------------------------------


def builtins(w, h):
    """The guides every geometry may use, for a shape of w by h; angles in 60000ths of a degree."""
    ss, ls = min(w, h), max(w, h)
    values = {
        "l": 0.0,
        "t": 0.0,
        "r": w,
        "b": h,
        "w": w,
        "h": h,
        "hc": w / 2,
        "vc": h / 2,
        "ss": ss,
        "ls": ls,
        "cd2": 10800000.0,
        "cd4": 5400000.0,
        "cd8": 2700000.0,
        "3cd4": 16200000.0,
        "3cd8": 8100000.0,
        "5cd8": 13500000.0,
        "7cd8": 18900000.0,
    }
    for divisor in (2, 3, 4, 5, 6, 8, 10, 12, 32):
        values[f"wd{divisor}"] = w / divisor
        values[f"hd{divisor}"] = h / divisor
    for divisor in (2, 4, 6, 8, 16, 32):
        values[f"ssd{divisor}"] = ss / divisor
    return values


def degrees(value):
    return math.radians(value / 60000)


def formula(text, values):
    """The value of a DrawingML guide formula, such as "*/ w adj1 100000"."""
    parts = (text or "val 0").split()
    operator, arguments = parts[0], [values[item] if item in values else number(item) for item in parts[1:]]
    x, y, z = (arguments + [0.0, 0.0, 0.0])[:3]
    if operator == "*/":
        return x * y / z if z else 0.0
    if operator == "+-":
        return x + y - z
    if operator == "+/":
        return (x + y) / z if z else 0.0
    if operator == "?:":
        return y if x > 0 else z
    if operator == "abs":
        return abs(x)
    if operator == "at2":
        return math.degrees(math.atan2(y, x)) * 60000
    if operator == "cat2":
        return x * math.cos(math.atan2(z, y))
    if operator == "sat2":
        return x * math.sin(math.atan2(z, y))
    if operator == "cos":
        return x * math.cos(degrees(y))
    if operator == "sin":
        return x * math.sin(degrees(y))
    if operator == "tan":
        return x * math.tan(degrees(y))
    if operator == "max":
        return max(x, y)
    if operator == "min":
        return min(x, y)
    if operator == "mod":
        return math.sqrt(x * x + y * y + z * z)
    if operator == "pin":
        return x if y < x else z if y > z else y
    if operator == "sqrt":
        return math.sqrt(max(x, 0.0))
    return x  # val


def custom(geometry):
    """A custGeom element as the preset definitions write their shapes."""

    def guides(name):
        found = geometry.find(A + name)
        return tuple(
            (item.get("name"), item.get("fmla")) for item in ([] if found is None else found.findall(A + "gd"))
        )

    def point(item):
        found = item.find(A + "pt")
        return (found.get("x"), found.get("y")) if found is not None else ("0", "0")

    paths = []
    for path in geometry.iterfind(f"{A}pathLst/{A}path"):
        commands = []
        for item in path:
            kind = local(item.tag)
            if kind == "moveTo":
                commands.append(("M",) + point(item))
            elif kind == "lnTo":
                commands.append(("L",) + point(item))
            elif kind == "arcTo":
                commands.append(("A", item.get("wR"), item.get("hR"), item.get("stAng"), item.get("swAng")))
            elif kind in ("cubicBezTo", "quadBezTo"):
                commands.append(
                    ("C" if kind == "cubicBezTo" else "Q",)
                    + tuple(value for found in item.findall(A + "pt") for value in (found.get("x"), found.get("y")))
                )
            elif kind == "close":
                commands.append(("Z",))
        paths.append(
            (
                int(number(path.get("w"))) or None,
                int(number(path.get("h"))) or None,
                path.get("fill", "norm"),
                on(path.get("stroke"), True),
                tuple(commands),
            )
        )
    rect = geometry.find(A + "rect")
    text = (rect.get("l"), rect.get("t"), rect.get("r"), rect.get("b")) if rect is not None else None
    return guides("avLst"), guides("gdLst"), text, tuple(paths)


class Outline:
    """One path of a geometry in a shape's own coordinates: SVG path data, how it is filled and stroked,
    and the direction of its ends for arrowheads."""

    __slots__ = ("data", "mode", "stroked", "closed", "start", "end")

    def __init__(self, data, mode, stroked, closed, start, end):
        self.data, self.mode, self.stroked, self.closed, self.start, self.end = data, mode, stroked, closed, start, end


def geometry(properties, w, h):
    """([Outline], text rectangle (l, t, r, b)) of a shape's preset or custom geometry at w by h px."""
    preset, own = child(properties, "prstGeom"), child(properties, "custGeom")
    overrides = {}
    if own is not None:
        definition = custom(own)
    else:
        name = preset.get("prst", "rect") if preset is not None else "rect"
        definition = PRESETS.get(name) or PRESETS["rect"]
        overrides = {
            item.get("name"): item.get("fmla")
            for item in ([] if preset is None else preset.iterfind(f"{A}avLst/{A}gd"))
        }
    adjust, guides, text, paths = definition
    values = builtins(w, h)
    for name, value in adjust:
        values[name] = formula(overrides.get(name, value), values)
    for name, value in guides:
        values[name] = formula(value, values)
    outlines = [
        trace(commands, values, (w / width) if width else 1.0, (h / height) if height else 1.0, mode, stroked)
        for width, height, mode, stroked, commands in paths
    ]
    box = (0.0, 0.0, w, h)
    if text:
        box = tuple(values[item] if item in values else number(item) for item in text)
    return [outline for outline in outlines if outline.data], box


def trace(commands, values, sx, sy, mode, stroked):
    """The Outline of one path's commands, scaled from the path's coordinate space by (sx, sy)."""

    def at(item):
        return values[item] if item in values else number(item)

    data, x, y, begin = [], 0.0, 0.0, (0.0, 0.0)
    start = end = None
    closed = False

    def segment(direction_start, direction_end, target):
        nonlocal start, end, x, y, closed
        if start is None:
            start = ((x, y), (-direction_start[0], -direction_start[1]))
        end = (target, direction_end)
        x, y = target
        closed = False

    for command in commands:
        kind = command[0]
        if kind == "M":
            x, y = at(command[1]) * sx, at(command[2]) * sy
            begin = (x, y)
            data.append(f"M{num(x)} {num(y)}")
        elif kind == "L":
            target = (at(command[1]) * sx, at(command[2]) * sy)
            data.append(f"L{num(target[0])} {num(target[1])}")
            direction = (target[0] - x, target[1] - y)
            segment(direction, direction, target)
        elif kind == "A":
            rx, ry = at(command[1]) * sx, at(command[2]) * sy
            first, sweep = at(command[3]) / 60000, at(command[4]) / 60000
            if not rx or not ry or not sweep:
                continue

            def parametric(angle):
                radians = math.radians(angle)
                return math.atan2(rx * math.sin(radians), ry * math.cos(radians))

            theta = parametric(first)
            cx, cy = x - rx * math.cos(theta), y - ry * math.sin(theta)
            sign = 1.0 if sweep > 0 else -1.0
            tangent = lambda angle: (-rx * math.sin(angle) * sign, ry * math.cos(angle) * sign)
            # An arc of a whole turn or more is drawn as two halves: SVG cannot draw it in one.
            pieces = 2 if abs(sweep) >= 359.99 else 1
            for piece in range(1, pieces + 1):
                angle = parametric(first + sweep * piece / pieces)
                target = (cx + rx * math.cos(angle), cy + ry * math.sin(angle))
                large = 1 if abs(sweep / pieces) > 180 else 0
                data.append(f"A{num(rx)} {num(ry)} 0 {large} {1 if sweep > 0 else 0} {num(target[0])} {num(target[1])}")
                segment(tangent(theta), tangent(angle), target)
        elif kind in ("C", "Q"):
            points_ = [(at(command[index]) * sx, at(command[index + 1]) * sy) for index in range(1, len(command), 2)]
            if not points_:
                continue
            data.append(kind + " ".join(f"{num(px)} {num(py)}" for px, py in points_))
            first_control = next((point for point in points_ if point != (x, y)), points_[-1])
            last_control = points_[-2] if len(points_) > 1 and points_[-2] != points_[-1] else (x, y)
            segment(
                (first_control[0] - x, first_control[1] - y),
                (points_[-1][0] - last_control[0], points_[-1][1] - last_control[1]),
                points_[-1],
            )
        elif kind == "Z":
            data.append("Z")
            x, y = begin
            closed = True
    return Outline("".join(data), mode, stroked, closed, start, end)


def arrowhead(end, line, point, direction):
    """SVG of an arrowhead at `point`, pointing along `direction` (away from the line)."""
    kind, width_name, length_name = end
    length = math.hypot(*direction)
    if not length:
        return ""
    ux, uy = direction[0] / length, direction[1] / length
    nx, ny = -uy, ux
    base = max(line.width, 2.65)
    width, size = base * ARROW_SIZES.get(width_name, 3.0), base * ARROW_SIZES.get(length_name, 3.0)
    px, py = point

    def at(along, across):
        return f"{num(px + ux * along + nx * across)},{num(py + uy * along + ny * across)}"

    color = line.color.fill("fill")
    if kind == "arrow":
        return (
            f'<polyline points="{at(-size, width / 2)} {at(0, 0)} {at(-size, -width / 2)}" fill="none" '
            f'{line.color.fill("stroke")} stroke-width="{num(max(line.width, 0.5))}" stroke-linejoin="miter"/>'
        )
    if kind == "stealth":
        return f'<polygon points="{at(0, 0)} {at(-size, width / 2)} {at(-size * 0.6, 0)} {at(-size, -width / 2)}" {color}/>'
    if kind == "diamond":
        return (
            f'<polygon points="{at(size / 2, 0)} {at(0, width / 2)} {at(-size / 2, 0)} {at(0, -width / 2)}" {color}/>'
        )
    if kind == "oval":
        angle = math.degrees(math.atan2(uy, ux))
        return (
            f'<ellipse cx="{num(px)}" cy="{num(py)}" rx="{num(size / 2)}" ry="{num(width / 2)}" '
            f'transform="rotate({num(angle)} {num(px)} {num(py)})" {color}/>'
        )
    return f'<polygon points="{at(0, 0)} {at(-size, width / 2)} {at(-size, -width / 2)}" {color}/>'


# --- DrawingML shapes -----------------------------------------------------------------------------


class Painter:
    """Paints DrawingML shapes, pictures, groups and canvases on a Canvas.

    `picture(ident)` gives (content type, bytes, size) of a picture of the current part or the text of a
    marker; `text(content)` gives (XHTML, plain text) of a Word text box (w:txbxContent); `base` is the CSS
    of the document's default text."""

    def __init__(self, palette, picture, text=None, base=""):
        self.palette, self.source, self.word_text, self.base = palette, picture, text, base

    # Pieces ----------------------------------------------------------------------------------

    def top(self, canvas, element, x, y, w, h):
        """A drawing Word placed at (x, y) with w by h px: a shape, group, canvas or picture."""
        kind = local(element.tag)
        frame = (x, y, w, h)
        if kind == "wpc":
            self.drawing_canvas(canvas, element, Matrix(), frame)
        elif kind in ("wgp", "grpSp"):
            self.group(canvas, element, Matrix(), frame, None, 0)
        elif kind == "pic":
            self.picture(canvas, element, Matrix(), frame)
        else:
            self.shape(canvas, element, Matrix(), frame, None)

    def item(self, canvas, element, matrix, mapping, group, depth):
        """A shape inside a group or canvas, its own frame mapped into the group's coordinates."""
        kind = local(element.tag)
        if kind not in ("wsp", "sp", "pic", "grpSp", "wgp", "graphicFrame", "cxnSp"):
            return
        properties = child(element, "grpSpPr" if kind in ("grpSp", "wgp") else "spPr")
        transform = child(properties, "xfrm") if properties is not None else child(element, "xfrm")
        offset, extent = child(transform, "off"), child(transform, "ext")
        x0, y0, scale_x, scale_y, origin_x, origin_y = mapping
        x = origin_x + (emu(None if offset is None else offset.get("x")) - x0) * scale_x
        y = origin_y + (emu(None if offset is None else offset.get("y")) - y0) * scale_y
        w = emu(None if extent is None else extent.get("cx")) * scale_x
        h = emu(None if extent is None else extent.get("cy")) * scale_y
        frame = (x, y, w, h)
        if kind in ("grpSp", "wgp"):
            self.group(canvas, element, matrix, frame, group, depth + 1)
        elif kind == "pic":
            self.picture(canvas, element, matrix, frame)
        elif kind == "graphicFrame":
            local_matrix = placed(matrix, *frame)
            missing_picture(canvas, local_matrix, w, h, "[차트·개체]")
            canvas.cover(local_matrix, 0, 0, w, h)
        else:
            self.shape(canvas, element, matrix, frame, group)

    def group(self, canvas, element, matrix, frame, group, depth):
        if depth > MAX_DEPTH:
            return
        properties = child(element, "grpSpPr")
        transform = child(properties, "xfrm")
        x, y, w, h = frame
        offset, extent = child(transform, "off"), child(transform, "ext")
        start, size = child(transform, "chOff"), child(transform, "chExt")
        child_x = emu(start.get("x")) if start is not None else emu(None if offset is None else offset.get("x"))
        child_y = emu(start.get("y")) if start is not None else emu(None if offset is None else offset.get("y"))
        child_w = emu(size.get("cx")) if size is not None else emu(None if extent is None else extent.get("cx"))
        child_h = emu(size.get("cy")) if size is not None else emu(None if extent is None else extent.get("cy"))
        rotation = number(None if transform is None else transform.get("rot")) / 60000
        cx, cy = x + w / 2, y + h / 2
        inner = (
            matrix
            @ turned(rotation, cx, cy)
            @ flipped(
                on(transform.get("flipH")) if transform is not None else False,
                on(transform.get("flipV")) if transform is not None else False,
                cx,
                cy,
            )
        )
        fill = self.palette.fill(properties, None, group)
        mapping = (child_x, child_y, w / child_w if child_w else 1.0, h / child_h if child_h else 1.0, x, y)
        for item in element:
            self.item(canvas, item, inner, mapping, fill, depth)

    def drawing_canvas(self, canvas, element, matrix, frame):
        """A drawing canvas: its background and outline, and its shapes placed from its top-left corner."""
        x, y, w, h = frame
        background, whole = child(element, "bg"), child(element, "whole")
        fill = self.palette.fill(background, None) if background is not None else None
        line = self.palette.line(whole, None) if whole is not None else None
        local_matrix = placed(matrix, x, y, w, h)
        if fill is not None or line is not None:
            stroke = line.svg() if line is not None else 'stroke="none"'
            canvas.parts.append(
                f'<rect x="0" y="0" width="{num(w)}" height="{num(h)}" {self.paint(canvas, fill)} '
                f'{stroke} transform="{local_matrix.svg()}"/>'
            )
        canvas.cover(local_matrix, 0, 0, w, h)
        for item in element:
            self.item(canvas, item, matrix, (0.0, 0.0, 1.0, 1.0, x, y), None, 0)

    # Shapes ----------------------------------------------------------------------------------

    def shape(self, canvas, element, matrix, frame, group):
        """A shape (wps:wsp, dsp:sp): its geometry filled and outlined, its arrowheads and its text."""
        x, y, w, h = frame
        properties, style = child(element, "spPr"), child(element, "style")
        transform = child(properties, "xfrm")
        rotation = number(None if transform is None else transform.get("rot")) / 60000
        flip_h = transform is not None and on(transform.get("flipH"))
        flip_v = transform is not None and on(transform.get("flipV"))
        local_matrix = placed(matrix, x, y, w, h, rotation, flip_h, flip_v)
        outlines, box = geometry(properties, w, h)
        fill = self.palette.fill(properties, style, group)
        line = self.palette.line(properties, style)
        heads = []
        for outline in outlines:
            painted = self.paint(canvas, fill, outline.mode) if outline.mode != "none" else 'fill="none"'
            stroked = line.svg() if line is not None and outline.stroked else 'stroke="none"'
            if painted == 'fill="none"' and stroked == 'stroke="none"':
                continue
            canvas.parts.append(f'<path d="{outline.data}" {painted} {stroked} transform="{local_matrix.svg()}"/>')
            if line is not None and outline.stroked and not outline.closed:
                for end, place in ((line.head, outline.start), (line.tail, outline.end)):
                    if end and place:
                        heads.append(arrowhead(end, line, *place))
        if heads:
            canvas.parts.append(f'<g transform="{local_matrix.svg()}">{"".join(heads)}</g>')
        canvas.cover(local_matrix, 0, 0, w, h, line.reach() if line is not None else 0)
        self.shape_text(canvas, element, matrix, frame, rotation, flip_v, box, style)

    def shape_text(self, canvas, element, matrix, frame, rotation, flip_v, box, style):
        x, y, w, h = frame
        body = child(element, "bodyPr")
        content, plain = None, ""
        box_x, box_y, box_w, box_h = box[0], box[1], box[2] - box[0], box[3] - box[1]
        text_matrix = placed(matrix, x, y, w, h, rotation + (180 if flip_v else 0))
        color = self.palette.color(child(style, "fontRef"))
        found = child(element, "txbx")
        words = None if found is None else child(found, "txbxContent")
        if words is not None and self.word_text is not None:
            content, plain = self.word_text(words)
        own = child(element, "txBody")
        if own is not None:
            body = child(own, "bodyPr")
            content, plain = self.drawing_text(own, color)
            place = child(element, "txXfrm")
            if place is not None:
                # SmartArt places the text of a shape in a rectangle of its own.
                offset, extent = child(place, "off"), child(place, "ext")
                tx = emu(None if offset is None else offset.get("x"))
                ty = emu(None if offset is None else offset.get("y"))
                tw, th = (
                    emu(None if extent is None else extent.get("cx")),
                    emu(None if extent is None else extent.get("cy")),
                )
                turn = number(place.get("rot")) / 60000
                text_matrix = placed(matrix, tx, ty, tw, th, turn)
                box_x, box_y, box_w, box_h = 0.0, 0.0, tw, th
        if not content:
            return
        canvas.text(plain)
        insets = tuple(
            emu(None if body is None else body.get(name), default)
            for name, default in (("lIns", 9.6), ("tIns", 4.8), ("rIns", 9.6), ("bIns", 4.8))
        )
        base = self.base + (f";color:{color.hex()}" if color is not None else "")
        text_box(
            canvas,
            text_matrix,
            box_x,
            box_y,
            box_w,
            box_h,
            content,
            insets=insets,
            anchor=(body.get("anchor", "t") if body is not None else "t"),
            vertical=(body.get("vert", "horz") if body is not None else "horz"),
            wrap=(body is None or body.get("wrap") != "none"),
            base=base.lstrip(";"),
        )

    def picture(self, canvas, element, matrix, frame):
        """A picture (pic:pic), cropped, in its geometry, with its outline."""
        x, y, w, h = frame
        properties = child(element, "spPr")
        transform = child(properties, "xfrm")
        rotation = number(None if transform is None else transform.get("rot")) / 60000
        local_matrix = placed(
            matrix,
            x,
            y,
            w,
            h,
            rotation,
            transform is not None and on(transform.get("flipH")),
            transform is not None and on(transform.get("flipV")),
        )
        fill = child(element, "blipFill")
        blip = None if fill is None else fill.find(A + "blip")
        ident = None if blip is None else (blip.get(R + "embed") or blip.get(R + "link"))
        found = self.source(ident) if ident else "[그림]"
        label = child(child(element, "nvPicPr"), "cNvPr")
        if label is not None:
            canvas.text(label.get("descr") or label.get("title"))
        if isinstance(found, tuple):
            outlines, _ = geometry(properties, w, h)
            clip = ""
            preset = child(properties, "prstGeom")
            if child(properties, "custGeom") is not None or (
                preset is not None and preset.get("prst", "rect") != "rect"
            ):
                ident_ = canvas.ident()
                canvas.defs.append(
                    f'<clipPath id="{ident_}">'
                    + "".join(f'<path d="{outline.data}"/>' for outline in outlines)
                    + "</clipPath>"
                )
                clip = f' clip-path="url(#{ident_})"'
            canvas.parts.append(
                f'<g transform="{local_matrix.svg()}"{clip}>{self.image(canvas, found, fill, w, h)}</g>'
            )
        else:
            missing_picture(canvas, local_matrix, w, h, found)
        line = self.palette.line(properties, child(element, "style"))
        if line is not None:
            outlines, _ = geometry(properties, w, h)
            canvas.parts.extend(
                f'<path d="{outline.data}" fill="none" {line.svg()} transform="{local_matrix.svg()}"/>'
                for outline in outlines
            )
        canvas.cover(local_matrix, 0, 0, w, h, line.reach() if line is not None else 0)

    def image(self, canvas, found, fill, w, h):
        """An <image> of a picture stretched over w by h px, cropped as the blipFill says."""
        kind, data, _ = found
        crop = None if fill is None else fill.find(A + "srcRect")
        left, top, right, bottom = (
            number(None if crop is None else crop.get(name)) / 100000 for name in ("l", "t", "r", "b")
        )
        shown_w, shown_h = max(1 - left - right, 0.01), max(1 - top - bottom, 0.01)
        width, height = w / shown_w, h / shown_h
        clip = ""
        if left > 0 or top > 0 or right > 0 or bottom > 0:
            ident = canvas.ident()
            canvas.defs.append(
                f'<clipPath id="{ident}"><rect x="0" y="0" width="{num(w)}" height="{num(h)}"/></clipPath>'
            )
            clip = f' clip-path="url(#{ident})"'
        return (
            f'<image href="{images.data_uri(kind, data)}" x="{num(-left * width)}" y="{num(-top * height)}" '
            f'width="{num(width)}" height="{num(height)}" preserveAspectRatio="none"{clip}/>'
        )

    # Paint -----------------------------------------------------------------------------------

    def paint(self, canvas, fill, mode="norm"):
        """SVG fill attributes of (fill element, placeholder color)."""
        if fill is None:
            return 'fill="none"'
        element, placeholder = fill
        kind = local(element.tag)
        change = MODES.get(mode)

        def adjusted(color):
            if color is None or change is None:
                return color
            return color.tinted(change[1]) if change[0] == "tint" else color.shaded(change[1])

        if kind == "solidFill":
            color = adjusted(self.palette.color(element, placeholder))
            return color.fill() if color is not None else 'fill="none"'
        if kind == "pattFill":
            front = self.palette.color(element.find(A + "fgClr"), placeholder) or Color(0, 0, 0)
            back = self.palette.color(element.find(A + "bgClr"), placeholder) or Color(1, 1, 1)
            share = number((re.findall(r"\d+", element.get("prst", "pct50")) or ["50"])[0], 50) / 100
            return adjusted(back.mixed(front, min(max(share, 0.05), 0.95))).fill()
        if kind == "gradFill":
            stops = sorted(
                (
                    (number(stop.get("pos")) / 100000, adjusted(self.palette.color(stop, placeholder)))
                    for stop in element.iterfind(f"{A}gsLst/{A}gs")
                ),
                key=lambda item: item[0],
            )
            stops = [(offset, color) for offset, color in stops if color is not None]
            if not stops:
                return 'fill="none"'
            if len(stops) == 1:
                return stops[0][1].fill()
            ident = canvas.ident()
            marks = "".join(
                f'<stop offset="{num(min(max(offset, 0), 1))}" stop-color="{color.hex()}"'
                + (f' stop-opacity="{num(color.alpha)}"' if color.alpha < 0.999 else "")
                + "/>"
                for offset, color in stops
            )
            path = element.find(A + "path")
            if path is not None:
                focus = path.find(A + "fillToRect")
                fx = (
                    number(None if focus is None else focus.get("l"), 50000)
                    + 100000
                    - number(None if focus is None else focus.get("r"), 50000)
                ) / 200000
                fy = (
                    number(None if focus is None else focus.get("t"), 50000)
                    + 100000
                    - number(None if focus is None else focus.get("b"), 50000)
                ) / 200000
                canvas.defs.append(
                    f'<radialGradient id="{ident}" cx="{num(fx)}" cy="{num(fy)}" r="0.71">{marks}</radialGradient>'
                )
            else:
                lin = element.find(A + "lin")
                angle = math.radians(number(None if lin is None else lin.get("ang")) / 60000)
                dx, dy = math.cos(angle) / 2, math.sin(angle) / 2
                canvas.defs.append(
                    f'<linearGradient id="{ident}" x1="{num(0.5 - dx)}" y1="{num(0.5 - dy)}" '
                    f'x2="{num(0.5 + dx)}" y2="{num(0.5 + dy)}">{marks}</linearGradient>'
                )
            return f'fill="url(#{ident})"'
        if kind == "blipFill":
            blip = element.find(A + "blip")
            ident = None if blip is None else (blip.get(R + "embed") or blip.get(R + "link"))
            found = self.source(ident) if ident else None
            if not isinstance(found, tuple):
                return 'fill="#F2F2F2"'
            pattern = canvas.ident()
            # The picture fills the shape's bounding box: a pattern of one tile in its units.
            canvas.defs.append(
                f'<pattern id="{pattern}" patternContentUnits="objectBoundingBox" width="1" height="1">'
                f"{self.image(canvas, found, element, 1, 1)}</pattern>"
            )
            return f'fill="url(#{pattern})"'
        return 'fill="none"'

    # DrawingML text --------------------------------------------------------------------------

    def drawing_text(self, body, color):
        """(XHTML, plain text) of a DrawingML text body, as SmartArt shapes carry it."""
        autofit = child(child(body, "bodyPr"), "normAutofit")
        scale = number(None if autofit is None else autofit.get("fontScale"), 100000) / 100000
        styles = child(body, "lstStyle")
        html, plain = [], []
        for paragraph in body.findall(A + "p"):
            properties = paragraph.find(A + "pPr")
            level = int(number(None if properties is None else properties.get("lvl"), 0))
            defaults = [
                found
                for found in (
                    None if styles is None else styles.find(f"{A}lvl{level + 1}pPr/{A}defRPr"),
                    None if properties is None else properties.find(A + "defRPr"),
                )
                if found is not None
            ]
            runs, words = [], []
            for item in paragraph:
                kind = local(item.tag)
                if kind == "br":
                    runs.append("<br/>")
                    words.append(" ")
                elif kind in ("r", "fld"):
                    text = "".join(found.text or "" for found in item.iter(A + "t"))
                    if not text:
                        continue
                    style = self.run_css(
                        defaults + [found for found in (item.find(A + "rPr"),) if found is not None], scale
                    )
                    runs.append(f'<span style="{attribute(style)}">{escape(text)}</span>' if style else escape(text))
                    words.append(text)
            bullet = "" if properties is None else self.bullet(properties)
            align = ALIGNS.get(None if properties is None else properties.get("algn"))
            look = css(
                [
                    ("text-align", align),
                    (
                        "margin-left",
                        points(emu(properties.get("marL")) * 0.75)
                        if properties is not None and properties.get("marL")
                        else None,
                    ),
                ]
            )
            size = self.run_css(
                defaults + [found for found in (paragraph.find(A + "endParaRPr"),) if found is not None], scale
            )
            content = "".join(runs) or "<br/>"
            html.append(
                f'<p style="{attribute(";".join(part for part in (look, size) if part))}">{bullet}{content}</p>'
            )
            plain.append("".join(words))
        return "".join(html), " ".join(" ".join(plain).split())

    def run_css(self, layers, scale):
        """CSS of DrawingML run properties, the later layers over the earlier."""
        found = {}
        for layer in layers:
            for name in ("sz", "b", "i", "u", "strike", "baseline"):
                if layer.get(name) is not None:
                    found[name] = layer.get(name)
            fill = child(layer, "solidFill")
            if fill is not None:
                found["color"] = self.palette.color(fill)
            for script in ("latin", "ea"):
                face = child(layer, script)
                if face is not None and face.get("typeface"):
                    found[script] = self.palette.font(face.get("typeface"))
        size = number(found.get("sz"), 0) / 100 * scale
        return css(
            [
                ("font-family", font_family([found.get("latin"), found.get("ea")]) or None),
                ("font-size", points(size) if size > 0 else None),
                ("font-weight", "bold" if on(found.get("b")) else None),
                ("font-style", "italic" if on(found.get("i")) else None),
                (
                    "text-decoration",
                    " ".join(
                        part
                        for part, yes in (
                            ("underline", found.get("u") not in (None, "none")),
                            ("line-through", found.get("strike") not in (None, "noStrike")),
                        )
                        if yes
                    )
                    or None,
                ),
                (
                    "vertical-align",
                    "super"
                    if number(found.get("baseline")) > 0
                    else "sub"
                    if number(found.get("baseline")) < 0
                    else None,
                ),
                ("color", found["color"].hex() if found.get("color") is not None else None),
            ]
        )

    @staticmethod
    def bullet(properties):
        if properties.find(A + "buNone") is not None:
            return ""
        character = properties.find(A + "buChar")
        if character is not None and character.get("char"):
            return f"{escape(character.get('char'))} "
        return ""


# --- Text boxes the page draws itself -------------------------------------------------------------


def color_css(color):
    """A CSS color of a Color, with its opacity."""
    if color is None:
        return None
    if color.alpha >= 0.999:
        return color.hex()
    return (
        "rgba("
        + ",".join(str(round(min(max(value, 0.0), 1.0) * 255)) for value in (color.r, color.g, color.b))
        + f",{num(color.alpha)})"
    )


def box_css(element, palette, w, h):
    """CSS of a Word text box (wps:wsp) the page can draw itself: an unrotated rectangle or rounded
    rectangle filled and outlined in plain colors or not at all. None for a shape to paint."""
    properties, style = child(element, "spPr"), child(element, "style")
    transform = child(properties, "xfrm")
    if transform is not None and (
        number(transform.get("rot")) or on(transform.get("flipH")) or on(transform.get("flipV"))
    ):
        return None
    preset = child(properties, "prstGeom")
    shape = preset.get("prst", "rect") if preset is not None else "rect"
    if child(properties, "custGeom") is not None or shape not in ("rect", "roundRect"):
        return None
    fill = palette.fill(properties, style)
    if fill is not None and local(fill[0].tag) not in ("solidFill", "noFill"):
        return None
    line = palette.line(properties, style)
    if line is not None and (line.head or line.tail):
        return None
    background = palette.color(fill[0], fill[1]) if fill is not None and local(fill[0].tag) == "solidFill" else None
    body = child(element, "bodyPr")
    insets = [
        emu(None if body is None else body.get(name), default)
        for name, default in (("tIns", 4.8), ("rIns", 9.6), ("bIns", 4.8), ("lIns", 9.6))
    ]
    text = palette.color(child(style, "fontRef"))
    radius = 0.0
    if shape == "roundRect":
        adjust = next(
            (number(item.get("fmla", "").split()[-1], 16667) for item in preset.iterfind(f"{A}avLst/{A}gd")), 16667
        )
        radius = min(w, h) * min(max(adjust, 0), 50000) / 100000
    border = (
        f"{num(max(line.width, 0.5))}px {'dashed' if line.dash else 'solid'} {color_css(line.color)}"
        if line is not None
        else "none"
    )
    return css(
        [
            ("width", f"{num(w)}px" if w > 0 else None),
            ("max-width", "100%"),
            ("min-height", f"{num(h)}px" if h > 0 else None),
            ("box-sizing", "border-box"),
            ("display", "flex"),
            ("flex-direction", "column"),
            ("justify-content", ANCHORS.get(body.get("anchor") if body is not None else "t", "flex-start")),
            ("padding", " ".join(f"{num(max(value, 0))}px" for value in insets)),
            ("background-color", color_css(background)),
            ("border", border),
            ("border-radius", f"{num(radius)}px" if radius else None),
            ("color", text.hex() if text is not None else None),
        ]
    )
