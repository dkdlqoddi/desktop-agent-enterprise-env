"""Local Confluence and SSO for the Playwright tests: two origins (127.0.0.1 and localhost) on one port, as
tests/fixtures/confluence.cjs serves them to the end-to-end tests.

Page N (/confluence/pages/viewpage.action?pageId=N, and /confluence/display/… for page 1) has a table, code, a
SyntaxHighlighter code macro that its script turns into a table, links, a picture, a collapsed expand macro, a
dangerous link and a form. The REST API lists page 1's children (2, then 4 on a second page) and page 2's (3, and 1
again: a cycle). Options: an SSO (sso) or Confluence's own login form (form) for signed-out visitors, HTTP 401
instead (auth), page 2 refused (forbidden), page 2's children refused (discovery_failure), slow pages (slow), and bodies
that only scripts draw, as in Confluence Cloud (rendered).

Each page has a version number (version), which changes with what the page holds unless `versions` sets it; the content
API gives it (/rest/api/content/N), and so does the subpage list asked to (expand=version) and the content search by
space and title (page N is titled "Page N"). The next `throttled` REST requests and the next `page_throttled` page
requests are answered 429 Too Many Requests with a Retry-After of `retry_after` seconds."""
import json
import re
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from jira_server import SSO_PAGE

SESSION_COOKIE = "session=valid; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax"

# Confluence Server's own login screen (login.action), for the 'atlassian' login method.
FORM_PAGE = """<!doctype html><html><head><title>Confluence 로그인</title></head><body>
<div id="main"><form id="loginform" method="post" action="{action}">
  <p class="error">{message}</p>
  <label>사용자 이름 <input id="os_username" name="os_username" type="text"></label>
  <label>비밀번호 <input id="os_password" name="os_password" type="password"></label>
  <input id="loginButton" type="submit" value="로그인">
</form></div></body></html>"""

# The site's start page names the signed-in user, as Confluence pages do.
DASHBOARD = ('<!doctype html><html><head><meta name="ajs-context-path" content="/confluence">'
             '<meta name="ajs-remote-user" content="{user}"><title>대시보드</title></head>'
             '<body><h1>Confluence 대시보드</h1></body></html>')

BODY = """<p>본문 ID 한국어 &amp; Confluence 지식</p><table><tr><th>기준</th><td>내용</td></tr></table><pre>if (x &lt; 3) {
  return x;
}</pre>
      <div class="code panel pdl"><div class="codeContent panelContent pdl"><pre class="syntaxhighlighter-pre" data-syntaxhighlighter-params="brush: java; gutter: false; theme: Confluence" data-theme="Confluence">int answer = ID;</pre></div></div>
      <a href="/attachment.txt">첨부 링크</a> <a href="#section-ID">목차</a><img src="/image.png" alt="그림 설명">
      <div class="expand-container"><div class="expand-control" role="button" tabindex="0">펼치기</div><div class="expand-content" style="display:none">접힌 세부 내용</div></div>
      <a href="javascript:alert(1)" onclick="alert(1)">위험 링크</a><form><input value="secret"></form><iframe src="about:blank"></iframe>"""

PAGE = """<!doctype html><html><head><meta name="ajs-page-id" content="ID"><meta name="ajs-context-path" content="/confluence"><title>페이지 ID</title></head><body>
      <nav>탐색 메뉴는 저장하지 않음</nav><h1 id="title-text">페이지 ID</h1>
      CONTENT
      <script>
        document.querySelector('.expand-control').onclick = () => { document.querySelector('.expand-content').style.display = 'block'; };
        // Like SyntaxHighlighter: the live DOM turns code macros into line-numbered tables.
        document.querySelectorAll('pre.syntaxhighlighter-pre').forEach((pre) => {
          const table = document.createElement('div');
          table.innerHTML = '<table><tr><td class="gutter">1</td><td class="code">' + pre.textContent + '</td></tr></table>';
          pre.replaceWith(table);
        });
      </script></body></html>"""


def page(ident, rendered):
    body = BODY.replace("ID", ident)
    # Server/Data Center ships #main-content in the HTML; Cloud-like pages only render it with scripts.
    if rendered:
        inner = '<div class="ak-renderer-document">' + body.replace("본문", "렌더링된 본문", 1) + "</div>"
        content = f"<div id=\"root\"></div><script>document.getElementById('root').innerHTML = {json.dumps(inner)};</script>"
    else:
        content = f'<div id="main-content">{body}</div>'
    return PAGE.replace("ID", ident).replace("CONTENT", content)


class ConfluenceServer:
    def __init__(self):
        self.requests, self.logins = [], []
        self.credentials = {"id": "tester", "password": "correct horse"}
        self.sso = self.form = self.auth = self.forbidden = self.discovery_failure = self.slow = self.rendered = False
        self.versions, self.throttled, self.page_throttled, self.retry_after = {}, 0, 0, "0"
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                server.handle(self, "GET")

            def do_POST(self):
                server.handle(self, "POST")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        port = self.httpd.server_address[1]
        self.origin, self.sso_origin = f"http://127.0.0.1:{port}", f"http://localhost:{port}"
        self.url = f"{self.origin}/confluence/pages/viewpage.action?pageId=1"
        # The site's base address, as saved for its account.
        self.site = f"{self.origin}/confluence"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    @staticmethod
    def read_form(handler):
        length = int(handler.headers.get("Content-Length") or 0)
        return {key: values[0] for key, values in parse_qs(handler.rfile.read(length).decode()).items()}

    @staticmethod
    def send(handler, status, body="", kind="text/html; charset=utf-8", headers=None):
        data = body.encode()
        try:
            handler.send_response(status)
            for name, value in {"Content-Type": kind, "Content-Length": str(len(data)), **(headers or {})}.items():
                handler.send_header(name, value)
            handler.end_headers()
            handler.wfile.write(data)
        except OSError:
            pass  # The browser went away, e.g. a cancelled import closed it.

    def version(self, ident):
        """The version number of page `ident`: as `versions` sets it, else one that changes with what the page holds."""
        return self.versions.get(ident) or 1 + zlib.crc32(page(ident, self.rendered).encode()) % 1000

    def redirect(self, handler, location, cookie=None):
        self.send(handler, 302, headers={"Location": location, **({"Set-Cookie": cookie} if cookie else {})})

    def handle(self, handler, method):
        url = urlsplit(handler.path)
        query = {key: values[0] for key, values in parse_qs(url.query).items()}
        self.requests.append(handler.path)
        # The SSO server is another origin (and cookie host) than Confluence.
        if handler.headers.get("Host", "").startswith("localhost"):
            return self.handle_sso(handler, method, url, query)
        if url.path == "/login":
            return self.send(handler, 200, "<h1>로그인 완료</h1>", headers={"Set-Cookie": SESSION_COOKIE})
        if url.path == "/confluence/sso/callback" and query.get("ticket") == "ok":
            target = urljoin(self.origin + "/", query.get("return") or "/")
            return self.redirect(handler, target if target.startswith(self.origin + "/") else self.origin, SESSION_COOKIE)
        if url.path == "/confluence/login.action":
            return self.handle_form(handler, method, query)
        signed_in = "session=valid" in (handler.headers.get("Cookie") or "")
        if self.sso and not signed_in:
            return self.redirect(handler, f"{self.sso_origin}/sso/login?return={quote(self.origin + handler.path, safe='')}")
        if self.form and not signed_in:
            return self.redirect(handler, f"/confluence/login.action?os_destination={quote(handler.path, safe='')}")
        if self.auth and not signed_in:
            return self.send(handler, 401, "Login required")
        if url.path in ("/confluence/", "/confluence"):
            return self.send(handler, 200, DASHBOARD.format(user=self.credentials["id"] if signed_in else ""))
        busy = {"Retry-After": self.retry_after} if self.retry_after is not None else {}
        if "/rest/" in url.path and self.throttled:
            self.throttled -= 1
            return self.send(handler, 429, "Too Many Requests", headers=busy)
        versioned = "version" in query.get("expand", "")
        api = re.search(r"/rest/api/content/(\d+)/child/page", url.path)
        if api:
            if self.discovery_failure and api[1] == "2":
                return self.send(handler, 403, "Forbidden")
            start = query.get("start", "0")
            ids = (["2"] if start == "0" else ["4"]) if api[1] == "1" else ["3", "1"] if api[1] == "2" else []
            links = {"next": "/rest/api/content/1/child/page?start=1&limit=1"} if api[1] == "1" and start == "0" else {}
            results = [{"id": ident, **({"version": {"number": self.version(ident)}} if versioned else {})}
                       for ident in ids]
            return self.send(handler, 200, json.dumps({"results": results, "_links": links}), "application/json")
        content = re.fullmatch(r"/confluence/rest/api/content/(\d+)", url.path)
        if content:
            if self.forbidden and content[1] == "2":
                return self.send(handler, 403, "Forbidden")
            return self.send(handler, 200, json.dumps({"id": content[1], "type": "page",
                                                       "version": {"number": self.version(content[1])}}),
                             "application/json")
        if url.path == "/confluence/rest/api/content":  # The content search by space and title.
            titled = re.fullmatch(r"Page (\d+)", query.get("title", ""))
            found = [titled[1]] if titled and query.get("spaceKey") == "SPACE" else []
            return self.send(handler, 200, json.dumps({"results": [
                {"id": ident, "version": {"number": self.version(ident)}} for ident in found]}), "application/json")
        ident = query.get("pageId") or ("1" if url.path.startswith("/confluence/display/") else None)
        if not ident:
            return self.send(handler, 404, "Not found")
        if self.page_throttled:
            self.page_throttled -= 1
            return self.send(handler, 429, "Too Many Requests", headers=busy)
        if self.forbidden and ident == "2":
            return self.send(handler, 403, "Forbidden")
        if self.slow:
            time.sleep(3)
        self.send(handler, 200, page(ident, self.rendered))

    def handle_sso(self, handler, method, url, query):
        if url.path != "/sso/login":
            return self.send(handler, 404, "Not found")
        action = f"/sso/login?return={quote(query.get('return', ''), safe='')}"
        if method != "POST":
            return self.send(handler, 200, SSO_PAGE.format(action=action, message=""))
        form = self.read_form(handler)
        attempt = {"id": form.get("id"), "password": form.get("pw")}
        self.logins.append(attempt)
        if attempt != self.credentials:
            return self.send(handler, 200, SSO_PAGE.format(action=action, message="ID 또는 비밀번호가 올바르지 않습니다."))
        self.redirect(handler, f"{self.origin}/confluence/sso/callback?ticket=ok&return={quote(query.get('return', ''), safe='')}")

    def handle_form(self, handler, method, query):
        destination = query.get("os_destination") or "/confluence/"
        action = f"/confluence/login.action?os_destination={quote(destination, safe='')}"
        if method != "POST":
            return self.send(handler, 200, FORM_PAGE.format(action=action, message=""))
        form = self.read_form(handler)
        attempt = {"id": form.get("os_username"), "password": form.get("os_password")}
        self.logins.append(attempt)
        if attempt != self.credentials:
            return self.send(handler, 200, FORM_PAGE.format(action=action, message="사용자 이름 또는 비밀번호가 올바르지 않습니다."))
        local = destination.startswith("/") and not destination.startswith("//")
        self.redirect(handler, self.origin + destination if local else f"{self.origin}/confluence/", SESSION_COOKIE)
