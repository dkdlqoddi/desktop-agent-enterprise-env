import base64
import io
import re

import pytest
from PIL import Image

from agent.html_document import index_markdown
from agent.llm import UserError
from agent.word_html import list_number, word_xml_to_html

NAMESPACES = (
    'xmlns:pkg="http://schemas.microsoft.com/office/2006/xmlPackage" '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:v="urn:schemas-microsoft-com:vml" xmlns:o="urn:schemas-microsoft-com:office:office" '
    'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" '
    'xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
)
RELATIONS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def part(name, xml):
    return f'<pkg:part pkg:name="{name}"><pkg:xmlData>{xml}</pkg:xmlData></pkg:part>'


def binary(name, data, content_type):
    return (
        f'<pkg:part pkg:name="{name}" pkg:contentType="{content_type}" pkg:compression="store">'
        f"<pkg:binaryData>{base64.b64encode(data).decode()}</pkg:binaryData></pkg:part>"
    )


def relations(*items):
    rows = "".join(
        f'<Relationship Id="{ident}" Type="{RELATIONS}/{kind}" Target="{target}"'
        + (' TargetMode="External"' if external else "")
        + "/>"
        for ident, kind, target, external in items
    )
    return f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rows}</Relationships>'


def package(body, *, styles="", numbering="", rels=(), parts="", section=""):
    xml = (
        f'<?xml version="1.0" standalone="yes"?><pkg:package {NAMESPACES}>'
        + part("/word/document.xml", f"<w:document><w:body>{body}{section}</w:body></w:document>")
        + (part("/word/styles.xml", f"<w:styles>{styles}</w:styles>") if styles else "")
        + (part("/word/numbering.xml", f"<w:numbering>{numbering}</w:numbering>") if numbering else "")
        + (part("/word/_rels/document.xml.rels", relations(*rels)) if rels else "")
        + parts
        + "</pkg:package>"
    )
    return xml


def body_of(html):
    return html[html.index('<div class="doc') : html.rindex("</div>")]


def png(width=4, height=3, color=(255, 0, 0)):
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


STYLES = """
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:asciiTheme="minorHAnsi" w:eastAsiaTheme="minorEastAsia" w:hAnsiTheme="minorHAnsi"/>
    <w:sz w:val="20"/></w:rPr></w:rPrDefault>
    <w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="259" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="a"><w:name w:val="Normal"/><w:pPr><w:jc w:val="both"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="1"><w:name w:val="heading 1"/><w:basedOn w:val="a"/>
    <w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr>
    <w:rPr><w:rFonts w:asciiTheme="majorHAnsi" w:eastAsiaTheme="majorEastAsia"/><w:b/><w:color w:val="2F5496"/><w:sz w:val="32"/></w:rPr></w:style>
  <w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="a"/>
    <w:pPr><w:jc w:val="center"/><w:ind w:left="864" w:right="864"/></w:pPr><w:rPr><w:i/></w:rPr></w:style>
  <w:style w:type="character" w:styleId="Strong"><w:name w:val="Strong"/><w:rPr><w:b/></w:rPr></w:style>
  <w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/></w:style>
  <w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:basedOn w:val="TableNormal"/>
    <w:tblPr><w:tblBorders><w:top w:val="single" w:sz="4" w:color="auto"/><w:left w:val="single" w:sz="4" w:color="auto"/>
      <w:bottom w:val="single" w:sz="4" w:color="auto"/><w:right w:val="single" w:sz="4" w:color="auto"/>
      <w:insideH w:val="single" w:sz="4" w:color="auto"/><w:insideV w:val="single" w:sz="4" w:color="auto"/></w:tblBorders></w:tblPr>
    <w:tblStylePr w:type="firstRow"><w:rPr><w:b/><w:color w:val="FFFFFF"/></w:rPr><w:tcPr><w:shd w:val="clear" w:fill="4472C4"/></w:tcPr></w:tblStylePr>
  </w:style>"""  # noqa: E501
THEME = part(
    "/word/theme/theme1.xml",
    """<a:theme><a:themeElements><a:fontScheme>
  <a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:font script="Hang" typeface="맑은 고딕"/></a:majorFont>
  <a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:font script="Hang" typeface="맑은 고딕"/></a:minorFont>
  </a:fontScheme></a:themeElements></a:theme>""",  # noqa: E501
)
A4 = (
    '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1701" w:right="1440" w:bottom="1440" w:left="1440"/>'
    "</w:sectPr>"
)


def test_formatting_is_resolved_through_defaults_theme_styles_and_direct_formatting():
    html = word_xml_to_html(
        package(
            """
      <w:p><w:pPr><w:pStyle w:val="1"/></w:pPr><w:r><w:t>개요</w:t></w:r></w:p>
      <w:p><w:r><w:t xml:space="preserve">기본 </w:t></w:r>
        <w:r><w:rPr><w:rStyle w:val="Strong"/></w:rPr><w:t>굵게</w:t></w:r>
        <w:r><w:rPr><w:i/><w:u w:val="double"/><w:color w:val="FF0000"/><w:sz w:val="28"/></w:rPr><w:t>강조</w:t></w:r>
        <w:r><w:rPr><w:highlight w:val="yellow"/><w:strike/></w:rPr><w:t>형광</w:t></w:r>
        <w:r><w:rPr><w:rFonts w:ascii="Arial" w:eastAsia="궁서"/><w:vertAlign w:val="superscript"/></w:rPr><w:t>위</w:t></w:r>
        <w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t>보통</w:t></w:r></w:p>
      <w:p><w:pPr><w:pStyle w:val="Quote"/><w:spacing w:before="240" w:after="0" w:line="360" w:lineRule="auto"/>
        <w:ind w:firstLine="400"/><w:shd w:val="clear" w:fill="F2F2F2"/>
        <w:pBdr><w:left w:val="single" w:sz="12" w:space="4" w:color="808080"/></w:pBdr></w:pPr>
        <w:r><w:t>인용</w:t></w:r></w:p>
      <w:p><w:pPr><w:jc w:val="right"/><w:ind w:leftChars="200" w:hanging="300"/></w:pPr><w:r><w:t>오른쪽</w:t></w:r></w:p>
    """,  # noqa: E501
            styles=STYLES,
            parts=THEME,
            section=A4,
        ),
        "보고서",
    )
    assert "<title>보고서</title>" in html
    # The page's text width, Normal's font, size, spacing and alignment on the root.
    root = re.search(r'<div class="doc word" style="([^"]*)"', html).group(1)
    assert root == (
        "font-family:&quot;Calibri&quot;,&quot;맑은 고딕&quot;;font-size:10pt;text-align:justify;"
        "line-height:1.295;max-width:451.3pt"
    )
    body = body_of(html)
    assert (
        '<h1 style="margin-bottom:8pt;font-family:&quot;Calibri Light&quot;,&quot;맑은 고딕&quot;;font-size:16pt;'
        'font-weight:bold;color:#2F5496">개요</h1>'
    ) in body
    assert '<span style="font-weight:bold">굵게</span>' in body
    assert (
        '<span style="font-size:14pt;font-style:italic;text-decoration:underline double;color:#FF0000">강조</span>'
        in body
    )
    assert '<span style="text-decoration:line-through;background-color:#FFFF00">형광</span>' in body
    assert '<span style="font-family:&quot;Arial&quot;,&quot;궁서&quot;"><sup>위</sup></span>' in body
    assert ">보통<" in body and "font-weight:normal" not in body
    assert (
        '<p style="text-align:center;margin-top:12pt;line-height:1.8;margin-left:43.2pt;margin-right:43.2pt;'
        "text-indent:20pt;background-color:#F2F2F2;border-left:1.5pt solid #808080;padding-left:4pt;"
        'font-style:italic">인용</p>'
    ) in body
    assert '<p style="text-align:right;margin-bottom:8pt;margin-left:2em;text-indent:-15pt">오른쪽</p>' in body


NUMBERING = """
  <w:abstractNum w:abstractNumId="0">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/>
      <w:pPr><w:ind w:left="400" w:hanging="400"/></w:pPr></w:lvl>
    <w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="ganada"/><w:lvlText w:val="%1.%2)"/>
      <w:pPr><w:ind w:left="800" w:hanging="400"/></w:pPr></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="1">
    <w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="\uf0b7"/><w:suff w:val="space"/>
      <w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr></w:lvl>
    <w:lvl w:ilvl="1"><w:numFmt w:val="bullet"/><w:lvlText w:val="\uf0e8"/><w:rPr><w:rFonts w:ascii="Wingdings 3"/></w:rPr></w:lvl>
  </w:abstractNum>
  <w:abstractNum w:abstractNumId="2">
    <w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimalEnclosedCircle"/><w:lvlText w:val="%1"/></w:lvl>
  </w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>
  <w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
  <w:num w:numId="3"><w:abstractNumId w:val="0"/></w:num>
  <w:num w:numId="4"><w:abstractNumId w:val="0"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride></w:num>
  <w:num w:numId="5"><w:abstractNumId w:val="2"/></w:num>"""  # noqa: E501


def item(ident, level, text):
    return (
        f'<w:p><w:pPr><w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="{ident}"/></w:numPr></w:pPr>'
        f"<w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def test_lists_carry_the_numbers_and_bullets_word_draws():
    html = word_xml_to_html(
        package(
            item(1, 0, "첫째")
            + item(1, 1, "하위 가")
            + item(1, 1, "하위 나")
            + item(1, 0, "둘째")
            + item(1, 1, "다시 가")
            # Another instance of the same list continues it; "restart at 1" starts again.
            + item(3, 0, "셋째")
            + item(4, 0, "새 목록")
            + item(2, 0, "글머리")
            + item(2, 1, "화살표")
            + item(5, 0, "원 숫자"),
            numbering=NUMBERING,
        ),
        "목록",
    )
    body = body_of(html)
    labels = re.findall(r'<p[^>]*><span style="([^"]*)">([^<]*)</span>', body)
    assert [text for _, text in labels][:7] == ["1. ", "1.가) ", "1.나) ", "2. ", "2.가) ", "3. ", "1. "]
    # A hanging number takes the indent's width, so the text starts at the indent, as at Word's tab stop.
    assert labels[0][0] == "display:inline-block;min-width:20pt;text-indent:0"
    assert (
        '<p style="margin-left:40pt;text-indent:-20pt">'
        '<span style="display:inline-block;min-width:20pt;text-indent:0">2.가) </span>다시 가</p>' in body
    )
    # A Symbol bullet becomes Unicode; a symbol without a known match keeps its font for Windows to draw.
    assert "<p>• 글머리</p>" in body
    assert '<span style="font-family:&quot;Wingdings 3&quot;">è</span>\t화살표' in body
    assert "<p>①\t원 숫자</p>" in body
    text = index_markdown(html)
    assert "1\\. 첫째" in text and "1.가) 하위 가" in text and "• 글머리" in text


@pytest.mark.parametrize(
    "value,kind,expected",
    [
        (4, "upperRoman", "IV"),
        (9, "lowerRoman", "ix"),
        (28, "upperLetter", "BB"),
        (3, "lowerLetter", "c"),
        (15, "ganada", "가"),
        (2, "chosung", "ㄴ"),
        (21, "koreanDigital", "이십일"),
        (3, "koreanCounting", "셋"),
        (12, "chineseCounting", "十二"),
        (3, "ideographTraditional", "丙"),
        (5, "decimalZero", "05"),
        (2, "ordinal", "2nd"),
        (7, "decimalFullWidth", "７"),
        (25, "decimalEnclosedCircle", "25"),
        (3, "none", ""),
    ],
)
def test_list_number_formats(value, kind, expected):
    assert list_number(value, kind) == expected


def test_tables_keep_grid_merged_cells_borders_and_style_formatting():
    html = word_xml_to_html(
        package(
            """
      <w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblW w:w="5000" w:type="pct"/><w:jc w:val="center"/>
          <w:tblLook w:val="04A0" w:firstRow="1" w:lastRow="0" w:firstColumn="1" w:lastColumn="0" w:noHBand="0" w:noVBand="1"/></w:tblPr>
        <w:tblGrid><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/><w:gridCol w:w="2000"/></w:tblGrid>
        <w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc><w:tcPr><w:gridSpan w:val="2"/></w:tcPr><w:p><w:r><w:t>합친 머리글</w:t></w:r></w:p></w:tc>
          <w:tc><w:p><w:r><w:t>c</w:t></w:r></w:p></w:tc></w:tr>
        <w:tr><w:trPr><w:trHeight w:val="400" w:hRule="exact"/></w:trPr>
          <w:tc><w:tcPr><w:vMerge w:val="restart"/><w:vAlign w:val="center"/></w:tcPr><w:p><w:r><w:t>구분</w:t></w:r></w:p></w:tc>
          <w:tc><w:tcPr><w:shd w:val="clear" w:fill="FFF2CC"/><w:tcBorders><w:bottom w:val="double" w:sz="4" w:color="FF0000"/></w:tcBorders></w:tcPr>
            <w:p><w:r><w:t>b</w:t></w:r></w:p></w:tc>
          <w:tc><w:p><w:r><w:t>내부</w:t></w:r></w:p>
            <w:tbl><w:tr><w:tc><w:p><w:r><w:t>안쪽 표</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:tc></w:tr>
        <w:tr><w:tc><w:tcPr><w:vMerge/></w:tcPr><w:p/></w:tc>
          <w:tc><w:p><w:r><w:t>e</w:t></w:r></w:p></w:tc><w:tc><w:p/></w:tc></w:tr>
      </w:tbl>""",  # noqa: E501
            styles=STYLES,
        ),
        "표",
    )
    body = body_of(html)
    assert '<table style="width:100%;margin-left:auto;margin-right:auto">' in body
    assert (
        '<colgroup><col style="width:100pt"><col style="width:100pt"><col style="width:100pt"></colgroup><thead><tr>'
        in body
    )
    first = re.search(r'<td colspan="2" style="([^"]*)">', body).group(1)
    # The style's header row: its fill and white bold text, with the Table Grid borders around it.
    assert first == (
        "border-top:0.5pt solid #000000;border-bottom:0.5pt solid #000000;border-left:0.5pt solid #000000;"
        "border-right:0.5pt solid #000000;background-color:#4472C4;padding-top:0pt;padding-bottom:0pt;"
        "padding-left:5.4pt;padding-right:5.4pt;font-weight:bold;color:#FFFFFF"
    )
    assert "</thead>" in body and '<tr style="height:20pt">' in body
    merged = re.search(r'<td rowspan="2" style="([^"]*)"><p style="margin-bottom:8pt">구분</p></td>', body).group(1)
    assert "vertical-align:middle" in merged
    assert "border-bottom:2.25pt double #FF0000" in body and "background-color:#FFF2CC" in body
    assert (
        '<table><tr><td style="padding-top:0pt;padding-bottom:0pt;padding-left:5.4pt;padding-right:5.4pt">'
        '<p style="margin-bottom:8pt">안쪽 표</p></td></tr></table>' in body
    )
    # The continuing merged cell is left out, so the last row has two cells; an empty one keeps its line.
    last = body[body.rindex("<tr>") :]
    assert last.count("<td") == 2 and '<p style="margin-bottom:8pt"><br></p>' in last
    assert "| 합친 머리글 |" in index_markdown(html)


def test_table_styles_shape_their_paragraphs_and_banding_starts_after_the_header_row():
    # Word's Table Grid: single spacing without space after, over the document's 8pt after.
    grid = STYLES.replace(
        '<w:basedOn w:val="TableNormal"/>\n    <w:tblPr>',
        '<w:basedOn w:val="TableNormal"/><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/>'
        "</w:pPr>\n    <w:tblPr>",
    )
    banded = grid.replace(
        '<w:tblStylePr w:type="firstRow">',
        '<w:tblStylePr w:type="band1Horz"><w:tcPr><w:shd w:val="clear" w:fill="D9E2F3"/></w:tcPr></w:tblStylePr>'
        '<w:tblStylePr w:type="firstRow">',
    )
    rows = "".join(f"<w:tr><w:tc><w:p><w:r><w:t>행 {index}</w:t></w:r></w:p></w:tc></w:tr>" for index in range(4))
    html = word_xml_to_html(
        package(
            '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/><w:tblLook w:firstRow="1" w:noHBand="0"/></w:tblPr>'
            f"{rows}</w:tbl>",
            styles=banded,
        ),
        "표 스타일",
    )
    cells = re.findall(r'<td style="([^"]*)"><p style="line-height:normal">행 (\d)</p></td>', html)
    assert [number for _, number in cells] == ["0", "1", "2", "3"]
    fills = ["#4472C4" if "#4472C4" in style else "#D9E2F3" if "#D9E2F3" in style else "" for style, _ in cells]
    # The header row has its own fill; the first band is the row after it.
    assert fills == ["#4472C4", "#D9E2F3", "", "#D9E2F3"]


def test_contextual_spacing_drops_space_between_paragraphs_of_the_same_style():
    styles = STYLES + (
        '<w:style w:type="paragraph" w:styleId="a3"><w:name w:val="List Paragraph"/><w:basedOn w:val="a"/>'
        '<w:pPr><w:ind w:left="800"/><w:contextualSpacing/></w:pPr></w:style>'
    )
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="a3"/></w:pPr><w:r><w:t>항목 {index}</w:t></w:r></w:p>' for index in range(3)
    )
    html = word_xml_to_html(package(body + "<w:p><w:r><w:t>본문</w:t></w:r></w:p>", styles=styles), "목록 단락")
    assert re.findall(r'<p style="([^"]*)">항목', html) == [
        "margin-left:40pt",
        "margin-left:40pt",
        "margin-bottom:8pt;margin-left:40pt",
    ]


def test_pictures_come_from_the_package_and_unshowable_ones_leave_a_marker():
    picture = png(8, 6)
    tiff = io.BytesIO()
    Image.new("RGB", (5, 5), (0, 128, 0)).save(tiff, "TIFF")
    drawing = """<w:r><w:drawing><wp:inline><wp:extent cx="1905000" cy="952500"/><wp:docPr id="1" name="그림 1" descr="{alt}"/>
      <a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic>
        <pic:blipFill><a:blip r:{kind}="{ident}"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>"""  # noqa: E501
    html = word_xml_to_html(
        package(
            "<w:p>"
            + drawing.format(alt="조직도", kind="embed", ident="rId1")
            + "</w:p>"
            + "<w:p>"
            + drawing.format(alt="스캔", kind="embed", ident="rId2")
            + "</w:p>"
            + "<w:p>"
            + drawing.format(alt="원격", kind="link", ident="rId3")
            + "</w:p>"
            + "<w:p>"
            + drawing.format(alt="", kind="embed", ident="rId4")
            + "</w:p>"
            + '<w:p><w:r><w:pict><v:shape style="width:30pt;height:15pt">'
            '<v:imagedata r:id="rId1" o:title="VML 그림"/></v:shape></w:pict></w:r></w:p>',
            rels=[
                ("rId1", "image", "media/image1.png", False),
                ("rId2", "image", "media/scan.tif", False),
                ("rId3", "image", "https://example.com/a.png", True),
                ("rId4", "image", "media/chart.emf", False),
            ],
            parts=binary("/word/media/image1.png", picture, "image/png")
            + binary("/word/media/scan.tif", tiff.getvalue(), "image/tiff")
            + binary(
                "/word/media/chart.emf", b"\x01\x00\x00\x00" + b"\x00" * 36 + b" EMF" + b"\x00" * 60, "image/x-emf"
            ),
        ),
        "그림",
    )
    body = body_of(html)
    data = base64.b64encode(picture).decode()
    # The extent sets the size in pixels (EMU / 9525); the stylesheet keeps proportions in a narrow window.
    assert f'<img src="data:image/png;base64,{data}" alt="조직도" width="200" height="100">' in body
    converted = re.search(r'<img src="data:image/png;base64,([^"]+)" alt="스캔"', body).group(1)
    assert Image.open(io.BytesIO(base64.b64decode(converted))).size == (5, 5)
    assert '<span class="missing">[연결된 그림: 원격]</span>' in body
    # A metafile is drawn by Windows; elsewhere it stays a marker.
    assert '<span class="missing">[그림 (EMF, 표시할 수 없는 형식): 그림 1]</span>' in body or 'alt="그림 1"' in body
    assert f'<img src="data:image/png;base64,{data}" alt="VML 그림" width="40" height="20">' in body
    text = index_markdown(html)
    assert "[그림: 조직도]" in text and "base64" not in text


def test_links_bookmarks_fields_notes_and_comments():
    html = word_xml_to_html(
        package(
            """
      <w:p><w:hyperlink r:id="rId1" w:tooltip="팀 위키"><w:r><w:t>위키</w:t></w:r></w:hyperlink>
        <w:r><w:t xml:space="preserve"> · </w:t></w:r><w:hyperlink w:anchor="_Toc1"><w:r><w:t>개요로</w:t></w:r></w:hyperlink>
        <w:r><w:t xml:space="preserve"> · </w:t></w:r><w:hyperlink r:id="rId2"><w:r><w:t>공유 폴더</w:t></w:r></w:hyperlink>
        <w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> HYPERLINK "https://jira.test/A-1" \\o "티켓" </w:instrText></w:r>
        <w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>A-1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>
        <w:r><w:t>각주</w:t></w:r><w:r><w:footnoteReference w:id="2"/></w:r>
        <w:commentRangeStart w:id="0"/><w:r><w:t>메모 단</w:t></w:r><w:commentRangeEnd w:id="0"/><w:r><w:commentReference w:id="0"/></w:r>
        <w:r><w:t>미주</w:t></w:r><w:r><w:endnoteReference w:id="1"/></w:r></w:p>
      <w:p><w:pPr><w:pStyle w:val="1"/></w:pPr><w:bookmarkStart w:id="0" w:name="_Toc1"/><w:r><w:t>개요</w:t></w:r><w:bookmarkEnd w:id="0"/></w:p>
      <w:p><w:fldSimple w:instr=" HYPERLINK &quot;mailto:a@b.test&quot; "><w:r><w:t>메일</w:t></w:r></w:fldSimple></w:p>""",  # noqa: E501
            styles=STYLES,
            rels=[
                ("rId1", "hyperlink", "https://wiki.test/page?a=1&amp;b=2", True),
                ("rId2", "hyperlink", "file:///\\\\share\\docs", True),
                ("rId8", "footnotes", "footnotes.xml", False),
                ("rId9", "comments", "comments.xml", False),
                ("rId10", "endnotes", "endnotes.xml", False),
            ],
            parts=part(
                "/word/footnotes.xml",
                """<w:footnotes><w:footnote w:type="separator" w:id="-1"><w:p/></w:footnote>
                <w:footnote w:id="2"><w:p><w:r><w:footnoteRef/></w:r><w:r><w:t xml:space="preserve"> 각주 본문</w:t></w:r></w:p></w:footnote></w:footnotes>""",  # noqa: E501
            )
            + part(
                "/word/endnotes.xml",
                '<w:endnotes><w:endnote w:id="1"><w:p><w:r><w:t>미주 본문</w:t></w:r></w:p></w:endnote></w:endnotes>',
            )
            + part(
                "/word/comments.xml",
                """<w:comments><w:comment w:id="0" w:author="홍길동" w:date="2026-09-30T10:00:00Z">
                <w:p><w:r><w:t>확인 필요</w:t></w:r></w:p></w:comment></w:comments>""",
            ),
        ),
        "링크",
    )
    body = body_of(html)
    assert '<a href="https://wiki.test/page?a=1&amp;b=2" title="팀 위키">위키</a>' in body
    assert '<a href="#_Toc1">개요로</a>' in body and '<span id="_Toc1"></span>개요' in body
    # A target the page cannot follow stays readable as the title.
    assert '<span title="file:///\\\\share\\docs">공유 폴더</span>' in body
    assert '<a href="https://jira.test/A-1" title="티켓">A-1</a>' in body and "HYPERLINK" not in body
    assert '<a href="mailto:a@b.test">메일</a>' in body
    assert '<sup><a href="#footnote-1" id="footnote-ref-1">1</a></sup>' in body
    assert '<sup class="comment"><a href="#comment-1">[메모 1]</a></sup>' in body
    assert '<sup><a href="#endnote-1" id="endnote-ref-1">i</a></sup>' in body
    spaced = '<p style="margin-bottom:8pt">'
    assert (
        f'<div class="notes"><p class="caption">각주</p><ol><li id="footnote-1">{spaced} 각주 본문</p></li></ol></div>'
        in body
    )
    assert f'<ol type="i"><li id="endnote-1">{spaced}미주 본문</p></li></ol>' in body
    assert f'<li id="comment-1"><p class="caption">홍길동 · 2026-09-30</p>{spaced}확인 필요</p></li>' in body
    text = index_markdown(html)
    assert "각주 본문" in text and "확인 필요" in text and '[위키](https://wiki.test/page?a=1&b=2 "팀 위키")' in text


def test_equations_charts_text_boxes_and_symbols():
    chart = """<c:chartSpace><c:chart><c:title><c:tx><c:rich><a:p><a:r><a:t>분기 매출</a:t></a:r></a:p></c:rich></c:tx></c:title>
      <c:plotArea><c:barChart><c:ser><c:tx><c:strRef><c:strCache><c:pt idx="0"><c:v>2026</c:v></c:pt></c:strCache></c:strRef></c:tx>
        <c:cat><c:strRef><c:strCache><c:pt idx="0"><c:v>1분기</c:v></c:pt><c:pt idx="1"><c:v>2분기</c:v></c:pt></c:strCache></c:strRef></c:cat>
        <c:val><c:numRef><c:numCache><c:pt idx="0"><c:v>10</c:v></c:pt><c:pt idx="1"><c:v>12.5</c:v></c:pt></c:numCache></c:numRef></c:val>
      </c:ser></c:barChart></c:plotArea></c:chart></c:chartSpace>"""  # noqa: E501
    html = word_xml_to_html(
        package(
            """
      <w:p><w:r><w:t xml:space="preserve">근의 공식 </w:t></w:r><m:oMath><m:r><m:t>x</m:t></m:r><m:r><m:t>=</m:t></m:r>
        <m:f><m:num><m:r><m:t>-b±</m:t></m:r><m:rad><m:radPr><m:degHide m:val="1"/></m:radPr><m:deg/><m:e>
          <m:sSup><m:e><m:r><m:t>b</m:t></m:r></m:e><m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup><m:r><m:t>-4ac</m:t></m:r></m:e></m:rad></m:num>
        <m:den><m:r><m:t>2a</m:t></m:r></m:den></m:f></m:oMath></w:p>
      <w:p><w:r><w:t>차트 앞</w:t></w:r><w:r><w:drawing><wp:inline><wp:extent cx="100" cy="100"/><wp:docPr id="2" name="차트 1"/>
        <a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/chart"><c:chart r:id="rId5"/></a:graphicData></a:graphic>
        </wp:inline></w:drawing></w:r></w:p>
      <w:p><w:r><mc:AlternateContent><mc:Choice Requires="wps"><w:drawing><wp:anchor><wp:docPr id="3" name="Text Box 3"/>
          <a:graphic><a:graphicData uri="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"><wps:wsp><wps:txbx>
          <w:txbxContent><w:p><w:r><w:t>상자 안</w:t></w:r></w:p></w:txbxContent></wps:txbx></wps:wsp></a:graphicData></a:graphic>
          </wp:anchor></w:drawing></mc:Choice>
        <mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent><w:p><w:r><w:t>상자 안</w:t></w:r></w:p></w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>
        </mc:AlternateContent></w:r><w:r><w:t>앵커 글</w:t></w:r></w:p>
      <w:p><w:r><w:sym w:font="Wingdings" w:char="F0FC"/><w:sym w:font="Symbol" w:char="F061"/><w:t>기호</w:t></w:r>
        <w:r><w:br w:type="page"/></w:r><w:r><w:t>다음 쪽</w:t></w:r></w:p>
      <w:p><w:r><w:pict><v:rect o:hr="t"/></w:pict></w:r></w:p>""",  # noqa: E501
            rels=[("rId5", "chart", "charts/chart1.xml", False)],
            parts=part("/word/charts/chart1.xml", chart),
        ),
        "수식",
    )
    body = body_of(html)
    assert (
        '<math alttext="x=(-b±√(b^(2)-4ac))/(2a)"><mrow><mi>x</mi><mo>=</mo><mfrac><mrow><mo>-</mo><mi>b</mi><mo>±</mo>'
        "<msqrt><mrow><msup><mrow><mi>b</mi></mrow><mrow><mn>2</mn></mrow></msup><mo>-</mo><mn>4</mn><mi>a</mi><mi>c</mi></mrow></msqrt>"
        "</mrow><mrow><mn>2</mn><mi>a</mi></mrow></mfrac></mrow></math>"
    ) in body
    # A chart becomes the table of its data after its paragraph.
    assert (
        '<p>차트 앞</p><figure class="chart"><figcaption>차트: 분기 매출 (막대형)</figcaption><table class="grid">'
        "<tr><th>항목</th><th>2026</th></tr><tr><th>1분기</th><td>10</td></tr><tr><th>2분기</th><td>12.5</td></tr></table></figure>"
    ) in body
    # Word 2010's text box (the mc:Choice) is read, not its VML fallback; without an outline of its own it
    # stays text on the page, drawn as Word draws it.
    assert body.count("상자 안") == 1
    assert (
        '<p>앵커 글</p><div class="textbox" style="max-width:100%;box-sizing:border-box;display:flex;'
        'flex-direction:column;justify-content:flex-start;padding:4.8px 9.6px 4.8px 9.6px;border:none">'
        "<p>상자 안</p></div>"
    ) in body
    assert '<p>✓α기호<span class="pagebreak"></span>다음 쪽</p>' in body
    assert "<hr>" in body and "<p><hr>" not in body
    text = index_markdown(html)
    assert "x=(-b±√(b^(2)-4ac))/(2a)" in text and "| 1분기 | 10 |" in text


def test_main_part_only_once_without_hidden_deleted_or_moved_away_text():
    html = word_xml_to_html(
        package("""
      <w:p><w:r><w:t xml:space="preserve">보이는 글 </w:t></w:r><w:r><w:rPr><w:vanish/></w:rPr><w:t>숨긴 글</w:t></w:r>
        <w:moveFrom><w:r><w:t>옮기기 전</w:t></w:r></w:moveFrom><w:moveTo><w:r><w:t>옮긴 글</w:t></w:r></w:moveTo>
        <w:del><w:r><w:delText>지운 글</w:delText></w:r></w:del><w:ins><w:r><w:t xml:space="preserve"> 넣은 글</w:t></w:r></w:ins>
        <w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>PAGE</w:instrText></w:r>
        <w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>7</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
      <w:sdt><w:sdtPr><w:alias w:val="컨트롤 이름"/></w:sdtPr><w:sdtContent><w:p><w:r><w:t>콘텐츠 컨트롤</w:t></w:r></w:p></w:sdtContent></w:sdt>
      <w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>MERGEFIELD 이름</w:instrText></w:r></w:p>
      <w:p><w:r><w:t>닫히지 않은 필드 뒤</w:t></w:r></w:p>""")  # noqa: E501
        + "",
        "본문",
    )
    body = body_of(html)
    assert "<p>보이는 글 옮긴 글 넣은 글7</p><p>콘텐츠 컨트롤</p>" in body
    # A field code left open ends with its paragraph instead of hiding the rest.
    assert "<p>닫히지 않은 필드 뒤</p>" in body
    for absent in ("숨긴 글", "옮기기 전", "지운 글", "PAGE", "컨트롤 이름", "MERGEFIELD"):
        assert absent not in body


def test_headers_and_footers_the_package_carries():
    html = word_xml_to_html(
        package(
            "<w:p><w:r><w:t>본문</w:t></w:r></w:p>",
            rels=[("rId7", "header", "header1.xml", False), ("rId8", "footer", "footer1.xml", False)],
            parts=part("/word/header1.xml", "<w:hdr><w:p><w:r><w:t>대외비</w:t></w:r></w:p></w:hdr>")
            + part("/word/footer1.xml", "<w:ftr><w:p><w:r><w:t>1쪽</w:t></w:r></w:p></w:ftr>"),
            section='<w:sectPr><w:headerReference w:type="default" r:id="rId7"/>'
            '<w:footerReference w:type="default" r:id="rId8"/>'
            '<w:headerReference w:type="first" r:id="rId9"/></w:sectPr>',
        ),
        "머리글",
    )
    body = body_of(html)
    assert (
        body.index('<div class="header"><p>대외비</p></div>')
        < body.index("<p>본문</p>")
        < body.index('<div class="footer"><p>1쪽</p></div>')
    )


@pytest.mark.parametrize(
    "xml,message",
    [
        ('<!DOCTYPE w:document [<!ENTITY x "y">]><w:document/>', "DTD"),
        ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"/>', "본문을 읽지"),
        (package("<w:p><w:r><w:rPr><w:vanish/></w:rPr><w:t>숨김</w:t></w:r></w:p><w:p/>"), "표시할 내용"),
    ],
    ids=["dtd", "no-body", "hidden-text-only"],
)
def test_word_xml_without_anything_to_show_is_refused(xml, message):
    with pytest.raises(UserError, match=message):
        word_xml_to_html(xml, "빈 문서")


def test_a_document_of_only_pictures_is_a_page_now():
    html = word_xml_to_html(
        package(
            '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="95250" cy="95250"/><wp:docPr id="1" name="사진"/>'
            "<a:graphic>"
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:blipFill>'
            '<a:blip r:embed="rId1"/>'
            "</pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>",
            rels=[("rId1", "image", "media/a.png", False)],
            parts=binary("/word/media/a.png", png(), "image/png"),
        ),
        "사진만",
    )
    assert 'alt="사진" width="10" height="10"' in html
    assert index_markdown(html) == "[그림: 사진]"


def anchors_inside_anchors(html):
    """The <a> elements inside another one, as a browser's parser would find them."""
    from html.parser import HTMLParser

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.depth = self.nested = 0

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                self.nested += self.depth > 0
                self.depth += 1

        def handle_endtag(self, tag):
            if tag == "a":
                self.depth -= 1

    links = Links()
    links.feed(html)
    return links.nested


FORMS = """
  <w:p><w:r><w:t xml:space="preserve">동의 여부: </w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"><w:ffData><w:name w:val="Check1"/><w:enabled/><w:calcOnExit w:val="0"/>
      <w:checkBox><w:sizeAuto/><w:default w:val="0"/><w:checked/></w:checkBox></w:ffData></w:fldChar></w:r>
    <w:r><w:instrText xml:space="preserve"> FORMCHECKBOX </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>
    <w:r><w:t xml:space="preserve"> 동의함 </w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"><w:ffData><w:name w:val="Check2"/><w:checkBox><w:sizeAuto/>
      <w:default w:val="1"/><w:checked w:val="0"/></w:checkBox></w:ffData></w:fldChar></w:r>
    <w:r><w:instrText xml:space="preserve"> FORMCHECKBOX </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>
    <w:r><w:t xml:space="preserve"> 동의 안 함 </w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"><w:ffData><w:name w:val="Check3"/><w:checkBox><w:sizeAuto/>
      <w:default w:val="1"/></w:checkBox></w:ffData></w:fldChar></w:r>
    <w:r><w:instrText xml:space="preserve"> FORMCHECKBOX </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>
    <w:r><w:t xml:space="preserve"> 기본 체크</w:t></w:r></w:p>
  <w:p><w:r><w:t xml:space="preserve">결재: </w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"><w:ffData><w:name w:val="Dropdown1"/><w:ddList><w:result w:val="1"/>
      <w:listEntry w:val="승인"/><w:listEntry w:val="반려"/><w:listEntry w:val="보류"/></w:ddList></w:ffData>
      </w:fldChar></w:r>
    <w:r><w:instrText xml:space="preserve"> FORMDROPDOWN </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>
    <w:r><w:t xml:space="preserve"> / 기본: </w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"><w:ffData><w:name w:val="Dropdown2"/><w:ddList>
      <w:listEntry w:val="대기"/><w:listEntry w:val="완료"/></w:ddList></w:ffData></w:fldChar></w:r>
    <w:r><w:instrText xml:space="preserve"> FORMDROPDOWN </w:instrText></w:r>
    <w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>대기</w:t></w:r>
    <w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>
  <w:p><w:r><w:t xml:space="preserve">이름: </w:t></w:r>
    <w:r><w:fldChar w:fldCharType="begin"><w:ffData><w:name w:val="Text1"/><w:textInput/></w:ffData></w:fldChar></w:r>
    <w:r><w:instrText xml:space="preserve"> FORMTEXT </w:instrText></w:r>
    <w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>홍길동</w:t></w:r>
    <w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>"""


def test_answers_of_legacy_form_fields_are_kept():
    body = body_of(word_xml_to_html(package(FORMS), "신청서"))
    assert "<p>동의 여부: ☒ 동의함 ☐ 동의 안 함 ☒ 기본 체크</p>" in body
    # The chosen entry of a list, or its first entry; a result Word wrote as well is not shown twice.
    assert "<p>결재: 반려 / 기본: 대기</p>" in body
    assert "<p>이름: 홍길동</p>" in body
    assert "FORM" not in body
    text = index_markdown(word_xml_to_html(package(FORMS), "신청서"))
    assert "동의 여부: ☒ 동의함" in text and "결재: 반려" in text


def test_notes_with_marks_of_their_own_keep_the_mark_and_their_text():
    html = word_xml_to_html(
        package(
            """
      <w:p><w:r><w:t>첫째</w:t></w:r><w:r><w:footnoteReference w:id="1"/></w:r>
        <w:r><w:t>별표</w:t></w:r><w:r><w:rPr><w:vertAlign w:val="superscript"/></w:rPr>
          <w:footnoteReference w:customMarkFollows="1" w:id="2"/><w:t>*</w:t></w:r>
        <w:r><w:t>둘째</w:t></w:r><w:r><w:footnoteReference w:id="3"/></w:r></w:p>""",
            rels=[("rId8", "footnotes", "footnotes.xml", False)],
            parts=part(
                "/word/footnotes.xml",
                "<w:footnotes>"
                + "".join(
                    f'<w:footnote w:id="{ident}"><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:footnote>'
                    for ident, text in ((1, "첫 각주"), (2, "별표 각주"), (3, "둘째 각주"))
                )
                + "</w:footnotes>",
            ),
        ),
        "각주",
    )
    body = body_of(html)
    # Word numbers the notes without a mark of their own: the third note is 2, after the one marked *.
    assert (
        '첫째<sup><a href="#footnote-1" id="footnote-ref-1">1</a></sup>별표'
        '<a href="#footnote-2" id="footnote-ref-2"><sup>*</sup></a>둘째'
        '<sup><a href="#footnote-3" id="footnote-ref-3">2</a></sup>'
    ) in body
    assert (
        '<li id="footnote-1"><p>첫 각주</p></li><li id="footnote-2" style="list-style-type:&quot;* &quot;">'
        '<p>별표 각주</p></li><li id="footnote-3" value="2"><p>둘째 각주</p></li>'
    ) in body
    assert "별표 각주" in index_markdown(html)


def test_links_hold_no_other_link_of_bookmarks_notes_or_comments():
    notes = "".join(
        f'<w:footnote w:id="{ident}"><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:footnote>'
        for ident, text in ((1, "근거"), (2, "출처"))
    )
    html = word_xml_to_html(
        package(
            """
      <w:p><w:hyperlink r:id="rId1"><w:r><w:t xml:space="preserve">팀 </w:t></w:r>
        <w:bookmarkStart w:id="0" w:name="_Hlk1234"/><w:r><w:t>규정 문서</w:t></w:r><w:bookmarkEnd w:id="0"/>
        <w:r><w:footnoteReference w:id="1"/></w:r><w:r><w:commentReference w:id="0"/></w:r></w:hyperlink>
        <w:r><w:fldChar w:fldCharType="begin"/></w:r>
        <w:r><w:instrText xml:space="preserve"> HYPERLINK "https://a.test" </w:instrText></w:r>
        <w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>필드 링크</w:t></w:r>
        <w:r><w:footnoteReference w:id="2"/></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>""",
            rels=[
                ("rId1", "hyperlink", "https://wiki.test/rules", True),
                ("rId8", "footnotes", "footnotes.xml", False),
                ("rId9", "comments", "comments.xml", False),
            ],
            parts=part("/word/footnotes.xml", f"<w:footnotes>{notes}</w:footnotes>")
            + part(
                "/word/comments.xml",
                '<w:comments><w:comment w:id="0"><w:p><w:r><w:t>확인</w:t></w:r></w:p></w:comment></w:comments>',
            ),
        ),
        "링크",
    )
    body = body_of(html)
    assert anchors_inside_anchors(body) == 0
    assert (
        '<a href="https://wiki.test/rules">팀 <span id="_Hlk1234"></span>규정 문서'
        '<sup><span id="footnote-ref-1">1</span></sup><sup class="comment">[메모 1]</sup></a>'
    ) in body
    assert '<sup><span id="footnote-ref-2">2</span></sup>' in body
    assert '<li id="footnote-2"><p>출처</p></li>' in body


def test_grid_columns_a_row_leaves_empty_keep_the_cells_under_their_columns():
    html = word_xml_to_html(
        package("""
      <w:tbl><w:tblGrid><w:gridCol w:w="1000"/><w:gridCol w:w="1000"/><w:gridCol w:w="1000"/></w:tblGrid>
        <w:tr><w:tc><w:p><w:r><w:t>H1</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>H2</w:t></w:r></w:p></w:tc>
          <w:tc><w:p><w:r><w:t>H3</w:t></w:r></w:p></w:tc></w:tr>
        <w:tr><w:trPr><w:gridBefore w:val="1"/><w:wBefore w:w="1000" w:type="dxa"/></w:trPr>
          <w:tc><w:p><w:r><w:t>B2</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>B3</w:t></w:r></w:p></w:tc></w:tr>
        <w:tr><w:trPr><w:gridAfter w:val="2"/></w:trPr><w:tc><w:p><w:r><w:t>C1</w:t></w:r></w:p></w:tc></w:tr>
      </w:tbl>"""),
        "표",
    )
    rows = re.findall(r"<tr>(.*?)</tr>", body_of(html))
    empty = '<td style="border:none;padding:0"></td>'
    assert rows[1].startswith(empty) and rows[1].count("<td") == 3
    assert rows[2].endswith('<td colspan="2" style="border:none;padding:0"></td>')
    assert "|  | B2 | B3 |" in index_markdown(html)


def test_cropped_pictures_show_the_part_word_shows():
    picture = png(10, 10)
    html = word_xml_to_html(
        package(
            '<w:p><w:r><w:drawing><wp:inline><wp:extent cx="952500" cy="476250"/>'
            '<wp:docPr id="1" name="자른 그림"/><a:graphic>'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:blipFill>'
            '<a:blip r:embed="rId1"/><a:srcRect t="10000" r="5000" b="40000" l="2500"/><a:stretch/></pic:blipFill>'
            "</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
            '<w:p><w:r><w:pict><v:shape style="width:30pt;height:15pt"><v:imagedata r:id="rId1" o:title="VML" '
            'croptop="16384f" cropleft=".25"/></v:shape></w:pict></w:r></w:p>'
            '<w:p><w:r><w:pict><v:shape style="width:30pt;height:15pt"><v:imagedata r:id="rId1" o:title="늘린 그림" '
            'croptop="-6554f"/></v:shape></w:pict></w:r></w:p>',
            rels=[("rId1", "image", "media/a.png", False)],
            parts=binary("/word/media/a.png", picture, "image/png"),
        ),
        "자르기",
    )
    body = body_of(html)
    assert 'alt="자른 그림" width="100" height="50" style="object-view-box:inset(10% 5% 40% 2.5%)">' in body
    assert 'alt="VML" width="40" height="20" style="object-view-box:inset(25% 0% 0% 25%)">' in body
    # A side Word extends rather than cuts shows the whole picture, as before.
    assert 'alt="늘린 그림" width="40" height="20">' in body


def noise_png(width, height, seed):
    """A PNG that does not compress, so its size is known in advance."""
    import random

    generator = random.Random(seed)
    buffer = io.BytesIO()
    Image.frombytes("RGB", (width, height), bytes(generator.randrange(256) for _ in range(width * height * 3))).save(
        buffer, "PNG"
    )
    return buffer.getvalue()


def inline_picture(ident, name):
    return (
        f'<w:r><w:drawing><wp:inline><wp:extent cx="95250" cy="95250"/><wp:docPr id="1" name="{name}"/><a:graphic>'
        '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:blipFill>'
        f'<a:blip r:embed="{ident}"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline>'
        "</w:drawing></w:r>"
    )


def test_pictures_stop_at_their_budget_and_a_picture_shown_again_gives_way_to_new_ones(monkeypatch):
    logo, chart = noise_png(60, 60, 1), noise_png(60, 60, 2)
    monkeypatch.setattr("agent.word_html.MAX_PICTURE_BYTES", 5 * len(logo) + len(chart))
    # A picture used on every row of a long table, and another one only at the end.
    rows = "".join(f"<w:p><w:r><w:t>{index}행</w:t></w:r>{inline_picture('rId1', '로고')}</w:p>" for index in range(70))
    html = word_xml_to_html(
        package(
            rows + f"<w:p>{inline_picture('rId2', '차트')}</w:p>",
            rels=[("rId1", "image", "media/logo.png", False), ("rId2", "image", "media/chart.png", False)],
            parts=binary("/word/media/logo.png", logo, "image/png")
            + binary("/word/media/chart.png", chart, "image/png"),
        ),
        "반복 그림",
    )
    body = body_of(html)
    # The new picture comes before more copies of the one shown already; the first copies keep their places.
    assert body.count('alt="차트"') == 1 and body.count('alt="로고"') == 5
    assert (
        body.index('alt="로고"') < body.index("<p>1행")
        and body.count('<span class="missing">[그림: 로고]</span>') == 65
    )
    assert body.startswith(
        '<div class="doc word" style="font-size:10pt"><p class="caption">그림 일부를 넣지 못했습니다: '
        "그림이 40MB를 넘어 나머지 그림은 넣지 않았습니다.</p>"
    )
    assert "[그림: 로고]" in index_markdown(html) and "base64" not in index_markdown(html)


def test_pictures_leave_the_page_room_for_its_text(monkeypatch):
    picture = noise_png(60, 60, 3)
    text = "가나다라마바사아자차" * 2000
    monkeypatch.setattr("agent.word_html.MAX_HTML_BYTES", len(text.encode()) + 64 * 1024 + 2 * len(picture))
    pictures = "".join(f"<w:p>{inline_picture('rId1', f'그림 {index}')}</w:p>" for index in range(3))
    html = word_xml_to_html(
        package(
            f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>{pictures}",
            rels=[("rId1", "image", "media/a.png", False)],
            parts=binary("/word/media/a.png", picture, "image/png"),
        ),
        "긴 글",
    )
    body = body_of(html)
    assert body.count("<img ") == 1 and '<span class="missing">[그림: 그림 2]</span>' in body
    assert "문서의 글이 많아 HTML 문서(80MB)에 담기지 않는 나머지 그림은 넣지 않았습니다." in body
    assert len(html.encode()) < len(text.encode()) + 64 * 1024 + 2 * len(picture)


def test_a_picture_used_again_is_converted_once(monkeypatch):
    from agent import images

    converted = []
    real = images.for_web
    monkeypatch.setattr(
        "agent.word_html.images.for_web", lambda *args, **options: converted.append(options) or real(*args, **options)
    )
    pages = [Image.new("RGB", (5, 5), (0, 128, index)) for index in range(3)]
    tiff = io.BytesIO()
    pages[0].save(tiff, "TIFF", save_all=True, append_images=pages[1:])
    html = word_xml_to_html(
        package(
            "".join(f"<w:p>{inline_picture('rId1', '스캔')}</w:p>" for _ in range(3)),
            rels=[("rId1", "image", "media/scan.tif", False)],
            parts=binary("/word/media/scan.tif", tiff.getvalue(), "image/tiff"),
        ),
        "스캔",
    )
    # Once, and only the page Word shows: the first.
    assert html.count('alt="스캔"') == 3 and converted == [{"pages": 1}]
    assert len(set(re.findall(r'src="data:image/png;base64,([^"]+)"', html))) == 1
