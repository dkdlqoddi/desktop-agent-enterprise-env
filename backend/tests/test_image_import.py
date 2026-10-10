import base64
import io
import re
import struct
import sys
from pathlib import Path

import pytest
from PIL import Image

from agent.db import Database
from agent.html_document import index_markdown
from agent.image_import import ImageImporter, image_page
from agent.images import for_web, sniff
from agent.service import Service
from test_rtf_import import FakeLLM, wait


def picture(kind, size=(6, 4), **options):
    buffer = io.BytesIO()
    Image.new("RGBA" if kind == "PNG" else "RGB", size, (20, 120, 220)).save(buffer, kind, **options)
    return buffer.getvalue()


SVG = (
    b'<?xml version="1.0"?>\n<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 30 20">'
    b'<rect width="30" height="20"/></svg>'
)


@pytest.fixture
def importer(tmp_path):
    service = Service(Database(tmp_path / "data" / "wiki.db"), FakeLLM())
    return service.images


@pytest.mark.parametrize(
    "name,data,kind",
    [
        ("a.png", picture("PNG"), "image/png"),
        ("b.JPG", picture("JPEG"), "image/jpeg"),
        ("c.gif", picture("GIF"), "image/gif"),
        ("d.bmp", picture("BMP"), "image/bmp"),
        ("e.webp", picture("WEBP"), "image/webp"),
        ("f.svg", SVG, "image/svg+xml"),
    ],
)
def test_pictures_the_app_shows_stay_as_they_are(tmp_path, name, data, kind):
    file = tmp_path / name
    file.write_bytes(data)
    assert sniff(data) == kind
    html = image_page(file)
    source = re.search(r'<img src="data:([^;]+);base64,([^"]+)" alt="([^"]+)"', html)
    assert source.group(1) == kind and base64.b64decode(source.group(2)) == data and source.group(3) == name
    assert '<div class="doc image">' in html and f"<title>{file.stem}</title>" in html
    label = {
        "image/png": "PNG",
        "image/jpeg": "JPEG",
        "image/gif": "GIF",
        "image/bmp": "BMP",
        "image/webp": "WebP",
        "image/svg+xml": "SVG",
    }[kind]
    assert f'<p class="caption">{name} · {label} · {"30×20px" if label == "SVG" else "6×4px"} · ' in html
    assert index_markdown(html).startswith(f"[그림: {name}]")


def test_recognizing_svg_stays_quick_for_files_full_of_comments():
    import time

    started = time.monotonic()
    assert sniff(b"<!---->" * 600 + b"not a picture") is None
    assert sniff(b"<!-- a -->\n<!-- b --><svg xmlns='http://www.w3.org/2000/svg'/>") == "image/svg+xml"
    assert time.monotonic() - started < 1


def test_tiff_pages_become_png_and_metafiles_need_windows(tmp_path):
    first = Image.new("RGB", (8, 5), (255, 0, 0))
    second = Image.new("RGB", (4, 4), (0, 0, 255))
    buffer = io.BytesIO()
    first.save(buffer, "TIFF", save_all=True, append_images=[second])
    file = tmp_path / "스캔.tif"
    file.write_bytes(buffer.getvalue())
    html = image_page(file)
    pages = re.findall(r'<img src="data:image/png;base64,([^"]+)" alt="([^"]+)" width="(\d+)" height="(\d+)"', html)
    assert [(alt, width, height) for _, alt, width, height in pages] == [
        ("스캔.tif (1/2쪽)", "8", "5"),
        ("스캔.tif (2/2쪽)", "4", "4"),
    ]
    assert Image.open(io.BytesIO(base64.b64decode(pages[0][0]))).getpixel((0, 0)) == (255, 0, 0)
    assert "스캔.tif · TIFF · 8×5px" in html and "PNG로 변환 · 2쪽" in html
    # An EMF header Pillow reads (100 pixels in a frame of an inch), which only Windows has the handler to draw.
    bounds_and_frame = struct.pack("<8i", 0, 0, 100, 100, 0, 0, 2540, 2540)
    metafile = b"\x01\x00\x00\x00" + b"\x00" * 4 + bounds_and_frame + b" EMF" + b"\x00" * 60
    assert sniff(metafile) == "image/x-emf"
    if sys.platform != "win32":
        with pytest.raises(OSError):
            for_web(metafile)


def test_tiff_pages_past_the_limits_are_left_out_and_the_page_says_so(tmp_path, monkeypatch):
    pages = [Image.new("RGB", (4, 4), (index, 0, 0)) for index in range(53)]
    pages[1] = Image.new("RGB", (40, 30), (0, 255, 0))
    buffer = io.BytesIO()
    pages[0].save(buffer, "TIFF", save_all=True, append_images=pages[1:])
    file = tmp_path / "긴 스캔.tif"
    file.write_bytes(buffer.getvalue())
    # A page is checked before it is decoded: one larger than the limit is left out.
    monkeypatch.setattr("agent.images.MAX_PIXELS", 1000)
    html = image_page(file)
    assert html.count("<img ") == 49 and "PNG로 변환 · 49쪽" in html
    assert (
        '<div class="doc image"><p class="caption">그림 일부를 넣지 못했습니다: 53쪽 중 앞 50쪽만 넣었습니다. '
        "2쪽은 1,000픽셀을 넘어 넣지 않았습니다.</p><figure>"
    ) in html
    # A picture of nothing but such pages is no picture.
    single = io.BytesIO()
    pages[1].save(single, "TIFF")
    with pytest.raises(ValueError):
        for_web(single.getvalue())


def test_svg_with_a_document_type_definition_of_its_own_is_refused():
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;">]>'
        b'<svg xmlns="http://www.w3.org/2000/svg"><text>&b;</text></svg>'
    )
    # Not recognized by its bytes, and refused when a package names it as SVG.
    assert sniff(bomb) is None
    entity = b'<svg xmlns="http://www.w3.org/2000/svg"><!ENTITY x "y"></svg>'
    for data in (bomb, bomb.decode().encode("utf-16"), entity):
        with pytest.raises(ValueError):
            for_web(data, "image/svg+xml")
    # A DOCTYPE without one of its own is a picture as before.
    public = b'<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" "http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd">'
    assert for_web(public + SVG[22:], "image/svg+xml")[0][0] == "image/svg+xml"


def test_folder_import_saves_each_picture_as_an_html_document_and_skips_others(importer, tmp_path):
    folder = tmp_path / "사진"
    (folder / "하위").mkdir(parents=True)
    (folder / "로고.png").write_bytes(picture("PNG"))
    (folder / "같은 로고.PNG").write_bytes(picture("PNG"))
    (folder / "하위" / "도면.svg").write_bytes(SVG)
    (folder / "깨진 그림.jpg").write_bytes(b"not a picture")
    (folder / "메모.txt").write_text("not an image")
    importer.start({"path": str(folder), "mode": "directory", "recursive": True})
    job = wait(importer)
    assert (job["total"], job["imported"], job["skipped"], job["failed"]) == (4, 2, 1, 1)
    assert "이미지 형식을 알아보지 못했습니다" in next(r["message"] for r in job["results"] if r["status"] == "failed")
    service = importer.service
    # Files are read in name order, so the copy named 같은 로고 is the one saved.
    statuses = {Path(r["path"]).name: r["status"] for r in job["results"]}
    assert statuses == {
        "같은 로고.PNG": "imported",
        "깨진 그림.jpg": "failed",
        "로고.png": "skipped",
        "도면.svg": "imported",
    }
    ident = next(r["doc_id"] for r in job["results"] if r["path"].endswith("같은 로고.PNG"))
    doc = service.wiki_get({"id": ident})
    assert (doc["title"], doc["format"], doc["source"]["kind"]) == ("같은 로고", "html", "image")
    assert doc["source"]["source_path"] == str(folder / "같은 로고.PNG")
    assert doc["html"].count("<img") == 1 and doc["content"].startswith("[그림: 같은 로고.PNG]")
    assert service.wiki_search({"query": "도면", "mode": "auto"})["documents"][0]["title"] == "도면"
    assert service.wiki_source_html({"id": ident})["filename"] == "같은 로고.html"
    importer.start({"path": str(folder / "로고.png"), "mode": "file"})
    assert wait(importer)["skipped"] == 1
    service.wiki_delete({"id": ident})
    service.wiki_purge({"ids": [ident]})
    with service.db.connect() as db:
        assert db.execute("SELECT count(*) FROM image_sources").fetchone()[0] == 1


def test_image_import_validates_paths_and_reports_its_capability(importer, tmp_path):
    from agent.llm import UserError

    text = tmp_path / "a.txt"
    text.write_text("x")
    with pytest.raises(UserError, match="존재하는 이미지 파일"):
        importer.start({"path": str(text), "mode": "file"})
    assert importer.service.dispatch("image.capabilities", {})["available"] is True
    importer.start({"path": str(tmp_path), "mode": "directory"})
    assert wait(importer)["error"] == "선택한 폴더에 이미지 파일이 없습니다."
    assert isinstance(importer, ImageImporter)
