"""Pictures for the HTML pages of imported files.

PNG, JPEG, GIF, BMP, WebP, AVIF, SVG and ICO stay as they are, since the app shows them. TIFF and the
Windows metafiles EMF and WMF become PNG through Pillow; metafiles are drawn by Windows itself (GDI), so
they convert only on Windows. Every picture goes into the page as a data: URI."""

import base64
import io
import re

from .html_document import attribute

WEB = {
    "image/png": "PNG",
    "image/jpeg": "JPEG",
    "image/gif": "GIF",
    "image/bmp": "BMP",
    "image/webp": "WebP",
    "image/avif": "AVIF",
    "image/svg+xml": "SVG",
    "image/x-icon": "ICO",
}
CONVERTED = {"image/tiff": "TIFF", "image/x-emf": "EMF", "image/x-wmf": "WMF"}
# Content types Office writes for the same pictures.
ALIASES = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
    "image/svg": "image/svg+xml",
    "image/vnd.microsoft.icon": "image/x-icon",
    "image/x-ms-bmp": "image/bmp",
    "image/emf": "image/x-emf",
    "image/wmf": "image/x-wmf",
    "image/x-tiff": "image/tiff",
}
# Image files the importer takes, as in the import widget (src/lib/links.ts) and the file picker.
EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".jfif",
    ".gif",
    ".bmp",
    ".webp",
    ".avif",
    ".svg",
    ".ico",
    ".tif",
    ".tiff",
    ".emf",
    ".wmf",
)
MAX_SIDE = 4096  # longest side, in pixels, of a converted picture
MAX_PAGES = 50  # pages of a multi-page TIFF
# Pixels of one page decoded for conversion: decoding holds the whole page in memory before it is made smaller. A 600
# dpi scan of an A4 page has 35 million.
MAX_PIXELS = 50_000_000
# A comment matches one way only, so a file of many comments cannot make the match backtrack for long.
SVG = re.compile(r"(?:<\?xml[^>]*>\s*)?(?:<!--(?:(?!-->).)*-->\s*)*(?:<!DOCTYPE\s+svg[^>]*>\s*)?<svg[\s>]", re.S | re.I)
# A document type definition of an SVG's own (<!DOCTYPE svg [ … ]>) or an entity: no picture needs one, and entities
# can make a small file grow without bounds where it is drawn.
SVG_DTD = re.compile(r"<!DOCTYPE[^>\[]*\[|<!ENTITY", re.I)


class Pages(list):
    """The pages of a converted picture, with what the conversion left out (`notes`, for the page that shows them)."""

    notes = ()


def sniff(data):
    """The content type of picture bytes from their signature, or None."""
    head = bytes(data[:64])
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis"):
        return "image/avif"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "image/tiff"
    if head[:4] == b"\x01\x00\x00\x00" and head[40:44] == b" EMF":
        return "image/x-emf"
    if head[:4] == b"\xd7\xcd\xc6\x9a" or head[:6] in (b"\x01\x00\x09\x00\x00\x03", b"\x02\x00\x09\x00\x00\x03"):
        return "image/x-wmf"
    if head[:4] == b"\x00\x00\x01\x00":
        return "image/x-icon"
    if head[:2] == b"BM" and len(data) > 26:
        return "image/bmp"
    if SVG.match(bytes(data[:4096]).decode("utf-8", "ignore").lstrip("\ufeff \t\r\n")):
        return "image/svg+xml"
    return None


def kind(content_type):
    """PNG, EMF, …: the short name of a content type."""
    content_type = ALIASES.get(content_type, content_type)
    return WEB.get(content_type) or CONVERTED.get(content_type) or (content_type or "알 수 없는 형식")


def for_web(data, content_type=None, pages=MAX_PAGES):
    """The picture as the app can show it: [(content type, bytes, (width, height) or None)], one item per
    page of a TIFF, up to `pages` of them. Raises ValueError or OSError when it cannot be shown or converted."""
    found = sniff(data) or ALIASES.get(content_type, content_type)
    if found == "image/svg+xml" and SVG_DTD.search(svg_text(data)):
        raise ValueError("SVG with a document type definition")
    if found in WEB:
        return Pages([(found, bytes(data), size(data, found))])
    if found in CONVERTED:
        return to_png(data, pages)
    raise ValueError(f"unsupported picture {found}")


def svg_text(data):
    """The text of SVG bytes, in UTF-16 when they start with its byte order mark."""
    data = bytes(data)
    return data.decode("utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8", "ignore")


def size(data, content_type):
    """(width, height) in pixels, or None when the picture does not say."""
    if content_type == "image/svg+xml":
        return svg_size(bytes(data[:8192]).decode("utf-8", "ignore"))
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as picture:
            return picture.size
    except Exception:
        return None


def svg_size(text):
    root = re.search(r"<svg\b([^>]*)>", text, re.I)
    if not root:
        return None
    attributes = dict(re.findall(r'([\w:-]+)\s*=\s*"([^"]*)"', root.group(1)))

    def length(name):
        match = re.fullmatch(r"\s*([\d.]+)\s*(px|pt)?\s*", attributes.get(name, ""))
        return float(match.group(1)) * (4 / 3 if match.group(2) == "pt" else 1) if match else None

    width, height = length("width"), length("height")
    if not (width and height):
        box = re.split(r"[\s,]+", attributes.get("viewBox", "").strip())
        try:
            width, height = float(box[2]), float(box[3])
        except (IndexError, ValueError):
            return None
    return (round(width), round(height)) if width > 0 and height > 0 else None


def to_png(data, limit=MAX_PAGES):
    """PNG pages of a TIFF or a metafile, each with the size it is meant to be shown at. The first `limit` pages of a
    TIFF are read and a page of more than MAX_PIXELS is left out; the `notes` of the pages say so."""
    from PIL import Image, ImageSequence

    with Image.open(io.BytesIO(data)) as picture:
        scale = 1.0
        if picture.format == "WMF":
            # Drawn at twice the metafile's own resolution for sharp lines, and shown at its own size.
            dpi = picture.info.get("dpi", 72)
            dpi = dpi if isinstance(dpi, tuple) else (dpi, dpi)
            scale = min(2.0, MAX_SIDE / max(picture.size + (1,)))
            picture.load(dpi=(dpi[0] * scale, dpi[1] * scale))  # only Windows has a metafile handler
            frames = [picture]
        else:
            frames = ImageSequence.Iterator(picture)
        found, large, looked = Pages(), [], 0
        for frame in frames:
            if looked == limit:
                break
            looked += 1
            if frame.size[0] * frame.size[1] > MAX_PIXELS:
                large.append(looked)  # checked before the page is decoded
                continue
            image = frame.convert("RGBA" if frame.mode in ("RGBA", "LA", "P", "PA") else "RGB")
            image.thumbnail((MAX_SIDE, MAX_SIDE))
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            found.append(("image/png", buffer.getvalue(), (image.size[0] / scale, image.size[1] / scale)))
        pages = getattr(picture, "n_frames", 1) if picture.format != "WMF" else 1
    if not found:
        raise ValueError(f"every page has more than {MAX_PIXELS} pixels")
    notes = []
    if pages > limit:
        notes.append(f"{pages:,}쪽 중 앞 {limit}쪽만 넣었습니다.")
    if large:
        notes.append(f"{', '.join(map(str, large))}쪽은 {MAX_PIXELS:,}픽셀을 넘어 넣지 않았습니다.")
    found.notes = tuple(notes)
    return found


def data_uri(content_type, data):
    return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"


def img(content_type, data, alt, width=None, height=None, style=""):
    """An <img> of the picture. Width and height are presentational hints, so the stylesheet's
    max-width:100% with height:auto keeps the proportions in a narrow window; `style`, such as a crop."""
    hints = "".join(
        f' {name}="{round(value)}"'
        for name, value in (("width", width), ("height", height))
        if isinstance(value, (int, float)) and 0 < value < 100000
    )
    look = f' style="{attribute(style)}"' if style else ""
    return f'<img src="{data_uri(content_type, data)}" alt="{attribute(alt)}"{hints}{look}>'


def crop_css(top, right, bottom, left):
    """CSS showing the part of a picture left after cropping these shares of its sides, as Word crops a picture
    (object-view-box); "" when nothing is cut away, or when a side is extended rather than cut (a negative share)."""
    sides = (top, right, bottom, left)
    if not any(sides) or min(sides) < 0 or top + bottom >= 1 or left + right >= 1:
        return ""
    return "object-view-box:inset(" + " ".join(f"{round(side * 100, 3):g}%" for side in sides) + ")"


def missing(label):
    """A marker where a picture could not be shown."""
    return f'<span class="missing">{attribute(label)}</span>'
