"""Local JIRA and SSO for the Playwright tests: two origins (127.0.0.1 and localhost) on one port.

The JIRA login page comes in several styles (JiraServer.style), one per kind of login screen the login methods of
electron/login-methods.json handle: an SSO behind an "SSO Login" link, the same behind an "AD SSO" link beside
JIRA's own form, JIRA's own login form, a form shown by a button (for user-defined methods) and a plain form with
unusual field names (for the generic method). One more, a two-step screen that no method fits, is for the LLM's help
(web_assist.py)."""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, urlsplit

# Synthetic JIRA entry page with a link to the test identity provider.
LOGIN_PAGE = """<!doctype html><html><body><a href="{sso}">SSO Login</a></body></html>"""

SSO_PAGE = """<!doctype html><html><body><main>
  <h1>Example Identity Provider</h1><p>{message}</p>
  <form method="post" action="{action}">
    <label>Account <input name="id" autocomplete="username"></label>
    <label>Password <input name="pw" type="password" autocomplete="current-password"></label>
    <button id="sign-in" type="submit">Sign in</button>
  </form></main></body></html>"""

# Synthetic login choice: an ordinary form and an identity-provider link.
DASHBOARD_PAGE = """<!doctype html><html><body><h1>Example Dashboard</h1>
<form method="post" action="{action}"><p>{message}</p><input name="os_username"><input name="os_password" type="password">
<button>Sign in</button><a id="sso-entry" href="{sso}" {late}>AD SSO</a></form>{script}</body></html>"""

REFUSED = "Sorry, your username and password are incorrect - please try again."

# JIRA Server/Data Center's own login form.
FORM_PAGE = """<!doctype html><html><head><title>JIRA 로그인</title></head><body>
<main><h1>JIRA</h1><p class="error">{message}</p>
<form id="login-form" method="post" action="{action}">
  <label>사용자 이름 <input id="login-form-username" name="os_username"></label>
  <label>비밀번호 <input id="login-form-password" name="os_password" type="password"></label>
  <input id="login-form-submit" type="submit" value="로그인">
</form></main></body></html>"""

# A login form hidden behind a button, for methods the user defines with their own selectors.
CUSTOM_PAGE = """<!doctype html><html><head><title>포털</title></head><body>
<p>{message}</p><button id="open-login" type="button">계정으로 로그인</button>
<form id="custom-form" method="post" action="{action}" hidden>
  <input id="user" name="os_username"><input id="pass" name="os_password" type="password">
  <button id="go" type="submit">들어가기</button>
</form>
<script>document.getElementById('open-login').onclick = () => {{ document.getElementById('custom-form').hidden = false; }};</script>
</body></html>"""

# A plain form without the names or positions of any known login screen.
GENERIC_PAGE = """<!doctype html><html><head><title>로그인</title></head><body>
<header><input type="search" placeholder="검색"></header><p>{message}</p>
<form method="post" action="{action}">
  <label>아이디 <input name="uid"></label><label>암호 <input type="password" name="secret"></label>
  <label><input type="checkbox" name="keep"> 로그인 유지</label><button>로그인</button>
</form></body></html>"""

# A screen none of the login methods fits: the account first, then the password, sent by a span that only a script
# made clickable. A refused login shows the password step again.
STEPS_PAGE = """<!doctype html><html><head><title>통합 로그인</title></head><body>
<h1>통합 로그인</h1><p>{message}</p>
<form id="steps" method="post" action="{action}">
  <div><label>계정 <input id="account" name="login_id" autocomplete="off"></label><button type="button" id="next">다음</button></div>
  <div id="second" {hidden}><label>암호 <input id="passcode" name="passcode" type="password"></label>
    <span id="go" style="cursor:pointer">확인</span></div>
</form>
<script>
  document.getElementById('next').onclick = () => {{ document.getElementById('second').hidden = false; }};
  document.getElementById('go').onclick = () => document.getElementById('steps').submit();
</script>
</body></html>"""

# The SSO accepted the login but waits for an approval that never comes: the page never navigates.
PENDING_PAGE = """<!doctype html><html><head><title>SSO 로그인</title></head><body>
<p>승인 대기 중입니다. 등록된 기기에서 로그인을 승인해 주세요.</p></body></html>"""

LOGIN_STYLES = ("sso", "mixed", "form", "custom", "generic", "steps")
SESSION_COOKIE = "session=valid; Path=/; Max-Age=3600; HttpOnly; SameSite=Lax"

USERS = {"tester": "테스터", "kim": "김개발"}
# category: the status category JIRA gives each status (new, indeterminate, done); updated changes with an edit.
ISSUES = {
    "TEAM-1": {
        "summary": "로그인 화면 오류",
        "assignee": "kim",
        "reporter": "tester",
        "status": "Open",
        "category": "new",
        "text": "로그인 버튼을 누르면 오류가 납니다",
    },
    "TEAM-2": {
        "summary": "결제 모듈 개선",
        "assignee": "tester",
        "reporter": "kim",
        "status": "Done",
        "category": "done",
        "text": "결제 API 응답 시간을 줄입니다",
    },
    "TEAM-3": {
        "summary": "로그인 세션 만료",
        "assignee": "kim",
        "reporter": "tester",
        "status": "Open",
        "category": "new",
        "text": "세션이 30분 뒤 만료됩니다",
    },
}
UPDATED = "2026-09-02T09:00:00.000+0900"
COMMENTS = [
    {"id": "c1", "author": "kim", "body": "첫 댓글", "html": "<p>첫 댓글</p>"},
    {"id": "c2", "author": "tester", "body": "재현 확인", "html": "<p>재현 <strong>확인</strong></p>"},
    {"id": "c3", "author": "kim", "body": "마지막 댓글 검색어", "html": "<p>마지막 댓글 검색어</p>"},
]


def user(name):
    return {"name": name, "displayName": USERS[name]}


def search_fields(key):
    """The fields a search gives of an issue (what the routines' reports ask for)."""
    data = ISSUES[key]
    done = data.get("category") == "done"
    return {
        "summary": data["summary"],
        "project": {"key": "TEAM", "name": "팀 프로젝트"},
        "issuetype": {"name": "버그"},
        "priority": {"name": "높음"},
        "status": {"name": data["status"], "statusCategory": {"key": data.get("category", "new")}},
        "assignee": user(data["assignee"]),
        "created": "2026-09-01T10:23:45.000+0900",
        "updated": data.get("updated", UPDATED),
        "duedate": data.get("due", "2026-10-01"),
        "resolutiondate": data.get("updated", UPDATED) if done else None,
    }


def issue_json(key, origin):
    data = ISSUES[key]
    fields = {
        "summary": data["summary"],
        "project": {"key": "TEAM", "name": "팀 프로젝트"},
        "issuetype": {"name": "버그"},
        "status": {"name": data["status"]},
        "priority": {"name": "높음"},
        "resolution": None,
        "assignee": user(data["assignee"]),
        "reporter": user(data["reporter"]),
        "created": "2026-09-01T10:23:45.000+0900",
        "updated": data.get("updated", UPDATED),
        "duedate": data.get("due", "2026-10-01"),
        "labels": ["login", "ui"],
        "components": [{"name": "웹"}],
        "fixVersions": [{"name": "1.0"}],
        "description": data["text"],
        "comment": {"comments": [], "total": 0, "maxResults": 0, "startAt": 0},
        "lastViewed": str(time.time()),
    }
    rendered = {"description": f"<p>{data['text']}</p>"}
    names = {"customfield_10001": "인수 조건", "customfield_10002": "심각도", "customfield_10003": "Rank"}
    if key == "TEAM-1":
        fields.update(
            {
                "customfield_10001": "로그인 후 목록이 보여야 한다",
                "customfield_10002": {"value": "심각", "id": "1"},
                "customfield_10003": "0|i0001:",
                "attachment": [
                    {
                        "filename": "설계.txt",
                        "content": f"{origin}/jira/secure/attachment/1/design.txt",
                        "size": 2048,
                        "author": user("kim"),
                        "created": "2026-09-01T11:00:00.000+0900",
                    }
                ],
                "issuelinks": [
                    {
                        "type": {"name": "Blocks", "outward": "blocks", "inward": "is blocked by"},
                        "outwardIssue": {
                            "key": "TEAM-2",
                            "fields": {"summary": "결제 모듈 개선", "status": {"name": "Done"}},
                        },
                    }
                ],
                # The ticket response holds only the first two of three comments.
                "comment": {
                    "comments": [
                        {
                            "id": c["id"],
                            "author": user(c["author"]),
                            "body": c["body"],
                            "created": "2026-09-01T12:00:00.000+0900",
                        }
                        for c in COMMENTS[:2]
                    ],
                    "total": 3,
                    "maxResults": 2,
                    "startAt": 0,
                },
            }
        )
        rendered.update(
            {
                "description": (
                    "<h1>재현 절차</h1><p>로그인 버튼을 누르면 <b>오류</b>가 납니다</p>"
                    "<table><tr><th>환경</th><th>결과</th></tr><tr><td>Chrome</td><td>실패</td></tr></table>"
                    '<div class="code panel"><div class="codeContent panelContent"><pre class="code-java">'
                    '<span class="code-keyword">int</span> answer = 1;</pre></div></div>'
                    '<a href="/jira/browse/TEAM-2">관련 티켓</a> <a href="javascript:alert(1)" onclick="secret()">위험</a>'
                    ' <img src="/jira/secure/attachment/2/capture.png" alt="화면 캡처"><script>secret()</script>'
                ),
                "customfield_10001": "<p>로그인 후 목록이 보여야 한다</p>",
                "comment": {"comments": [{"id": c["id"], "body": c["html"]} for c in COMMENTS[:2]]},
            }
        )
    return {"key": key, "fields": fields, "renderedFields": rendered, "names": names}


class JiraServer:
    def __init__(self):
        self.requests, self.logins, self.jql = [], [], []
        self.form_logins = []  # The logins of `logins` sent to JIRA's own forms instead of the SSO.
        self.credentials = {"id": "tester", "password": "correct horse"}
        self.slow = False
        self.forbidden = set()
        self.style = "sso"  # One of LOGIN_STYLES: the JIRA login page shown to signed-out visitors.
        self.pending = False  # The SSO accepts logins but never returns to JIRA.
        self.sso_delay = 0  # Milliseconds after which a script shows the AD SSO link of style mixed.
        self.drop = 0  # The next this many ticket page loads get an answer the browser refuses.
        self.throttled = 0  # The next this many REST requests are answered 429 Too Many Requests (Retry-After: 0).
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
        self.origin, self.sso = f"http://127.0.0.1:{port}", f"http://localhost:{port}"
        self.url = f"{self.origin}/jira/browse/TEAM-1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def form(self):
        """JIRA's own login form instead of the SSO Login link."""
        return self.style == "form"

    @form.setter
    def form(self, value):
        self.style = "form" if value else "sso"

    def login_page(self, destination, message=""):
        target = f"{self.origin}/jira{destination}"
        if self.style == "sso":
            return LOGIN_PAGE.format(sso=f"{self.sso}/sso/login?return={quote(target, safe='')}")
        action = f"/jira/login.jsp?os_destination={quote(destination, safe='')}"
        if self.style == "mixed":
            return self.identity_page(DASHBOARD_PAGE, destination, action, message)
        page = {"form": FORM_PAGE, "custom": CUSTOM_PAGE, "generic": GENERIC_PAGE, "steps": STEPS_PAGE}[self.style]
        return page.format(action=action, message=message, hidden="" if message else "hidden")

    def identity_page(self, page, destination, action, message=""):
        query = f"os_destination={quote(destination, safe='')}"
        # A link drawn a moment after the site's own form, as a script of the page may add it.
        late = f"<script>setTimeout(() => {{ document.getElementById('sso-entry').hidden = false; }}, {self.sso_delay});</script>"
        return page.format(
            sso=f"/jira/sso/start?{query}",
            action=action,
            message=message,
            late="hidden" if self.sso_delay else "",
            script=late if self.sso_delay else "",
        )

    def dashboard(self, message=""):
        """What the site address shows signed-out visitors of style mixed: the dashboard with its login box."""
        return self.identity_page(DASHBOARD_PAGE, "/", "/jira/rest/gadget/1.0/login", message)

    @staticmethod
    def read_form(handler):
        return {
            k: v[0] for k, v in parse_qs(handler.rfile.read(int(handler.headers["Content-Length"])).decode()).items()
        }

    @staticmethod
    def send(handler, status, body="", kind="text/html; charset=utf-8", headers=None):
        data = body.encode() if isinstance(body, str) else body
        handler.send_response(status)
        for name, value in {"Content-Type": kind, "Content-Length": str(len(data)), **(headers or {})}.items():
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(data)

    def json(self, handler, value, status=200):
        self.send(handler, status, json.dumps(value, ensure_ascii=False), "application/json;charset=UTF-8")

    def redirect(self, handler, location, cookie=None):
        self.send(handler, 302, "", headers={"Location": location, **({"Set-Cookie": cookie} if cookie else {})})

    def handle(self, handler, method):
        url = urlsplit(handler.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.requests.append(handler.path)
        if handler.headers.get("Host", "").startswith("localhost"):
            return self.handle_sso(handler, method, url, query)
        if self.drop and method == "GET" and url.path.startswith("/jira/browse/"):
            # Two lengths: the browser refuses the answer (an empty one it might send again on its own).
            self.drop -= 1
            handler.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nContent-Length: 2\r\n\r\nxx")
            return
        signed_in = "session=valid" in (handler.headers.get("Cookie") or "")
        if url.path == "/jira/login.jsp":
            destination = query.get("os_destination", "/")
            if method == "POST" and self.style != "sso":
                form = self.read_form(handler)
                attempt = {
                    "id": form.get("os_username", form.get("uid", form.get("login_id"))),
                    "password": form.get("os_password", form.get("secret", form.get("passcode"))),
                }
                self.logins.append(attempt)
                self.form_logins.append(attempt)
                if attempt == self.credentials and self.style != "mixed":
                    return self.redirect(handler, f"{self.origin}/jira{destination}", SESSION_COOKIE)
                message = REFUSED if self.style == "mixed" else "ID 또는 비밀번호가 올바르지 않습니다."
                return self.send(handler, 200, self.login_page(destination, message))
            return self.send(handler, 200, self.login_page(destination))
        if url.path == "/jira/sso/callback" and query.get("ticket") == "ok":
            return self.redirect(handler, query.get("return", self.origin), SESSION_COOKIE)
        if url.path == "/jira/sso/start":
            target = f"{self.origin}/jira{query.get('os_destination', '/')}"
            return self.redirect(handler, f"{self.sso}/sso/login?return={quote(target, safe='')}")
        if url.path == "/jira/rest/gadget/1.0/login" and method == "POST":  # The dashboard's login box.
            form = self.read_form(handler)
            attempt = {"id": form.get("os_username"), "password": form.get("os_password")}
            self.logins.append(attempt)
            self.form_logins.append(attempt)
            return self.send(handler, 200, self.dashboard(REFUSED))
        if url.path in ("/jira", "/jira/"):
            if not signed_in and self.style == "mixed":
                return self.send(handler, 200, self.dashboard())
            if not signed_in:
                return self.redirect(handler, "/jira/login.jsp?os_destination=%2F")
            return self.send(
                handler,
                200,
                '<!doctype html><html><head><meta name="ajs-context-path" content="/jira">'
                '<meta name="ajs-remote-user" content="tester"><title>대시보드</title></head>'
                "<body><h1>시스템 대시보드</h1></body></html>",
            )
        project = re.fullmatch(r"/jira/projects/([A-Z]+)/?", url.path)
        if project:  # A project's page, which routines link to.
            if not signed_in:
                return self.redirect(handler, f"/jira/login.jsp?os_destination={quote(url.path[5:], safe='')}")
            return self.send(
                handler,
                200,
                '<!doctype html><html><head><meta name="ajs-context-path" content="/jira">'
                f'<meta name="ajs-remote-user" content="tester"><title>{project[1]}</title></head>'
                f"<body><h1>{project[1]} 프로젝트</h1></body></html>",
            )
        page = re.fullmatch(r"/jira/(?:browse|projects/[^/]+/issues)/([A-Z]+-\d+)", url.path)
        if page:
            if not signed_in:
                return self.redirect(handler, f"/jira/login.jsp?os_destination={quote(url.path[5:], safe='')}")
            return self.send(
                handler,
                200,
                '<!doctype html><html><head><meta name="ajs-context-path" content="/jira">'
                f'<meta name="ajs-remote-user" content="tester"><title>{page[1]}</title></head>'
                f"<body><h1>{page[1]}</h1></body></html>",
            )
        api = re.fullmatch(r"/jira/rest/api/2/(.+)", url.path)
        if not api:
            return self.send(handler, 404, "Not found")
        if not signed_in:
            return self.json(handler, {"errorMessages": ["로그인이 필요합니다."]}, 401)
        if self.throttled:
            self.throttled -= 1
            return self.send(handler, 429, "Too Many Requests", headers={"Retry-After": "0"})
        resource = api[1]
        if resource == "search":
            return self.search(handler, query)
        issue = re.fullmatch(r"issue/([A-Z]+-\d+)(/comment)?", resource)
        if not issue or issue[1] not in ISSUES:
            return self.json(handler, {"errorMessages": ["Issue Does Not Exist"]}, 404)
        if issue[1] in self.forbidden:
            return self.json(handler, {"errorMessages": ["권한이 없습니다."]}, 403)
        if issue[2]:
            start = int(query.get("startAt", 0))
            values = [
                {
                    "id": c["id"],
                    "author": user(c["author"]),
                    "body": c["body"],
                    "renderedBody": c["html"],
                    "created": "2026-09-01T12:00:00.000+0900",
                }
                for c in COMMENTS
            ]
            return self.json(
                handler,
                {"startAt": start, "maxResults": 1, "total": len(values), "comments": values[start : start + 1]},
            )
        if self.slow:
            time.sleep(3)
        return self.json(handler, issue_json(issue[1], self.origin))

    def handle_sso(self, handler, method, url, query):
        if url.path != "/sso/login":
            return self.send(handler, 404, "Not found")
        action = f"/sso/login?return={quote(query.get('return', ''), safe='')}"
        if method == "GET":
            return self.send(handler, 200, SSO_PAGE.format(action=action, message=""))
        form = self.read_form(handler)
        self.logins.append({"id": form.get("id"), "password": form.get("pw")})
        if {"id": form.get("id"), "password": form.get("pw")} != self.credentials:
            return self.send(
                handler, 200, SSO_PAGE.format(action=action, message="ID 또는 비밀번호가 올바르지 않습니다.")
            )
        if self.pending:
            return self.send(handler, 200, PENDING_PAGE)
        self.redirect(
            handler, f"{self.origin}/jira/sso/callback?ticket=ok&return={quote(query.get('return', ''), safe='')}"
        )

    def search(self, handler, query):
        jql = query.get("jql", "")
        self.jql.append(jql)
        keys = sorted(ISSUES)
        if text := re.search(r'text ~ "([^"]*)"', jql):
            words = text[1].lower().split()
            keys = [k for k in keys if all(w in f"{ISSUES[k]['summary']} {ISSUES[k]['text']}".lower() for w in words)]
        for field in ("assignee", "reporter"):
            if match := re.search(rf'{field} = "([^"]*)"', jql):
                if match[1] not in USERS:
                    return self.json(
                        handler,
                        {
                            "errorMessages": [f"The value '{match[1]}' does not exist for the field '{field}'."],
                            "errors": {},
                        },
                        400,
                    )
        people = re.findall(r'(assignee|reporter) = "([^"]*)"', jql)
        if people:
            keys = [k for k in keys if any(ISSUES[k][field] == name for field, name in people)]
        if "currentUser()" in jql:
            keys = [k for k in keys if ISSUES[k]["assignee"] == "tester"]
        if "resolution = Unresolved" in jql:
            keys = [k for k in keys if ISSUES[k].get("category") != "done"]
        if "resolutiondate >=" in jql:
            keys = [k for k in keys if ISSUES[k].get("category") == "done"]
        if "ORDER BY updated DESC" in jql:
            keys.sort(key=lambda k: ISSUES[k].get("updated", UPDATED), reverse=True)
        start = int(query.get("startAt", 0))
        if query.get("maxResults") == "0":  # Only the count.
            return self.json(handler, {"startAt": start, "maxResults": 0, "total": len(keys), "issues": []})
        fielded = "status" in query.get("fields", "")
        # One ticket per page, so the importer has to follow the pagination.
        return self.json(
            handler,
            {
                "startAt": start,
                "maxResults": 1,
                "total": len(keys),
                "issues": [
                    {"key": k, "fields": search_fields(k) if fielded else {"summary": ISSUES[k]["summary"]}}
                    for k in keys[start : start + 1]
                ],
            },
        )
