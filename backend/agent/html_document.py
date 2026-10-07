"""One self-contained HTML page per imported file, and the Markdown text the Wiki searches.

A page carries everything it shows: formatting as inline styles, the stylesheet below in its head and
images as data: URIs. It loads nothing from elsewhere, so it looks the same in the app and as a
downloaded .html file. Search, PageIndex and the LLM read a Markdown text made from the same page."""
import re
from html import escape

from bs4 import BeautifulSoup

from .llm import UserError
from .markdown_import import WikiMarkdownConverter, clean_soup

# A 50MB file of pictures becomes about 67MB of base64.
MAX_HTML_BYTES = 80 * 1024 * 1024

STYLE = """
.doc{margin:0 auto;color:#000;background:#fff;font-family:"맑은 고딕","Malgun Gothic",sans-serif;font-size:10pt;line-height:1.2;overflow-wrap:break-word;tab-size:8}
.doc p,.doc h1,.doc h2,.doc h3,.doc h4,.doc h5,.doc h6,.doc li{white-space:pre-wrap}
.doc table{border-collapse:collapse;border-spacing:0}
.doc td,.doc th{vertical-align:top}
.doc img{max-width:100%;height:auto}
.doc a{color:#0563c1;text-decoration:underline}
.doc sup,.doc sub{line-height:0}
.doc figure{margin:6pt 0}
.doc figcaption,.doc .caption{margin:2pt 0;color:#595959;font-size:9pt;font-weight:normal}
.doc .missing{display:inline-block;padding:0 4pt;border:1px dashed #b4b4b4;color:#595959;font-size:9pt;line-height:1.6;white-space:normal}
.doc .pagebreak{display:block;margin:12pt 0;border-top:1px dashed #b4b4b4}
.doc .textbox{margin:4pt 0;padding:4pt 6pt;border:1px solid #c6c6c6}
.doc .grid td,.doc .grid th{padding:1pt 6pt;border:1px solid #c6c6c6;text-align:left}
.doc .grid th{background:#f2f2f2;font-weight:bold}
.doc .notes{margin-top:18pt;padding-top:6pt;border-top:1px solid #c6c6c6;font-size:9pt}
.doc .notes>ol,.doc .notes>ul{margin:4pt 0;padding-left:20pt}
.doc .header,.doc .footer{color:#595959;font-size:9pt}
.doc .header{margin-bottom:12pt;padding-bottom:6pt;border-bottom:1px solid #e0e0e0}
.doc .footer{margin-top:12pt;padding-top:6pt;border-top:1px solid #e0e0e0}
.doc.word p,.doc.word h1,.doc.word h2,.doc.word h3,.doc.word h4,.doc.word h5,.doc.word h6{margin:0;font-size:inherit;font-weight:inherit}
.doc:not(.word) h2{margin:0 0 8pt;font-size:14pt;font-weight:bold}
.doc:not(.word) h3{margin:10pt 0 4pt;font-size:11pt;font-weight:bold}
.doc:not(.word) p{margin:0 0 4pt}
.doc:not(.word) ul,.doc:not(.word) ol{margin:0 0 4pt;padding-left:20pt}
.doc .sheet,.doc .slide,.doc .page{margin:0 0 20pt}
.doc .cells td{padding:1pt 3pt;border:1px solid #e1e1e1;vertical-align:bottom;white-space:pre}
.doc .cells th{padding:1pt 4pt;border:1px solid #d0d0d0;background:#f3f3f3;color:#666;font-size:8pt;font-weight:normal;text-align:center}
.doc .cells tr.hidden>td{opacity:.55}
.doc .comment,.doc .note-mark{color:#c00000;font-size:8pt;vertical-align:super}
.doc .slide{padding:12pt 18pt;border:1px solid #d0d0d0;border-radius:4pt}
.doc .slide.hidden{opacity:.7}
.doc .shape{margin:0 0 6pt}
.doc .shape>table td{padding:2pt 5pt;border:1px solid #c6c6c6}
.doc.image figure{margin:0;text-align:center}
.doc.text pre{margin:0;white-space:pre-wrap}
"""


def page(title, body, kind, style=""):
    """The page of one imported file; `kind` (word, excel, powerpoint, visio, image, text) selects its stylesheet."""
    root = f' style="{escape(style)}"' if style else ""
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{escape(title)}</title>'
            f'<style>{STYLE}</style></head><body><div class="doc {kind}"{root}>{body}</div></body></html>')


def validate_html(html):
    if not isinstance(html, str) or not html.strip():
        raise UserError("변환된 HTML 본문이 없습니다.")
    if len(html) > MAX_HTML_BYTES:
        raise UserError("변환된 HTML이 80MB를 초과해 가져오지 않았습니다.")
    try:
        size = len(html.encode("utf-8"))
    except UnicodeEncodeError:
        # Text from Windows (Office, Notepad) is UTF-16 and may hold half of a surrogate pair, which UTF-8 cannot store.
        html = html.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
        size = len(html.encode("utf-8"))
    if size > MAX_HTML_BYTES:
        raise UserError("변환된 HTML이 80MB를 초과해 가져오지 않았습니다.")
    return html


def html_filename(title):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", title).strip(" .")[:120]
    return (name or "document") + ".html"


# Images are left out of the text, so their data never reaches the HTML parser or the LLM.
DATA_SOURCE = re.compile(r'(<img\b[^>]*?\ssrc=")data:[^"]*"', re.I)


class IndexConverter(WikiMarkdownConverter):
    """Markdown of a page for search and the LLM: pictures and equations keep a readable trace."""

    def convert_img(self, el, text, parent_tags):
        alt = " ".join((el.get("alt") or "").split())
        return f"[그림: {self.escape(alt, parent_tags)}]" if alt else "[그림]"

    def convert_math(self, el, text, parent_tags):
        return " ".join((el.get("alttext") or text).split())


def index_markdown(html):
    """The Markdown text of a page: what search, PageIndex and the LLM read. A text longer than a Wiki document holds
    is saved in parts (parts.py)."""
    text = page_text(html)
    if not text:
        raise UserError("문서에 표시할 내용이 없습니다. 빈 문서인지 확인해 주세요.")
    return text


def page_text(html):
    """The Markdown text of a page, however long or short."""
    soup = BeautifulSoup(DATA_SOURCE.sub(r'\1"', html), "html.parser")
    # Column widths carry no text, and a table's first row stays its Markdown header after them. Marks of
    # comments listed elsewhere on the page are only for the eye.
    for tag in soup.select("colgroup, col, .note-mark"):
        tag.decompose()
    # A formula sits in its cell as data-formula; the text keeps it after the value, as Excel's formula bar.
    for cell in soup.select("[data-formula]"):
        code = soup.new_tag("code")
        code.string = cell["data-formula"]
        cell.append(soup.new_tag("br"))
        cell.append("수식: ")
        cell.append(code)
    clean_soup(soup)
    return IndexConverter(heading_style="ATX", bullets="-", escape_misc=True, autolinks=False,
                          table_infer_header=False).convert_soup(soup).strip()


def chart_table(title, kinds, labels, series):
    """A chart as the table of its data: a row per category ({index: label}) and a column per series
    [(name, {index: value})]."""
    heading = "차트" + (f": {title}" if title else "") + (f" ({kinds})" if kinds else "")
    indices = sorted(set(labels) | {index for _, values in series for index in values})
    if not series:
        return f'<figure class="chart"><figcaption>{escape(heading)}</figcaption></figure>'
    head = "<tr><th>항목</th>" + "".join(f"<th>{escape(name)}</th>" for name, _ in series) + "</tr>"
    rows = "".join("<tr><th>" + escape(labels.get(index, str(index + 1))) + "</th>"
                   + "".join(f"<td>{escape(values.get(index, ''))}</td>" for _, values in series) + "</tr>"
                   for index in indices)
    return f'<figure class="chart"><figcaption>{escape(heading)}</figcaption><table class="grid">{head}{rows}</table></figure>'


# --- Inline CSS shared by the readers ------------------------------------------------------------

HEX = re.compile(r"#?([0-9a-fA-F]{6})")
FONT_NAME = re.compile(r"[^\w .&+()\-가-힣ㄱ-ㅎㅏ-ㅣ一-龥ぁ-ゟ゠-ヿ]")


def css(pairs):
    """`property:value;…` of the pairs whose value is not None or empty."""
    return ";".join(f"{name}:{value}" for name, value in pairs if value not in (None, ""))


def hex_color(value):
    """#RRGGBB of a color written as RRGGBB or #RRGGBB, or None ("auto" and anything else)."""
    match = HEX.fullmatch(str(value or "").strip())
    return f"#{match.group(1).upper()}" if match else None


def bgr_color(value):
    """#RRGGBB of an Office RGB value, which COM gives as an integer 0xBBGGRR."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFF:
        return None
    return f"#{value & 0xFF:02X}{value >> 8 & 0xFF:02X}{value >> 16 & 0xFF:02X}"


def font_family(names):
    """A CSS font-family of font names, without anything that could end the declaration."""
    found = []
    for name in names:
        name = " ".join(FONT_NAME.sub("", str(name or "")).split())[:60]
        if name and name not in found:
            found.append(name)
    return ",".join(f'"{name}"' for name in found)


def points(value):
    """A length in points for CSS, without float noise."""
    return f"{round(value, 2):g}pt"


def attribute(value):
    return escape(str(value), quote=True)
