"""Collect Confluence pages with Python Playwright in the background.

A Chromium profile kept for each Confluence site signs in with the site's login method (web_login.py), then reads each
page as the server rendered it, with a request made inside a Confluence page so the browser's cookies and certificate
trust apply. A page whose body only scripts draw (Cloud) is opened and read from the rendered page once its expand
macros are open. With subpages, the page tree is listed through Confluence's REST API, also inside the page. Each page
goes to confluence.import_page as an in-memory HTML capture; nothing but the browser profile is written to disk.

A routine's import (track_changes) asks Confluence for a page's version number first, which the subpage list gives
along: a page still at the version the Wiki took, read in full within sources.RECHECK, is not read again.
"""
import asyncio
import contextlib
import json
import re
import uuid
from html import escape
from urllib.parse import parse_qs, parse_qsl, unquote, unquote_plus, urlencode, urljoin, urlsplit, urlunsplit

from . import diagnostics, sources
from .browser_import import BUSY_ERROR, BrowserImporter, close_popup, open_page, polite
from .confluence import import_page
from .llm import BusyError, UserError
from .service import routine_name
from .web_login import origin_of, redirected, sign_in, signed_in, validate_plan, with_scheme

MAX_PAGES = 5000
MAX_HTML_BYTES = 10 * 1024 * 1024
MAX_LIST_CHARS = 10 * 1024 * 1024
# The visible login window waits for the user, e.g. to approve a sign-in prompt.
WINDOW_SECONDS = 600
MAX_EXPANDS = 100
CONTENT = '#main-content, .ak-renderer-document, [data-testid="renderer-wrapper"]'
EXPAND = ('.expand-control:not([data-wiki-expanded]), '
          'button[aria-expanded="false"][data-testid*="expand"]:not([data-wiki-expanded])')
# Page link shapes, also below a context path such as /confluence or /wiki.
PAGE_PATHS = (
    re.compile(r"/display/[^/]+/[^/]+"),  # /display/SPACEKEY/Page+Title
    re.compile(r"/spaces/[^/]+/pages/[0-9]+"),  # /spaces/SPACEKEY/pages/123456/Page+Title
    re.compile(r"/x/[A-Za-z0-9_-]+/?$"),  # /x/AbCdEf short links
)
DIGITS = re.compile(r"[0-9]+")
FORMAT_ERROR = ("Confluence 페이지 링크 형식이 아닙니다. 예) https://<주소>/display/SPACE/Page+Title, "
                "https://<주소>/pages/viewpage.action?pageId=123456")
LINK_ERROR = "Confluence 페이지 링크를 확인해 주세요. 예) https://<주소>/display/SPACE/Page+Title"
SITE_ERROR = "Confluence 주소를 확인해 주세요. 예) https://confluence.example.com/wiki, confluence.example.com/wiki"
NOT_FOUND = "Confluence 페이지를 확인하지 못했습니다. 로그인 및 페이지 접근 권한을 확인해 주세요."
API_ERROR = "하위 페이지 API 경로가 올바르지 않습니다."
ANALYSIS_ERROR = "Markdown 문서는 저장되었지만 AI 분석에 실패했습니다. 문서에서 재시도해 주세요."
WINDOW_ERROR = "자동 입력하지 못했습니다. 창에서 직접 로그인해 주세요."
# The HTML page of a capture: the page's body in a document of its own, with its title and link.
DOCUMENT = ("<!doctype html>\n<html lang=\"ko\"><head><meta charset=\"utf-8\"><meta http-equiv=\"Content-Security-Policy\" "
            "content=\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; "
            "form-action 'none'\"><title>{title}</title><style>body{{max-width:960px;margin:40px auto;"
            "font-family:sans-serif;line-height:1.7}}table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;"
            "padding:8px}}pre{{white-space:pre-wrap}}</style></head><body><h1>{title}</h1><p>출처: <a href=\"{url}\">"
            "{url}</a></p><main>{body}</main></body></html>")

# Runs in a signed-in Confluence page; a login redirect shows up as status 0.
FETCH_JSON = """async ({url, limit}) => {
  const response = await fetch(url, {
    credentials: 'same-origin', redirect: 'manual', cache: 'no-store',
    headers: {Accept: 'application/json'}, signal: AbortSignal.timeout(30000),
  });
  const text = response.ok ? await response.text() : '';
  return {status: response.status, link: response.headers.get('link') || '', text: text.length > limit ? null : text,
          retry: response.headers.get('retry-after') || ''};
}"""

# Runs in the page. With `server`, it reads the server-rendered page with the page's session: Confluence's scripts later
# rewrite code blocks in the live DOM (SyntaxHighlighter tables). Only passive markup is kept, never application
# scripts, session metadata or form values, and links become absolute http(s) addresses.
CAPTURE = """async ({url, server}) => {
  let doc = document;
  let base = document.baseURI;
  let root;
  if (server) {
    const response = await fetch(url, {credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(45000)});
    if (new URL(response.url).origin !== location.origin) return null;
    if (!response.ok) return {status: response.status, retry: response.headers.get('retry-after') || ''};
    doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    base = response.url;
    root = doc.querySelector('#main-content');
  } else root = document.querySelector('#main-content, .ak-renderer-document, [data-testid="renderer-wrapper"]');
  if (!root) return null;
  const clone = root.cloneNode(true);
  clone.querySelectorAll('script,style,iframe,object,embed,form,input,button,textarea,select,noscript')
    .forEach((node) => node.remove());
  const allowed = ['href', 'src', 'alt', 'title', 'colspan', 'rowspan', 'open', 'lang', 'dir'];
  for (const node of [clone, ...clone.querySelectorAll('*')]) {
    for (const attribute of [...node.attributes]) {
      // Code macros keep their language as "brush: java; ...".
      const language = node.localName === 'pre' && attribute.name === 'data-syntaxhighlighter-params';
      if (!allowed.includes(attribute.name) && !language) node.removeAttribute(attribute.name);
    }
    for (const key of ['href', 'src']) {
      const value = node.getAttribute(key);
      if (!value || (key === 'href' && value.startsWith('#'))) continue;
      try {
        const target = new URL(value, base);
        if (!['http:', 'https:'].includes(target.protocol)) node.removeAttribute(key);
        else node.setAttribute(key, target.href);
      } catch {
        node.removeAttribute(key);
      }
    }
  }
  const meta = (name) => doc.querySelector(`meta[name="${name}"]`)?.getAttribute('content') ?? null;
  return {
    url: server ? base : location.href,
    id: meta('ajs-page-id'),
    context: meta('ajs-context-path'),
    title: doc.querySelector('#title-text, [data-testid="title-text"], h1')?.textContent?.trim() ||
      meta('ajs-page-title') || doc.title,
    html: clone.innerHTML,
    text: clone.textContent?.trim() ?? '',
  };
}"""

# An expand macro that shows its content already.
EXPANDED = """(element) => {
  const content = element.parentElement?.querySelector('.expand-content');
  return element.getAttribute('aria-expanded') === 'true' ||
    (!!content && getComputedStyle(content).display !== 'none' && !content.classList.contains('expand-hidden'));
}"""
MARK_EXPANDED = "(element) => element.setAttribute('data-wiki-expanded', 'true')"

# Scroll through the document to have lazily rendered content and images drawn; false when it is too long to.
SCROLL = """async () => {
  document.querySelectorAll('details').forEach((element) => { element.open = true; });
  let y = 0;
  for (let steps = 0; y < document.documentElement.scrollHeight && steps < 500; y += Math.max(innerHeight, 600), steps++) {
    scrollTo(0, y);
    await new Promise((resolve) => setTimeout(resolve, 20));
  }
  scrollTo(0, 0);
  return y >= document.documentElement.scrollHeight;
}"""

# Wait for the macros' and the renderer's changes of the body without networkidle (Confluence can long-poll): true once
# the body has not changed for half a second, false when it still changes after 10 seconds.
SETTLED = """(element) => new Promise((resolve) => {
  let quiet;
  const finish = (ok) => { clearTimeout(quiet); clearTimeout(deadline); observer.disconnect(); resolve(ok); };
  const reset = () => { clearTimeout(quiet); quiet = setTimeout(() => finish(true), 500); };
  const observer = new MutationObserver(reset);
  const deadline = setTimeout(() => finish(false), 10000);
  observer.observe(element, {childList: true, subtree: true, characterData: true, attributes: true});
  reset();
})"""


class AccessError(UserError):
    """A page that could not be opened: the site did not answer or refused it, or sent the login screen."""


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def page_url(value):
    """A link as the browser opens it, without its fragment: scheme and host in lower case, without a default port.
    UserError unless it is a plain http(s) link without an account in it."""
    try:
        url = urlsplit(value.strip()) if isinstance(value, str) and len(value) <= 4096 else None
        origin = origin_of(value) if url else None
    except ValueError:
        origin = None
    # The browser reads a backslash as a slash, so the host it opens would not be the one checked here.
    if not origin or "\\" in value or re.search(r"[\x00-\x20\x7f]", value.strip()):
        raise UserError(LINK_ERROR)
    return urlunsplit((url.scheme.lower(), origin.split("://", 1)[1], url.path or "/", url.query, ""))


def is_page_url(value):
    """A link to a Confluence page: viewpage.action with a page ID or a space and title, or a page path."""
    try:
        url = urlsplit(value) if isinstance(value, str) else None
    except ValueError:
        return False
    if url is None or url.scheme.lower() not in ("http", "https") or not url.netloc:
        return False
    if url.path.endswith("/pages/viewpage.action"):
        query = parse_qs(url.query)

        def first(name):
            return (query.get(name) or [""])[0]
        return bool(first("pageId")) or bool(first("spaceKey") and first("title"))
    return any(pattern.search(url.path) for pattern in PAGE_PATHS)


def location_info(value, metadata=None):
    """The page's ID, the site's base address (origin and context path) and the page's canonical link, from a page
    link and what the page says of itself (`metadata`: id, context)."""
    metadata = metadata or {}
    url = urlsplit(page_url(value))
    numbered = re.search(r"/pages/([0-9]+)(?:/|$)", url.path)
    ident = metadata.get("id") or (parse_qs(url.query).get("pageId") or [""])[0] or (numbered[1] if numbered else "")
    if not isinstance(ident, str) or not DIGITS.fullmatch(ident):
        raise UserError(NOT_FOUND)
    context = metadata.get("context")
    prefix = context if context is not None else re.split(r"/(?:spaces|pages|display|x)(?:/|$)", url.path)[0]
    if not isinstance(prefix, str) or (prefix and not re.fullmatch(r"/(?!/)[^?#\\\s]*", prefix)):
        raise UserError("Confluence 사이트 경로를 확인하지 못했습니다.")
    base = f"{url.scheme}://{url.netloc}{re.sub('/$', '', prefix)}"
    return {"id": ident, "base": base, "url": f"{base}/pages/viewpage.action?pageId={ident}"}


def api_link(value, current, base):
    """A pagination link of a subpage list, made absolute. Links may be context-relative (/rest/…) or include the
    context path (/wiki, /confluence); anything but the site's REST API is refused."""
    if not isinstance(value, str) or "\\" in value or re.search("%2e", value, re.I):
        raise UserError(API_ERROR)
    target = base + value if value.startswith(("/rest/", "/api/")) else urljoin(current, value)
    found, site = urlsplit(target), urlsplit(base)
    if "@" in found.netloc or origin_of(target) != origin_of(base) \
            or not found.path.startswith(re.sub("/$", "", site.path) + "/"):
        raise UserError("다른 사이트를 가리키는 하위 페이지 응답을 거부했습니다.")
    if not found.path.startswith((urlsplit(base + "/rest/api/").path, urlsplit(base + "/api/v2/").path)):
        raise UserError(API_ERROR)
    return urlunsplit((found.scheme, found.netloc, found.path, found.query, ""))


def arrived_at(origin):
    """Signed in once a Confluence page of the site opens, or any page of the site names the signed-in user."""
    async def arrived(page):
        if origin_of(page.url) == origin and is_page_url(page.url):
            return True
        return await signed_in(page, origin)
    return arrived


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


def page_request(page):
    """REST requests made inside the signed-in page: the browser's cookies and certificate trust apply, where the
    backend's own requests could refuse a private certificate authority. Each goes within the site's rate, and one the
    site refuses as too many is waited out and sent again (browser_import.polite)."""
    async def request(url):
        return await polite(url, lambda: page.evaluate(FETCH_JSON, {"url": url, "limit": MAX_LIST_CHARS}))
    return request


async def json_page(page, url, origin):
    """A REST answer of the site read by opening its address in the browser, which also brings the browser to the site
    for the in-page requests after it; as FETCH_JSON answers, or None when the site sent another page (its login) or
    did not answer."""
    try:
        response = await polite(url, lambda: page.goto(url, wait_until="domcontentloaded", timeout=45000))
        if response is None or origin_of(page.url) != origin:
            return None
        text = await response.text()
        return {"status": response.status, "link": response.headers.get("link", ""),
                "text": text if len(text) <= MAX_LIST_CHARS else None}
    except Exception:
        return None


def site_request(page, origin):
    """REST requests inside a page of the site (page_request), or while the browser is on no page of it yet, by opening
    the request's address itself (json_page); a login page instead answers as a login redirect does (status 0)."""
    inside = page_request(page)

    async def request(url):
        if origin_of(page.url) == origin:
            return await inside(url)
        return await json_page(page, url, origin) or {"status": 0, "link": "", "text": ""}
    return request


def version_of(item):
    """The version number a REST answer gives of a page ({"version": {"number"}}), as text; '' when it gives none."""
    version = item.get("version") if isinstance(item, dict) else None
    number = version.get("number") if isinstance(version, dict) else None
    return str(number) if isinstance(number, int) and not isinstance(number, bool) and number >= 0 else ""


async def page_version(request, info):
    """The version number of the page `info` (id, base) as Confluence gives it, or '' when it does not: from the
    content API of Server and Data Center, else from Cloud's v2 API."""
    endpoints = [f"{info['base']}/rest/api/content/{info['id']}?expand=version"]
    if urlsplit(info["base"]).path == "/wiki":
        endpoints.append(f"{info['base']}/api/v2/pages/{info['id']}")
    for endpoint in endpoints:
        response = await request(endpoint)
        status = response.get("status") if isinstance(response, dict) else None
        if status in (404, 405, 410):
            continue
        if status != 200 or not isinstance(response.get("text"), str):
            return ""
        try:
            body = json.loads(response["text"])
        except ValueError:
            return ""
        return version_of(body) if str(body.get("id") if isinstance(body, dict) else "") == info["id"] else ""
    return ""


def title_of(value):
    """(site base, space key, title) of a page link that names its page by its space and title: /display/SPACE/Title
    or viewpage.action?spaceKey=…&title=…; None for another link."""
    try:
        url = urlsplit(page_url(value))
    except UserError:
        return None
    query = parse_qs(url.query)
    if url.path.endswith("/pages/viewpage.action") and not query.get("pageId"):
        prefix = url.path[:-len("/pages/viewpage.action")]
        space, title = (query.get("spaceKey") or [""])[0], (query.get("title") or [""])[0]
    else:
        match = re.fullmatch(r"(.*?)/display/([^/]+)/([^/]+)/?", url.path)
        if not match:
            return None
        prefix, space, title = match[1], unquote(match[2]), unquote_plus(match[3])
    if not space or not title or (prefix and not re.fullmatch(r"/(?!/)[^?#\\\s]*", prefix)):
        return None
    return f"{url.scheme}://{url.netloc}{prefix.rstrip('/')}", space, title


async def page_by_title(request, link):
    """The page a link names by its space and title (title_of), found through Confluence's content search, as
    {"id", "base", "url", "version"}; None when the link names none that way or the search does not find exactly one."""
    named = title_of(link)
    if not named:
        return None
    base, space, title = named
    query = urlencode({"spaceKey": space, "title": title, "expand": "version", "limit": 2})
    response = await request(f"{base}/rest/api/content?{query}")
    if not isinstance(response, dict) or response.get("status") != 200 or not isinstance(response.get("text"), str):
        return None
    try:
        results = json.loads(response["text"]).get("results")
    except (ValueError, AttributeError):
        return None
    item = results[0] if isinstance(results, list) and len(results) == 1 and isinstance(results[0], dict) else {}
    ident = str(item.get("id") or "")
    if not DIGITS.fullmatch(ident):
        return None
    return {"id": ident, "base": base, "url": f"{base}/pages/viewpage.action?pageId={ident}",
            "version": version_of(item)}


async def children(request, info, on_child):
    """Each subpage of the page `info` (id, base) as on_child({"id", "base", "url", "version"}), following every page of
    the list the server gives: Server/Data Center offsets and Link headers, and Cloud's v2 cursors. The version number
    comes along where the list gives it ('' where not)."""
    endpoint = f"{info['base']}/rest/api/content/{info['id']}/child/page?limit=100&start=0&expand=version"
    visited, count = [], 0
    while endpoint:
        if endpoint in visited:
            raise UserError("하위 페이지 목록의 다음 링크가 반복됩니다.")
        visited.append(endpoint)
        response = await request(endpoint)
        status = response.get("status") if isinstance(response, dict) else None
        if status in (404, 405) and len(visited) == 1 and urlsplit(info["base"]).path == "/wiki":
            endpoint = f"{info['base']}/api/v2/pages/{info['id']}/children?limit=100"
            continue
        if status == 0:
            raise UserError("하위 페이지 목록 요청이 로그인 화면으로 이동했습니다. 로그인 상태를 확인해 주세요.")
        if status == 429:
            raise UserError("요청이 너무 많다며 Confluence가 하위 페이지 목록을 거절했습니다 (HTTP 429). 잠시 후 다시 "
                            "시도해 주세요.")
        if not isinstance(status, int) or not 200 <= status <= 299:
            raise UserError(f"하위 페이지 목록을 읽지 못했습니다 (HTTP {status}). 로그인·권한·REST API 사용 가능 여부를 "
                            "확인해 주세요.")
        if not isinstance(response.get("text"), str):
            raise UserError("하위 페이지 목록 응답이 10MB를 초과합니다.")
        header = re.search(r'<([^>]+)>;\s*rel="?next"?', response.get("link") or "")
        try:
            body = json.loads(response["text"])
        except ValueError:
            raise UserError("하위 페이지 목록 대신 로그인 화면 또는 잘못된 응답을 받았습니다.") from None
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise UserError("하위 페이지 목록 형식을 확인하지 못했습니다.")
        for item in results:
            ident = item.get("id") if isinstance(item, dict) else None
            ident = str(ident) if isinstance(ident, (str, int)) and not isinstance(ident, bool) else ""
            if not DIGITS.fullmatch(ident):
                raise UserError("하위 페이지 ID가 올바르지 않습니다.")
            count += 1
            if count > MAX_PAGES:
                raise UserError(f"하위 페이지가 {MAX_PAGES:,}개를 초과합니다. 범위를 나누어 가져와 주세요.")
            on_child({"id": ident, "base": info["base"], "url": f"{info['base']}/pages/viewpage.action?pageId={ident}",
                      "version": version_of(item)})
        links = body.get("_links") if isinstance(body.get("_links"), dict) else {}
        following = links.get("next") or (header[1] if header else None)
        endpoint = api_link(following, endpoint, info["base"]) if following else ""
        # Some Data Center versions only give start, limit and size.
        start, limit = body.get("start"), body.get("limit")
        if not endpoint and results and type(start) is int and type(limit) is int and 0 < limit <= len(results):
            last = urlsplit(visited[-1])
            query = [(key, value) for key, value in parse_qsl(last.query, keep_blank_values=True) if key != "start"]
            endpoint = urlunsplit(last._replace(query=urlencode([*query, ("start", str(start + len(results)))])))


async def open_source(page, url, origin, assist=None):
    """Open a page of the site in the browser and wait for its body. With the LLM's help (web_assist.py), a page that
    does not open may be opened once more."""
    try:
        response = await polite(url, lambda: page.goto(url, wait_until="domcontentloaded", timeout=45000))
    except Exception as error:
        if redirected(error):  # A client-side redirect, e.g. to the SSO page, replaced the navigation.
            response = None
        else:
            failure = AccessError("Confluence에 접속하지 못했습니다. 링크와 네트워크 연결을 확인해 주세요.")
            if assist is None or page.is_closed():
                raise failure from error
            response = await assist.reconnect(page, url, error, failure)
    if response is not None and not response.ok:
        raise AccessError(f"페이지에 접근하지 못했습니다 (HTTP {response.status}).")
    if origin_of(page.url) != origin:
        raise AccessError("로그인이 필요합니다. 로그인 정보를 저장하거나 로그인 창에서 먼저 로그인해 주세요.")
    try:
        await page.locator(CONTENT).first.wait_for(state="visible", timeout=15000)
    except Exception:
        raise AccessError("Confluence 본문을 찾지 못했습니다. 로그인·페이지 권한을 확인해 주세요.") from None


async def render(page):
    """Make the rendered body complete: expand macros, lazy content and pending renderer updates."""
    body = page.locator(CONTENT).first
    expand = body.locator(EXPAND)
    for _ in range(MAX_EXPANDS):
        if not await expand.count():
            break
        control = expand.first
        if not await control.evaluate(EXPANDED):
            await control.click(timeout=5000)
        await control.evaluate(MARK_EXPANDED)
    if await expand.count():
        raise UserError(f"펼치기 영역이 {MAX_EXPANDS}개를 초과합니다. 페이지를 나누어 가져와 주세요.")
    if not await page.evaluate(SCROLL):
        raise UserError("페이지가 너무 길어 전체 로딩을 확인하지 못했습니다. 페이지를 나누어 가져와 주세요.")
    if not await body.evaluate(SETTLED):
        raise UserError("본문 로딩이 끝나지 않았습니다. 페이지 로딩 후 다시 시도해 주세요.")


async def snapshot(page, source, origin, assist=None):
    """The page `source` ({"url"}, with its "id" and "base" when known) captured for import_page: {"id", "base", "url",
    "html"}. Once a page of the site shows, later pages are read without leaving it."""
    opened = origin_of(page.url) != origin
    if opened:  # The in-page request needs a Confluence page.
        await open_source(page, source["url"], origin, assist)
    target = page.url if opened else source["url"]
    try:
        data = await polite(target, lambda: page.evaluate(CAPTURE, {"url": target, "server": True}))
    except Exception:
        data = None
    if isinstance(data, dict) and data.get("status"):
        raise AccessError(f"페이지에 접근하지 못했습니다 (HTTP {data['status']}).")
    if not (isinstance(data, dict) and data.get("text")):
        # Cloud and script-rendered bodies: the rendered page instead.
        if not opened:
            await open_source(page, source["url"], origin, assist)
        await render(page)
        data = await page.evaluate(CAPTURE, {"url": None, "server": False})
    if not (isinstance(data, dict) and data.get("text")):
        raise UserError("페이지에서 검색 가능한 본문을 추출하지 못했습니다.")
    info = location_info(data.get("url"), data)
    if source.get("id") and source["id"] != info["id"]:
        raise UserError("요청한 페이지와 열린 페이지의 ID가 다릅니다.")
    html = DOCUMENT.format(title=escape(str(data.get("title") or "")), url=escape(info["url"]),
                           body=data.get("html") or "")
    if len(html.encode("utf-8", "surrogatepass")) > MAX_HTML_BYTES:
        raise UserError("페이지 HTML이 10MB를 초과해 가져오지 않았습니다. 페이지를 나누어 가져와 주세요.")
    return {**info, "html": html}


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------


class ConfluenceImporter(BrowserImporter):
    product = "Confluence"
    site_error = SITE_ERROR
    profiles_variable, profiles_folder = "PRIVATE_AGENT_CONFLUENCE_PROFILES", "confluence-profiles"

    def __init__(self, service):
        super().__init__(service)
        self.window_seconds = WINDOW_SECONDS

    def start(self, params):
        url = page_url(with_scheme(params.get("url")))
        if not is_page_url(url):
            raise UserError(FORMAT_ERROR)
        recursive, analyze = params.get("recursive"), params.get("analyze")
        if not isinstance(recursive, bool) or not isinstance(analyze, bool):
            raise UserError("하위 페이지 및 AI 분석 옵션을 확인해 주세요.")
        # An update from the document window: the document to update, and whether text the user changed is replaced.
        doc_id, replace = sources.options(params)
        login = validate_plan(params.get("login"))
        if analyze and not self.service.llm.public()["configured"]:
            raise UserError("AI 분석을 사용하려면 먼저 LLM을 연결해 주세요.")
        # An automatic routine (routines.py) that watches the page learns what each import changed.
        options = {"recursive": recursive, "analyze": analyze, "doc_id": doc_id, "replace": replace,
                   "track_changes": params.get("track_changes") is True}
        routine = routine_name(params)
        origin = origin_of(url)
        with self.lock:
            if self._busy():
                raise BusyError(BUSY_ERROR)
            job = {"id": str(uuid.uuid4()), "url": url, **options, "routine": routine, "auto_login": bool(login),
                   "login": {"site": login["site_name"] if login else "", "method": None, "label": "",
                             "detected": False, "error": ""},
                   "status": "scanning", "total": 1, "processed": 0, "imported": 0, "updated": 0, "skipped": 0,
                   "failed": 0, "analysis_failed": 0, "discovery_failed": 0, "current_url": "", "notice": "",
                   "results": [], "error": "", "diagnosis": None}
            self._begin(job, lambda: self._collect(job, url, origin, options, login), "confluence-browser")
            return self.status()

    async def _collect(self, job, url, origin, options, login):
        try:
            # A background browser with the app's profile for the site.
            async with self._browser(origin, headless=True) as context:
                page = context.pages[0] if context.pages else await context.new_page()
                # No popups during background collection.
                context.on("page", lambda popup: close_popup(page, popup))
                assist = self._assistant(page, login, lambda **values: self._update(job, **values))
                if login:
                    self._update(job, status="login")
                    async with self._explaining(job, assist, page, "로그인"):
                        try:
                            result = await sign_in(context, page, url, login, product=self.product, assist=assist,
                                                   arrived=arrived_at(origin), **self._timeouts())
                        except UserError as error:
                            with self.lock:
                                job["login"].update(error=str(error), sent=getattr(error, "sent", False))
                            raise
                    with self.lock:
                        job["login"].update(result)
                await self._pages(job, page, url, origin, options, assist)
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self._update(job, error=diagnostics.report(
                error, "Confluence 가져오기를 완료하지 못했습니다. 링크·네트워크·로그인 상태를 확인해 주세요.", "Confluence 가져오기"))
        finally:
            with self.lock:
                job["status"] = "cancelled" if job["status"] == "cancelling" else "error" if job["error"] else "completed"
                job["current_url"] = ""

    async def _pages(self, job, page, url, origin, options, assist):
        """Read the page, and with `recursive` every page below it, one after another, each saved once it is read. A
        routine's import (track_changes) skips the pages it need not read again (_unchanged)."""
        request = site_request(page, origin)
        root = {"url": url}
        with contextlib.suppress(UserError):  # Short and title links: the browser finds the page.
            root = {**location_info(url), "url": url}
        queue = [root]
        seen = {f"{root['base']}:{root['id']}"} if "id" in root else set()

        def add(child):
            key = f"{child['base']}:{child['id']}"
            if key in seen:
                return
            if len(queue) >= MAX_PAGES:
                raise UserError(f"최대 {MAX_PAGES:,}페이지까지 지원합니다. 범위를 나누어 가져와 주세요.")
            seen.add(key)
            queue.append(child)
            self._update(job, total=len(queue))

        self._update(job, status="running")
        cursor = 0
        while cursor < len(queue):
            source = queue[cursor]
            self._update(job, current_url=source["url"])
            try:
                result = await self._unchanged(request, source) if options["track_changes"] else None
                if result is None:
                    # The first page shows whether Confluence opens; it may be opened again (web_assist.py).
                    captured = await snapshot(page, source, origin, assist if cursor == 0 else None)
                    source = {"id": captured["id"], "base": captured["base"], "url": captured["url"],
                              "version": source.get("version", "")}
                    seen.add(f"{source['base']}:{source['id']}")
                    result = import_page(self.service, {"source_url": source["url"], "page_id": source["id"],
                                                        "html": captured["html"], "doc_id": options["doc_id"],
                                                        "replace": options["replace"], "version": source["version"],
                                                        "track_changes": options["track_changes"]})
                    if options["analyze"] and result["status"] in ("imported", "updated"):
                        await self._analyze(job, result)
                else:
                    seen.add(f"{source['base']}:{source['id']}")
            except Exception as error:
                result = {"status": "failed", "message": diagnostics.report(
                    error, "페이지를 읽거나 저장하지 못했습니다. 네트워크·접근 권한·저장 공간을 확인해 주세요.",
                    "Confluence 페이지 가져오기")}
                # A first page that does not open: the LLM says why Confluence could not be reached.
                if cursor == 0 and isinstance(error, AccessError) and assist is not None and not page.is_closed():
                    found = getattr(error, "diagnosis", None) or await assist.diagnose(
                        page, "페이지 열기", str(error), error)
                    with self.lock:
                        job["diagnosis"] = found
            # Even when a page fails, the pages below it are listed by its known ID.
            if options["recursive"] and source.get("id"):
                try:
                    await children(request, source, add)
                except Exception as error:
                    result["discovery_error"] = str(error) if isinstance(error, UserError) else \
                        "하위 페이지 목록을 읽지 못했습니다. 이 페이지 아래에 누락된 문서가 있을 수 있습니다."
                    with self.lock:
                        job["discovery_failed"] += 1
            with self.lock:
                job["results"].append({"url": source["url"], **result})
                job[result["status"]] += 1
                job["processed"] += 1
            cursor += 1

    async def _unchanged(self, request, source):
        """For a routine's import: the result of the page `source` when it need not be read again, or None to read
        it. A page whose documents were all purged is left out (sources.ignored); one Confluence says is still at the
        version the Wiki holds, read in full within sources.RECHECK, is skipped (sources.fresh). Its version is asked
        for unless the subpage list gave it, and a link that names its page by title is looked up; both go into
        `source`. A page whose version cannot be asked for is read in full."""
        try:
            if "id" not in source:
                found = await page_by_title(request, source["url"])
                if not found:
                    return None
                source.update(id=found["id"], base=found["base"], version=found["version"])
            link = f"{source['base']}/pages/viewpage.action?pageId={source['id']}"
            with self.service.db.connect() as db:
                if sources.ignored(db, "confluence_sources", link):
                    return sources.skipped(sources.Plan("skip", [], "ignored"), "")
            # Asked for also when the Wiki has no version to compare with yet: the import records it with the page.
            if not source.get("version"):
                source["version"] = await page_version(request, source)
        except Exception:
            return None
        with self.service.db.connect() as db:
            found = sources.fresh(db, "confluence_sources", link, "version", source["version"])
        return sources.unmodified(found) if found else None

    async def _analyze(self, job, result):
        """A long page is saved as several documents; each is analyzed."""
        for ident in result["doc_ids"]:
            try:
                await asyncio.to_thread(self.service.wiki_analyze, {"id": ident})
            except Exception:
                result["analysis_error"] = ANALYSIS_ERROR
        if "analysis_error" in result:
            with self.lock:
                job["analysis_failed"] += 1

    async def _prepare_login(self, context, page, url, origin, login):
        if not login:
            await open_page(page, url, self.product)
            return None
        # With a saved account the window runs the same automatic login, so the user can approve a sign-in prompt
        # ("always allow") once for later background imports, or take over by hand when it fails.
        return asyncio.ensure_future(self._login_helper(context, page, url, origin, login))

    async def _login_helper(self, context, page, url, origin, login):
        try:
            result = await sign_in(context, page, url, login, product=self.product, arrived=arrived_at(origin),
                                   watched=True, **{**self._timeouts(), "login_seconds": self.window_seconds})
        except Exception as error:
            if not page.is_closed():  # Closing the window ends the helper; that is no failed login.
                with self.lock:
                    self.login_error = f"{WINDOW_ERROR} {error}" if isinstance(error, UserError) else WINDOW_ERROR
            return
        with self.lock:
            self.login_result = result

    async def _verify(self, context, page, url, origin, login, assist):
        # A check tells whether the account and the method sign in: never the session a run or the window left.
        return await sign_in(context, page, url, login, product=self.product, assist=assist, fresh=True,
                             arrived=lambda value: signed_in(value, origin), **self._timeouts())
