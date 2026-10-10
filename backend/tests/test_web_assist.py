"""The LLM's help when a site does not open or sign in (web_assist.py) on real Chromium: what the LLM reads of a page,
which of its answers count, a login screen no method fits signed in with the fields it picks, a page opened again, and
the analysis of a failure that reaches the job and the login check. The LLM is a stand-in that reads the described page
as a person would."""

import asyncio
import json
import time
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from agent import web_assist
from agent.db import Database
from agent.jira import ticket_page
from agent.llm import UserError
from agent.service import Service
from agent.web_assist import DIAGNOSE_SYSTEM, LOGIN_SYSTEM, Assist, clean_state, for_llm, pick
from agent.web_login import LEARNED_LABEL, LoginError, sign_in, signed_in, validate_plan
from jira_server import JiraServer
from site_pages import SitePages

# Real Chromium (conftest.py skips the module without it).
pytestmark = pytest.mark.browser
CATALOG = json.loads(
    (Path(__file__).resolve().parents[2] / "electron" / "login-methods.json").read_text(encoding="utf-8")
)["methods"]
PASSWORD = "correct horse"
ANALYSIS = {
    "category": "credentials",
    "cause": "SSO가 ID 또는 비밀번호를 거부했습니다.",
    "evidence": ["화면에 'ID 또는 비밀번호가 올바르지 않습니다' 안내가 있습니다."],
    "actions": ["관리 → 사이트 계정에서 비밀번호를 고친 뒤 로그인 확인을 누르세요."],
    "retry": False,
    "wait": 0,
}


class ReadingLLM:
    """Finds a login screen's fields by their labels, as a person would, and answers an analysis with `analysis`."""

    def __init__(self, analysis=None):
        self.calls, self.analysis = [], analysis or ANALYSIS

    def public(self):
        return {"configured": True}

    def structured(self, system, content, timeout=None):
        payload = json.loads(content)
        self.calls.append({"system": system, "payload": payload, "timeout": timeout})
        if system == DIAGNOSE_SYSTEM:
            return dict(self.analysis)
        assert system == LOGIN_SYSTEM
        elements = payload["page"]["elements"]

        def find(kind, *words):
            return next(
                (
                    element["n"]
                    for element in elements
                    if element["kind"] == kind
                    and any(
                        word in " ".join(str(element.get(key, "")) for key in ("text", "label", "name", "id"))
                        for word in words
                    )
                ),
                None,
            )

        password, login = find("password", ""), find("text", "계정", "user")
        if password or login:
            return {
                "screen": "login",
                "id": login,
                "password": password,
                "next": None if password else find("button", "다음"),
                "submit": find("button", "확인", "들어가기") if password else None,
                "reason": "로그인 화면입니다.",
            }
        click = find("button", "로그인")
        return (
            {"screen": "before_login", "click": click, "reason": "로그인 버튼이 있습니다."}
            if click
            else {"screen": "other", "reason": "로그인 화면이 아닙니다."}
        )

    def asked(self, system):
        return [call["payload"] for call in self.calls if call["system"] == system]


class FailingLLM:
    def public(self):
        return {"configured": True}

    def structured(self, system, content, timeout=None):
        raise UserError("LLM 오류")


def plan(credentials=None, mode="auto", methods=None):
    return {
        "site_id": "site-1",
        "site_name": "JIRA",
        "mode": mode,
        "methods": methods or CATALOG,
        "credentials": dict(credentials or {"id": "tester", "password": PASSWORD}),
    }


def learned_method(custom):
    """The method sites.cjs keeps for the fields the LLM found (customMethod)."""
    steps = [
        {"action": "click", "target": target, "optional": True, "unless": custom["id"]} for target in custom["before"]
    ]
    steps.append({"action": "fill", "value": "id", "target": custom["id"]})
    if custom["next"]:
        steps.append({"action": "click", "target": custom["next"]})
    steps.append({"action": "fill", "value": "password", "target": custom["password"]})
    steps.append({"action": "submit", "target": custom["submit"]} if custom["submit"] else {"action": "submit"})
    return {"id": "learned", "label": LEARNED_LABEL, "detect": [*custom["before"][:1], custom["id"]], "steps": steps}


@pytest.fixture
def server():
    server = JiraServer()
    yield server
    server.close()


def browse(work):
    """Run work(context, page) in a fresh browser."""

    async def main():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(channel="chromium", headless=True)
            try:
                context = await browser.new_context()
                return await work(context, await context.new_page())
            finally:
                await browser.close()

    return asyncio.run(main())


def attempt(server, login, llm, *, form=5, seconds=20):
    """Sign in to the ticket with the LLM's help: the result or the LoginError, and the seconds it took."""

    async def at_ticket(page):
        return ticket_page(page.url, server.origin)

    async def work(context, page):
        assist = Assist(llm, "JIRA", login["credentials"]).watch(page)
        started = time.monotonic()
        try:
            result = await sign_in(
                context,
                page,
                server.url,
                validate_plan(login),
                product="JIRA 티켓",
                assist=assist,
                arrived=at_ticket,
                optional_seconds=1,
                form_seconds=form,
                login_seconds=seconds,
            )
        except LoginError as error:
            result = error
        return result, time.monotonic() - started

    return browse(work)


# ---------------------------------------------------------------------------
# What the LLM reads and which answers count
# ---------------------------------------------------------------------------


PAGE = """<!doctype html><html><head><title>tester 로그인</title></head><body>
<p>tester님, 다시 로그인해 주세요. 도움말: https://sso.test/help?token=abc#top</p>
<form><label>계정 <input name="account" value="tester"></label><input type="hidden" name="csrf" value="hidden-token">
  <input type="password" id="pw" value="correct horse"><div id="go" style="cursor:pointer"><span>확인</span></div></form>
<form><input type="search" placeholder="검색"><button disabled>검색</button></form>
<a href="/help?x=1">도움말</a><div style="display:none"><input name="unseen"></div>
<svg width="10" height="10" onclick="go()"><rect width="10" height="10"/></svg>
</body></html>"""


def test_the_llm_reads_visible_fields_without_values_or_the_account():
    async def work(context, page):
        await page.set_content(PAGE)
        state = await Assist(None, "JIRA", {"id": "tester", "password": PASSWORD}).state(page)
        found = [await page.locator(element["selector"]).count() for element in state["elements"]]
        return state, found

    state, found = browse(work)
    elements = state["elements"]
    assert [(e["kind"], e["tag"]) for e in elements] == [
        ("text", "input"),
        ("password", "input"),
        ("button", "div"),
        ("other", "input"),
        ("button", "button"),
        ("button", "a"),
    ]
    # Each selector names exactly its element: a unique id or name, else the element's position.
    assert [e["selector"] for e in elements[:3]] == ['input[name="account"]', "#pw", "#go"]
    assert elements[5]["selector"] == "xpath=/html/body/a" and found == [1] * 6
    assert (elements[0]["label"], elements[2]["text"], elements[4]["disabled"]) == ("계정", "확인", True)
    assert (elements[0]["form"], elements[1]["form"], elements[3]["form"]) == (1, 1, 2)
    # Field values, hidden fields, the account and the query of addresses never reach the LLM.
    sent = json.dumps(for_llm(state), ensure_ascii=False)
    for secret in ("correct horse", "tester", "hidden-token", "token=abc", "unseen", "selector"):
        assert secret not in sent, secret
    assert (
        state["title"] == "[ID] 로그인"
        and "[ID]님, 다시 로그인해 주세요. 도움말: https://sso.test/help" in state["text"]
    )
    assert for_llm(state)["elements"][0] == {
        "n": 1,
        "kind": "text",
        "tag": "input",
        "type": "text",
        "label": "계정",
        "name": "account",
        "form": 1,
    }
    # A page that changed the browser's functions may describe anything; what does not fit is left out.
    assert clean_state(
        {
            "url": "https://a.test/x?y=1",
            "elements": [
                {"kind": "script", "selector": "#a"},
                "x",
                {"kind": "text", "selector": "#b\n"},
                {"kind": "text"},
            ],
        }
    ) == {"address": "https://a.test/x", "title": "", "text": "", "frames": 0, "elements": []}


def test_only_answers_that_fit_the_page_count():
    elements = [
        {"kind": kind, "selector": f"#e{n}", "disabled": False, "form": form}
        for n, (kind, form) in enumerate([("text", 1), ("password", 1), ("button", 1), ("text", 2), ("button", 0)], 1)
    ]
    elements.append({"kind": "button", "selector": "#e6", "disabled": True, "form": 0})
    assert pick({"screen": "login", "id": 1, "password": 2, "submit": 3, "reason": " 로그인\n화면 "}, elements) == {
        "screen": "login",
        "reason": "로그인 화면",
        "click": None,
        "id": "#e1",
        "next": None,
        "password": "#e2",
        "submit": "#e3",
    }
    # The password only into a password field, the ID only into a text field of the same form.
    for answer in (
        {"screen": "login", "id": 1, "password": 4},
        {"screen": "login", "id": 2, "password": 2},
        {"screen": "login", "id": 4, "password": 2},
        {"screen": "login", "id": 1, "password": 9},
        {"screen": "login", "id": 1, "password": True},
        {"screen": "login", "id": 1, "next": 6},
        {"screen": "login", "password": 2, "submit": 1},
        {"screen": "before_login", "click": 1},
        {"screen": "before_login", "click": 6},
        {"screen": "steal"},
    ):
        assert pick(answer, elements)["screen"] == "other", answer
    # The ID first, then a button that goes on; a notice or link to click first.
    assert pick({"screen": "login", "id": 1, "next": 5, "submit": 3}, elements) == {
        "screen": "login",
        "reason": "",
        "click": None,
        "id": "#e1",
        "next": "#e5",
        "password": None,
        "submit": None,
    }
    assert pick({"screen": "before_login", "click": 5}, elements)["click"] == "#e5"
    # A password field alone, for a site that remembers the ID, is sent with Enter.
    assert pick({"screen": "login", "password": 2}, elements)["password"] == "#e2"

    analysis = web_assist.diagnosis(
        {**ANALYSIS, "category": "mystery", "wait": 999, "retry": "yes", "actions": ["a", 3, "", "b", "c", "d"]}
    )
    assert (analysis["category"], analysis["label"], analysis["wait"], analysis["retry"]) == (
        "unknown",
        "원인 불명",
        30,
        False,
    )
    assert analysis["actions"] == ["a", "b", "c"] and analysis["attempt"] == ""
    with pytest.raises(UserError, match="실패 원인"):
        web_assist.diagnosis({"category": "network"})


# ---------------------------------------------------------------------------
# Signing in with the fields the LLM picks
# ---------------------------------------------------------------------------


def test_a_two_step_screen_no_method_fits_signs_in_with_the_fields_the_llm_picks(server):
    server.style = "steps"
    llm = ReadingLLM()
    result, _ = attempt(server, plan(), llm)
    assert result == {
        "method": "learned",
        "label": LEARNED_LABEL,
        "detected": True,
        "learned": {"before": [], "id": "#account", "next": "#next", "password": "#passcode", "submit": "#go"},
    }, str(result)
    assert server.logins == [server.credentials]
    # The ID page, then the password step; neither the ID nor the password was in what the LLM read.
    pages = llm.asked(LOGIN_SYSTEM)
    assert [page["page"]["title"] for page in pages] == ["통합 로그인"] * 2
    assert all(call["timeout"] <= web_assist.LLM_SECONDS for call in llm.calls)
    assert "correct horse" not in json.dumps(llm.calls) and "tester" not in json.dumps(llm.calls)

    # Kept for the site, the fields sign in by themselves the next time, without the LLM.
    calls = len(llm.calls)
    result, _ = attempt(server, plan(methods=[learned_method(result["learned"]), *CATALOG]), llm)
    assert (result["method"], len(llm.calls), server.logins) == ("learned", calls, [server.credentials] * 2), str(
        result
    )


def test_a_button_before_the_form_is_clicked_through_and_a_fixed_method_gets_the_same_help(server):
    server.style = "custom"
    llm = ReadingLLM()
    result, _ = attempt(server, plan(mode="fixed", methods=[CATALOG[1]]), llm)
    assert result == {
        "method": "learned",
        "label": LEARNED_LABEL,
        "detected": False,
        "learned": {"before": ["#open-login"], "id": "#user", "next": "", "password": "#pass", "submit": "#go"},
    }, str(result)
    assert server.logins == [server.credentials]


@pytest.fixture
def pages():
    pages = SitePages({"id": "tester", "password": PASSWORD})
    yield pages
    pages.close()


def assisted_at(pages, path, llm, origin=None):
    """Sign in at a page of `pages` with the LLM's help: the result or the LoginError. Signed in means the named
    user's page of `origin` (the site's own by default)."""

    async def work(context, page):
        login = plan(methods=CATALOG)
        assist = Assist(llm, "테스트", login["credentials"]).watch(page)
        try:
            return await sign_in(
                context,
                page,
                pages.origin + path,
                validate_plan(login),
                product="테스트",
                assist=assist,
                arrived=lambda shown: signed_in(shown, origin or pages.origin),
                optional_seconds=1,
                form_seconds=3,
                login_seconds=20,
            )
        except LoginError as error:
            return error

    return browse(work)


def test_a_click_the_llm_chose_never_takes_the_account_to_another_site(pages):
    # A page of the site whose ‘로그인’ leads to another site (localhost), e.g. a link in a page's text: the LLM takes
    # it for the way to the login screen, and its link is not followed.
    llm = ReadingLLM()
    error = assisted_at(pages, "/lure", llm)
    assert str(error) == (
        "로그인 화면을 찾지 못했습니다. 주소를 확인하거나 관리 → 사이트 계정에서 로그인 방식을 선택해 주세요. "
        "(시도한 방식: AI 분석: 다른 사이트(localhost)로 가는 버튼이라 누르지 않음)"
    ), str(error)
    # The LLM is told whose account it is.
    assert llm.asked(LOGIN_SYSTEM)[0]["site_host"] == "127.0.0.1"
    # A button whose script goes there: its login screen is read, but the account is not typed into it.
    error = assisted_at(pages, "/lure-script", ReadingLLM())
    assert isinstance(error, LoginError) and error.sent is False
    assert str(error).startswith(
        "로그인 화면이 사이트 주소와 다른 곳(localhost)에 있어 ID·비밀번호를 입력하지 않았습니다."
    ), str(error)
    assert pages.posts == [] and any(path == "/generic" for path, _ in pages.visits)


def test_the_login_screen_a_site_redirects_to_is_signed_in_with_the_llms_help(pages):
    # The site sends the browser on to a login screen of another host by itself: the LLM finds its fields, and the
    # account is typed there.
    result = assisted_at(pages, "/away", ReadingLLM(), pages.other)
    assert result == {
        "method": "learned",
        "label": LEARNED_LABEL,
        "detected": True,
        "learned": {
            "before": ["#open-login"],
            "id": "#user-name",
            "next": "",
            "password": "#user-pass",
            "submit": "#do-login",
        },
        "hosts": ["localhost"],
    }, str(result)
    assert pages.posts == [{"path": "/custom", "id": "tester", "password": PASSWORD, "search": None}]


def test_the_account_is_sent_once_and_what_the_llm_could_not_do_is_listed(server):
    server.style = "steps"
    llm = ReadingLLM()
    error, seconds = attempt(server, plan({"id": "tester", "password": "wrong horse"}), llm, seconds=30)
    assert isinstance(error, LoginError) and str(error).startswith(
        "로그인하지 못했습니다. ID·비밀번호를 확인해 주세요."
    ), error
    assert str(error).endswith(f"{LEARNED_LABEL}: 로그인 화면이 다시 표시됨)") and seconds < 25
    assert len(server.logins) == 1 and len(llm.asked(LOGIN_SYSTEM)) == 2

    # A page the LLM does not take for a login screen ends the sign-in with its reason.
    server.style = "generic"
    error, _ = attempt(server, plan(mode="fixed", methods=[CATALOG[1]]), FailingLLM())
    assert str(error) == (
        "로그인 화면을 찾지 못했습니다. 주소를 확인하거나 관리 → 사이트 계정에서 로그인 방식을 선택해 주세요. "
        "(시도한 방식: AI 분석: 분석하지 못함 (LLM 오류))"
    )
    assert len(server.logins) == 1


# ---------------------------------------------------------------------------
# Jobs and login checks
# ---------------------------------------------------------------------------


@pytest.fixture
def jira(tmp_path, server):
    llm = ReadingLLM()
    service = Service(Database(tmp_path / "data" / "wiki.db"), llm)
    service.preferences_save({"web_assist": True})  # Off until the user turns it on.
    importer = service.jira
    importer.profile_root = tmp_path / "profiles"
    importer.optional_seconds, importer.form_seconds, importer.login_seconds = 1, 5, 20
    yield service, importer, llm
    importer.shutdown()


def wait(condition, seconds=90):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def run(server, importer, **params):
    importer.start({"url": server.url, "scope": "issue", "analyze": False, "login": plan(), **params})
    wait(lambda: importer.status()["job"]["status"] not in ("scanning", "login", "running", "cancelling"))
    return importer.status()["job"]


def test_a_job_reports_why_its_login_failed_and_keeps_the_fields_the_llm_found(jira, server):
    service, importer, llm = jira
    # Without a login, the LLM tells why the ticket did not open; with the option off, it is not asked.
    job = run(server, importer, login=None)
    assert job["status"] == "error" and "로그인이 필요합니다" in job["error"], str(job)
    assert job["diagnosis"]["cause"] == ANALYSIS["cause"] and llm.asked(DIAGNOSE_SYSTEM)[-1]["stage"] == "티켓 열기"
    service.preferences_save({"web_assist": False})
    calls = len(llm.calls)
    job = run(server, importer, login=None)
    assert job["status"] == "error" and job["diagnosis"] is None and len(llm.calls) == calls
    service.preferences_save({"web_assist": True})

    server.style = "form"
    job = run(server, importer, login=plan({"id": "tester", "password": "wrong horse"}))
    assert job["status"] == "error" and job["error"].startswith("로그인하지 못했습니다"), str(job)
    assert job["diagnosis"] == {**ANALYSIS, "label": "ID·비밀번호", "attempt": ""} and job["notice"] == ""
    # The LLM read the step, the app's message, the pages loaded and the page shown, never the password.
    facts = llm.asked(DIAGNOSE_SYSTEM)[-1]
    assert (facts["site"], facts["stage"], facts["message"]) == ("JIRA", "로그인", job["error"])
    # The page the refused login left, with the site's message, though the site was opened again for other methods.
    assert (facts["after_login"]["method"], facts["after_login"]["outcome"]) == (
        "JIRA·Confluence 기본 로그인",
        "rejected",
    )
    assert "ID 또는 비밀번호가 올바르지 않습니다" in facts["after_login"]["page"]["text"]
    assert "ID 또는 비밀번호가 올바르지 않습니다" not in facts["page"]["text"]
    assert {"address": f"{server.origin}/jira/login.jsp", "status": 200} in facts["loads"]
    assert "wrong horse" not in json.dumps(llm.calls)

    # A screen no method fits: the job signs in with the LLM's fields, which go to Electron with the login.
    server.style = "steps"
    job = run(server, importer)
    assert (job["status"], job["imported"], job["diagnosis"]) == ("completed", 1, None), str(job)
    assert job["login"] == {
        "site": "JIRA",
        "method": "learned",
        "label": LEARNED_LABEL,
        "detected": True,
        "error": "",
        "learned": {"before": [], "id": "#account", "next": "#next", "password": "#passcode", "submit": "#go"},
    }


def test_a_page_that_does_not_open_is_opened_again_when_the_llm_expects_it_to(jira, server):
    service, importer, llm = jira
    llm.analysis = {
        "category": "network",
        "cause": "연결이 끊겼습니다.",
        "evidence": [],
        "actions": [],
        "retry": True,
        "wait": 0,
    }
    server.drop = 1
    job = run(server, importer)
    assert (job["status"], job["imported"]) == ("completed", 1), str(job)
    assert job["diagnosis"]["attempt"] == "AI 분석에 따라 0초 뒤 다시 접속해 이어서 진행했습니다."
    facts = llm.asked(DIAGNOSE_SYSTEM)[-1]
    assert facts["stage"] == "접속" and "net::ERR_RESPONSE_HEADERS_MULTIPLE_CONTENT_LENGTH" in facts["error"]
    # The browser's error page, which explains the failure, is what the LLM read of the page.
    assert facts["page"]["address"] == "chrome-error:chromewebdata"
    assert {
        "address": f"{server.origin}/jira/browse/TEAM-1",
        "error": "net::ERR_RESPONSE_HEADERS_MULTIPLE_CONTENT_LENGTH",
    } in facts["loads"]

    # Not expected to work later: the job ends with the analysis it made, without a second try or analysis.
    llm.analysis = {**llm.analysis, "category": "address", "retry": False}
    server.drop, calls = 2, len(llm.calls)
    job = run(server, importer)
    assert job["status"] == "error" and job["error"].startswith("JIRA에 접속하지 못했습니다"), str(job)
    assert (job["diagnosis"]["category"], job["diagnosis"]["attempt"]) == ("address", "")
    assert len(llm.calls) == calls + 1 and server.drop == 1
    server.drop = 0


def test_a_failing_llm_never_hides_the_error(jira, server):
    service, importer, llm = jira
    service.llm = FailingLLM()
    server.style = "form"
    job = run(server, importer, login=plan({"id": "tester", "password": "wrong horse"}))
    assert job["status"] == "error" and job["error"].startswith("로그인하지 못했습니다"), str(job)
    assert job["diagnosis"] == {"error": "LLM 오류"}


def test_a_login_check_brings_the_analysis_and_the_learned_fields(jira, server):
    service, importer, llm = jira
    server.style = "steps"
    site = f"{server.origin}/jira/"
    result = service.dispatch("jira.check", {"url": site, "login": plan()})
    assert result["ok"] is True and (result["method"], result["learned"]["id"]) == ("learned", "#account"), str(result)
    assert result["message"] == f"로그인했습니다 · {LEARNED_LABEL}"
    result = service.dispatch("jira.check", {"url": site, "login": plan({"id": "tester", "password": "wrong horse"})})
    assert result["ok"] is False and result["message"].startswith("로그인하지 못했습니다"), str(result)
    assert result["diagnosis"]["label"] == "ID·비밀번호"
    assert llm.asked(DIAGNOSE_SYSTEM)[-1]["stage"] == "로그인 확인"


def test_the_account_is_taken_out_of_addresses_too_and_the_option_turns_the_help_off(tmp_path):
    # An SSO page with the ID in its path, and a page that shows the password with its spaces run together.
    account = {"id": "user.one", "password": "pa  ss\tword"}
    secrets = web_assist.secrets_of(account)
    state = clean_state(
        {
            "url": "https://sso.corp.test/users/user.one/login?ticket=1",
            "title": "USER.ONE 로그인",
            "text": "입력한 비밀번호 pa ss word 를 확인해 주세요. user.ones 님",
        },
        secrets,
    )
    assert (state["address"], state["title"]) == ("https://sso.corp.test/users/[ID]/login", "[ID] 로그인")
    assert state["text"] == "입력한 비밀번호 [비밀번호] 를 확인해 주세요. user.ones 님"

    class Navigation:  # A main-frame load the browser reports.
        url, failure, frame = "https://sso.corp.test/users/user.one/login?ticket=1", "net::ERR_FAILED", None

        def is_navigation_request(self):
            return True

    request = Navigation()
    request.frame = type("Frame", (), {"parent_frame": None})()
    assist = Assist(None, "Confluence", account)
    assist._failed(request)
    assert list(assist.loads) == [{"address": "https://sso.corp.test/users/[ID]/login", "error": "net::ERR_FAILED"}]

    # The option on 관리 → 사이트 계정 turns the help on, for every site's browser: it is off until the user does,
    # as it sends the words of login pages to the LLM server.
    service = Service(Database(tmp_path / "wiki.db"), ReadingLLM())
    assert service.assist_on() is False
    service.preferences_save({"web_assist": True})
    assert service.assist_on() is True
    service.preferences_save({"web_assist": False})
    assert service.assist_on() is False
    for importer in (service.confluence, service.jira):
        assert importer._assistant(None, plan(), lambda **values: None) is None
    with pytest.raises(UserError, match="AI 분석 설정"):
        service.preferences_save({"web_assist": "yes"})
    # Every browser runs in the backend, so Electron asks nothing of the LLM's help through RPC.
    for method in ("web.page_script", "web.login_form", "web.diagnose"):
        with pytest.raises(UserError, match="지원하지 않는"):
            service.dispatch(method, {})
