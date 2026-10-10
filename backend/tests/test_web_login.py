"""Login methods on real Chromium: detection, the order of the saved method and of the SSO link and AD SSO methods,
rejected and stalled logins and the methods tried after them, user-defined steps and generic forms, other products'
login screens, closed and aborted pages, and the JIRA importer's login check and login window."""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from agent import browser_import
from agent.db import Database
from agent.jira import ticket_page
from agent.llm import UserError
from agent.service import Service
from agent.web_login import Elsewhere, Hosts, LoginError, origin_of, sign_in, signed_in, validate_plan, with_scheme
from jira_server import JiraServer
from site_pages import SitePages

# Real Chromium (conftest.py skips the module without it).
pytestmark = pytest.mark.browser
CATALOG = json.loads(
    (Path(__file__).resolve().parents[2] / "electron" / "login-methods.json").read_text(encoding="utf-8")
)["methods"]
METHODS = {method["id"]: method for method in CATALOG}
PASSWORD = "correct horse"
# What Electron builds from a site whose login method the user defined with their own selectors.
CUSTOM = {
    "id": "custom",
    "label": "사용자 지정",
    "detect": ["#open-login", "#user"],
    "steps": [
        {"action": "click", "target": "#open-login", "optional": True, "unless": "#user"},
        {"action": "fill", "value": "id", "target": "#user"},
        {"action": "fill", "value": "password", "target": "#pass"},
        {"action": "submit", "target": "#go"},
    ],
}


def plan(credentials=None, mode="auto", methods=None):
    return {
        "site_id": "site-1",
        "site_name": "JIRA",
        "mode": mode,
        "methods": methods or CATALOG,
        "credentials": dict(credentials or {"id": "tester", "password": PASSWORD}),
    }


@pytest.fixture
def server():
    server = JiraServer()
    yield server
    server.close()


async def at_ticket(page, origin):
    return ticket_page(page.url, origin) or await signed_in(page, origin)


def attempt(
    server, login, url=None, *, arrived=None, product="JIRA 티켓", optional=1, form=10, seconds=20, watched=False
):
    """Sign in once with a fresh browser. Returns the result or the LoginError, the seconds it took and the
    page's final URL."""
    arrived = arrived or (lambda value: at_ticket(value, server.origin))

    async def main():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chromium", headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                started = time.monotonic()
                try:
                    result = await sign_in(
                        context,
                        page,
                        url or server.url,
                        validate_plan(login),
                        arrived=arrived,
                        product=product,
                        watched=watched,
                        optional_seconds=optional,
                        form_seconds=form,
                        login_seconds=seconds,
                    )
                except LoginError as error:
                    result = error
                return result, time.monotonic() - started, page.url
            finally:
                await browser.close()

    return asyncio.run(main())


def test_auto_mode_detects_the_sso_link_method(server):
    result, _, url = attempt(server, plan())
    # The SSO form is on another host than the site: Electron keeps it as the site's login host (login_hosts).
    assert result == {
        "method": "sso-link",
        "label": METHODS["sso-link"]["label"],
        "detected": True,
        "hosts": ["localhost"],
    }
    assert url == server.url
    assert server.logins == [server.credentials]


def test_saved_method_comes_first_and_fixed_mode_uses_only_its_method(server):
    # JIRA's own login form fits both the Atlassian method and the generic one: catalog order picks the former,
    # a saved method in front of the catalog is tried first.
    server.style = "form"
    assert attempt(server, plan())[0]["method"] == "atlassian"
    saved = [METHODS["generic"], *CATALOG]  # Electron puts the saved method first; its repeat is ignored.
    assert attempt(server, plan(methods=saved))[0] == {
        "method": "generic",
        "label": METHODS["generic"]["label"],
        "detected": True,
    }
    assert attempt(server, plan(mode="fixed", methods=[METHODS["atlassian"]]))[0]["detected"] is False
    assert server.logins == [server.credentials] * 3
    # A page that needs no login (the session is cleared first, so a public page) returns without a method.

    async def public(_):
        return True

    assert attempt(server, plan(), url=f"{server.origin}/jira/help", arrived=public)[0] == {
        "method": None,
        "label": "",
        "detected": False,
    }
    assert len(server.logins) == 3


def test_fixed_method_that_does_not_fit_the_page(server):
    # The JIRA login page only has the SSO Login link: JIRA's own form never shows.
    error, seconds, _ = attempt(server, plan(mode="fixed", methods=[METHODS["atlassian"]]), form=2)
    assert isinstance(error, LoginError) and str(error).startswith("로그인 화면을 찾지 못했습니다.")
    assert seconds < 10 and server.logins == [] and error.sent is False
    # A method whose screen shows but whose password field does not: the step that failed is named.
    broken = {
        **CUSTOM,
        "steps": [
            *CUSTOM["steps"][:2],
            {"action": "fill", "value": "password", "target": "#missing"},
            CUSTOM["steps"][3],
        ],
    }
    server.style = "custom"
    error = attempt(server, plan(mode="fixed", methods=[broken]), form=2)[0]
    assert str(error) == (
        "로그인 화면을 찾지 못했습니다. 주소를 확인하거나 관리 → 사이트 계정에서 로그인 방식을 선택해 "
        "주세요. (시도한 방식: 사용자 지정: 비밀번호 입력란 찾지 못함)"
    )
    assert server.logins == []
    # Electron sends the chosen method first and the others after it: one that fits signs in instead.
    chosen = {**broken, "id": "custom-old", "label": "예전 화면", "priority": True}
    result = attempt(server, plan(mode="fixed", methods=[chosen, *CATALOG, CUSTOM]), form=2)[0]
    assert result == {
        "method": "custom",
        "label": "사용자 지정",
        "detected": False,
        "tried": ["예전 화면: 비밀번호 입력란 찾지 못함"],
    }, str(result)
    assert server.logins == [server.credentials]


def test_rejected_login_is_recognized_quickly_without_the_password(server):
    wrong = {"id": "tester", "password": "wrong horse"}
    # The SSO shows its form again after the first submission, and no other method fits the page.
    error, seconds, _ = attempt(server, plan(wrong, "fixed", [METHODS["sso-link"]]), seconds=30)
    assert str(error) == (
        "로그인하지 못했습니다. ID·비밀번호를 확인해 주세요. "
        "(시도한 방식: SSO 링크 · 표준 로그인 폼: 로그인 화면이 다시 표시됨)"
    )
    assert seconds < 15 and len(server.logins) == 1 and error.sent is True
    error, seconds, _ = attempt(server, plan(wrong), seconds=30)
    assert str(error) == (
        "로그인하지 못했습니다. ID·비밀번호를 확인해 주세요. "
        "(시도한 방식: SSO 링크 · 표준 로그인 폼: 로그인 화면이 다시 표시됨)"
    )
    assert seconds < 15 and len(server.logins) == 2
    # Another method that fits the page too (the generic one on JIRA's own form) would type the refused ID and
    # password into the very same fields: it is not sent, as more failed attempts could lock the account.
    server.style = "form"
    error = attempt(server, plan(wrong), seconds=30)[0]
    assert str(error) == (
        "로그인하지 못했습니다. ID·비밀번호를 확인해 주세요. "
        "(시도한 방식: JIRA·Confluence 기본 로그인: 로그인 화면이 다시 표시됨, "
        "일반 로그인 폼 · 자동 탐색: 앞서 보낸 입력란과 같아 다시 보내지 않음)"
    )
    assert len(server.logins) == 3
    assert "wrong horse" not in str(error)


def test_after_a_refused_login_the_next_method_that_fits_signs_in(server):
    # A JIRA shows its own form beside an AD SSO link, and its own form refuses the SSO account. Where the user chose
    # JIRA's own login for the site, it goes first, and the AD SSO link signs in after its refusal.
    server.style = "mixed"
    chosen = {**METHODS["atlassian"], "priority": True}
    login = plan(mode="fixed", methods=[chosen, *(method for method in CATALOG if method["id"] != "atlassian")])
    result, _, url = attempt(server, login, seconds=30)
    assert result == {
        "method": "sso-link",
        "label": METHODS["sso-link"]["label"],
        "detected": False,
        "hosts": ["localhost"],
        "tried": ["JIRA·Confluence 기본 로그인: 로그인 화면이 다시 표시됨"],
    }, str(result)
    assert (url, server.form_logins, server.logins) == (server.url, [server.credentials], [server.credentials] * 2)


def test_an_sso_login_drawn_late_comes_before_the_sites_own_form(server):
    # The AD SSO link shows a second after JIRA's own form: auto mode waits for it rather than typing the SSO
    # account into JIRA's own form.
    server.style, server.sso_delay = "mixed", 1000
    result, _, url = attempt(server, plan())
    assert (result["method"], url) == ("sso-link", server.url), str(result)
    assert server.form_logins == [] and server.logins == [server.credentials]


def test_a_watched_window_leaves_a_login_that_does_not_arrive_to_the_user(server):
    # The SSO accepts the account but waits for an approval. In the background the sign-in goes on with the next
    # method that fits; in the login window the user watches, the approval may be under way, so it stops there.
    server.style, server.pending = "mixed", True
    login = plan(methods=[METHODS["sso-link"], METHODS["atlassian"]])
    error = attempt(server, login, seconds=2)[0]
    assert str(error) == (
        "로그인하지 못했습니다. ID·비밀번호를 확인해 주세요. (시도한 방식: SSO 링크 · 표준 로그인 폼: "
        "2초 안에 사이트로 이동하지 않음, JIRA·Confluence 기본 로그인: 로그인 화면이 다시 표시됨)"
    )
    assert (len(server.logins), len(server.form_logins), error.sent) == (2, 1, True)
    error = attempt(server, login, seconds=2, watched=True)[0]
    assert str(error).startswith("로그인 후 2초 안에 JIRA 티켓 페이지로 이동하지 않았습니다.")
    assert str(error).endswith("(시도한 방식: SSO 링크 · 표준 로그인 폼: 2초 안에 사이트로 이동하지 않음)")
    assert (len(server.logins), len(server.form_logins), error.sent) == (3, 1, True)


def test_auto_mode_moves_on_when_a_method_does_not_fit(server):
    # The first method's screen shows but its password field never does: nothing was submitted, so the next
    # method that fits the page signs in, and the result is the one to save.
    broken = {
        **CUSTOM,
        "id": "custom-old",
        "label": "예전 화면",
        "steps": [
            *CUSTOM["steps"][:2],
            {"action": "fill", "value": "password", "target": "#old-password"},
            CUSTOM["steps"][3],
        ],
    }
    server.style = "custom"
    result, seconds, url = attempt(server, plan(methods=[broken, CUSTOM]), form=2)
    assert result == {
        "method": "custom",
        "label": "사용자 지정",
        "detected": True,
        "tried": ["예전 화면: 비밀번호 입력란 찾지 못함"],
    }
    assert (url, server.logins) == (server.url, [server.credentials])
    # When no method fits, the misses are listed.
    error = attempt(server, plan(methods=[broken, METHODS["atlassian"]]), form=2)[0]
    assert str(error) == (
        "로그인 화면을 찾지 못했습니다. 주소를 확인하거나 관리 → 사이트 계정에서 로그인 방식을 선택해 "
        "주세요. (시도한 방식: 예전 화면: 비밀번호 입력란 찾지 못함)"
    )
    assert len(server.logins) == 1


def test_stalled_login_times_out(server):
    server.pending = True
    error, seconds, _ = attempt(server, plan(), seconds=2)
    assert str(error).startswith("로그인 후 2초 안에 JIRA 티켓 페이지로 이동하지 않았습니다.")
    assert seconds < 15 and server.logins == [server.credentials]


def test_user_defined_and_generic_methods(server):
    # A button reveals the form; the user named the elements to click and fill.
    server.style = "custom"
    assert attempt(server, plan(mode="fixed", methods=[CUSTOM]))[0] == {
        "method": "custom",
        "label": "사용자 지정",
        "detected": False,
    }
    # Unusual field names and positions: the generic method finds the password field and the ID field before it.
    server.style = "generic"
    result, _, url = attempt(server, plan())
    assert (result["method"], url) == ("generic", server.url)
    # A user-defined method without a submit button presses Enter in the password field.
    server.style = "form"
    enter = {
        "id": "custom",
        "label": "사용자 지정",
        "detect": ["#login-form-username"],
        "steps": [
            {"action": "fill", "value": "id", "target": "#login-form-username"},
            {"action": "fill", "value": "password", "target": "#login-form-password"},
            {"action": "submit"},
        ],
    }
    result, _, url = attempt(server, plan(mode="fixed", methods=[enter]))
    assert (result["method"], url) == ("custom", server.url)
    assert server.logins == [server.credentials] * 3


def test_enter_without_a_submission_is_a_missing_button(server, monkeypatch):
    import jira_server

    original = jira_server.SSO_PAGE
    monkeypatch.setattr(
        jira_server,
        "SSO_PAGE",
        original.replace(
            '<button id="sign-in" type="submit">Sign in</button>', '<input name="domain" value="EXAMPLE">'
        ),
    )
    # The SSO form has no submit button and two text fields, so Enter submits nothing: the generic method must not
    # wait for a login that never started.
    sso = f"{server.sso}/sso/login?return={server.origin}/jira/"
    error, seconds, _ = attempt(server, plan(mode="fixed", methods=[METHODS["generic"]]), url=sso, seconds=30)
    assert str(error).endswith("(시도한 방식: 일반 로그인 폼 · 자동 탐색: 버튼 찾지 못함)")
    assert seconds < 15 and server.logins == []
    monkeypatch.setattr(jira_server, "SSO_PAGE", original)
    # A conventional identity-provider page uses generic form detection.
    assert attempt(server, plan(), url=sso)[0]["method"] == "generic"


def test_plan_validation():
    good = plan()
    normalized = validate_plan({**good, "site_name": " JIRA ", "methods": [METHODS["generic"], *CATALOG]})
    assert [m["id"] for m in normalized["methods"]] == ["generic"] + [m["id"] for m in CATALOG if m["id"] != "generic"]
    assert normalized["site_name"] == "JIRA" and normalized["credentials"] == good["credentials"]
    # The SSO link and AD SSO methods are the ones to try first.
    assert [m["id"] for m in normalized["methods"] if m["priority"]] == ["sso-link", "adfs"]
    assert validate_plan(None) is None
    step = {"action": "fill", "value": "id", "target": "#user"}
    password, submit = {"action": "fill", "value": "password", "target": "#pass"}, {"action": "submit", "target": "#go"}
    method = {"id": "m", "label": "방식", "detect": ["#user"], "steps": [step, password, submit]}

    def steps(*items):
        return {**good, "methods": [{**method, "steps": list(items)}]}

    for bad in [
        [],
        "auto",
        {**good, "mode": "manual"},
        {**good, "methods": []},
        {**good, "methods": [method] * 13},
        {**good, "methods": ["sso"]},
        {**good, "methods": [{**method, "id": ""}]},
        {**good, "methods": [{**method, "id": "x" * 41}]},
        {**good, "methods": [{**method, "label": ""}]},
        {**good, "methods": [{**method, "detect": []}]},
        {**good, "methods": [{**method, "detect": ["#a"] * 5}]},
        {**good, "methods": [{**method, "detect": [""]}]},
        {**good, "methods": [{**method, "priority": "yes"}]},
        steps(),
        steps(*[step] * 15, password, submit),
        steps({**step, "action": "type"}, password, submit),
        steps({**step, "value": "otp"}, password, submit),
        steps({"action": "click"}, password, submit),
        steps({**step, "target": "x" * 1001}, password, submit),
        steps({**step, "optional": "yes"}, password, submit),
        # The password is filled, and the login is sent last, once; the account's fields and the submit always count.
        steps(step, submit),
        steps(step, password),
        steps(step, submit, password, submit),
        steps(submit, step, password),
        steps({**step, "optional": True}, password, submit),
        steps(step, password, {**submit, "optional": True}),
        # A text the user wrote: a line of at most 200 characters.
        steps({**step, "value": "text"}, password, submit),
        steps({**step, "value": "text", "text": ""}, password, submit),
        steps({**step, "value": "text", "text": "a\nb"}, password, submit),
        steps({**step, "value": "text", "text": "x" * 201}, password, submit),
        {**good, "credentials": None},
        {**good, "credentials": {"id": "tester"}},
        {**good, "credentials": {"id": "a\nb", "password": "x"}},
        {**good, "credentials": {"id": "a", "password": "x\ny"}},
        {**good, "credentials": {"id": "a" * 257, "password": "x"}},
        {**good, "credentials": {"id": "a", "password": "x" * 1025}},
        {**good, "site_name": "x" * 121},
        {**good, "site_id": 3},
    ]:
        with pytest.raises(UserError, match="사이트 로그인 정보 형식이 올바르지 않습니다") as raised:
            validate_plan(bad)
        assert PASSWORD not in str(raised.value)
    # A submit step may leave its button out (Enter), and optional/unless may be empty.
    assert validate_plan(steps(step, password, {"action": "submit", "target": "", "unless": None, "optional": None}))
    # The site's login hosts, lower case and at most 20 of them; none when Electron gives none.
    assert validate_plan({**good, "hosts": ["IdP.Corp.Test", "idp.corp.test", "[::1]:8443"]})["hosts"] == [
        "idp.corp.test",
        "[::1]:8443",
    ]
    assert validate_plan(good)["hosts"] == [] and validate_plan({**good, "hosts": None})["hosts"] == []
    for hosts in ("idp.corp.test", ["idp corp"], [""], ["x" * 254], [3], ["a"] * 21):
        with pytest.raises(UserError, match="사이트 로그인 정보 형식"):
            validate_plan({**good, "hosts": hosts})
    # Up to 16 steps, with texts the user wrote, which may be optional, as clicks may.
    domain = {
        "action": "fill",
        "value": "text",
        "text": "EXAMPLE",
        "target": "#domain",
        "optional": True,
        "unless": "#x",
    }
    found = validate_plan(
        steps(*[{"action": "click", "target": "#notice", "optional": True}] * 12, domain, step, password, submit)
    )["methods"][0]
    assert (len(found["steps"]), found["priority"]) == (16, False)
    assert found["steps"][12] == {**domain, "text": "EXAMPLE"} and found["steps"][13]["text"] is None
    assert origin_of("HTTPS://Jira.Example.com:443/jira/secure/Dashboard.jspa") == "https://jira.example.com"
    assert origin_of("http://jira.test:8080/browse/A-1") == "http://jira.test:8080"
    for value in ("ftp://jira.test/", "https://user:pw@jira.test/", "not a url", None, "https://jira.test:99999/"):
        assert origin_of(value) is None


def test_an_address_without_its_scheme_reads_as_the_address_bar_reads_it():
    assert with_scheme("example.com") == "https://example.com"
    assert with_scheme("  //example.com/portal?x=1#y ", "http") == "http://example.com/portal?x=1#y"
    assert with_scheme("127.0.0.1:8080/jira") == "https://127.0.0.1:8080/jira"
    assert with_scheme("[::1]:8080/a") == "https://[::1]:8080/a"
    assert with_scheme("HTTP://jira.test/x") == "HTTP://jira.test/x"
    assert origin_of(with_scheme("Jira.test:8443/jira/browse/A-1")) == "https://jira.test:8443"
    # A path, a name with spaces, an account in the address and other schemes are no address to complete.
    for value in (
        "C:\\jira",
        "C:/jira",
        "\\\\server\\share",
        "jira test",
        "jira.test/a b",
        "user@jira.test",
        "mailto:a@jira.test",
        "about:blank",
        "javascript:alert(1)",
        "ftp://jira.test",
        "",
        None,
        3,
    ):
        assert with_scheme(value) == value, value
        assert origin_of(with_scheme(value)) is None, value


class Refusing:
    """A page whose site answers over neither scheme: every address opened is recorded, and refused."""

    def __init__(self):
        self.url, self.opened = "about:blank", []

    async def goto(self, url, **options):
        self.opened.append(url)
        self.url = "chrome-error://chromewebdata/"
        raise Exception(f"net::ERR_CONNECTION_REFUSED at {url}")

    def is_closed(self):
        return False


def test_a_site_address_without_its_scheme_opens_over_https_and_never_over_http(tmp_path):
    jira = Service(Database(tmp_path / "wiki.db"), None).jira
    page = Refusing()

    def reach(url):
        return asyncio.run(jira._reach(page, url))

    # One that names its scheme opens as it is: http only when the user wrote http://.
    assert reach("http://jira.test/jira") == ("http://jira.test/jira", "http://jira.test")
    # Without one, https: the account must never go over a plain connection because https did not answer.
    assert reach("jira.test/jira") == ("https://jira.test/jira", "https://jira.test")
    assert reach("wiki.test:8080/wiki") == ("https://wiki.test:8080/wiki", "https://wiki.test:8080")
    assert reach("//portal.test") == ("https://portal.test", "https://portal.test")
    # Nothing is tried first to find a scheme: the site is opened once, by the steps that follow.
    assert page.opened == []


class Page:
    """Where Hosts listens for the page's requests."""

    def on(self, event, listener):
        self.listener = listener

    def remove_listener(self, event, listener):
        self.listener = None


class Navigation:
    """A main-frame navigation request as Playwright reports it."""

    def __init__(self, url, redirected_from=None):
        self.url, self.redirected_from = url, redirected_from
        self.frame = type("Frame", (), {"parent_frame": None})()

    def is_navigation_request(self):
        return True


def test_the_account_is_typed_only_where_the_site_led_and_over_https():
    page = Page()
    hosts = Hosts(page, "https://jira.test/jira/browse/TEAM-1")
    # Trust the site's own host and observed redirects, with no identity-provider hosts trusted in advance.
    hosts.require("https://jira.test/jira/login.jsp")
    with pytest.raises(LoginError):
        hosts.require("https://identity.example.com/adfs/ls/?SAMLRequest=x")
    page.listener(Navigation("https://jira.test/jira/sso/start"))
    page.listener(Navigation("https://sso.corp.test/login"))
    hosts.require("https://sso.corp.test/login?return=x")
    # A click the LLM chose that took the browser to another host, also through that host's redirects; a link of the
    # site that the site's server sent on to another host is the site's.
    with hosts.following():
        lured = Navigation("https://evil.test/login")
        page.listener(lured)
        page.listener(Navigation("https://evil2.test/login", lured))
        own = Navigation("https://jira.test/jira/sso")
        page.listener(own)
        page.listener(Navigation("https://idp.corp.test/adfs", own))
    for url in ("https://evil.test/login", "https://evil2.test/login"):
        with pytest.raises(LoginError, match="사이트 주소와 다른 곳") as raised:
            hosts.require(url)
        assert raised.value.sent is False
    hosts.require("https://idp.corp.test/adfs")
    # Never in plain text on an https site, and never on a page without an address.
    with pytest.raises(LoginError, match="암호화되지 않은 주소"):
        hosts.require("http://jira.test/jira/login.jsp")
    with pytest.raises(LoginError, match="주소를 확인할 수 없어"):
        hosts.require("about:blank")
    # The LLM may follow links to an already trusted host, or links that do not navigate to another page.
    for url in ("https://jira.test/x", "", "javascript:void(0)", "mailto:a@b.test"):
        hosts.lead(url)
    with pytest.raises(Elsewhere) as raised:
        hosts.lead("https://evil.test/login")
    assert raised.value.host == "evil.test"
    hosts.close()
    assert page.listener is None
    # A site explicitly registered over HTTP may also sign in over HTTP on the same host.
    portal = Hosts(Page(), "http://portal.example.com/home")
    portal.require("http://portal.example.com/auth/login")
    with pytest.raises(LoginError):
        portal.require("http://www.example.com/login")
    # The hosts the account went to besides the site's own are what Electron keeps as the site's login hosts.
    assert {"jira.test", "idp.corp.test"} <= hosts.used and not {"evil.test", "evil2.test"} & hosts.used
    # With them pinned, the account goes to the site and those alone: a host the site leads to anew (an open
    # redirect, a moved login screen) is refused until the user resets the hosts, and the LLM's clicks may not lead
    # there either.
    page = Page()
    pinned = Hosts(page, "https://jira.test/jira", pinned=["IdP.Corp.Test"])
    page.listener(Navigation("https://idp.corp.test/adfs"))
    page.listener(Navigation("https://new-idp.test/login"))
    pinned.require("https://jira.test/jira/login.jsp")
    pinned.require("https://idp.corp.test/adfs/ls")
    with pytest.raises(LoginError, match="저장된 로그인 주소에 없는 곳\\(new-idp.test\\)"):
        pinned.require("https://new-idp.test/login")
    with pytest.raises(Elsewhere):
        pinned.lead("https://new-idp.test/login")
    assert pinned.used == {"jira.test", "idp.corp.test"}


# ---------------------------------------------------------------------------
# Other products' login screens, and pages that close or answer without content
# ---------------------------------------------------------------------------


def custom_method(custom):
    """The method Electron builds from a site whose login screen the user defined (sites.cjs customMethod)."""
    steps = [
        {"action": "click", "target": target, "optional": True, "unless": custom["id"]} for target in custom["before"]
    ]
    steps.append({"action": "fill", "value": "id", "target": custom["id"]})
    if custom["next"]:
        steps.append({"action": "click", "target": custom["next"]})
    steps.append({"action": "fill", "value": "password", "target": custom["password"]})
    steps.append({"action": "submit", "target": custom["submit"]} if custom["submit"] else {"action": "submit"})
    return {"id": "custom", "label": "사용자 지정", "detect": [*custom["before"][:1], custom["id"]], "steps": steps}


@pytest.fixture
def pages():
    pages = SitePages({"id": "tester", "password": PASSWORD})
    yield pages
    pages.close()


def on_site(pages):
    return lambda page: signed_in(page, pages.origin)


def test_atlassian_cloud_adfs_a_plain_form_and_a_custom_screen(pages):
    account = {"path": None, "id": "tester", "password": PASSWORD, "search": None}
    for path, ident in [("/cloud", "atlassian-cloud"), ("/adfs", "adfs"), ("/generic", "generic")]:
        result, _, _ = attempt(pages, plan(), pages.origin + path, arrived=on_site(pages), product="테스트")
        assert (result["method"], result["detected"]) == (ident, True), (path, result)
        # The plain form's ID is the field before the password, not the search box.
        assert pages.posts[-1] == {**account, "path": path}
    # A login form behind a button, with a clickable element instead of a submit button.
    custom = custom_method(
        {
            "before": ['xpath=//button[@id="open-login"]'],
            "id": "#user-name",
            "next": "",
            "password": "#user-pass",
            "submit": "#do-login",
        }
    )
    result, _, _ = attempt(
        pages, plan(mode="fixed", methods=[custom]), pages.origin + "/custom", arrived=on_site(pages), product="테스트"
    )
    assert result == {"method": "custom", "label": "사용자 지정", "detected": False}
    assert pages.posts[-1] == {**account, "path": "/custom"}
    # Without a submit button the password field is submitted with Enter.
    enter = custom_method(
        {"before": [], "id": 'input[name="user"]', "next": "", "password": 'input[name="pw"]', "submit": ""}
    )
    attempt(
        pages, plan(mode="fixed", methods=[enter]), pages.origin + "/generic", arrived=on_site(pages), product="테스트"
    )
    assert pages.posts[-1] == {**account, "path": "/generic"}
    # A page that needs no login returns at once.
    count = len(pages.posts)
    result, _, _ = attempt(pages, plan(), pages.origin + "/home", arrived=on_site(pages), product="테스트")
    assert (result, len(pages.posts)) == ({"method": None, "label": "", "detected": False}, count)


def test_an_sso_entry_with_a_common_label_signs_in_through_the_sso_link_method(pages):
    # "Log in with SSO" as a button: the SSO link method clicks it and fills the form it leads to.
    result, _, _ = attempt(pages, plan(), pages.origin + "/sso-button", arrived=on_site(pages), product="테스트")
    assert (result["method"], result["detected"]) == ("sso-link", True), result
    assert pages.posts == [{"path": "/generic", "id": "tester", "password": PASSWORD, "search": None}]


def test_a_password_field_redrawn_during_a_successful_login_is_not_a_rejection(pages):
    redraw = custom_method(
        {"before": [], "id": 'input[name="user"]', "next": "", "password": 'input[name="pw"]', "submit": "#go"}
    )
    result, _, _ = attempt(
        pages, plan(mode="fixed", methods=[redraw]), pages.origin + "/redraw", arrived=on_site(pages), product="테스트"
    )
    assert result == {"method": "custom", "label": "사용자 지정", "detected": False}


def test_the_password_goes_only_into_a_password_field_and_the_id_only_into_a_text_field(pages):
    # Positions that point at other fields, e.g. after the login screen changed: nothing is typed into a field of
    # another kind, and nothing is sent.
    swapped = custom_method(
        {"before": [], "id": 'input[name="pw"]', "next": "", "password": 'input[name="user"]', "submit": ""}
    )
    error = attempt(
        pages,
        plan(mode="fixed", methods=[swapped]),
        pages.origin + "/generic",
        arrived=on_site(pages),
        product="테스트",
        form=3,
    )[0]
    assert str(error).endswith("(시도한 방식: 사용자 지정: ID 입력란 찾지 못함)")
    # The password never goes into the search box beside the form.
    search = custom_method(
        {"before": [], "id": 'input[name="user"]', "next": "", "password": 'input[name="search"]', "submit": ""}
    )
    error = attempt(
        pages,
        plan(mode="fixed", methods=[search]),
        pages.origin + "/generic",
        arrived=on_site(pages),
        product="테스트",
        form=3,
    )[0]
    assert str(error).endswith("(시도한 방식: 사용자 지정: 비밀번호 입력란 찾지 못함)")
    assert pages.posts == []


def test_a_text_the_user_wrote_and_steps_only_some_screens_show(pages):
    # The user's own steps: a domain typed beside the account, and a notice and a field this screen does not show.
    def method(ident, domain, target='input[name="domain"]'):
        return {
            "id": ident,
            "label": f"도메인 {domain}",
            "detect": ['input[name="domain"]'],
            "steps": [
                {"action": "click", "target": "#notice-close", "optional": True},
                {"action": "fill", "value": "text", "text": domain, "target": target},
                {"action": "fill", "value": "text", "text": "1234", "target": "#otp-hint", "optional": True},
                {"action": "fill", "value": "id", "target": 'input[name="user"]'},
                {"action": "fill", "value": "password", "target": 'input[name="pw"]'},
                {"action": "submit", "target": "xpath=/html/body/form/button"},
            ],
        }

    account = {"path": "/domain", "id": "tester", "password": PASSWORD, "search": None}
    result, _, _ = attempt(
        pages,
        plan(mode="fixed", methods=[method("custom", "EXAMPLE")]),
        pages.origin + "/domain",
        arrived=on_site(pages),
        product="테스트",
    )
    assert result == {"method": "custom", "label": "도메인 EXAMPLE", "detected": False}
    assert pages.posts == [{**account, "domain": "EXAMPLE"}]
    # Another text makes another login: after the one with a wrong domain was refused, the next method sends its own.
    result, _, _ = attempt(
        pages,
        plan(methods=[method("wrong", "XXX"), method("right", "EXAMPLE")]),
        pages.origin + "/domain",
        arrived=on_site(pages),
        product="테스트",
        seconds=30,
    )
    assert result == {
        "method": "right",
        "label": "도메인 EXAMPLE",
        "detected": True,
        "tried": ["도메인 XXX: 로그인 화면이 다시 표시됨"],
    }, str(result)
    assert pages.posts[1:] == [{**account, "domain": "XXX"}, {**account, "domain": "EXAMPLE"}]
    # A text goes into text fields only, never into the password field.
    error = attempt(
        pages,
        plan(mode="fixed", methods=[method("custom", "EXAMPLE", 'input[name="pw"]')]),
        pages.origin + "/domain",
        arrived=on_site(pages),
        product="테스트",
        form=3,
    )[0]
    assert str(error).endswith("(시도한 방식: 도메인 EXAMPLE: 글자 입력란 찾지 못함)") and len(pages.posts) == 3


def test_the_screen_of_the_method_the_user_chose_gets_a_moment_before_the_adfs_one(pages):
    # The ADFS form shows at once, the form of the user's own method a second later. Auto mode signs in with the
    # ADFS method; the user's chosen method goes first and is waited for.
    chosen = {
        "id": "custom",
        "label": "사용자 지정",
        "detect": ["#late-user"],
        "priority": True,
        "steps": [
            {"action": "fill", "value": "id", "target": "#late-user"},
            {"action": "fill", "value": "password", "target": "#late-pw"},
            {"action": "submit", "target": "#late-go"},
        ],
    }
    result, _, _ = attempt(
        pages,
        plan(mode="fixed", methods=[chosen, *CATALOG]),
        pages.origin + "/late",
        arrived=on_site(pages),
        product="테스트",
    )
    assert result == {"method": "custom", "label": "사용자 지정", "detected": False}, str(result)
    result, _, _ = attempt(pages, plan(), pages.origin + "/late", arrived=on_site(pages), product="테스트")
    assert result == {"method": "adfs", "label": METHODS["adfs"]["label"], "detected": True}, str(result)
    assert [post["path"] for post in pages.posts] == ["/generic", "/adfs"]


def test_a_one_time_code_asked_where_the_password_was_is_no_refused_login(pages):
    # The right password brings the step of a one-time code, an empty password field at the password's position: the
    # login screen did not come back, so the sign-in waits for the site instead of reporting the password as refused.
    positions = custom_method(
        {
            "before": [],
            "id": "xpath=/html/body/form/input[1]",
            "next": "",
            "password": "xpath=/html/body/form/input[2]",
            "submit": "xpath=/html/body/form/button",
        }
    )
    for method in (positions, METHODS["generic"]):
        error, seconds, _ = attempt(
            pages,
            plan(mode="fixed", methods=[method]),
            pages.origin + "/otp",
            arrived=on_site(pages),
            product="테스트",
            seconds=3,
        )
        assert str(error).startswith("로그인 후 3초 안에 테스트 페이지로 이동하지 않았습니다."), (
            method["id"],
            str(error),
        )
        assert error.sent is True
    assert pages.posts == [{"path": "/otp", "id": "tester", "password": PASSWORD, "search": None}] * 2
    # A wrong password shows the login screen again: refused at once, as before.
    error, seconds, _ = attempt(
        pages,
        plan({"id": "tester", "password": "wrong"}, "fixed", [positions]),
        pages.origin + "/otp",
        arrived=on_site(pages),
        product="테스트",
        seconds=30,
    )
    assert str(error).startswith("로그인하지 못했습니다. ID·비밀번호를 확인해 주세요.") and seconds < 15, str(error)


def test_after_a_method_misses_a_page_showing_only_its_screen_ends_detection_early(pages):
    stale = custom_method(
        {"before": [], "id": 'input[name="user"]', "next": "", "password": "#no-such-field", "submit": ""}
    )
    error, seconds, _ = attempt(
        pages,
        plan(methods=[stale, METHODS["adfs"]]),
        pages.origin + "/generic",
        arrived=on_site(pages),
        product="테스트",
        form=5,
    )
    assert str(error) == (
        "로그인 화면을 찾지 못했습니다. 주소를 확인하거나 관리 → 사이트 계정에서 로그인 방식을 선택해 주세요. "
        "(시도한 방식: 사용자 지정: 비밀번호 입력란 찾지 못함)"
    )
    # The page was opened again for the other method, which gave up about 2 seconds later instead of waiting the
    # whole 5 seconds for a screen that was not coming. The sign-in ended at most `seconds` after the page first
    # opened; the browser's closing after it does not count, as security software may slow it down.
    opened = [at for path, at in pages.visits if path == "/generic"]
    assert len(opened) == 2 and opened[0] + seconds - opened[1] < 4, (opened, seconds)
    assert pages.posts == []


def test_signed_in_needs_the_site_origin_a_named_user_and_an_open_page(pages):
    async def main():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chromium", headless=True)
            try:
                page = await browser.new_page()
                await page.goto(pages.origin + "/home")
                found = [await signed_in(page, pages.origin), await signed_in(page, "http://localhost:1")]
                await page.goto(pages.origin + "/anonymous")
                found.append(await signed_in(page, pages.origin))
                await page.goto(pages.origin + "/home")
                await page.close()
                return [*found, await signed_in(page, pages.origin)]
            finally:
                await browser.close()

    assert asyncio.run(main()) == [True, False, False, False]


def test_a_closed_page_ends_the_sign_in_at_once(pages):
    # The login of /pending never finishes: closing its window ends the sign-in, not its time limit.
    async def main():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chromium", headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()

                async def close_after_sending():
                    # Once the login was sent and its page waits for an approval: a page.close() of Playwright's own
                    # while the form's navigation is still under way may never return.
                    while not pages.posts:
                        await asyncio.sleep(0.05)
                    await page.wait_for_function("document.body?.innerText.includes('승인을 기다리고')", timeout=10000)
                    await page.close()

                closing = asyncio.ensure_future(close_after_sending())
                started = time.monotonic()
                try:
                    await sign_in(
                        context,
                        page,
                        pages.origin + "/pending",
                        validate_plan(plan(mode="fixed", methods=[METHODS["generic"]])),
                        arrived=on_site(pages),
                        product="테스트",
                        optional_seconds=1,
                        form_seconds=10,
                        login_seconds=60,
                    )
                except LoginError as error:
                    await closing
                    return str(error), time.monotonic() - started
            finally:
                await browser.close()

    message, seconds = asyncio.run(main())
    assert message == "로그인 브라우저가 닫혔습니다." and seconds < 20


def test_an_aborted_navigation_is_no_failure_to_connect(pages):
    # A link that answers without content leaves the browser where it was; Chromium reports net::ERR_ABORTED, as for a
    # navigation a client-side redirect replaced. The sign-in goes on and finds no login screen there.
    error, seconds, _ = attempt(
        pages,
        plan(mode="fixed", methods=[METHODS["generic"]]),
        pages.origin + "/empty",
        arrived=on_site(pages),
        product="테스트",
        form=2,
    )
    assert str(error).startswith("로그인 화면을 찾지 못했습니다."), str(error)
    assert seconds < 15 and pages.posts == []


class FailingLLM:
    def public(self):
        return {"configured": True}

    def structured(self, system, content, timeout=None):
        raise UserError("LLM 오류")


@pytest.fixture
def importer(tmp_path, server):
    service = Service(Database(tmp_path / "data" / "wiki.db"), FailingLLM())
    service.preferences_save({"web_assist": True})  # The LLM's help with logins: off until the user turns it on.
    importer = service.jira
    importer.profile_root = tmp_path / "profiles"
    importer.optional_seconds, importer.form_seconds, importer.login_seconds = 1, 10, 20
    importer.login_headless = True
    yield service, importer
    importer.shutdown()


def wait(condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def test_login_check_on_the_site_address(importer, server):
    service, jira = importer
    dashboard = f"{server.origin}/jira/"
    result = service.dispatch("jira.check", {"url": dashboard, "login": plan()})
    assert result == {
        "ok": True,
        "method": "sso-link",
        "label": METHODS["sso-link"]["label"],
        "detected": True,
        "hosts": ["localhost"],
        "message": f"로그인했습니다 · {METHODS['sso-link']['label']}",
    }
    assert server.logins == [server.credentials]
    # JIRA's own form on the same site, checked with the method the user chose.
    server.style = "form"
    result = jira.check({"url": f"{server.origin}/jira", "login": plan(mode="fixed", methods=[METHODS["atlassian"]])})
    assert (result["method"], result["detected"]) == ("atlassian", False)
    # A refused login is the check's result, with what the LLM could say about it (here: that it failed).
    result = jira.check(
        {"url": dashboard, "login": plan({"id": "tester", "password": "wrong horse"}, "fixed", [METHODS["atlassian"]])}
    )
    assert (result["ok"], result["diagnosis"]) == (False, {"error": "LLM 오류"}), str(result)
    assert result["message"].startswith("로그인하지 못했습니다") and "wrong horse" not in str(result)
    assert jira.status() == {"job": None, "login_open": False, "login_error": "", "login_result": None}

    # A check that runs too long stops within its own limit and leaves the importer free.
    server.style, server.pending = "sso", True
    jira.check_seconds = 3
    started = time.monotonic()
    result = jira.check({"url": dashboard, "login": plan()})
    assert result == {
        "ok": False,
        "message": "로그인 확인 시간이 초과되었습니다. 주소를 확인하거나 ‘로그인 창 열기’에서 직접 확인해 주세요.",
    }
    assert time.monotonic() - started < 20 and jira.checking is False


def test_a_login_check_of_a_site_address_without_its_scheme_never_falls_back_to_http(importer, server, monkeypatch):
    # The JIRA of the tests answers over http alone: its address typed without a scheme is opened over https, which
    # fails, and never over http; written with http:// it is the user's choice, and the check signs in.
    service, jira = importer
    opened = []
    goto = browser_import.open_page

    async def spy(page, url, product, assist=None):
        opened.append(url)
        return await goto(page, url, product, assist)

    monkeypatch.setattr(browser_import, "open_page", spy)
    bare = f"{server.origin.removeprefix('http://')}/jira/"
    result = service.dispatch("jira.check", {"url": bare, "login": plan()})
    assert result["ok"] is False and "접속하지 못했습니다" in result["message"], str(result)
    assert not any(url.startswith("http://") for url in opened), opened
    result = service.dispatch("jira.check", {"url": f"http://{bare}", "login": plan()})
    assert (result["ok"], result["method"]) == (True, "sso-link"), str(result)
    with pytest.raises(UserError, match="JIRA 주소"):
        jira.check({"url": "jira test/jira", "login": plan()})


def checking(jira, url, login):
    """A login check on its own thread, as Electron's request waits for it: the thread and the answer it fills in."""
    answer = {}
    thread = threading.Thread(target=lambda: answer.update(jira.check({"url": url, "login": login})))
    thread.start()
    return thread, answer


def test_the_user_stops_a_login_check(importer, server):
    # The SSO accepted the login but never returns to JIRA. The user stops the check: it answers at once that it was
    # stopped, and the importer is free for the next one.
    service, jira = importer
    server.pending = True
    thread, answer = checking(jira, f"{server.origin}/jira/", plan())
    wait(lambda: server.logins)
    started = time.monotonic()
    assert service.dispatch("jira.check_cancel", {}) == {"stopping": True}
    thread.join(20)
    assert answer == {"ok": False, "stopped": True, "message": "로그인 확인을 중지했습니다."}
    assert time.monotonic() - started < 10 and jira.checking is False
    assert jira.check_cancel() == {"stopping": False}
    server.pending = False
    assert jira.check({"url": f"{server.origin}/jira/", "login": plan()})["ok"] is True


def test_a_check_out_of_time_whose_browser_does_not_close_is_stopped_before_the_next_and_at_exit(tmp_path):
    # A check whose browser never closes once stopped: it is stopped harder, and while it still has not closed, no other
    # browser of the importer starts beside it; the next check stops it once more first, and so does the app's exit.
    jira = Service(Database(tmp_path / "wiki.db"), FailingLLM()).jira
    jira.check_seconds, jira.close_seconds = 0.5, 0.5
    cancels = []

    async def stuck(url, origin, login, outcome, done):
        try:
            await asyncio.sleep(3600)  # Signing in.
        except asyncio.CancelledError:
            # A browser that does not close: its cleanup ends at the third cancel.
            while len(cancels) < 2:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    cancels.append(time.monotonic())
        finally:
            done.set()

    async def signed(url, origin, login, outcome, done):
        outcome["result"] = {"method": "sso-link", "label": "SSO 로그인", "detected": True}
        done.set()

    url = "http://jira.test/jira"
    for exit_app in (False, True):
        cancels.clear()
        jira._check = stuck
        result = jira.check({"url": url, "login": plan()})
        assert result["ok"] is False and result["message"].startswith("로그인 확인 시간이 초과되었습니다"), result
        [(worker, done)] = jira.lingering
        assert len(cancels) == 1 and not done.is_set() and jira.checking is False
        with pytest.raises(UserError, match="로그인 창을 먼저 닫아"):
            jira.login({"url": url, "login": plan()})
        if exit_app:
            jira.shutdown()
        else:
            jira._check = signed
            assert jira.check({"url": url, "login": plan()})["ok"] is True
        assert len(cancels) == 2 and done.is_set() and jira.lingering == []
        worker.thread.join(10)
        assert not worker.thread.is_alive()


def test_the_profile_of_a_deleted_site_goes_once_no_browser_runs(importer, server):
    service, jira = importer
    profile = jira._profile("http://old-jira.test")
    (profile / "Default").mkdir(parents=True)
    (profile / "Default" / "Cookies").write_text("session")
    # While a browser of the importer runs, it waits for the next browser to start.
    jira.checking = True
    assert service.dispatch("jira.forget", {"url": "http://old-jira.test/jira"}) == {"removed": False}
    jira.checking = False
    assert profile.exists()
    assert jira.check({"url": f"{server.origin}/jira/", "login": plan()})["ok"] is True
    assert not profile.exists() and jira.forgotten == set()
    # At once otherwise; the profiles of other sites stay.
    profile.mkdir(parents=True)
    assert jira.forget({"url": "http://old-jira.test/jira/browse/A-1"}) == {"removed": True}
    assert not profile.exists() and jira._profile(server.origin).exists()


class WaitingLLM:
    """An LLM that answers only once the test lets it."""

    def __init__(self):
        self.asked, self.answer = threading.Event(), threading.Event()

    def public(self):
        return {"configured": True}

    def structured(self, system, content, timeout=None):
        self.asked.set()
        self.answer.wait(60)
        raise UserError("LLM 오류")


def test_a_check_stopped_while_the_llm_analyzes_its_failure_does_not_wait_for_the_answer(tmp_path, server):
    llm = WaitingLLM()
    service = Service(Database(tmp_path / "data" / "wiki.db"), llm)
    service.preferences_save({"web_assist": True})
    jira = service.jira
    jira.profile_root = tmp_path / "profiles"
    jira.optional_seconds, jira.form_seconds, jira.login_seconds = 1, 10, 20
    try:
        thread, answer = checking(jira, f"{server.origin}/jira/", plan({"id": "tester", "password": "wrong horse"}))
        assert llm.asked.wait(60)
        started = time.monotonic()
        jira.check_cancel()
        thread.join(20)
        assert answer == {"ok": False, "stopped": True, "message": "로그인 확인을 중지했습니다."}
        assert time.monotonic() - started < 10
        # The browser has closed: the next check signs in while the LLM's thread still waits.
        assert jira.check({"url": f"{server.origin}/jira/", "login": plan()})["ok"] is True
        assert not llm.answer.is_set()
    finally:
        llm.answer.set()
        jira.shutdown()


def test_login_window_reports_the_method_and_blocks_other_work(importer, server):
    service, jira = importer
    status = jira.login({"url": f"{server.origin}/jira/", "login": plan()})
    assert (status["login_open"], status["login_result"]) == (True, None)
    for call in (
        lambda: jira.check({"url": server.url, "login": plan()}),
        lambda: jira.start({"url": server.url, "scope": "issue", "analyze": False}),
    ):
        with pytest.raises(UserError, match="로그인 창을 먼저 닫아"):
            call()
    wait(lambda: jira.status()["login_result"] is not None)
    assert jira.status()["login_result"] == {
        "method": "sso-link",
        "label": METHODS["sso-link"]["label"],
        "detected": True,
        "hosts": ["localhost"],
    }
    assert jira.login_close()["login_open"] is False
    # Without a login plan the window only opens the page for the user, keeping the profile's session: the
    # dashboard opens signed in. The next window starts without a result.
    seen = len(server.requests)
    jira.login({"url": f"{server.origin}/jira/"})
    assert jira.status()["login_result"] is None
    wait(lambda: "/jira/" in server.requests[seen:])
    assert not any(path.startswith("/jira/login.jsp") for path in server.requests[seen:])
    assert jira.login_close()["login_open"] is False
    assert server.logins == [server.credentials]
    # The window left the profile signed in: a job then opens its ticket with that session, typing nothing.
    job_status = jira.start({"url": server.url, "scope": "issue", "analyze": False, "login": plan()})
    assert job_status["job"]["auto_login"] is True
    wait(lambda: jira.status()["job"]["status"] not in ("scanning", "login", "running", "cancelling"))
    job = jira.status()["job"]
    assert (job["status"], job["imported"], job["login"]["method"]) == ("completed", 1, None), str(job)
    assert server.logins == [server.credentials]
