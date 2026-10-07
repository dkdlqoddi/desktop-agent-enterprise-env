"""Convert source markup to Markdown in memory: the stored text of Confluence and JIRA pages, and the
search text of imported HTML pages (html_document.py)."""
import re
from urllib.parse import quote, urljoin, urlsplit

from bs4 import BeautifulSoup
from markdownify import MarkdownConverter

from .llm import UserError

MAX_SOURCE_BYTES = 10 * 1024 * 1024
# The text of one Wiki document; a longer import is saved as several (parts.py).
MAX_MARKDOWN_CHARS = 80000


def code_language(el):
    """Confluence code macros keep their language as data-syntaxhighlighter-params="brush: java; ...",
    JIRA code blocks as class="code-java"."""
    match = re.search(r"brush:\s*([\w+#-]+)", el.get("data-syntaxhighlighter-params") or "")
    language = match.group(1) if match else next(
        (name[5:] for name in el.get("class") or [] if re.fullmatch(r"code-[\w+#-]+", name)), "")
    return language if language not in ("text", "none", "plain") else ""


class WikiMarkdownConverter(MarkdownConverter):
    def convert_pre(self, el, text, parent_tags):
        if not text:
            return ""
        # A source containing Markdown fences must remain one code block.
        fence = "`" * max(3, 1 + max((len(run) for run in re.findall(r"`+", text)), default=0))
        return f"\n\n{fence}{code_language(el)}\n{text.rstrip(chr(10))}\n{fence}\n\n"

    def convert_img(self, el, text, parent_tags):
        # Preserve descriptions without fetching or embedding image resources.
        return self.escape(el.get("alt", ""), parent_tags)

    def convert_td(self, el, text, parent_tags):
        # GFM needs escaped pipes even inside inline code in a table cell.
        return super().convert_td(el, re.sub(r"(?<!\\)\|", r"\\|", text), parent_tags)

    def convert_th(self, el, text, parent_tags):
        return super().convert_th(el, re.sub(r"(?<!\\)\|", r"\\|", text), parent_tags)


def validate_markdown(content):
    """The converted text; one longer than a Wiki document holds is saved in parts (parts.py)."""
    if not isinstance(content, str) or not content.strip():
        raise UserError("변환된 Markdown 본문이 없습니다. 이미지로만 된 문서는 지원하지 않습니다.")
    return content.strip()


def clean_soup(soup, source_url=""):
    """Drop executable and invisible parts, and every link that is not http(s), mailto or a fragment."""
    for tag in soup.select("head, script, style, noscript, iframe, object, svg, template, form, input, button"):
        tag.decompose()
    for link in soup.find_all("a", href=True):
        try:
            href = urljoin(source_url, link["href"])
            valid = href.startswith("#") or urlsplit(href).scheme.lower() in {"http", "https", "mailto"}
        except ValueError:
            valid = False
        if not valid:
            del link["href"]
        else:
            link["href"] = quote(href, safe=":/?#[]@!$&'*+,;=%")


def html_to_markdown(html, source_url="", *, validate=True):
    if not isinstance(html, str):
        raise UserError("변환할 원문이 올바르지 않습니다.")
    if validate and len(html.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise UserError("변환할 원문이 10MB를 초과해 가져오지 않았습니다.")
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    clean_soup(soup, source_url)
    content = WikiMarkdownConverter(heading_style="ATX", bullets="-", escape_misc=True,
                                    autolinks=False, table_infer_header=False).convert_soup(soup).strip()
    return title, validate_markdown(content) if validate else content


def markdown_filename(title):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", title).strip(" .")[:120]
    return (name or "document") + ".md"
