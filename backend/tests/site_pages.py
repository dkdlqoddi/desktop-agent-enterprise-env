"""Login screens of other products on one local origin, for the login method tests: two-step Atlassian Cloud, ADFS, a
plain form beside a search box, a custom screen behind a button, one whose login never finishes, one that redraws its
password field while signing in, one that asks for a one-time code after the password, one that needs a domain typed
beside the account, one that shows a second form after the ADFS one, an entry page whose "Log in with SSO" button leads
to the plain form, and a link that answers without content. A login with the right account goes to /home, which names
the signed-in user.

The same pages answer on a second origin, localhost on the same port, as another site: /lure and /lure-script lead
there with a ‘로그인’ link and a button that sends the browser there, and /away redirects there as an SSO does."""

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PAGES = {
    "/home": '<!doctype html><html><head><meta name="ajs-remote-user" content="tester"></head><body><h1>홈</h1></body>'
    "</html>",
    "/anonymous": '<!doctype html><html><head><meta name="ajs-remote-user" content=""></head><body></body></html>',
    # An SSO entry as many sites label it, a button rather than a link.
    "/sso-button": """<!doctype html><p>로그인이 필요합니다.</p>
    <button type="button" onclick="location.href = '/generic'">Log in with SSO</button>""",
    "/cloud": """<!doctype html><form id="form" method="post" action="/cloud">
    <input id="username" name="username" type="email">
    <div id="pw" hidden><input id="password" name="password" type="password"></div>
    <button id="login-submit" type="button">계속</button></form>
    <script>
      const button = document.getElementById('login-submit');
      button.onclick = () => {
        const box = document.getElementById('pw');
        if (box.hidden) { box.hidden = false; button.textContent = '로그인'; }
        else document.getElementById('form').submit();
      };
    </script>""",
    "/adfs": """<!doctype html><form method="post" action="/adfs">
    <input id="userNameInput" name="UserName"><input id="passwordInput" name="Password" type="password">
    <span id="submitButton" onclick="this.closest('form').submit()">로그인</span></form>""",
    "/generic": """<!doctype html><header><input name="search" placeholder="검색"></header>
    <form method="post" action="/generic"><label>아이디 <input name="user"></label>
    <input type="hidden" name="token" value="t"><label>암호 <input name="pw" type="password"></label>
    <button>들어가기</button></form>""",
    "/custom": """<!doctype html><button id="open-login"
      onclick="document.getElementById('box').hidden = false; this.hidden = true">로그인하기</button>
    <div id="box" hidden><form method="post" action="/custom"><input id="user-name" name="user">
    <input id="user-pass" name="pw" type="password">
    <div id="do-login" role="button" onclick="this.closest('form').submit()">들어가기</div></form></div>""",
    "/pending": """<!doctype html><form method="post" action="/pending"><input name="user">
    <input name="pw" type="password"><button>로그인</button></form>""",
    "/otp": """<!doctype html><form method="post" action="/otp"><input name="user">
    <input name="pw" type="password"><button>로그인</button></form>""",
    # An ADFS form at once, and a second form a script shows a second later, for a method the user chose.
    "/late": """<!doctype html><form method="post" action="/adfs">
    <input id="userNameInput" name="UserName"><input id="passwordInput" name="Password" type="password">
    <span id="submitButton" onclick="this.closest('form').submit()">로그인</span></form>
    <form id="late" method="post" action="/generic" hidden><input id="late-user" name="user">
    <input id="late-pw" name="pw" type="password"><button id="late-go">들어가기</button></form>
    <script>setTimeout(() => { document.getElementById('late').hidden = false; }, 1000);</script>""",
    # The account signs in only with the domain EXAMPLE typed beside it.
    "/domain": """<!doctype html><form method="post" action="/domain"><label>도메인 <input name="domain"></label>
    <label>아이디 <input name="user"></label><label>암호 <input name="pw" type="password"></label>
    <button>로그인</button></form>""",
    # A single-page login that redraws its password field while signing in successfully.
    "/redraw": """<!doctype html><form><input name="user"><span id="field"><input name="pw" type="password"></span>
    <button id="go" type="button">로그인</button></form>
    <script>
      document.getElementById('go').onclick = () => {
        document.getElementById('field').innerHTML = '<input name="pw" type="password">';
        setTimeout(() => { location.href = '/home'; }, 600);
      };
    </script>""",
}
# The step after a right password on /otp: a one-time code, in an empty password field where the password was.
OTP_PAGE = """<!doctype html><form method="post" action="/otp-code"><input name="user" type="hidden" value="">
<input name="code" type="password" autocomplete="one-time-code"><button>확인</button></form>"""
# The ID and password fields each form posts.
FIELDS = {
    "/cloud": ("username", "password"),
    "/adfs": ("UserName", "Password"),
    "/generic": ("user", "pw"),
    "/custom": ("user", "pw"),
    "/pending": ("user", "pw"),
    "/otp": ("user", "pw"),
    "/domain": ("user", "pw"),
}


class SitePages:
    def __init__(self, credentials):
        self.credentials = credentials
        self.posts, self.visits = [], []  # What each form sent; each page opened, with time.monotonic().
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                server.get(self)

            def do_POST(self):
                server.post(self)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        port = self.httpd.server_address[1]
        self.origin, self.other = f"http://127.0.0.1:{port}", f"http://localhost:{port}"
        self.pages = {
            **PAGES,
            "/lure": f'<!doctype html><p>계정이 필요합니다.</p><a href="{self.other}/generic">로그인</a>',
            "/lure-script": f"""<!doctype html><p>계정이 필요합니다.</p>
            <button type="button" onclick="location.href = '{self.other}/generic'">로그인</button>""",
        }
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    @staticmethod
    def send(handler, status, body="", headers=None):
        data = body.encode()
        handler.send_response(status)
        for name, value in {
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(data)),
            **(headers or {}),
        }.items():
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(data)

    def get(self, handler):
        path = urlsplit(handler.path).path
        self.visits.append((path, time.monotonic()))
        if path == "/empty":  # No content: the browser stays where it was and reports the navigation as aborted.
            handler.send_response(204)
            handler.end_headers()
            return
        if path == "/away":  # Another site's login screen, as an SSO's redirect leads there.
            return self.send(handler, 302, headers={"Location": f"{self.other}/custom"})
        self.send(handler, 200, self.pages[path]) if path in self.pages else self.send(handler, 404, "Not found")

    def post(self, handler):
        path = urlsplit(handler.path).path
        length = int(handler.headers.get("Content-Length") or 0)
        form = {key: values[0] for key, values in parse_qs(handler.rfile.read(length).decode()).items()}
        login, password = FIELDS[path]
        values = {"id": form.get(login), "password": form.get(password)}
        self.posts.append(
            {
                "path": path,
                **values,
                "search": form.get("search"),
                **({"domain": form.get("domain")} if path == "/domain" else {}),
            }
        )
        if path == "/pending":  # Waits for an approval that never comes, without showing the form again.
            return self.send(handler, 200, "<!doctype html><p>승인을 기다리고 있습니다…</p>")
        if path == "/otp" and values == self.credentials:
            return self.send(handler, 200, OTP_PAGE)
        if values == self.credentials and (path != "/domain" or form.get("domain") == "EXAMPLE"):
            return self.send(handler, 302, headers={"Location": "/home"})
        self.send(handler, 200, PAGES[path])
