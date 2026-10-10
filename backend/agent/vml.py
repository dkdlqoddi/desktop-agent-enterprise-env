"""VML shapes as SVG, for the drawings of older Word documents.

Word writes the shapes of .doc files, of documents in compatibility mode and of web pages in VML: a
rectangle, rounded rectangle, oval, line, polyline, curve or arc, a group of them, or a shape whose path
and formulas come with it or with the shape type it names. They are painted on the Canvas of drawing.py,
with their fill, outline, dashes, arrowheads, pictures and text; shadows, 3-D and other effects are left
out. WordArt keeps its text in the shape's fill color."""

import math
import re
from html import escape

from . import images
from .drawing import (
    NAMED_COLORS,
    SYSTEM_COLORS,
    Color,
    Line,
    Matrix,
    arc_tangent,
    arrowhead,
    color_css,
    missing_picture,
    num,
    placed,
    rgb_hex,
    text_box,
)
from .html_document import css, font_family

V = "{urn:schemas-microsoft-com:vml}"
O = "{urn:schemas-microsoft-com:office:office}"  # noqa: E741 - named by its prefix (o:), as V, R and W are
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SHAPES = {
    V + name for name in ("shape", "rect", "roundrect", "oval", "line", "polyline", "curve", "arc", "image", "group")
}
MAX_DEPTH = 32
UNITS = {"pt": 4 / 3, "px": 1.0, "in": 96.0, "cm": 96 / 2.54, "mm": 96 / 25.4, "pc": 16.0, "pi": 16.0, "emu": 1 / 9525}
VML_DASHES = {
    "shortdash": (3, 1),
    "shortdot": (1, 1),
    "shortdashdot": (3, 1, 1, 1),
    "shortdashdotdot": (3, 1, 1, 1, 1, 1),
    "dot": (1, 3),
    "dash": (4, 3),
    "longdash": (8, 3),
    "dashdot": (4, 3, 1, 3),
    "longdashdot": (8, 3, 1, 3),
    "longdashdotdot": (8, 3, 1, 3, 1, 3),
}
ARROWS = {"block": "triangle", "classic": "stealth", "open": "arrow", "oval": "oval", "diamond": "diamond"}
ARROW_WIDTHS = {"narrow": "sm", "medium": "med", "wide": "lg"}
ARROW_LENGTHS = {"short": "sm", "medium": "med", "long": "lg"}
TWO_LETTER = (
    "nf",
    "ns",
    "ae",
    "al",
    "at",
    "ar",
    "wa",
    "wr",
    "qx",
    "qy",
    "qb",
    "ha",
    "hb",
    "hc",
    "hd",
    "he",
    "hf",
    "hg",
    "hh",
    "hi",
)
PATH_TOKEN = re.compile(r"[a-z]+|[@#]\d+|[-+]?(?:\d+\.?\d*|\.\d+)|,", re.I)
FD = 65536  # VML angles and fixed-point numbers are in 1/65536


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def style_of(text):
    """The properties of a VML style attribute, names in lower case."""
    found = {}
    for part in (text or "").split(";"):
        name, _, value = part.partition(":")
        if name.strip():
            found[name.strip().lower()] = value.strip()
    return found


def length(text, default=0.0, unit=1.0):
    """Pixels of a CSS length such as 72pt; a bare number is in `unit` pixels (px, or a group's units)."""
    match = re.fullmatch(r"\s*([-+]?(?:\d+\.?\d*|\.\d+))\s*([a-z]*)\s*", str(text or ""), re.I)
    if not match:
        return default
    value, name = float(match.group(1)), match.group(2).lower()
    if not name:
        return value * unit
    return value * UNITS[name] if name in UNITS else default


def fraction(text, default=0.0):
    """A VML number that may be fixed-point (32768f), a percentage or a plain fraction."""
    text = str(text or "").strip()
    try:
        if text.endswith("f"):
            return float(text[:-1]) / FD
        if text.endswith("%"):
            return float(text[:-1]) / 100
        return float(text)
    except ValueError:
        return default


def pair(text, default=(0.0, 0.0)):
    parts = [part.strip() for part in str(text or "").split(",")]
    if len(parts) < 2:
        return default
    try:
        return float(parts[0] or 0), float(parts[1] or 0)
    except ValueError:
        return default


def color_of(text, base=None):
    """A Color of a VML color: #RRGGBB, a name, "black [3213]", or "fill darken(118)" of `base`."""
    text = str(text or "").strip()
    relative = re.search(r"(darken|lighten)\(\s*(\d+)\s*\)", text, re.I)
    if relative:
        base = base or Color(1, 1, 1)
        amount = min(int(relative.group(2)), 255) / 255
        if relative.group(1).lower() == "darken":
            return Color(base.r * amount, base.g * amount, base.b * amount)
        return Color(*(1 - (1 - value) * amount for value in (base.r, base.g, base.b)))
    text = re.sub(r"\[[^\]]*\]", "", text).strip()
    rgb = (
        rgb_hex(text)
        if text.startswith("#")
        else rgb_hex(
            NAMED_COLORS.get(text.lower())
            or next((value for name, value in SYSTEM_COLORS.items() if name.lower() == text.lower()), None)
        )
    )
    return None if rgb is None else Color(*rgb)


def path_tokens(text):
    """[(command, [parameters])] of a VML path; an omitted parameter is 0."""
    commands = []
    for match in PATH_TOKEN.finditer(text or ""):
        token = match.group(0)
        if token[0].isalpha():
            if commands and commands[-1][2]:
                commands[-1][1].append("0")  # a parameter left empty before the command
            letters = token.lower()
            while letters:
                name = letters[:2] if letters[:2] in TWO_LETTER else letters[:1]
                commands.append([name, [], False])
                letters = letters[len(name) :]
        elif not commands:
            continue
        elif token == ",":
            if commands[-1][2] or not commands[-1][1]:
                commands[-1][1].append("0")
            commands[-1][2] = True
        else:
            commands[-1][1].append(token)
            commands[-1][2] = False
    if commands and commands[-1][2]:
        commands[-1][1].append("0")
    return [(name, parameters) for name, parameters, _ in commands]


class Formulas:
    """The formulas (v:formulas) and adjust values of a VML shape type, evaluated for one shape."""

    def __init__(self, equations, adjust, size, origin):
        self.adjust = adjust
        width, height = size
        self.names = {
            "width": width,
            "height": height,
            "xcenter": origin[0] + width / 2,
            "ycenter": origin[1] + height / 2,
            "xlimo": 0.0,
            "ylimo": 0.0,
            "hasstroke": 1.0,
            "hasfill": 1.0,
            "pixellinewidth": 1.0,
            "pixelwidth": width,
            "pixelheight": height,
            "emuwidth": width * 9525,
            "emuheight": height * 9525,
            "emuwidth2": width * 9525 / 2,
            "emuheight2": height * 9525 / 2,
            "linedrawn": 1.0,
        }
        self.values = []
        for equation in equations:
            try:
                self.values.append(self.evaluate(equation))
            except (ValueError, ZeroDivisionError, OverflowError):
                self.values.append(0.0)

    def value(self, token):
        token = str(token).strip()
        if token.startswith("@"):
            index = int(token[1:])
            return self.values[index] if index < len(self.values) else 0.0
        if token.startswith("#"):
            index = int(token[1:])
            return self.adjust[index] if index < len(self.adjust) else 0.0
        if token.lower() in self.names:
            return self.names[token.lower()]
        try:
            return float(token)
        except ValueError:
            return 0.0

    def evaluate(self, equation):
        parts = (equation or "val 0").split()
        operator, arguments = parts[0].lower(), [self.value(item) for item in parts[1:]]
        a, b, c = (arguments + [0.0, 0.0, 0.0])[:3]

        def angle(value):
            return math.radians(value / FD)

        if operator == "sum":
            return a + b - c
        if operator in ("product", "prod"):
            return a * b / c if c else 0.0
        if operator == "mid":
            return (a + b) / 2
        if operator == "abs":
            return abs(a)
        if operator == "min":
            return min(a, b)
        if operator == "max":
            return max(a, b)
        if operator == "if":
            return b if a > 0 else c
        if operator == "mod":
            return math.sqrt(a * a + b * b + c * c)
        if operator == "atan2":
            return math.degrees(math.atan2(b, a)) * FD
        if operator == "sin":
            return a * math.sin(angle(b))
        if operator == "cos":
            return a * math.cos(angle(b))
        if operator == "tan":
            return a * math.tan(angle(b))
        if operator == "cosatan2":
            return a * math.cos(math.atan2(c, b))
        if operator == "sinatan2":
            return a * math.sin(math.atan2(c, b))
        if operator == "sqrt":
            return math.sqrt(max(a, 0.0))
        if operator == "sumangle":
            return a + b * FD - c * FD
        if operator == "ellipse":
            return c * math.sqrt(max(1 - (a / b) ** 2, 0.0)) if b else 0.0
        return a  # val


class Trace:
    """SVG path data of a VML path, in a shape's own pixels, split by what is filled and what is stroked."""

    def __init__(self, mapping):
        self.map = mapping
        self.filled, self.stroked = [], []
        self.current, self.points = [], []
        self.fill, self.stroke = True, True
        self.x = self.y = 0.0
        self.begin = (0.0, 0.0)
        self.start = self.end = None
        self.closed = False

    def emit(self, text):
        self.current.append(text)

    def flush(self):
        if self.current:
            data = "".join(self.current)
            if self.fill:
                self.filled.append(data)
            if self.stroke:
                self.stroked.append(data)
        self.current, self.fill, self.stroke = [], True, True

    def move(self, x, y):
        self.x, self.y = x, y
        self.begin = (x, y)
        px, py = self.map(x, y)
        self.emit(f"M{num(px)} {num(py)}")

    def line(self, x, y):
        start = self.map(self.x, self.y)
        px, py = self.map(x, y)
        self.emit(f"L{num(px)} {num(py)}")
        self.reached(start, (px - start[0], py - start[1]), (px - start[0], py - start[1]), (px, py))
        self.x, self.y = x, y

    def curve(self, *values):
        start = self.map(self.x, self.y)
        points = [self.map(values[index], values[index + 1]) for index in range(0, len(values), 2)]
        letter = "C" if len(points) == 3 else "Q"
        self.emit(letter + " ".join(f"{num(px)} {num(py)}" for px, py in points))
        first = next((point for point in points if point != start), points[-1])
        last = points[-2] if len(points) > 1 and points[-2] != points[-1] else start
        self.reached(
            start,
            (first[0] - start[0], first[1] - start[1]),
            (points[-1][0] - last[0], points[-1][1] - last[1]),
            points[-1],
        )
        self.x, self.y = values[-2], values[-1]

    def arc(self, cx, cy, rx, ry, start, sweep, connect):
        """An elliptical arc from parametric angle `start` (radians) through `sweep`, clockwise when positive."""
        if not rx or not ry:
            return
        begin = (cx + rx * math.cos(start), cy + ry * math.sin(start))
        if connect and self.current:
            self.line(*begin)
        else:
            self.move(*begin)
        pieces = 2 if abs(sweep) >= 2 * math.pi - 1e-6 else 1
        sign = 1.0 if sweep > 0 else -1.0
        for piece in range(1, pieces + 1):
            angle = start + sweep * piece / pieces
            target = (cx + rx * math.cos(angle), cy + ry * math.sin(angle))
            origin = self.map(self.x, self.y)
            px, py = self.map(*target)
            scale_x = abs(self.map(1, 0)[0] - self.map(0, 0)[0])
            scale_y = abs(self.map(0, 1)[1] - self.map(0, 0)[1])
            large = 1 if abs(sweep / pieces) > math.pi else 0
            self.emit(f"A{num(rx * scale_x)} {num(ry * scale_y)} 0 {large} {1 if sweep > 0 else 0} {num(px)} {num(py)}")
            self.reached(
                origin,
                arc_tangent(rx * scale_x, ry * scale_y, start + sweep * (piece - 1) / pieces, sign),
                arc_tangent(rx * scale_x, ry * scale_y, angle, sign),
                (px, py),
            )
            self.x, self.y = target

    def close(self):
        self.emit("Z")
        self.x, self.y = self.begin
        self.closed = True

    def reached(self, origin, first, last, target):
        if self.start is None:
            self.start = (origin, (-first[0], -first[1]))
        self.end = (target, last)
        self.closed = False


class Vml:
    """Paints VML shapes on a Canvas. `picture(ident)` gives (content type, bytes, size) or a marker text;
    `text(content)` gives (XHTML, plain text) of a w:txbxContent; `types` maps shape type ids to v:shapetype."""

    def __init__(self, picture, text, types, base=""):
        self.source, self.word_text, self.types, self.base = picture, text, types, base

    @staticmethod
    def size(element):
        """(width, height) px of a top-level shape from its style."""
        style = style_of(element.get("style"))
        return length(style.get("width")), length(style.get("height"))

    def top(self, canvas, element, x, y):
        """A shape at (x, y) px: its own size, or a line or polyline drawn by its points from there."""
        w, h = self.size(element)
        self.paint(canvas, element, Matrix(), (x, y, w, h), 1.0, 0)

    def paint(self, canvas, element, matrix, frame, unit, depth):
        """A shape in `frame` (x, y, w, h); lines, curves and polylines use their own points, in `unit` px."""
        if depth > MAX_DEPTH:
            return
        style = style_of(element.get("style"))
        if style.get("visibility") == "hidden" or style.get("display") == "none":
            return
        kind = local(element.tag)
        x, y, w, h = frame
        rotation = style.get("rotation", "0")
        rotation = fraction(rotation[:-2]) / FD if rotation.endswith("fd") else fraction(rotation)
        flip = style.get("flip", "").lower()
        local_matrix = placed(matrix, x, y, w, h, rotation, "x" in flip, "y" in flip)
        if kind == "group":
            self.group(canvas, element, local_matrix, w, h, depth)
            return
        shape_type = self.types.get((element.get("type") or "").lstrip("#")) if kind == "shape" else None

        def attr(name, default=None):
            value = element.get(name)
            if value is None and shape_type is not None:
                value = shape_type.get(name)
            return default if value is None else value

        def part(name):
            found = element.find(V + name)
            if found is None and shape_type is not None:
                found = shape_type.find(V + name)
            return found

        if kind in ("line", "curve", "polyline"):
            # Their points are in the coordinates the shape sits in, so they ignore the frame.
            local_matrix, w, h, trace = self.points(element, kind, matrix, x, y, unit)
        else:
            trace = self.outline(element, kind, attr, part, w, h)
        fill_part, stroke_part = part("fill"), part("stroke")
        fill = None
        # Open lines have no area; a polyline, curve or arc is filled only when it says so.
        if kind not in ("line", "polyline", "curve", "arc") or (
            kind != "line" and (element.get("filled") or element.get("fillcolor") or fill_part is not None)
        ):
            fill = self.fill(attr, fill_part)
        line = self.line(attr, stroke_part)
        picture = element if kind == "image" else part("imagedata")
        textpath = part("textpath")
        if textpath is not None and on(textpath.get("on"), False):
            self.wordart(canvas, local_matrix, w, h, textpath, fill, line)
            canvas.cover(local_matrix, 0, 0, w, h)
            return
        if picture is not None:
            ident = picture.get(R + "id") or picture.get(O + "relid") or picture.get(R + "pict")
            found = self.source(ident) if ident else "[그림]"
            title = " ".join((picture.get(O + "title") or element.get("alt") or "").split())
            if title:
                canvas.labels.append(title)
            if isinstance(found, tuple):
                kind_, data, _ = found
                canvas.parts.append(
                    f'<image href="{images.data_uri(kind_, data)}" x="0" y="0" width="{num(w)}" '
                    f'height="{num(h)}" preserveAspectRatio="none" transform="{local_matrix.svg()}"/>'
                )
            else:
                missing_picture(canvas, local_matrix, w, h, found)
            fill = None  # the picture is what fills the shape
        if trace is not None:
            if fill is not None:
                for data in trace.filled:
                    canvas.parts.append(f'<path d="{data}" {fill} stroke="none" transform="{local_matrix.svg()}"/>')
            if line is not None:
                for data in trace.stroked:
                    canvas.parts.append(f'<path d="{data}" fill="none" {line.svg()} transform="{local_matrix.svg()}"/>')
                heads = [
                    arrowhead(end, line, *place)
                    for end, place in ((line.head, trace.start), (line.tail, trace.end))
                    if end and place and not trace.closed
                ]
                if heads:
                    canvas.parts.append(f'<g transform="{local_matrix.svg()}">{"".join(heads)}</g>')
        canvas.cover(local_matrix, 0, 0, w, h, line.reach() if line is not None else 0)
        self.text(canvas, element, part, attr, local_matrix, w, h, fill)

    def group(self, canvas, element, matrix, w, h, depth):
        """A group: its shapes in its own coordinate space (coordsize, coordorigin) stretched over w by h."""
        size = pair(element.get("coordsize"), (1000.0, 1000.0))
        origin = pair(element.get("coordorigin"), (0.0, 0.0))
        scale_x = w / size[0] if size[0] else 1.0
        scale_y = h / size[1] if size[1] else 1.0
        for item in element:
            if item.tag not in SHAPES:
                continue
            style = style_of(item.get("style"))
            left = length(style.get("left"), unit=1.0) + length(style.get("margin-left"), unit=1.0)
            top = length(style.get("top"), unit=1.0) + length(style.get("margin-top"), unit=1.0)
            frame = (
                (left - origin[0]) * scale_x,
                (top - origin[1]) * scale_y,
                length(style.get("width"), unit=1.0) * scale_x,
                length(style.get("height"), unit=1.0) * scale_y,
            )
            inner = (
                matrix @ Matrix(scale_x, 0, 0, scale_y, -origin[0] * scale_x, -origin[1] * scale_y)
                if local(item.tag) in ("line", "polyline", "curve")
                else matrix
            )
            self.paint(canvas, item, inner, frame, 1.0, depth + 1)

    def points(self, element, kind, matrix, x, y, unit):
        """(transform, width, height, Trace) of a line, curve or polyline, drawn by its own points."""

        def at(text):
            parts = [part.strip() for part in str(text or "").split(",")]
            parts += ["0"] * (2 - len(parts))
            return length(parts[0] or "0", unit=unit), length(parts[1] or "0", unit=unit)

        trace = Trace(lambda px, py: (px, py))
        if kind == "polyline":
            values = [part for part in re.split(r"[\s,]+", (element.get("points") or "").strip()) if part]
            found = [
                (length(values[index], unit=unit), length(values[index + 1], unit=unit))
                for index in range(0, len(values) - 1, 2)
            ]
            found = [(px + x, py + y) for px, py in found]
        elif kind == "curve":
            found = [at(element.get(name)) for name in ("from", "control1", "control2", "to")]
        else:
            found = [at(element.get("from", "0,0")), at(element.get("to", "10,10"))]
        if not found:
            return matrix, 0.0, 0.0, None
        trace.move(*found[0])
        if kind == "curve":
            trace.curve(*found[1], *found[2], *found[3])
        else:
            for point in found[1:]:
                trace.line(*point)
        trace.flush()
        xs, ys = [px for px, _ in found], [py for _, py in found]
        # The shape's box is the extent of its points; the transform keeps the points where they are.
        left, top = min(xs), min(ys)
        shifted = Trace(lambda px, py: (px - left, py - top))
        shifted.move(*found[0])
        if kind == "curve":
            shifted.curve(*found[1], *found[2], *found[3])
        else:
            for point in found[1:]:
                shifted.line(*point)
        shifted.flush()
        return matrix @ Matrix(e=left, f=top), max(xs) - left, max(ys) - top, shifted

    def outline(self, element, kind, attr, part, w, h):
        """The Trace of a shape's geometry in its own pixels, or None for a shape without one."""
        if kind == "rect" or kind == "image":
            trace = Trace(lambda px, py: (px, py))
            trace.move(0, 0), trace.line(w, 0), trace.line(w, h), trace.line(0, h), trace.close()
            trace.flush()
            return trace
        if kind == "roundrect":
            radius = min(w, h) * min(max(fraction(element.get("arcsize"), 0.2), 0.0), 0.5)
            trace = Trace(lambda px, py: (px, py))
            trace.move(radius, 0), trace.line(w - radius, 0)
            trace.arc(w - radius, radius, radius, radius, -math.pi / 2, math.pi / 2, True)
            trace.line(w, h - radius), trace.arc(w - radius, h - radius, radius, radius, 0, math.pi / 2, True)
            trace.line(radius, h), trace.arc(radius, h - radius, radius, radius, math.pi / 2, math.pi / 2, True)
            trace.line(0, radius), trace.arc(radius, radius, radius, radius, math.pi, math.pi / 2, True)
            trace.close()
            trace.flush()
            return trace
        if kind == "oval":
            trace = Trace(lambda px, py: (px, py))
            trace.arc(w / 2, h / 2, w / 2, h / 2, 0, 2 * math.pi, False)
            trace.close()
            trace.flush()
            return trace
        if kind == "arc":
            # Angles from 12 o'clock, clockwise.
            start = math.radians(fraction(element.get("startangle"), 0.0) - 90)
            end = math.radians(fraction(element.get("endangle"), 90.0) - 90)
            trace = Trace(lambda px, py: (px, py))
            trace.arc(w / 2, h / 2, w / 2, h / 2, start, (end - start) % (2 * math.pi) or 2 * math.pi, False)
            trace.flush()
            return trace
        path = part("path")
        data = attr("path") or (None if path is None else path.get("v"))
        if not data:
            return None
        size = pair(attr("coordsize"), (21600.0, 21600.0))
        origin = pair(attr("coordorigin"), (0.0, 0.0))
        adjust = self.adjust(attr, element)
        formulas = part("formulas")
        equations = [item.get("eqn") for item in ([] if formulas is None else formulas.findall(V + "f"))]
        values = Formulas(equations, adjust, size, origin)
        scale_x = w / size[0] if size[0] else 1.0
        scale_y = h / size[1] if size[1] else 1.0
        trace = Trace(lambda px, py: ((px - origin[0]) * scale_x, (py - origin[1]) * scale_y))
        self.walk(trace, path_tokens(data), values)
        trace.flush()
        return trace

    def adjust(self, attr, element):
        """Adjust values (#0, #1, …): the shape's own over its type's defaults."""
        shape_type = self.types.get((element.get("type") or "").lstrip("#"))
        defaults = [
            part.strip() for part in ((shape_type.get("adj") if shape_type is not None else "") or "").split(",")
        ]
        own = [part.strip() for part in (element.get("adj") or "").split(",")]
        found = []
        for index in range(max(len(defaults), len(own))):
            text = own[index] if index < len(own) and own[index] else defaults[index] if index < len(defaults) else ""
            found.append(fraction(text, 0.0) if text else 0.0)
        return found

    @staticmethod
    def walk(trace, commands, values):
        def numbers(parameters):
            return [values.value(item) for item in parameters]

        for name, parameters in commands:
            found = numbers(parameters)
            if name == "m":
                for index in range(0, len(found) - 1, 2):
                    (trace.move if index == 0 else trace.line)(found[index], found[index + 1])
            elif name == "l":
                for index in range(0, len(found) - 1, 2):
                    trace.line(found[index], found[index + 1])
            elif name == "c":
                for index in range(0, len(found) - 5, 6):
                    trace.curve(*found[index : index + 6])
            elif name == "t":
                trace.move(trace.x + (found[0] if found else 0), trace.y + (found[1] if len(found) > 1 else 0))
            elif name == "r":
                for index in range(0, len(found) - 1, 2):
                    trace.line(trace.x + found[index], trace.y + found[index + 1])
            elif name == "v":
                for index in range(0, len(found) - 5, 6):
                    x0, y0 = trace.x, trace.y
                    trace.curve(
                        *(
                            value + (x0 if position % 2 == 0 else y0)
                            for position, value in enumerate(found[index : index + 6])
                        )
                    )
            elif name == "x":
                trace.close()
            elif name == "e":
                trace.flush()
            elif name == "nf":
                trace.fill = False
            elif name == "ns":
                trace.stroke = False
            elif name in ("ae", "al"):
                for index in range(0, len(found) - 5, 6):
                    cx, cy, rx, ry, start, sweep = found[index : index + 6]
                    trace.arc(
                        cx, cy, rx, ry, math.radians(start / FD), math.radians(sweep / FD), name == "ae" or index > 0
                    )
            elif name in ("at", "ar", "wa", "wr"):
                for index in range(0, len(found) - 7, 8):
                    left, top, right, bottom, x1, y1, x2, y2 = found[index : index + 8]
                    cx, cy, rx, ry = (
                        (left + right) / 2,
                        (top + bottom) / 2,
                        abs(right - left) / 2,
                        abs(bottom - top) / 2,
                    )
                    if not rx or not ry:
                        continue
                    start = math.atan2((y1 - cy) / ry, (x1 - cx) / rx)
                    end = math.atan2((y2 - cy) / ry, (x2 - cx) / rx)
                    if name in ("wa", "wr"):
                        sweep = (end - start) % (2 * math.pi) or 2 * math.pi
                    else:
                        sweep = -((start - end) % (2 * math.pi) or 2 * math.pi)
                    trace.arc(cx, cy, rx, ry, start, sweep, name in ("at", "wa") or index > 0)
            elif name in ("qx", "qy"):
                horizontal = name == "qx"
                for index in range(0, len(found) - 1, 2):
                    x, y = found[index], found[index + 1]
                    x0, y0 = trace.x, trace.y
                    if horizontal:
                        sweep = 1 if (x - x0) * (y - y0) > 0 else 0
                    else:
                        sweep = 0 if (x - x0) * (y - y0) > 0 else 1
                    origin = trace.map(x0, y0)
                    px, py = trace.map(x, y)
                    rx, ry = abs(px - origin[0]), abs(py - origin[1])
                    if rx and ry:
                        trace.emit(f"A{num(rx)} {num(ry)} 0 0 {sweep} {num(px)} {num(py)}")
                        first = (px - origin[0], 0.0) if horizontal else (0.0, py - origin[1])
                        last = (0.0, py - origin[1]) if horizontal else (px - origin[0], 0.0)
                        trace.reached(origin, first, last, (px, py))
                        trace.x, trace.y = x, y
                    else:
                        trace.line(x, y)
                    horizontal = not horizontal
            elif name == "qb":
                points = [(found[index], found[index + 1]) for index in range(0, len(found) - 1, 2)]
                # A quadratic spline: on-curve points sit halfway between its control points.
                for index in range(len(points) - 1):
                    control, after = points[index], points[index + 1]
                    if index < len(points) - 2:
                        after = ((control[0] + after[0]) / 2, (control[1] + after[1]) / 2)
                    trace.curve(*control, *after)
        return trace

    def fill(self, attr, part):
        """SVG fill attributes of a shape, or None when it is not filled."""
        if not on(attr("filled", "t"), True) or (part is not None and not on(part.get("on"), True)):
            return None
        color = color_of(attr("fillcolor", "white")) or Color(1, 1, 1)
        if part is not None and part.get("color"):
            color = color_of(part.get("color"), color) or color
        opacity = fraction(part.get("opacity"), 1.0) if part is not None and part.get("opacity") else 1.0
        kind = (part.get("type") or "solid").lower() if part is not None else "solid"
        if kind.startswith("gradient") and part.get("color2"):
            other = color_of(part.get("color2"), color) or color
            color = color.mixed(other, 0.5)
        elif kind == "pattern" and part.get("color2"):
            other = color_of(part.get("color2"), color) or color
            color = color.mixed(other, 0.5)
        color.alpha = min(max(opacity, 0.0), 1.0)
        return color.fill()

    def line(self, attr, part):
        """The Line of a shape's stroke, or None when it is not stroked."""
        if not on(attr("stroked", "t"), True) or (part is not None and not on(part.get("on"), True)):
            return None
        color = color_of(attr("strokecolor", "black")) or Color(0, 0, 0)
        if part is not None and part.get("color"):
            color = color_of(part.get("color"), color) or color
        if part is not None and part.get("opacity"):
            color.alpha = min(max(fraction(part.get("opacity"), 1.0), 0.0), 1.0)
        weight = (part.get("weight") if part is not None and part.get("weight") else None) or attr(
            "strokeweight", "0.75pt"
        )
        width = length(weight, 1.0, unit=4 / 3)  # a bare stroke weight is in points
        dash = None
        style = ((part.get("dashstyle") if part is not None else None) or "solid").strip().lower()
        if style in VML_DASHES:
            dash = VML_DASHES[style]
        elif re.fullmatch(r"[\d.\s]+", style or ""):
            dash = tuple(float(value) for value in style.split()) or None

        def end(which):
            kind = ARROWS.get(((part.get(f"{which}arrow") if part is not None else None) or "none").lower())
            if not kind:
                return None
            return (
                kind,
                ARROW_WIDTHS.get((part.get(f"{which}arrowwidth") or "medium").lower(), "med"),
                ARROW_LENGTHS.get((part.get(f"{which}arrowlength") or "medium").lower(), "med"),
            )

        cap = {"round": "round", "square": "square", "flat": "butt"}.get(
            ((part.get("endcap") if part is not None else None) or "").lower()
        )
        join = ((part.get("joinstyle") if part is not None else None) or "").lower() or None
        return Line(color, width, dash, cap, join, end("start"), end("end"))

    def text(self, canvas, element, part, attr, matrix, w, h, fill):
        """The text box of a shape (v:textbox with w:txbxContent) in its text rectangle."""
        box = part("textbox")
        content = None if box is None else box.find(W + "txbxContent")
        if content is None or self.word_text is None:
            return
        html, plain = self.word_text(content)
        if not html:
            return
        canvas.text(plain)
        left, top, right, bottom = 0.0, 0.0, w, h
        path = part("path")
        rectangle = None if path is None else path.get("textboxrect")
        if rectangle:
            size = pair(attr("coordsize"), (21600.0, 21600.0))
            origin = pair(attr("coordorigin"), (0.0, 0.0))
            formulas = part("formulas")
            values = Formulas(
                [item.get("eqn") for item in ([] if formulas is None else formulas.findall(V + "f"))],
                self.adjust(attr, element),
                size,
                origin,
            )
            found = [values.value(item or "0") for item in rectangle.split(";")[0].split(",")]
            if len(found) == 4 and size[0] and size[1]:
                left, right = ((found[0] - origin[0]) * w / size[0], (found[2] - origin[0]) * w / size[0])
                top, bottom = ((found[1] - origin[1]) * h / size[1], (found[3] - origin[1]) * h / size[1])
        insets = [length(value or "0", unit=4 / 3) for value in (box.get("inset") or "").split(",")]
        defaults = (9.6, 4.8, 9.6, 4.8)
        insets = tuple(
            insets[index]
            if index < len(insets) and (box.get("inset") or "").split(",")[index].strip()
            else defaults[index]
            for index in range(4)
        )
        flow = style_of(box.get("style"))
        vertical = "vert" if flow.get("layout-flow") == "vertical" else "horz"
        if flow.get("mso-layout-flow-alt") == "bottom-to-top":
            vertical = "vert270"
        text_box(
            canvas,
            matrix,
            left,
            top,
            right - left,
            bottom - top,
            html,
            insets=insets,
            vertical=vertical,
            base=self.base,
        )

    def wordart(self, canvas, matrix, w, h, textpath, fill, line):
        """WordArt (v:textpath): its text across the shape, in the shape's fill color."""
        text = textpath.get("string") or ""
        if not text.strip():
            return
        canvas.text(text)
        style = style_of(textpath.get("style"))
        family = font_family([style.get("font-family", "").strip("\"'")])
        color = re.search(r'fill="([^"]+)"', fill or "")
        lines = max(text.count("\n") + 1, 1)
        size = max(min(h / lines * 0.8, length(style.get("font-size"), h * 0.8)), 1)
        look = css(
            [
                ("font-family", family or None),
                ("font-size", f"{num(size)}px"),
                ("color", color.group(1) if color else None),
                ("text-align", "center"),
                ("white-space", "pre"),
                ("font-weight", "bold" if style.get("font-weight") == "bold" else None),
                ("font-style", "italic" if style.get("font-style") == "italic" else None),
            ]
        )
        text_box(
            canvas,
            matrix,
            0,
            0,
            w,
            h,
            escape(text).replace("\n", "<br/>"),
            anchor="ctr",
            insets=(0, 0, 0, 0),
            base=look,
        )


def on(text, default=True):
    return default if text is None else str(text).strip().lower() not in ("f", "false", "0", "off")


def box_css(element, types, w, h):
    """CSS of a VML text box the page can draw itself: an unrotated rectangle or rounded rectangle in
    plain colors. None for a shape to paint."""
    kind = local(element.tag)
    shape_type = types.get((element.get("type") or "").lstrip("#"))
    kind_number = element.get(O + "spt") or (shape_type.get(O + "spt") if shape_type is not None else None)
    style = style_of(element.get("style"))
    if style.get("rotation", "0").strip() not in ("0", "") or style.get("flip"):
        return None
    if not (
        kind in ("rect", "roundrect")
        or kind == "shape"
        and (kind_number in ("1", "202") or (not element.get("path") and shape_type is None))
    ):
        return None

    def attr(name, default=None):
        value = element.get(name)
        if value is None and shape_type is not None:
            value = shape_type.get(name)
        return default if value is None else value

    def part(name):
        found = element.find(V + name)
        return shape_type.find(V + name) if found is None and shape_type is not None else found

    fill, stroke, box = part("fill"), part("stroke"), part("textbox")
    if fill is not None and (fill.get("type") or "solid").lower() != "solid":
        return None
    if stroke is not None and any(
        (stroke.get(name) or "none").lower() != "none" for name in ("startarrow", "endarrow")
    ):
        return None
    background = None
    if on(attr("filled", "t")) and (fill is None or on(fill.get("on"), True)):
        background = color_of(
            fill.get("color") if fill is not None and fill.get("color") else attr("fillcolor", "white")
        )
        if background is not None and fill is not None and fill.get("opacity"):
            background.alpha = min(max(fraction(fill.get("opacity"), 1.0), 0.0), 1.0)
    border = "none"
    if on(attr("stroked", "t")) and (stroke is None or on(stroke.get("on"), True)):
        color = color_of(
            stroke.get("color") if stroke is not None and stroke.get("color") else attr("strokecolor", "black")
        )
        weight = (stroke.get("weight") if stroke is not None and stroke.get("weight") else None) or attr(
            "strokeweight", "0.75pt"
        )
        dashed = ((stroke.get("dashstyle") if stroke is not None else None) or "solid").lower() != "solid"
        if color is not None:
            border = (
                f"{num(max(length(weight, 1.0, unit=4 / 3), 0.5))}px {'dashed' if dashed else 'solid'} {color.hex()}"
            )
    values = [part_.strip() for part_ in ((box.get("inset") if box is not None else None) or "").split(",")]
    insets = [
        length(values[index], default, unit=4 / 3) if index < len(values) and values[index] else default
        for index, default in enumerate((9.6, 4.8, 9.6, 4.8))
    ]
    radius = min(w, h) * min(max(fraction(element.get("arcsize"), 0.2), 0.0), 0.5) if kind == "roundrect" else 0.0
    return css(
        [
            ("width", f"{num(w)}px" if w > 0 else None),
            ("max-width", "100%"),
            ("min-height", f"{num(h)}px" if h > 0 else None),
            ("box-sizing", "border-box"),
            ("padding", " ".join(f"{num(insets[index])}px" for index in (1, 2, 3, 0))),
            ("background-color", color_css(background)),
            ("border", border),
            ("border-radius", f"{num(radius)}px" if radius else None),
        ]
    )
