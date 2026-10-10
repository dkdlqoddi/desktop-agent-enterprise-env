"""Image files as Wiki documents: one HTML page per picture file, made by Python without Office."""

from html import escape

from . import images
from .file_import import FileImporter
from .html_document import page
from .llm import UserError

METAFILES = ("image/x-emf", "image/x-wmf")


def capabilities():
    return {
        "available": True,
        "reason": "PNG·JPEG·GIF·BMP·WebP·AVIF·SVG·ICO는 그대로, TIFF·EMF·WMF는 PNG로 바꿔 HTML 문서로 저장합니다.",
    }


def readable(size):
    for unit in ("B", "KB", "MB"):
        if size < 1024 or unit == "MB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024


def image_page(file, cancel=None):
    """The HTML page of an image file: the picture itself, and a caption with its name, format and size."""
    data = file.read_bytes()
    kind = images.sniff(data)
    if not kind:
        raise UserError(
            "이미지 형식을 알아보지 못했습니다. PNG·JPEG·GIF·BMP·WebP·SVG·TIFF·EMF·WMF 파일인지 확인해 주세요."
        )
    try:
        pictures = images.for_web(data, kind)
    except Exception:
        hint = " EMF·WMF 그림은 Windows에서만 변환할 수 있습니다." if kind in METAFILES else ""
        raise UserError(f"{images.kind(kind)} 이미지를 화면에 보이는 형식으로 바꾸지 못했습니다.{hint}") from None
    figures = []
    for number, (content_type, picture, size) in enumerate(pictures, 1):
        alt = file.name + (f" ({number}/{len(pictures)}쪽)" if len(pictures) > 1 else "")
        figures.append(f"<figure>{images.img(content_type, picture, alt, *(size or (None, None)))}</figure>")
    own = images.size(data, kind) if kind not in METAFILES else None
    details = [file.name, images.kind(kind)]
    if own:
        details.append(f"{own[0]}×{own[1]}px")
    details.append(readable(len(data)))
    if pictures[0][0] != kind:
        details.append("PNG로 변환" + (f" · {len(pictures)}쪽" if len(pictures) > 1 else ""))
    # Pages a conversion left out, such as those of a TIFF after its first 50, are said at the top.
    notes = " ".join(getattr(pictures, "notes", ()))
    notice = f'<p class="caption">그림 일부를 넣지 못했습니다: {escape(notes)}</p>' if notes else ""
    return page(file.stem, notice + "".join(figures) + f'<p class="caption">{escape(" · ".join(details))}</p>', "image")


class ImageImporter(FileImporter):
    extensions = set(images.EXTENSIONS)
    label = "이미지"
    format_label = "이미지"
    source_table = "image_sources"
    uses_office = False

    def __init__(self, service, converter=None, capability_check=capabilities):
        super().__init__(service, converter or image_page, capability_check)
