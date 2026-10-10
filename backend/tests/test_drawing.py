import base64
import re
from xml.etree import ElementTree as ET

import pytest

from agent import drawing, vml
from agent.html_document import index_markdown
from agent.shape_presets import PRESETS
from agent.word_html import word_xml_to_html
from test_word_html import A4, STYLES, THEME, binary, body_of, package, part, png

SVG = "{http://www.w3.org/2000/svg}"
EXTRA = (
    'xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" '
    'xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" '
    'xmlns:dgm="http://schemas.openxmlformats.org/drawingml/2006/diagram" '
    'xmlns:dsp="http://schemas.microsoft.com/office/drawing/2008/diagram"'
)
SHAPE_URI = "http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
# The style Word 2013 and later give a new shape: blue fill, dark blue outline, white text.
STYLE = (
    '<wps:style><a:lnRef idx="2"><a:schemeClr val="accent1"><a:shade val="50000"/></a:schemeClr></a:lnRef>'
    '<a:fillRef idx="1"><a:schemeClr val="accent1"/></a:fillRef><a:effectRef idx="0"><a:schemeClr val="accent1"/></a:effectRef>'
    '<a:fontRef idx="minor"><a:schemeClr val="lt1"/></a:fontRef></wps:style>'
)
BODY = (
    '<wps:bodyPr rot="0" vert="horz" wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720" anchor="ctr"/>'
)


def convert(body, *, rels=(), parts=""):
    xml = package(body, styles=STYLES, rels=rels, section=A4, parts=THEME + parts)
    return word_xml_to_html(xml.replace("xmlns:wps=", EXTRA + " xmlns:wps=", 1), "도형")


def pictures(html):
    """[(alt, SVG root)] of the SVG pictures on a page."""
    found = []
    for data, alt in re.findall(r'<img src="data:image/svg\+xml;base64,([^"]+)" alt="([^"]*)"', html):
        found.append((alt, ET.fromstring(base64.b64decode(data))))
    return found


def anchor(ident, x, y, cx, cy, graphic, *, uri=SHAPE_URI, frames=("column", "paragraph"), behind=False, descr=""):
    alt = f' descr="{descr}"' if descr else ""
    return (
        f'<w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:anchor simplePos="0" relativeHeight="{251659264 + ident}" '
        f'behindDoc="{1 if behind else 0}" locked="0" layoutInCell="1" allowOverlap="1"><wp:simplePos x="0" y="0"/>'
        f'<wp:positionH relativeFrom="{frames[0]}"><wp:posOffset>{x}</wp:posOffset></wp:positionH>'
        f'<wp:positionV relativeFrom="{frames[1]}"><wp:posOffset>{y}</wp:posOffset></wp:positionV>'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:wrapNone/><wp:docPr id="{ident}" name="도형 {ident}"{alt}/>'
        f'<a:graphic><a:graphicData uri="{uri}">{graphic}</a:graphicData></a:graphic></wp:anchor></w:drawing></mc:Choice>'
        f'<mc:Fallback><w:pict><v:rect style="position:absolute;width:10pt;height:10pt"><v:textbox><w:txbxContent>'
        f"<w:p><w:r><w:t>대체 VML</w:t></w:r></w:p></w:txbxContent></v:textbox></v:rect></w:pict></mc:Fallback>"
        f"</mc:AlternateContent></w:r>"
    )


def shape(cx, cy, prst, properties="", *, words="", style=STYLE, xfrm=""):
    text = (
        f"<wps:txbx><w:txbxContent><w:p><w:r><w:t>{words}</w:t></w:r></w:p></w:txbxContent></wps:txbx>" if words else ""
    )
    return (
        f'<wps:wsp><wps:cNvSpPr/><wps:spPr><a:xfrm{xfrm}><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="{prst}"><a:avLst/></a:prstGeom>{properties}</wps:spPr>{style}{text}{BODY}</wps:wsp>'
    )


ARROW = '<a:ln w="19050"><a:solidFill><a:srgbClr val="404040"/></a:solidFill><a:tailEnd type="triangle"/></a:ln>'


def test_preset_geometry_follows_the_definitions_of_the_standard():
    outlines, text = drawing.geometry(
        ET.fromstring(
            '<wps:spPr xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:prstGeom prst="rightArrow"/></wps:spPr>'
        ),
        100,
        50,
    )
    assert [outline.data for outline in outlines] == ["M0 12.5L75 12.5L75 0L100 25L75 50L75 37.5L0 37.5Z"]
    assert text == (0.0, 12.5, 87.5, 37.5)
    # Adjust values override the preset's own; an unknown preset is a rectangle.
    outlines, _ = drawing.geometry(
        ET.fromstring(
            '<p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:prstGeom prst="roundRect"><a:avLst>'
            '<a:gd name="adj" fmla="val 50000"/></a:avLst></a:prstGeom></p>'
        ),
        100,
        40,
    )
    assert outlines[0].data.startswith("M0 20A20 20 0 0 1 20 0L80 0A20 20 0 0 1 100 20")
    outlines, _ = drawing.geometry(
        ET.fromstring(
            '<p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:prstGeom prst="noSuchShape"/></p>'
        ),
        10,
        10,
    )
    assert outlines[0].data == "M0 0L10 0L10 10L0 10Z"
    # The standard draws an ellipse as four quarter arcs; a single arcTo of a whole turn becomes two halves,
    # as SVG cannot draw a full turn in one.
    outlines, _ = drawing.geometry(
        ET.fromstring(
            '<p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:prstGeom prst="ellipse"/></p>'
        ),
        40,
        20,
    )
    assert outlines[0].data == "M0 10A20 10 0 0 1 20 0A20 10 0 0 1 40 10A20 10 0 0 1 20 20A20 10 0 0 1 0 10Z"
    outlines, _ = drawing.geometry(
        ET.fromstring(
            '<p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:custGeom><a:pathLst><a:path>'
            '<a:moveTo><a:pt x="0" y="10"/></a:moveTo><a:arcTo wR="20" hR="10" stAng="10800000" swAng="21600000"/>'
            "</a:path></a:pathLst></a:custGeom></p>"
        ),
        40,
        20,
    )
    assert outlines[0].data == "M0 10A20 10 0 0 1 40 10A20 10 0 0 1 0 10"
    # Every preset evaluates and gives path data.
    for name in PRESETS:
        found, _ = drawing.geometry(
            ET.fromstring(
                f'<p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:prstGeom prst="{name}"/></p>'
            ),
            120,
            80,
        )
        assert found and all(outline.data.startswith("M") for outline in found), name


def test_custom_geometry_colors_and_theme_styles_resolve_as_office_does():
    outlines, _ = drawing.geometry(
        ET.fromstring(
            '<p xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:custGeom><a:pathLst><a:path w="2" h="2">'
            '<a:moveTo><a:pt x="0" y="0"/></a:moveTo><a:lnTo><a:pt x="2" y="1"/></a:lnTo><a:cubicBezTo><a:pt x="2" y="2"/>'
            '<a:pt x="1" y="2"/><a:pt x="0" y="2"/></a:cubicBezTo></a:path></a:pathLst></a:custGeom></p>'
        ),
        100,
        50,
    )
    outline = outlines[0]
    assert outline.data == "M0 0L100 25C100 50 50 50 0 50" and not outline.closed
    assert outline.start == ((0.0, 0.0), (-100.0, -25.0)) and outline.end == ((0.0, 50.0), (-50.0, 0.0))
    palette = drawing.Palette(
        ET.fromstring(
            '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:themeElements><a:clrScheme name="t">'
            '<a:dk1><a:sysClr val="windowText" lastClr="111111"/></a:dk1><a:accent1><a:srgbClr val="4472C4"/></a:accent1>'
            "</a:clrScheme></a:themeElements></a:theme>"
        )
    )
    color = lambda xml: palette.color(
        ET.fromstring(
            f'<a:solidFill xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">{xml}</a:solidFill>'
        )
    )
    assert color('<a:schemeClr val="tx1"/>').hex() == "#111111"
    assert color('<a:schemeClr val="accent1"><a:lumMod val="75000"/></a:schemeClr>').hex() == "#2F5597"
    assert (
        color('<a:schemeClr val="accent1"><a:lumMod val="60000"/><a:lumOff val="40000"/></a:schemeClr>').hex()
        == "#8FAADC"
    )
    assert (
        color('<a:srgbClr val="FF0000"><a:alpha val="50000"/></a:srgbClr>').fill()
        == 'fill="#FF0000" fill-opacity="0.5"'
    )
    assert color('<a:prstClr val="ltGray"/>').hex() == "#D3D3D3"
    # Without a theme of its own a document has Office's: fill style 1 and line style 2 in the shape's colors.
    shape_element = ET.fromstring(
        f'<wps:wsp xmlns:wps="{SHAPE_URI}" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        f"<wps:spPr/>{STYLE}</wps:wsp>"
    )
    office = drawing.Palette()
    fill = office.fill(drawing.child(shape_element, "spPr"), drawing.child(shape_element, "style"))
    line = office.line(drawing.child(shape_element, "spPr"), drawing.child(shape_element, "style"))
    # accent1 shaded 50% is the outline Word also writes into its VML fallback: strokecolor="#2f528f".
    assert office.color(*fill).hex() == "#4472C4"
    assert line.color.hex() == "#2F528F" and line.width == pytest.approx(12700 / 9525)


def test_floating_shapes_of_a_paragraph_are_painted_together_where_word_placed_them():
    flow = (
        "<w:p><w:r><w:t>순서도</w:t></w:r>"
        + anchor(1, 0, 190500, 952500, 476250, shape(952500, 476250, "flowChartTerminator", words="요청 접수"))
        + anchor(
            2,
            1905000,
            190500,
            952500,
            476250,
            shape(952500, 476250, "rect", '<a:solidFill><a:srgbClr val="C00000"/></a:solidFill>', words="반려"),
        )
        + anchor(3, 952500, 428625, 952500, 0, shape(952500, 0, "straightConnector1", ARROW, style=""))
        + anchor(4, 0, 0, 952500, 952500, shape(952500, 952500, "ellipse"), behind=True)
        + "</w:p>"
    )
    html = convert(flow)
    body = body_of(html)
    assert (
        '<p style="margin-bottom:8pt">순서도</p><img src="data:image/svg+xml;base64,' in body and "대체 VML" not in body
    )
    [(alt, svg)] = pictures(html)
    assert alt == "도형: 요청 접수 / 반려"
    # 300 by 100 px of shapes and the reach of their outlines.
    assert (svg.get("width"), svg.get("height"), svg.get("viewBox")) == ("306", "106", "-3 -3 306 106")
    shapes = [element for element in svg if element.tag in (SVG + "path", SVG + "g", SVG + "foreignObject")]
    # Behind the text first, then in Word's order; each where Word placed it.
    assert shapes[0].get("d").startswith("M0 50A50 50") and shapes[0].get("fill") == "#4472C4"
    assert shapes[1].get("transform") == "matrix(1 0 0 1 0 20)" and shapes[1].get("stroke") == "#2F528F"
    red = next(element for element in shapes if element.get("fill") == "#C00000")
    assert red.get("transform") == "matrix(1 0 0 1 200 20)"
    line = next(element for element in shapes if element.get("stroke") == "#404040")
    assert line.get("d") == "M0 0L100 0" and line.get("transform") == "matrix(1 0 0 1 100 45)"
    head = svg.find(f"{SVG}g/{SVG}polygon")
    assert head is not None and head.get("fill") == "#404040"
    # The text sits in the text rectangle of the preset, here inside the terminator's rounded ends.
    text = svg.find(f"{SVG}foreignObject")
    assert (text.get("x"), text.get("y"), text.get("width"), text.get("height")) == ("4.71", "7.32", "90.57", "35.36")
    assert "color:#FFFFFF" in text[0].get("style") and "font-size:10pt" in text[0].get("style")
    assert "".join(text.itertext()) == "요청 접수"
    assert "[그림: 도형: 요청 접수 / 반려]" in index_markdown(html)


def test_groups_canvases_and_pictures_inside_drawings():
    group = (
        '<wpg:wgp><wpg:cNvGrpSpPr/><wpg:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="1905000" cy="952500"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="3810000" cy="1905000"/></a:xfrm></wpg:grpSpPr>'
        + shape(
            1905000,
            1905000,
            "ellipse",
            '<a:gradFill><a:gsLst><a:gs pos="0"><a:srgbClr val="9DC3E6"/></a:gs>'
            '<a:gs pos="100000"><a:srgbClr val="1F4E79"/></a:gs></a:gsLst><a:lin ang="5400000"/></a:gradFill>',
            words="사용자",
        )
        + '<pic:pic><pic:nvPicPr><pic:cNvPr id="0" name="서버" descr="서버 사진"/><pic:cNvPicPr/></pic:nvPicPr>'
        '<pic:blipFill><a:blip r:embed="rId1"/><a:srcRect l="25000"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        '<pic:spPr><a:xfrm><a:off x="1905000" y="0"/><a:ext cx="1905000" cy="1905000"/></a:xfrm><a:prstGeom prst="ellipse"/>'
        '<a:ln w="28575"><a:solidFill><a:srgbClr val="7030A0"/></a:solidFill></a:ln></pic:spPr></pic:pic></wpg:wgp>'
    )
    canvas = (
        '<wpc:wpc><wpc:bg><a:solidFill><a:srgbClr val="F2F2F2"/></a:solidFill></wpc:bg><wpc:whole><a:ln><a:noFill/></a:ln></wpc:whole>'
        + shape(952500, 476250, "rect", words="캔버스 안").replace(
            '<a:off x="0" y="0"/>', '<a:off x="476250" y="238125"/>'
        )
        + "</wpc:wpc>"
    )
    inline = lambda ident, graphic, uri: (
        f'<w:r><w:drawing><wp:inline><wp:extent cx="1905000" cy="952500"/>'
        f'<wp:docPr id="{ident}" name="그림 {ident}"/><a:graphic><a:graphicData uri="{uri}">'
        f"{graphic}</a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"
    )
    html = convert(
        "<w:p>"
        + inline(1, group, "http://schemas.microsoft.com/office/word/2010/wordprocessingGroup")
        + "</w:p>"
        + "<w:p>"
        + inline(2, canvas, "http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas")
        + "</w:p>",
        rels=[("rId1", "image", "media/server.png", False)],
        parts=binary("/word/media/server.png", png(8, 6, (0, 128, 0)), "image/png"),
    )
    (group_alt, group_svg), (canvas_alt, canvas_svg) = pictures(html)
    assert group_alt == "도형: 사용자 / 서버 사진" and canvas_alt == "도형: 캔버스 안"
    # The group's children are scaled from its child coordinates into its extent.
    ellipse = next(element for element in group_svg.iter(SVG + "path") if element.get("fill", "").startswith("url(#"))
    assert ellipse.get("d").startswith("M0 50A50 50") and group_svg.find(f"{SVG}defs/{SVG}linearGradient") is not None
    image = group_svg.find(f".//{SVG}image")
    assert (
        image.get("href").startswith("data:image/png;base64,")
        and image.get("x") == "-33.33"
        and image.get("width") == "133.33"
    )
    assert group_svg.find(f".//{SVG}clipPath") is not None
    assert any(element.get("stroke") == "#7030A0" for element in group_svg.iter(SVG + "path"))
    assert canvas_svg.find(SVG + "rect").get("fill") == "#F2F2F2"
    assert next(canvas_svg.iter(SVG + "foreignObject")).get("transform") == "matrix(1 0 0 1 50 25)"


def test_a_picture_alone_in_its_paragraph_joins_the_boxes_and_arrows_marking_it():
    screenshot = (
        '<w:r><w:drawing><wp:inline><wp:extent cx="1905000" cy="952500"/><wp:docPr id="9" name="그림 9" descr="설정 화면"/>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>'
        '<pic:nvPicPr><pic:cNvPr id="0" name="화면"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="rId1"/>'
        '<a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/>'
        '<a:ext cx="1905000" cy="952500"/></a:xfrm><a:prstGeom prst="rect"/></pic:spPr></pic:pic></a:graphicData>'
        "</a:graphic></wp:inline></w:drawing></w:r>"
    )
    box = shape(
        476250,
        285750,
        "rect",
        '<a:noFill/><a:ln w="28575"><a:solidFill><a:srgbClr val="FF0000"/></a:solidFill></a:ln>',
        style="",
    )
    html = convert(
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
        + screenshot
        + anchor(5, 2381250, 285750, 476250, 285750, box)
        + "</w:p>"
        + "<w:p><w:r><w:t>설명</w:t></w:r>"
        + screenshot
        + anchor(6, 0, 0, 476250, 285750, box)
        + "</w:p>",
        rels=[("rId1", "image", "media/screen.png", False)],
        parts=binary("/word/media/screen.png", png(20, 10), "image/png"),
    )
    found = pictures(html)
    assert [alt for alt, _ in found] == ["도형", "도형"]
    marked = found[0][1]
    # A4 with 1-inch margins: a 200 px picture centered in the 601.7 px column starts at 200.87 px.
    image = marked.find(f".//{SVG}image")
    assert image.get("width") == "200" and marked.find(SVG + "g").get("transform") == "matrix(1 0 0 1 200.87 0)"
    red = next(element for element in marked.iter(SVG + "path") if element.get("stroke") == "#FF0000")
    assert red.get("transform") == "matrix(1 0 0 1 250 30)"
    # Beside text the picture stays in its line, and only the box is painted after the paragraph.
    body = body_of(html)
    assert re.search(
        r'<p style="margin-bottom:8pt">설명<img src="data:image/png;base64,[^"]+" alt="설정 화면" width="200" '
        r'height="100"></p><img src="data:image/svg',
        body,
    )
    # The marked picture keeps its place in the column.
    assert 'max-width:451.3pt"><div style="margin-left:199px"><img src="data:image/svg+xml;base64,' in body
    assert found[1][1].find(f".//{SVG}image") is None


def test_text_boxes_the_page_draws_keep_their_text_and_others_are_painted():
    plain = (
        '<wps:wsp><wps:cNvSpPr txBox="1"/><wps:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="2857500" cy="571500"/></a:xfrm>'
        '<a:prstGeom prst="rect"/><a:solidFill><a:srgbClr val="FFF2CC"/></a:solidFill><a:ln w="12700"><a:solidFill>'
        '<a:srgbClr val="BF9000"/></a:solidFill><a:prstDash val="dash"/></a:ln></wps:spPr><wps:txbx><w:txbxContent><w:p>'
        '<w:r><w:rPr><w:b/></w:rPr><w:t>참고:</w:t></w:r><w:r><w:t xml:space="preserve"> 검색되는 글</w:t></w:r></w:p>'
        '</w:txbxContent></wps:txbx><wps:bodyPr lIns="91440" tIns="45720" rIns="91440" bIns="45720" anchor="t"/></wps:wsp>'
    )
    rotated = shape(952500, 476250, "rect", words="기울인 상자", xfrm=' rot="1800000"')
    html = convert(
        "<w:p>"
        + anchor(1, 0, 0, 2857500, 571500, plain)
        + "</w:p><w:p>"
        + anchor(2, 0, 0, 952500, 476250, rotated)
        + "</w:p>"
    )
    body = body_of(html)
    assert (
        '<div class="textbox" style="width:300px;max-width:100%;min-height:60px;box-sizing:border-box;display:flex;'
        "flex-direction:column;justify-content:flex-start;padding:4.8px 9.6px 4.8px 9.6px;background-color:#FFF2CC;"
        'border:1.33px dashed #BF9000"><p style="margin-bottom:8pt"><span style="font-weight:bold">참고:</span> 검색되는 글</p>'
        "</div>"
    ) in body
    [(alt, svg)] = pictures(html)
    assert alt == "도형: 기울인 상자" and "matrix(0.87 0.5 -0.5 0.87" in svg.find(SVG + "path").get("transform")
    assert "참고: 검색되는 글" in index_markdown(html)
    # A text box of more than a label stays text on the page beside the drawings of its paragraph.
    long = plain.replace("검색되는 글", "긴 글 " * 80)
    html = convert(
        "<w:p>" + anchor(1, 0, 0, 2857500, 571500, long) + anchor(2, 0, 0, 952500, 476250, rotated) + "</w:p>"
    )
    [(alt, _)] = pictures(html)
    assert alt == "도형: 기울인 상자" and re.search(
        r'alt="도형: 기울인 상자"[^>]*><div class="textbox"[^>]*><p[^>]*>.*긴 글 긴 글', html
    )


def test_vml_shapes_of_older_documents_are_painted():
    shapes = (
        "<w:p><w:r><w:t>VML</w:t></w:r><w:r><w:pict>"
        '<v:shapetype id="_x0000_t13" coordsize="21600,21600" o:spt="13" adj="16200,5400" '
        'path="m@0,l@0@1,0@1,0@2@0@2@0,21600,21600,10800xe"><v:formulas><v:f eqn="val #0"/><v:f eqn="val #1"/>'
        '<v:f eqn="sum height 0 #1"/><v:f eqn="sum 10800 0 #1"/><v:f eqn="sum width 0 #0"/><v:f eqn="prod @4 @3 10800"/>'
        '<v:f eqn="sum width 0 @5"/></v:formulas><v:path textboxrect="0,@1,@6,@2"/></v:shapetype>'
        '<v:rect style="position:absolute;margin-left:0;margin-top:15pt;width:75pt;height:37.5pt;z-index:2" fillcolor="#c0504d" '
        'strokecolor="#632423" strokeweight="1.5pt"><v:textbox><w:txbxContent><w:p><w:r><w:t>VML 상자</w:t></w:r></w:p>'
        "</w:txbxContent></v:textbox></v:rect>"
        '<v:shape type="#_x0000_t13" style="position:absolute;margin-left:90pt;margin-top:15pt;width:75pt;height:37.5pt;z-index:3" '
        'fillcolor="#4f81bd" strokecolor="#243f60"/>'
        '<v:oval style="position:absolute;margin-left:180pt;margin-top:0;width:60pt;height:60pt;z-index:1" filled="f"/>'
        '<v:line style="position:absolute;z-index:4" from="0,75pt" to="240pt,75pt"><v:stroke endarrow="block" dashstyle="dash"/></v:line>'
        "</w:pict></w:r></w:p>"
    )
    html = convert(shapes)
    [(alt, svg)] = pictures(html)
    assert alt == "도형: VML 상자" and "대체" not in html
    paths = list(svg.iter(SVG + "path"))
    arrow = next(element for element in paths if element.get("fill") == "#4F81BD")
    assert arrow.get("d") == "M75 0L75 12.5L0 12.5L0 37.5L75 37.5L75 50L100 25Z"
    assert arrow.get("transform") == "matrix(1 0 0 1 120 20)"
    oval = next(element for element in paths if element.get("d", "").startswith("M80 40A40 40"))
    assert oval.get("fill") == "none" and oval.get("stroke") == "#000000"
    line = next(element for element in paths if element.get("stroke-dasharray"))
    assert line.get("d") == "M0 0L320 0" and line.get("transform") == "matrix(1 0 0 1 0 100)"
    assert svg.find(f".//{SVG}polygon") is not None
    # A VML text box of plain colors, alone, keeps its text on the page.
    html = convert(
        '<w:p><w:r><w:pict><v:rect style="position:absolute;width:150pt;height:30pt" fillcolor="yellow" stroked="f">'
        '<v:textbox inset="0,0,0,0"><w:txbxContent><w:p><w:r><w:t>노란 상자</w:t></w:r></w:p></w:txbxContent>'
        "</v:textbox></v:rect></w:pict></w:r></w:p>"
    )
    assert pictures(html) == [] and (
        '<div class="textbox" style="width:200px;max-width:100%;min-height:40px;'
        'box-sizing:border-box;padding:0px 0px 0px 0px;background-color:#FFFF00;border:none">'
        '<p style="margin-bottom:8pt">노란 상자</p></div>'
    ) in body_of(html)


def test_vml_paths_formulas_and_colors():
    assert vml.path_tokens("m,l21600,21600nfe") == [("m", ["0", "0"]), ("l", ["21600", "21600"]), ("nf", []), ("e", [])]
    assert vml.path_tokens("m@0,l@0@1,0@1xe") == [
        ("m", ["@0", "0"]),
        ("l", ["@0", "@1", "0", "@1"]),
        ("x", []),
        ("e", []),
    ]
    formulas = vml.Formulas(
        ["val #0", "sum @0 10 0", "prod width 1 2", "if #1 @1 7", "sumangle #0 90 0"],
        [5.0, -1.0],
        (21600.0, 21600.0),
        (0.0, 0.0),
    )
    assert formulas.values == [5.0, 15.0, 10800.0, 7.0, 5.0 + 90 * 65536]
    assert vml.color_of("#4f81bd").hex() == "#4F81BD" and vml.color_of("black [3213]").hex() == "#000000"
    assert vml.color_of("fill darken(128)", drawing.Color(1, 1, 1)).hex() == "#808080"
    assert vml.color_of("windowText").hex() == "#000000" and vml.color_of("nonsense") is None
    assert (
        vml.length("72pt") == 96
        and vml.length("1in") == 96
        and vml.length("20") == 20
        and vml.fraction("32768f") == 0.5
    )


def test_smartart_is_painted_from_the_layout_office_saved():
    data = (
        '<dgm:dataModel><dgm:ptLst><dgm:pt modelId="{1}" type="doc"/><dgm:pt modelId="{2}"><dgm:t><a:p><a:r><a:t>기획</a:t></a:r></a:p>'
        '</dgm:t></dgm:pt></dgm:ptLst><dgm:cxnLst><dgm:cxn modelId="{3}" srcId="{1}" destId="{2}" srcOrd="0"/></dgm:cxnLst>'
        '<dgm:extLst><a:ext uri="http://schemas.microsoft.com/office/drawing/2008/diagram"><dsp:dataModelExt relId="rId14"/></a:ext>'
        "</dgm:extLst></dgm:dataModel>"
    )
    layout = (
        '<dsp:drawing><dsp:spTree><dsp:nvGrpSpPr><dsp:cNvPr id="0" name=""/><dsp:cNvGrpSpPr/></dsp:nvGrpSpPr><dsp:grpSpPr/>'
        '<dsp:sp modelId="{9}"><dsp:nvSpPr><dsp:cNvPr id="0" name=""/><dsp:cNvSpPr/></dsp:nvSpPr><dsp:spPr><a:xfrm>'
        '<a:off x="95250" y="95250"/><a:ext cx="1714500" cy="952500"/></a:xfrm><a:prstGeom prst="chevron"/>'
        '<a:solidFill><a:schemeClr val="accent2"/></a:solidFill></dsp:spPr><dsp:style><a:lnRef idx="0"><a:scrgbClr r="0" g="0" b="0"/></a:lnRef>'
        '<a:fillRef idx="1"><a:scrgbClr r="0" g="0" b="0"/></a:fillRef><a:effectRef idx="0"><a:scrgbClr r="0" g="0" b="0"/></a:effectRef>'
        '<a:fontRef idx="minor"><a:schemeClr val="lt1"/></a:fontRef></dsp:style><dsp:txBody><a:bodyPr anchor="ctr">'
        '<a:normAutofit fontScale="50000"/></a:bodyPr><a:lstStyle/><a:p><a:pPr algn="ctr"/><a:r><a:rPr lang="ko-KR" sz="2400" b="1"/>'
        '<a:t>기획</a:t></a:r></a:p></dsp:txBody><dsp:txXfrm><a:off x="571500" y="95250"/><a:ext cx="762000" cy="952500"/></dsp:txXfrm>'
        "</dsp:sp></dsp:spTree></dsp:drawing>"
    )
    smartart = (
        '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="1905000" cy="1143000"/><wp:docPr id="1" name="다이어그램 1"/><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/diagram"><dgm:relIds r:dm="rId10" r:lo="rId11" '
        'r:qs="rId12" r:cs="rId13"/></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>'
    )
    rels = [
        ("rId10", "diagramData", "diagrams/data1.xml", False),
        ("rId14", "diagramDrawing", "diagrams/drawing1.xml", False),
    ]
    html = convert(
        smartart, rels=rels, parts=part("/word/diagrams/data1.xml", data) + part("/word/diagrams/drawing1.xml", layout)
    )
    [(alt, svg)] = pictures(html)
    assert alt == "SmartArt: 기획" and svg.get("width") == "202" and svg.get("height") == "122"
    assert next(svg.iter(SVG + "path")).get("fill") == "#ED7D31"
    text = svg.find(SVG + "foreignObject")
    assert text.get("transform") == "matrix(1 0 0 1 60 10)" and "color:#FFFFFF" in text[0].get("style")
    assert "font-size:12pt;font-weight:bold" in ET.tostring(text, encoding="unicode")
    # Without the layout, SmartArt stays the outline of its text.
    html = convert(smartart, rels=rels[:1], parts=part("/word/diagrams/data1.xml", data))
    assert (
        pictures(html) == []
        and '<figure class="smartart"><figcaption>SmartArt: 다이어그램 1</figcaption><ul><li>기획</li></ul></figure>'
        in html
    )


def test_a_drawing_that_cannot_be_painted_never_costs_the_document(monkeypatch):
    def broken(*args):
        raise ValueError("unexpected drawing")

    monkeypatch.setattr(drawing.Painter, "top", broken)
    html = convert(
        "<w:p><w:r><w:t>본문</w:t></w:r>"
        + anchor(1, 0, 0, 952500, 476250, shape(952500, 476250, "ellipse", words="타원"))
        + anchor(
            2,
            0,
            0,
            952500,
            476250,
            '<wps:wsp><wps:spPr><a:prstGeom prst="rect"/></wps:spPr><wps:txbx><w:txbxContent>'
            "<w:p><w:r><w:t>글상자</w:t></w:r></w:p></w:txbxContent></wps:txbx><wps:bodyPr/></wps:wsp>",
        )
        + "</w:p>"
    )
    body = body_of(html)
    assert (
        '<span class="missing">[그리지 못한 도형]</span>' in body
        and '<p style="margin-bottom:8pt">글상자</p></div>' in body
    )
    assert '<p style="margin-bottom:8pt">본문</p>' in body
    # Nor does a shape that fails before it is painted, DrawingML or VML.
    monkeypatch.setattr(drawing, "box_css", broken)
    monkeypatch.setattr("agent.word_html.vml_box_css", broken)
    html = convert(
        "<w:p><w:r><w:t>본문</w:t></w:r>"
        + anchor(1, 0, 0, 952500, 476250, shape(952500, 476250, "rect", words="상자"))
        + '</w:p><w:p><w:r><w:pict><v:rect style="width:10pt;height:10pt"><v:textbox><w:txbxContent><w:p><w:r>'
        "<w:t>VML</w:t></w:r></w:p></w:txbxContent></v:textbox></v:rect></w:pict></w:r></w:p>"
    )
    assert body_of(html).count("[그리지 못한 도형]") == 2 and "본문" in html


def test_choices_of_shapes_are_read_and_others_fall_back():
    html = convert(
        '<w:p><w:r><mc:AlternateContent><mc:Choice Requires="w14"><w:t>새 기능</w:t></mc:Choice>'
        "<mc:Fallback><w:t>대체 글</w:t></mc:Fallback></mc:AlternateContent></w:r></w:p>"
    )
    assert "대체 글" in html and "새 기능" not in html


def test_shape_text_beyond_the_alternative_text_of_its_picture_is_said_at_the_top():
    words = "긴 설명 " * 900  # more than drawing.MAX_TEXT characters in one painted shape
    html = convert(
        "<w:p><w:r><w:t>그림</w:t></w:r>"
        + anchor(1, 0, 0, 952500, 952500, shape(952500, 952500, "ellipse", words=words.strip()))
        + "</w:p>"
    )
    [(alt, _)] = pictures(html)
    assert len(alt) < drawing.MAX_TEXT + 20 and alt.endswith("…")
    assert re.search(
        r'<div class="doc word"[^>]*><p class="caption">도형 속 글이 길어 그림의 대체 텍스트\(검색·AI가 읽는 '
        r"글\)에는 그림마다 앞 4,000자만 넣었습니다\.</p>",
        html,
    )
    # A drawing whose text fits says nothing.
    short = anchor(1, 0, 0, 952500, 952500, shape(952500, 952500, "ellipse", words="짧음"))
    assert "대체 텍스트" not in convert(f"<w:p>{short}</w:p>")
