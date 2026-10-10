"""Pictures PowerPoint, Excel and Visio draw of slides, pages, charts and shapes, for the pages of their files.

These applications hand over such a picture only as a file (or through the clipboard, which belongs to
the user; a picture object in memory cannot leave their process), so the application writes each one
into a private scratch folder; it is read back at once, checked to be a picture and deleted. A copy the
security software keeps encrypted is no picture: it is left out, and so are the rest after it, rather
than storing what cannot be shown; an export that fails stops the rest too, as the next ones would fail
for the same reason. A large PNG is kept as JPEG when that is smaller, and one page stops taking pictures
at a size and a time limit, so its text still arrives."""

import io
import re
import tempfile
import time
from contextlib import contextmanager
from html import escape
from pathlib import Path
from urllib.parse import unquote

from . import images
from .folders import remove_tree

# Pictures of one page: with base64 and the text they stay under the 80MB of an HTML page.
MAX_PICTURE_BYTES = 40 * 1024 * 1024
COMPACT_BYTES = 400 * 1024  # a larger PNG is kept as JPEG when that is smaller
# Seconds of exporting per file, half the Office worker's limit: the text is read whatever happens.
MAX_SECONDS = 150
SHOWN = {"image/png", "image/jpeg", "image/gif", "image/bmp"}
TAG = re.compile(r"<img\b[^>]*>", re.I)
ATTRIBUTE = re.compile(r"""([\w:.-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")
CHARSET = re.compile(rb"""charset\s*=\s*["']?([\w.:-]+)""", re.I)
FAILED = "Office가 그림을 만들지 못했습니다."
ENCRYPTED = "Office가 만든 그림 파일을 읽을 수 없습니다. 보안 정책(DRM)이 그림 파일을 암호화하는 것으로 보입니다."
FULL = "그림이 40MB를 넘어 나머지 그림은 넣지 않았습니다."
LATE = "그림을 만드는 시간이 150초를 넘어 나머지 그림은 넣지 않았습니다."


@contextmanager
def scratch(folder=None):
    """A private folder for the pictures of one conversion: `folder`, which its owner removes, or a new
    one removed afterwards."""
    if folder is not None:
        yield Path(folder)
        return
    created = Path(tempfile.mkdtemp(prefix="private-agent-pictures-"))
    try:
        yield created
    finally:
        remove_tree(created)


def compact(data, kind):
    """(bytes, content type): a large PNG as JPEG on white when that is smaller."""
    if kind != "image/png" or len(data) <= COMPACT_BYTES:
        return data, kind
    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as picture:
            picture.load()
            rgba = picture.convert("RGBA")
            flat = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            flat.alpha_composite(rgba)
            buffer = io.BytesIO()
            flat.convert("RGB").save(buffer, "JPEG", quality=85, optimize=True)
    except Exception:
        return data, kind
    smaller = buffer.getvalue()
    return (smaller, "image/jpeg") if len(smaller) < len(data) else (data, kind)


def xml_name(text):
    """A name as Office writes it in HTML, _x0020_ for a space, decoded."""
    return re.sub(r"_x([0-9A-Fa-f]{4})_", lambda match: chr(int(match.group(1), 16)), text)


class Exports:
    """The pictures one Office application writes for one page, and why any are missing."""

    def __init__(self, folder, budget=MAX_PICTURE_BYTES, seconds=MAX_SECONDS):
        self.folder, self.budget = Path(folder), budget
        self.deadline = time.monotonic() + seconds
        self.used = self.count = 0
        self.stopped = False
        self.problems = []

    def note(self, problem):
        if problem not in self.problems:
            self.problems.append(problem)

    def stop(self, problem):
        self.stopped = True
        self.note(problem)

    def ready(self):
        """Whether another picture may be exported."""
        if not self.stopped and time.monotonic() > self.deadline:
            self.stop(LATE)
        return not self.stopped

    def notice(self):
        """A line at the top of the page saying why pictures are missing, or ""."""
        if not self.problems:
            return ""
        return f'<p class="caption">그림 일부를 넣지 못했습니다: {escape(" ".join(self.problems))}</p>'

    def path(self, suffix):
        self.count += 1
        return self.folder / f"picture-{self.count}{suffix}"

    def picture(self, export, suffix=".png"):
        """(content type, bytes, size) of the picture `export(path)` writes, or None."""
        if not self.ready():
            return None
        path = self.path(suffix)
        try:
            export(str(path))
            data = path.read_bytes()
        except Exception:
            self.stop(FAILED)
            return None
        finally:
            remove(path)
        return self.accept(data)

    def accept(self, data):
        kind = images.sniff(data)
        if kind not in SHOWN:
            # Encrypted by the security software, or no picture at all: the next ones would be the same.
            self.stop(ENCRYPTED)
            return None
        data, kind = compact(data, kind)
        if self.used + len(data) > self.budget:
            self.note(FULL)
            return None
        self.used += len(data)
        return kind, data, images.size(data, kind)

    def web_page(self, publish):
        """{shape name: picture} of the pictures of shapes on a page the application publishes as HTML
        (`publish(path)`), as Excel draws a sheet's pictures, shapes, groups and charts for it."""
        if not self.ready():
            return {}
        folder = self.path("")
        target = folder / "page.htm"
        try:
            folder.mkdir()
            publish(str(target))
            raw = target.read_bytes()
        except Exception:
            self.stop(FAILED)
            remove_tree(folder)
            return {}
        try:
            match = CHARSET.search(raw[:4096])
            try:
                html = raw.decode(match.group(1).decode("ascii") if match else "utf-8", errors="replace")
            except LookupError:
                html = raw.decode("utf-8", errors="replace")
            if "<html" not in html[:4096].lower():
                self.stop(ENCRYPTED)
                return {}
            found, inside = {}, folder.resolve()
            for tag in TAG.finditer(html):
                attributes = {name.lower(): value.strip("\"'") for name, value in ATTRIBUTE.findall(tag.group(0))}
                names, source = attributes.get("v:shapes", "").split(), attributes.get("src", "")
                if not names or not source or re.match(r"[a-z][\w+.-]*:", source, re.I):
                    continue
                name = xml_name(names[0])
                file = (target.parent / unquote(source.replace("\\", "/"))).resolve()
                if name in found or inside not in file.parents:
                    continue
                try:
                    data = file.read_bytes()
                except OSError:
                    continue
                picture = self.accept(data)
                if picture is not None:
                    found[name] = picture
                elif self.stopped or self.used >= self.budget:
                    break
            return found
        finally:
            remove_tree(folder)


def remove(path):
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass  # the scratch folder goes as a whole afterwards
