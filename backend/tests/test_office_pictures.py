import io
import random
from pathlib import Path
from types import SimpleNamespace as NS

from PIL import Image

from agent.html_document import index_markdown, page
from agent.office_converter import convert_with_office
from agent.office_html import excel_html, powerpoint_html, visio_html
from agent.office_pictures import COMPACT_BYTES, ENCRYPTED, FAILED, FULL, LATE, Exports, compact
from test_office_import import (
    chart_fixture,
    collection,
    drawing_fixture,
    placeholder,
    powerpoint_app,
    text_shape,
    used_range,
    visio_app,
)
from test_rtf_import import fake_modules


def png(width=4, height=3, color=(255, 0, 0), mode="RGB"):
    buffer = io.BytesIO()
    Image.new(mode, (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def noise(width, height):
    """A PNG that does not compress: a photo-like slide."""
    generator = random.Random(7)
    buffer = io.BytesIO()
    Image.frombytes("RGB", (width, height), bytes(generator.randrange(256) for _ in range(width * height * 3))).save(
        buffer, "PNG"
    )
    return buffer.getvalue()


def test_exported_pictures_are_read_back_checked_and_deleted_at_once(tmp_path):
    exports = Exports(tmp_path)
    picture = png(8, 6)
    written = []
    found = exports.picture(lambda path: written.append(path) or Path(path).write_bytes(picture))
    assert found == ("image/png", picture, (8, 6))
    assert Path(written[0]).parent == tmp_path and list(tmp_path.iterdir()) == []
    assert exports.notice() == ""


def test_an_export_the_security_software_keeps_encrypted_is_left_out_with_the_rest(tmp_path):
    exports = Exports(tmp_path)
    attempts = []

    def encrypted(path):
        attempts.append(path)
        Path(path).write_bytes(b"\x00\x01DRM-ENCRYPTED-CONTENT" * 40)

    assert exports.picture(encrypted) is None and exports.picture(encrypted) is None
    assert len(attempts) == 1 and list(tmp_path.iterdir()) == []
    assert exports.notice() == f'<p class="caption">그림 일부를 넣지 못했습니다: {ENCRYPTED}</p>'


def test_a_failed_export_stops_the_rest_and_exports_stop_at_their_time_limit(tmp_path):
    exports = Exports(tmp_path)
    attempts = []

    def refuse(path):
        attempts.append(path)
        raise RuntimeError("blocked")

    assert all(exports.picture(refuse) is None for _ in range(5))
    assert len(attempts) == 1 and FAILED in exports.notice()
    late = Exports(tmp_path, seconds=-1)
    assert late.picture(lambda path: attempts.append(path)) is None and late.web_page(attempts.append) == {}
    assert len(attempts) == 1 and LATE in late.notice()


def test_large_pictures_are_kept_as_jpeg_and_a_page_stops_taking_pictures_at_its_limit(tmp_path):
    photo = noise(400, 400)
    assert len(photo) > COMPACT_BYTES
    data, kind = compact(photo, "image/png")
    assert kind == "image/jpeg" and data[:3] == b"\xff\xd8\xff" and len(data) < len(photo)
    # A transparent picture goes on white; a small one stays as it is.
    see_through = png(4, 4, (0, 0, 0, 0), "RGBA")
    assert compact(see_through, "image/png") == (see_through, "image/png")
    small = png(8, 8)
    exports = Exports(tmp_path, budget=len(small) * 2)
    assert all(exports.picture(lambda path: Path(path).write_bytes(small)) for _ in range(2))
    assert exports.picture(lambda path: Path(path).write_bytes(small)) is None
    assert FULL in exports.notice() and list(tmp_path.iterdir()) == []


def test_pictures_of_a_published_page_are_found_by_shape_name(tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (tmp_path / "outside.png").write_bytes(png())

    def publish(path):
        target = Path(path)
        files = target.parent / "page.files"
        files.mkdir()
        (files / "image001.png").write_bytes(png(2, 2))
        (files / "image002.png").write_bytes(png(30, 20))
        (files / "image003.png").write_bytes(png(10, 10))
        target.write_bytes(
            (
                '<html><head><meta http-equiv=Content-Type content="text/html; charset=ks_c_5601-1987"></head><body>'
                '<!--[if gte vml 1]><v:shape id="그림_x0020_1"><v:imagedata src="page.files/image001.png"/></v:shape><![endif]-->'
                '<![if !vml]><img width=30 height=20 src="page.files/image002.png" v:shapes="그림_x0020_1"><![endif]>'
                "<img src='page.files/image003.png' v:shapes='Group_x0020_5 _x0000_s1027 _x0000_s1028'>"
                '<img src="../../outside.png" v:shapes="Escape"><img src="http://intranet/a.png" v:shapes="Remote">'
                "</body></html>"
            ).encode("cp949")
        )

    found = Exports(scratch).web_page(publish)
    assert set(found) == {"그림 1", "Group 5"}
    assert found["그림 1"][0] == "image/png" and found["그림 1"][2] == (30, 20) and found["Group 5"][2] == (10, 10)
    assert list(scratch.iterdir()) == []


def test_a_page_that_cannot_be_published_or_read_gives_no_pictures(tmp_path):
    exports = Exports(tmp_path)
    assert exports.web_page(lambda path: (_ for _ in ()).throw(RuntimeError("blocked"))) == {}
    assert FAILED in exports.notice()
    exports = Exports(tmp_path)
    assert exports.web_page(lambda path: Path(path).write_bytes(b"\x00\x13encrypted" * 100)) == {}
    assert ENCRYPTED in exports.notice() and list(tmp_path.iterdir()) == []


def slide(index, shapes, exported):
    def export(path, kind, width, height):
        exported.append((kind, width, height))
        Path(path).write_bytes(png(16, 9, (index * 40, 0, 0)))

    return NS(Shapes=collection(shapes), NotesPage=NS(Shapes=collection([])), Export=export)


def test_slides_carry_the_picture_powerpoint_draws_and_their_pictures_leave_no_marker(tmp_path):
    exported = []
    picture = NS(Type=13, HasTable=False, HasChart=0, HasSmartArt=False, AlternativeText="제품 사진", Name="그림 3")
    group = NS(
        Type=6,
        GroupItems=collection(
            [text_shape("그룹 글"), NS(Type=16, HasTable=False, HasChart=0, HasSmartArt=False, Name="동영상 1")]
        ),
    )
    presentation = NS(
        PageSetup=NS(SlideWidth=960.0, SlideHeight=540.0),
        Slides=collection(
            [slide(1, [placeholder(1, "개요"), text_shape("본문"), picture, group], exported), slide(2, [], exported)]
        ),
    )
    body = powerpoint_html(presentation, exports=Exports(tmp_path))
    assert exported == [("PNG", 1280, 720)] * 2 and list(tmp_path.iterdir()) == []
    assert '<h2>슬라이드 1 · 개요</h2><figure class="slide-picture"><img src="data:image/png;base64,' in body
    assert 'alt="슬라이드 1 그림" width="1280" height="720"></figure><div class="shape"><p>본문</p></div>' in body
    assert "제품 사진" not in body and "동영상" not in body and "그룹 글" in body
    text = index_markdown(page("발표", body, "powerpoint"))
    assert "[그림: 슬라이드 1 그림]" in text and "[그림: 슬라이드 2 그림]" in text
    # A portrait slide keeps its proportions within the same long side.
    portrait = NS(PageSetup=NS(SlideWidth=540.0, SlideHeight=720.0), Slides=collection([slide(1, [], exported)]))
    powerpoint_html(portrait, exports=Exports(tmp_path))
    assert exported[-1] == ("PNG", 960, 1280)


def test_slides_whose_picture_cannot_be_had_keep_their_markers_and_say_why(tmp_path):
    def refuse(*args):
        raise RuntimeError("보안 정책")

    picture = NS(Type=13, HasTable=False, HasChart=0, HasSmartArt=False, AlternativeText="제품 사진", Name="그림 3")
    blocked = NS(Shapes=collection([picture]), NotesPage=NS(Shapes=collection([])), Export=refuse)
    body = powerpoint_html(
        NS(PageSetup=NS(SlideWidth=960.0, SlideHeight=540.0), Slides=collection([blocked])), exports=Exports(tmp_path)
    )
    assert body.startswith(f'<p class="caption">그림 일부를 넣지 못했습니다: {FAILED}</p>')
    assert '<span class="missing">[그림: 제품 사진]</span>' in body and "slide-picture" not in body
    # Without a slide size there is nothing to export.
    exported = []
    body = powerpoint_html(NS(Slides=collection([slide(1, [picture], exported)])), exports=Exports(tmp_path))
    assert exported == [] and "[그림: 제품 사진]" in body


def test_powerpoint_conversion_exports_through_a_scratch_folder_it_empties(monkeypatch, tmp_path):
    calls = []
    app = powerpoint_app(calls)
    exported = []
    presentation_slides = collection([slide(1, [placeholder(1, "결과")], exported)])
    opened = app.Presentations.Open

    def open_presentation(**kwargs):
        presentation = opened(**kwargs)
        presentation.PageSetup = NS(SlideWidth=720.0, SlideHeight=540.0)
        presentation.Slides = presentation_slides
        return presentation

    app.Presentations.Open = open_presentation
    fake_modules(monkeypatch, app, calls)
    folders = []
    real = __import__("tempfile").mkdtemp
    monkeypatch.setattr(
        "agent.office_pictures.tempfile.mkdtemp",
        lambda **kwargs: folders.append(real(dir=tmp_path, **kwargs)) or folders[-1],
    )
    result = convert_with_office(tmp_path / "발표.pptx")
    assert exported == [("PNG", 1280, 960)]
    assert '<figure class="slide-picture"><img src="data:image/png;base64,' in result
    assert len(folders) == 1 and not Path(folders[0]).exists()
    assert [path.name for path in tmp_path.iterdir()] == []


def visio_drawing(calls, write=lambda path, index: Path(path).write_bytes(png(30 * index, 20))):
    """drawing_fixture() and a page without shapes; Visio exports page n as a picture 30n × 20 px."""
    drawing = drawing_fixture()
    pages = [drawing.Pages.Item(index) for index in range(1, drawing.Pages.Count + 1)]
    pages.append(NS(Name="도형 없음", Background=0, Shapes=collection([])))
    for index, item in enumerate(pages, 1):

        def export(path, index=index):
            calls.append(("export", index, Path(path).suffix))
            write(path, index)

        item.Export = export
    drawing.Pages = collection(pages)
    return drawing


def test_visio_pages_carry_the_picture_visio_exports(tmp_path):
    calls = []
    body = visio_html(visio_drawing(calls), exports=Exports(tmp_path))
    # Each page with shapes once, as PNG (Visio picks the filter by the extension), read back and deleted at once.
    assert calls == [("export", 1, ".png"), ("export", 2, ".png"), ("export", 3, ".png")]
    assert list(tmp_path.iterdir()) == []
    assert '<h2>페이지 1 · 시스템 구성도</h2><figure><img src="data:image/png;base64,' in body
    assert 'alt="시스템 구성도 그림" width="30" height="20"></figure><h3>도형 텍스트</h3>' in body
    assert '<h2>페이지 2 · 배경-1 (배경)</h2><figure><img src="data:image/png;base64,' in body
    # A page of shapes without any text has its picture; a page without shapes is left out.
    assert '<h2>페이지 3 · 빈 페이지</h2><figure><img src="data:image/png;base64,' in body
    assert 'alt="빈 페이지 그림" width="90" height="20"></figure></section>' in body and "도형 없음" not in body
    text = index_markdown(page("구성도", body, "visio"))
    assert "[그림: 시스템 구성도 그림]" in text and "- 사용자 단말 → API 서버: HTTPS" in text


def test_visio_pages_whose_picture_cannot_be_had_keep_their_text_and_say_why(tmp_path):
    calls = []

    def refuse(path, index):
        raise RuntimeError("보안 정책")

    body = visio_html(visio_drawing(calls, refuse), exports=Exports(tmp_path))
    assert body.startswith(
        f'<p class="caption">그림 일부를 넣지 못했습니다: {FAILED}</p>'
        '<section class="page"><h2>페이지 1 · 시스템 구성도</h2><h3>도형 텍스트</h3>'
    )
    # The next pages would fail the same way, so Visio is not asked again; text-less shapes leave no page.
    assert calls == [("export", 1, ".png")] and "<figure>" not in body and "빈 페이지" not in body
    calls.clear()
    body = visio_html(
        visio_drawing(calls, lambda path, index: Path(path).write_bytes(b"\x00\x01DRM-ENCRYPTED" * 40)),
        exports=Exports(tmp_path),
    )
    assert body.startswith(f'<p class="caption">그림 일부를 넣지 못했습니다: {ENCRYPTED}</p>')
    assert calls == [("export", 1, ".png")] and "<figure>" not in body and list(tmp_path.iterdir()) == []


def test_visio_conversion_exports_through_a_scratch_folder_it_empties(monkeypatch, tmp_path):
    calls = []
    fake_modules(monkeypatch, visio_app(calls, drawing=visio_drawing(calls)), calls)
    folders = []
    real = __import__("tempfile").mkdtemp
    monkeypatch.setattr(
        "agent.office_pictures.tempfile.mkdtemp",
        lambda **kwargs: folders.append(real(dir=tmp_path, **kwargs)) or folders[-1],
    )
    result = convert_with_office(tmp_path / "구성도.vsdx")
    # The pages are exported while the drawing is open, before Visio closes it and quits.
    assert [call[0] for call in calls] == ["dispatch", "open", "export", "export", "export", "close", "quit"]
    assert '<figure><img src="data:image/png;base64,' in result and "그림 일부를 넣지 못했습니다" not in result
    assert len(folders) == 1 and not Path(folders[0]).exists()
    assert [path.name for path in tmp_path.iterdir()] == []


def cell(address):
    """A cell as Shape.TopLeftCell and BottomRightCell give it."""
    column = "".join(char for char in address if char.isalpha())
    return NS(
        Address="$" + column + "$" + address[len(column) :],
        Row=int(address[len(column) :]),
        Column=sum((ord(char) - 64) * 26**power for power, char in enumerate(reversed(column))),
    )


def excel_workbook(tmp_path, calls, *, pictures_in=(1, 4)):
    chart = chart_fixture()
    chart.Export = lambda path, kind: calls.append(("chart", kind)) or Path(path).write_bytes(png(48, 32))
    shapes = [
        NS(Type=13, Name="그림 2", AlternativeText="로고", TopLeftCell=cell("E2"), BottomRightCell=cell("G6")),
        NS(
            Type=17,
            Name="Text Box 3",
            AlternativeText="",
            TopLeftCell=cell("A10"),
            BottomRightCell=cell("C12"),
            TextFrame2=NS(HasText=-1, TextRange=NS(Text="참고 사항")),
        ),
        NS(
            Type=6,
            Name="Group 5",
            AlternativeText="",
            TopLeftCell=cell("G1"),
            BottomRightCell=cell("H4"),
            GroupItems=collection(
                [
                    NS(Type=1, TextFrame2=NS(HasText=-1, TextRange=NS(Text="입력"))),
                    NS(Type=1, TextFrame2=NS(HasText=-1, TextRange=NS(Text="출력"))),
                ]
            ),
        ),
        NS(
            Type=1,
            Name="Oval 7",
            AlternativeText="",
            TopLeftCell=cell("A1"),
            BottomRightCell=cell("B2"),
            TextFrame2=NS(HasText=-1, TextRange=NS(Text="타원")),
        ),
        NS(Type=3, Name="차트 1"),
        NS(Type=4, Name="Comment 1"),
    ]
    sheet = NS(
        Name="매출",
        Visible=-1,
        UsedRange=used_range(1, 1, (("값",),)),
        ChartObjects=lambda: collection([NS(Name="차트 1", Chart=chart)]),
        Shapes=collection(shapes),
    )

    def add(**kwargs):
        calls.append(("publish", kwargs))
        target = Path(kwargs["Filename"])

        def publish(create):
            files = target.parent / "page_files"
            files.mkdir(exist_ok=True)
            for index, size in enumerate(((60, 40), (90, 30), (50, 50)), 1):
                (files / f"image00{index}.png").write_bytes(png(*size))
            # The pages of the sources (XlSourceType) Excel draws the shapes on; the others hold the cells alone.
            pictures = kwargs["SourceType"] in pictures_in
            target.write_text(
                '<html><head><meta charset="utf-8"></head><body>'
                + (
                    '<img src="page_files/image001.png" v:shapes="그림_x0020_2">'
                    '<img src="page_files/image002.png" v:shapes="Text_x0020_Box_x0020_3">'
                    '<img src="page_files/image003.png" v:shapes="Group_x0020_5 _x0000_s1029">'
                    if pictures
                    else "<table><tr><td>값</td></tr></table>"
                )
                + "</body></html>",
                encoding="utf-8",
            )
            calls.append(("published", create))

        return NS(Publish=publish, Delete=lambda: calls.append(("delete",)))

    options = NS(AllowPNG=False, Encoding=949, RelyOnVML=True, OrganizeInFolder=False)
    return NS(Worksheets=collection([sheet]), WebOptions=options, PublishObjects=NS(Add=add))


def test_excel_charts_pictures_and_shapes_come_as_excel_draws_them(tmp_path):
    calls = []
    workbook = excel_workbook(tmp_path, calls)
    body = excel_html(workbook, exports=Exports(tmp_path))
    published = next(call[1] for call in calls if call[0] == "publish")
    # Only the cells under the shapes are published (their pictures come with them), not the whole sheet.
    assert {key: value for key, value in published.items() if key != "Filename"} == {
        "SourceType": 4,
        "Sheet": "매출",
        "Source": "A1:H12",
        "HtmlType": 0,
    }
    assert [call[0] for call in calls] == ["chart", "publish", "published", "delete"]
    options = workbook.WebOptions
    assert (options.AllowPNG, options.Encoding, options.RelyOnVML, options.OrganizeInFolder) == (
        True,
        65001,
        False,
        True,
    )
    assert list(tmp_path.iterdir()) == []
    # The chart as Excel draws it, then the table of its data.
    assert '<figure class="chart"><img src="data:image/png;base64,' in body
    assert (
        'alt="차트: 차트 1" width="48" height="32"></figure><figure class="chart"><figcaption>차트: 분기 매출' in body
    )
    assert 'alt="로고" width="60" height="40"> (E2)</li>' in body
    assert 'alt="Text Box 3: 참고 사항" width="90" height="30"> (A10)</li>' in body
    assert 'alt="Group 5: 입력 / 출력" width="50" height="50"> (G1)</li>' in body
    # A shape Excel gave no picture of keeps its text.
    assert "<li>타원 (A1)</li>" in body
    text = index_markdown(page("통합 문서", body, "excel"))
    assert "[그림: 로고] (E2)" in text and "[그림: Group 5: 입력 / 출력] (G1)" in text and "| 1분기 | 10 |" in text


def test_excel_without_pictures_keeps_markers_and_text(tmp_path):
    calls = []
    workbook = excel_workbook(tmp_path, calls)
    workbook.PublishObjects = NS(Add=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("blocked")))
    body = excel_html(workbook, exports=Exports(tmp_path))
    assert "[그림: 로고]" in body and "<li>참고 사항 (A10)</li>" in body and "<li>입력</li><li>출력</li>" in body
    assert body.startswith('<p class="caption">그림 일부를 넣지 못했습니다:')
    # A sheet of charts and comments only is not published, nor is a hidden sheet.
    calls.clear()
    workbook = excel_workbook(tmp_path, calls)
    workbook.Worksheets.Item(1).Shapes = collection([NS(Type=3, Name="차트 1"), NS(Type=4, Name="Comment 1")])
    excel_html(workbook, exports=Exports(tmp_path))
    assert [call[0] for call in calls] == ["chart"]
    calls.clear()
    workbook = excel_workbook(tmp_path, calls)
    workbook.Worksheets.Item(1).Visible = 0
    body = excel_html(workbook, exports=Exports(tmp_path))
    assert [call[0] for call in calls] == ["chart"] and "[그림: 로고]" in body


def test_the_whole_sheet_is_published_only_when_the_cells_under_its_shapes_cannot_be(tmp_path):
    def sources(calls):
        return [call[1]["SourceType"] for call in calls if call[0] == "publish"]

    # An Excel that refuses to publish the range publishes the sheet, as before.
    calls = []
    workbook = excel_workbook(tmp_path, calls)
    add = workbook.PublishObjects.Add
    workbook.PublishObjects = NS(
        Add=lambda **kwargs: (
            (_ for _ in ()).throw(RuntimeError("no range")) if kwargs["SourceType"] == 4 else add(**kwargs)
        )
    )
    body = excel_html(workbook, exports=Exports(tmp_path))
    assert 'alt="로고" width="60" height="40"> (E2)</li>' in body and "그림 일부를 넣지 못했습니다" not in body
    assert sources(calls) == [1] and list(tmp_path.iterdir()) == []
    # One whose page of the range came without the shapes' pictures publishes the sheet for them, once.
    calls = []
    body = excel_html(excel_workbook(tmp_path, calls, pictures_in=(1,)), exports=Exports(tmp_path))
    assert sources(calls) == [4, 1] and 'alt="Group 5: 입력 / 출력" width="50" height="50"> (G1)</li>' in body
    calls = []
    workbook = excel_workbook(tmp_path, calls, pictures_in=())
    add = workbook.PublishObjects.Add
    workbook.PublishObjects = NS(
        Add=lambda **kwargs: (
            (_ for _ in ()).throw(RuntimeError("no range")) if kwargs["SourceType"] == 4 else add(**kwargs)
        )
    )
    assert "[그림: 로고]" in excel_html(workbook, exports=Exports(tmp_path)) and sources(calls) == [1]
    # A shape that does not say where it is: the sheet is published.
    calls = []
    workbook = excel_workbook(tmp_path, calls)
    del workbook.Worksheets.Item(1).Shapes.Item(1).BottomRightCell
    excel_html(workbook, exports=Exports(tmp_path))
    assert sources(calls) == [1] and list(tmp_path.iterdir()) == []


def form_control(kind, name, place, value=None, caption="", entries=None):
    """An Excel form control (msoFormControl) of the XlFormControl `kind`."""
    return NS(
        Type=8,
        FormControlType=kind,
        Name=name,
        AlternativeText="",
        TopLeftCell=cell(place),
        BottomRightCell=cell(place),
        OLEFormat=NS(Object=NS(Caption=caption)),
        ControlFormat=NS(Value=value, **({"List": entries} if entries is not None else {})),
    )


def test_form_controls_keep_their_captions_and_answers(tmp_path):
    calls = []
    workbook = excel_workbook(tmp_path, calls)
    sheet = workbook.Worksheets.Item(1)
    sheet.Shapes = collection(
        [
            form_control(1, "Check Box 1", "B3", 1, "동의함"),
            form_control(1, "Check Box 2", "B4", -4146, "동의 안 함"),
            form_control(7, "Option Button 3", "B5", 1, "승인"),
            form_control(7, "Option Button 4", "C5", -4146, "반려"),
            form_control(2, "Drop Down 5", "B6", 2, "", ("승인", "반려", "보류")),
            form_control(6, "List Box 6", "B7", 0, "부서", ("개발", "영업")),
            form_control(0, "Button 7", "B8", caption="제출"),
            form_control(5, "Label 8", "B9", caption=""),
        ]
    )
    body = excel_html(workbook, exports=Exports(tmp_path))
    assert (
        "<li>☒ 동의함 (B3)</li><li>☐ 동의 안 함 (B4)</li><li>◉ 승인 (B5)</li><li>○ 반려 (C5)</li>"
        "<li>반려 (B6)</li><li>부서: (선택 없음) (B7)</li><li>제출 (B8)</li></ul>"
    ) in body
    # Controls have no picture to publish.
    assert [call[0] for call in calls] == ["chart"]
    text = index_markdown(page("통합 문서", body, "excel"))
    assert "☒ 동의함 (B3)" in text and "반려 (B6)" in text


def test_a_sheet_of_more_shapes_than_are_listed_says_so(tmp_path):
    calls = []
    workbook = excel_workbook(tmp_path, calls)
    many = [
        NS(
            Type=1,
            Name=f"Oval {index}",
            AlternativeText="",
            TopLeftCell=cell("A1"),
            BottomRightCell=cell("A1"),
            TextFrame2=NS(HasText=-1, TextRange=NS(Text=f"도형 {index}")),
        )
        for index in range(2001)
    ]
    workbook.Worksheets.Item(1).Shapes = collection(many)
    body = excel_html(workbook)
    assert '<p class="caption">도형이 많아 2,001개 중 앞 2,000개만 적었습니다.</p>' in body
    assert "<li>도형 1999 (A1)</li>" in body and "도형 2000" not in body
