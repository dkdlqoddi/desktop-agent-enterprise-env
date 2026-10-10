"""The Confluence importer (confluence_import.py): page links and the subpage list, and on real Chromium against a
local Confluence and SSO: pages read as the server rendered them or as scripts draw them, subpages, failures,
sessions, the saved account's login in the background, the login check and window, and the LLM's analysis."""

import asyncio
import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

import pytest

from agent import browser_import, confluence_import, sources
from agent.confluence_import import api_link, children, is_page_url, location_info, page_url
from agent.db import Database
from agent.llm import UserError
from agent.service import Service
from agent.web_assist import DIAGNOSE_SYSTEM
from agent.web_login import with_scheme
from confluence_server import ConfluenceServer

CATALOG = json.loads(
    (Path(__file__).resolve().parents[2] / "electron" / "login-methods.json").read_text(encoding="utf-8")
)["methods"]
METHODS = {method["id"]: method for method in CATALOG}
SSO_FORM = METHODS["generic"]["label"]
USER = {"id": "tester", "password": "correct horse"}
PAGE = "https://wiki.test/confluence/pages/viewpage.action?pageId=1"
ANALYSIS = {
    "category": "credentials",
    "cause": "SSO가 비밀번호를 거부했습니다.",
    "evidence": ["로그인 화면의 안내 문구"],
    "actions": ["비밀번호를 고친 뒤 로그인 확인을 누르세요."],
    "retry": False,
    "wait": 0,
}


def plan(credentials=USER, mode="auto", methods=None):
    """A site's login plan, as the main process builds it from the saved account."""
    return {
        "site_id": "site-1",
        "site_name": "Confluence",
        "mode": mode,
        "methods": methods or CATALOG,
        "credentials": dict(credentials),
    }


@pytest.fixture
def service(tmp_path):
    return Service(Database(tmp_path / "wiki.db"), None)


def wait(condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


# ---------------------------------------------------------------------------
# Links and the subpage list
# ---------------------------------------------------------------------------


def test_page_links_site_paths_and_the_rest_api_links_of_a_subpage_list():
    for value in [
        "https://wiki.test/display/SPACE/Page+Title",
        "https://wiki.test/confluence/display/SPACE/%ED%8E%98%EC%9D%B4%EC%A7%80",
        "https://wiki.test/pages/viewpage.action?pageId=123456",
        "https://wiki.test/confluence/pages/viewpage.action?spaceKey=SPACE&title=Page+Title",
        "https://wiki.test/spaces/SPACE/pages/123456/Page+Title",
        "https://wiki.test/wiki/spaces/SPACE/pages/123456",
        "https://wiki.test/x/AbCd-_9",
        "http://wiki.test/x/AbCdEf/",
    ]:
        assert is_page_url(value), value
    for value in [
        "https://wiki.test/",
        "https://wiki.test/display/SPACE",
        "https://wiki.test/pages/viewpage.action",
        "https://wiki.test/pages/viewpage.action?pageId=",
        "https://wiki.test/pages/viewpage.action?spaceKey=SPACE",
        "https://wiki.test/dashboard.action",
        "https://wiki.test/login.action?os_destination=%2Fdisplay%2FA%2FB",
        "https://wiki.test/x/a/b",
        "ftp://wiki.test/display/SPACE/Page",
        "not a url",
        None,
    ]:
        assert not is_page_url(value), value
    assert location_info("https://wiki.test/wiki/spaces/SPACE/pages/123/title")["base"] == "https://wiki.test/wiki"
    assert location_info("https://wiki.test/confluence/pages/viewpage.action?pageId=123")["id"] == "123"
    assert (
        location_info("https://wiki.test/confluence/x/short", {"id": "123", "context": "/confluence"})["url"]
        == "https://wiki.test/confluence/pages/viewpage.action?pageId=123"
    )
    # A site at the root, named as the browser names it.
    assert (
        location_info("HTTPS://Wiki.Test:443/display/S/P", {"id": "5", "context": ""})["url"]
        == "https://wiki.test/pages/viewpage.action?pageId=5"
    )
    # A link typed without its scheme is an https link (ConfluenceImporter.start).
    assert page_url(with_scheme("Wiki.Test/display/S/P#top")) == "https://wiki.test/display/S/P"
    for value in [
        "file:///etc/passwd",
        "javascript:alert(1)",
        "https://user:password@wiki.test/",
        "invalid",
        "https://wiki.test\\.evil.test/display/S/P",
        "https://wiki.test/a b",
        3,
    ]:
        with pytest.raises(UserError, match="페이지 링크를 확인"):
            page_url(value)
    for value, metadata in [
        ("https://wiki.test/login", None),
        (PAGE, {"id": "١٢"}),
        (PAGE, {"id": "1", "context": "//other.test"}),
        (PAGE, {"id": "1", "context": "/a?b"}),
    ]:
        with pytest.raises(UserError, match="Confluence"):
            location_info(value, metadata)
    base = "https://wiki.test/confluence"
    assert api_link("/rest/api/content/1?start=2", base, base) == f"{base}/rest/api/content/1?start=2"
    for value in [
        "https://other.test/rest/api/content",
        "/login",
        "/rest/api/%2e%2e/%2e%2e/login",
        "//other.test/rest/api/x",
        None,
        {"href": "/rest/api/x"},
    ]:
        with pytest.raises(UserError):
            api_link(value, base, base)


def test_cloud_subpages_fall_back_to_v2_follow_link_cursors_and_refuse_login_redirects():
    urls, ids = [], []

    async def cloud(url):
        urls.append(url)
        old, last = "/rest/" in url, "cursor=next" in url
        return {
            "status": 404 if old else 200,
            "link": "" if last else '</wiki/api/v2/pages/1/children?cursor=next>; rel="next"',
            "text": "" if old else json.dumps({"results": [{"id": "3" if last else "2"}]}),
        }

    asyncio.run(children(cloud, {"base": "https://wiki.test/wiki", "id": "1"}, lambda item: ids.append(item["id"])))
    assert (ids, len(urls)) == (["2", "3"], 3)

    # A same-origin request with redirect: 'manual' reports a login redirect as status 0.
    async def redirect(url):
        return {"status": 0, "link": "", "text": ""}

    with pytest.raises(UserError, match="로그인"):
        asyncio.run(children(redirect, {"base": "https://wiki.test/confluence", "id": "1"}, lambda item: None))

    # Data Center versions that only give start and limit, and a list that repeats itself.
    found, pages = [], {0: [2, 3], 2: [4]}

    async def offsets(url):
        start = int(re.search(r"start=(\d+)", url)[1])
        return {
            "status": 200,
            "link": "",
            "text": json.dumps({"results": [{"id": n} for n in pages.get(start, [])], "start": start, "limit": 2}),
        }

    asyncio.run(
        children(offsets, {"base": "https://wiki.test/confluence", "id": "1"}, lambda item: found.append(item["id"]))
    )
    assert found == ["2", "3", "4"]

    async def loop(url):
        return {
            "status": 200,
            "link": "",
            "text": json.dumps(
                {"results": [{"id": 2}], "_links": {"next": "/rest/api/content/1/child/page?limit=100&start=0"}}
            ),
        }

    with pytest.raises(UserError, match="반복"):
        asyncio.run(children(loop, {"base": "https://wiki.test/confluence", "id": "1"}, lambda item: None))

    async def bad(url):
        return {"status": 200, "link": "", "text": json.dumps({"results": [{"id": "../2"}]})}

    with pytest.raises(UserError, match="ID"):
        asyncio.run(children(bad, {"base": "https://wiki.test/confluence", "id": "1"}, lambda item: None))


def test_start_checks_its_request_before_a_browser_starts(service):
    importer = service.confluence
    base = {"url": PAGE, "recursive": False, "analyze": False}
    for params, message in [
        ({**base, "url": "https://wiki.test/dashboard.action"}, "페이지 링크 형식"),
        ({**base, "url": "https://wiki.test/display/SPACE"}, "페이지 링크 형식"),
        ({**base, "url": "file:///etc/passwd"}, "페이지 링크를 확인"),
        ({**base, "url": "wiki test/display/S/P"}, "페이지 링크를 확인"),
        ({**base, "recursive": "yes"}, "하위 페이지 및 AI 분석 옵션"),
        ({**base, "analyze": None}, "하위 페이지 및 AI 분석 옵션"),
        ({**base, "replace": "yes"}, "replace"),
        ({**base, "doc_id": 7}, "doc_id"),
        ({**base, "login": {"mode": "auto"}}, "로그인 정보 형식"),
        ({**base, "login": plan({"id": "tester"})}, "로그인 정보 형식"),
    ]:
        with pytest.raises(UserError, match=message):
            importer.start(params)
    # A login window or check opens any page of the site, not only pages.
    for method in (importer.login, importer.check):
        with pytest.raises(UserError, match="Confluence 주소"):
            method({"url": "ftp://wiki.test/", "login": plan()})
    with pytest.raises(UserError, match="로그인 정보를 저장한 뒤"):
        importer.check({"url": "https://wiki.test/confluence"})

    class Unconfigured:
        def public(self):
            return {"configured": False}

    service.llm = Unconfigured()
    with pytest.raises(UserError, match="LLM"):
        importer.start({**base, "analyze": True})
    assert importer.status() == {"job": None, "login_open": False, "login_error": "", "login_result": None}
    assert not importer.profile_root.exists() or not any(importer.profile_root.iterdir())


def test_shutdown_stops_a_browser_that_is_still_starting(service, tmp_path, monkeypatch):
    events, starting = [], threading.Event()

    class Driver:  # async_playwright()
        async def start(self):
            return self

        async def stop(self):
            events.append("stop")

    async def launch(playwright, profile, headless):
        starting.set()
        await asyncio.sleep(60)

    monkeypatch.setattr("playwright.async_api.async_playwright", Driver)
    importer = service.confluence
    importer.profile_root, importer._launch = tmp_path / "profiles", launch
    importer.start({"url": PAGE, "recursive": False, "analyze": False})
    assert starting.wait(20)
    started = time.monotonic()
    importer.shutdown()
    assert time.monotonic() - started < 10
    assert importer.status()["job"]["status"] == "cancelled" and events == ["stop"]


# ---------------------------------------------------------------------------
# Real Chromium
# ---------------------------------------------------------------------------


class FailingLLM:
    def public(self):
        return {"configured": True}

    def structured(self, system, content, timeout=None):
        raise UserError("LLM 오류")


class AnalyzingLLM:
    """Answers every analysis of a failure with ANALYSIS, and keeps what it was asked."""

    def __init__(self):
        self.asked = []

    def public(self):
        return {"configured": True}

    def structured(self, system, content, timeout=None):
        assert system == DIAGNOSE_SYSTEM
        self.asked.append(json.loads(content))
        return dict(ANALYSIS)


@pytest.fixture
def browser(tmp_path, monkeypatch):
    """The local Confluence, a Service without an LLM, its Confluence importer with short waits and a headless login
    window, and the captures the importer saved, as import_page got them."""
    server = ConfluenceServer()
    service = Service(Database(tmp_path / "data" / "wiki.db"), None)
    importer = service.confluence
    importer.profile_root = tmp_path / "profiles"
    importer.optional_seconds, importer.form_seconds, importer.login_seconds = 1, 15, 20
    importer.login_headless = True
    captures, saving = [], confluence_import.import_page

    def save(service, params):
        captures.append(params)
        return saving(service, params)

    monkeypatch.setattr(confluence_import, "import_page", save)
    yield server, service, importer, captures
    importer.shutdown()
    server.close()


def run(importer, url, **params):
    """The finished job. Assertions show it as str(job): pytest shortens a dict to its first keys, without the error."""
    importer.start({"url": url, "recursive": False, "analyze": False, **params})
    wait(lambda: importer.status()["job"]["status"] not in ("scanning", "login", "running", "cancelling"))
    return importer.status()["job"]


def remember_session(importer, server):
    """A session left in the site's profile, as a login in the login window leaves it."""

    async def visit():
        async with importer._browser(server.origin, headless=True) as context:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(server.origin + "/login")

    asyncio.run(visit())


def forget_session(importer, server):
    """The session the last job left in the site's profile is gone, as after the site signed the user out."""

    async def clear():
        async with importer._browser(server.origin, headless=True) as context:
            await context.clear_cookies()

    asyncio.run(clear())


def test_real_browser_pages_subpages_failures_session_and_cancel(browser, tmp_path):
    server, service, importer, captures = browser
    job = run(importer, server.url)
    assert job["status"] == "completed", str(job)
    assert (job["imported"], job["auto_login"], job["total"]) == (1, False, 1)
    # The page is read as the server rendered it, with an in-page request, without the REST API.
    assert not [path for path in server.requests if "/rest/" in path]
    html = captures[0]["html"]
    assert "<table>" in html and "<pre>if (x &lt; 3)" in html
    # The server-rendered code macro, not the live DOM's line-numbered table.
    assert (
        '<pre data-syntaxhighlighter-params="brush: java; gutter: false; theme: Confluence">int answer = 1;</pre>'
        in html
    )
    assert "접힌 세부 내용" in html and re.search(r"http://127\.0\.0\.1:\d+/attachment\.txt", html)
    assert 'href="#section-1"' in html
    assert not re.search(r"<script|onclick=|javascript:|secret|<iframe|탐색 메뉴|class=", html)
    assert captures[0]["source_url"] == server.url and captures[0]["page_id"] == "1"
    doc = service.wiki_get({"id": job["results"][0]["doc_id"]})
    assert doc["title"] == "페이지 1" and doc["source"]["kind"] == "confluence" and "| 기준 | 내용 |" in doc["content"]
    first = job["id"]
    assert run(importer, server.url)["skipped"] == 1
    # The site keeps how a job signed in, so the job stays readable by its ID while the next one runs.
    assert importer.status({"id": first})["job"]["id"] == first

    job = run(importer, server.url, recursive=True)
    assert (job["total"], job["processed"], job["imported"], job["skipped"], job["discovery_failed"]) == (
        4,
        4,
        3,
        1,
        0,
    ), str(job)
    assert any("start=1" in path for path in server.requests)

    server.forbidden = True
    job = run(importer, server.url, recursive=True)
    # Page 3 is still found below page 2, which fails.
    assert (job["failed"], job["processed"]) == (1, 4), str(job)
    assert "403" in next(item["message"] for item in job["results"] if item["status"] == "failed")
    server.forbidden, server.discovery_failure = False, True
    job = run(importer, server.url, recursive=True)
    assert (job["discovery_failed"], job["processed"]) == (1, 3), str(job)
    server.discovery_failure = False

    server.auth = True
    job = run(importer, server.url)
    assert job["failed"] == 1 and job["results"][0]["message"] == "페이지에 접근하지 못했습니다 (HTTP 401).", str(job)
    assert job["diagnosis"] is None  # Without an LLM, no analysis.
    # Without a saved login, the session left in the profile reaches the pages and the subpage list.
    remember_session(importer, server)
    job = run(importer, server.url, recursive=True)
    assert (job["failed"], job["processed"]) == (0, 4), str(job)

    # A cleared job stays for Electron; only the job that was cleared.
    assert "dismissed" not in service.dispatch("confluence.dismiss", {"id": "older job"})["job"]
    assert service.dispatch("confluence.dismiss", {"id": job["id"]})["job"] == {**job, "dismissed": True}

    server.slow = True
    importer.start({"url": server.url, "recursive": True, "analyze": False})
    with pytest.raises(UserError, match="진행 중인 가져오기"):
        importer.start({"url": server.url, "recursive": False, "analyze": False})
    wait(lambda: importer.status()["job"]["status"] == "running")
    # A running job keeps showing its progress.
    assert "dismissed" not in importer.dismiss({"id": importer.status()["job"]["id"]})["job"]
    assert importer.cancel()["job"]["status"] == "cancelling"
    wait(lambda: importer.status()["job"]["status"] == "cancelled", 20)
    assert importer.status()["job"]["imported"] == 0
    importer.job_worker.thread.join(20)
    assert not importer.job_worker.thread.is_alive()
    # Only the database is written: no HTML or Markdown files beside it.
    assert sorted(p.name for p in (tmp_path / "data").iterdir() if not p.name.startswith("wiki.db")) == []


def test_real_browser_an_update_from_the_document_window(browser):
    server, service, importer, captures = browser
    job = run(importer, server.url)
    ident = job["results"][0]["doc_id"]
    original = service.wiki_get({"id": ident})["content"]
    service.wiki_save({"id": ident, "title": "페이지 1", "content": "고친 본문"})
    # The page did not change: an import keeps the user's text, an update from the document window brings it back.
    job = run(importer, server.url)
    assert (job["skipped"], job["results"][0]["reason"]) == (1, "same"), str(job)
    assert service.wiki_get({"id": ident})["content"] == "고친 본문"
    job = run(importer, server.url, doc_id=ident, replace=True)
    assert job["status"] == "completed", str(job)
    assert (job["imported"], job["updated"], job["skipped"]) == (0, 1, 0)
    assert [(params["doc_id"], params["replace"]) for params in captures] == [
        (None, False),
        (None, False),
        (ident, True),
    ]
    assert service.wiki_get({"id": ident})["content"] == original


def test_real_browser_a_saved_account_signs_in_through_the_sso_in_the_background(browser):
    server, service, importer, captures = browser
    server.sso = True
    # A new profile holds no session: the job signs in with the saved account, through the SSO on the other host.
    job = run(importer, f"{server.origin}/confluence/display/SPACE/Page+1", recursive=True, login=plan())
    assert job["status"] == "completed", str(job)
    assert job["auto_login"] is True
    assert job["login"] == {
        "site": "Confluence",
        "method": "generic",
        "label": SSO_FORM,
        "detected": True,
        "error": "",
        "hosts": ["localhost"],
    }
    assert server.logins == [USER]
    assert (job["processed"], job["imported"]) == (4, 4), str(job["results"])
    assert job["results"][0]["url"] == server.url and "brush: java" in captures[0]["html"]
    # Subpages are read with in-page requests, without opening each page in the browser.
    assert len([path for path in server.requests if "/rest/" in path]) == 5
    assert len([path for path in server.requests if "pageId=3" in path]) == 1
    assert "correct horse" not in json.dumps(importer.status())

    # A wrong password shows the login form again: said at once, not after the time limit. The session the job before
    # left would sign in by itself, so it goes first.
    forget_session(importer, server)
    importer.login_seconds = 60
    started = time.monotonic()
    job = run(importer, server.url, login=plan({"id": "tester", "password": "wrong"}, "fixed", [METHODS["generic"]]))
    assert time.monotonic() - started < 30
    assert job["status"] == "error"
    assert (
        job["error"]
        == f"로그인하지 못했습니다. ID·비밀번호를 확인해 주세요. (시도한 방식: {SSO_FORM}: 로그인 화면이 다시 표시됨)"
    )
    assert (job["login"]["error"], job["login"]["method"], job["processed"]) == (job["error"], None, 0)
    assert server.logins[-1]["password"] == "wrong" and "wrong" not in json.dumps(importer.status())

    # Cancelling during the login stops the job without a failed login.
    server.slow = True
    importer.start({"url": server.url, "recursive": False, "analyze": False, "login": plan()})
    wait(lambda: importer.status()["job"]["status"] == "login" and len(server.logins) == 3)
    importer.cancel()
    wait(lambda: importer.status()["job"]["status"] == "cancelled", 20)
    assert importer.status()["job"]["login"]["error"] == ""


def test_real_browser_the_login_check_reports_the_method_without_importing(browser):
    server, service, importer, captures = browser
    server.sso = True
    # Checked on the site's start page, which names the signed-in user.
    assert service.dispatch("confluence.check", {"url": server.site, "login": plan()}) == {
        "ok": True,
        "method": "generic",
        "label": SSO_FORM,
        "detected": True,
        "hosts": ["localhost"],
        "message": f"로그인했습니다 · {SSO_FORM}",
    }
    server.sso, server.form = False, True
    assert importer.check({"url": server.site, "login": plan()})["method"] == "atlassian"
    # A refused login is the check's result; without the LLM's help there is no analysis.
    refused = importer.check(
        {"url": server.site, "login": plan({"id": "tester", "password": "wrong"}, "fixed", [METHODS["atlassian"]])}
    )
    assert refused["ok"] is False and refused["message"].startswith(
        "로그인하지 못했습니다. ID·비밀번호를 확인해 주세요."
    )
    assert "diagnosis" not in refused
    assert captures == [] and importer.status()["job"] is None
    # One browser per profile: a running import refuses a check, and a check refuses an import.
    server.slow = True
    importer.start({"url": server.url, "recursive": False, "analyze": False})
    with pytest.raises(UserError, match="진행 중인 가져오기"):
        importer.check({"url": server.site, "login": plan()})
    importer.cancel()
    wait(lambda: importer.status()["job"]["status"] == "cancelled", 20)
    server.slow = False
    results = []
    checking = threading.Thread(target=lambda: results.append(importer.check({"url": server.site, "login": plan()})))
    checking.start()
    wait(lambda: importer.checking)
    with pytest.raises(UserError, match="진행 중인 가져오기"):
        importer.start({"url": server.url, "recursive": False, "analyze": False})
    checking.join(60)
    assert results[0]["ok"] is True and importer.checking is False


def test_real_browser_rendered_pages_and_the_visible_login_window(browser):
    server, service, importer, captures = browser
    server.sso = server.rendered = True
    job = run(importer, server.url, login=plan())
    assert (job["status"], job["imported"]) == ("completed", 1), str(job)
    # Cloud-like bodies are read from the rendered page, its expand macros open.
    assert "렌더링된 본문 1" in captures[0]["html"] and "접힌 세부 내용" in captures[0]["html"]

    # The login window runs the same automatic login (headless here) for a one-time sign-in approval, and reports the
    # method it detected for the site. The session the job before left would sign in by itself, so it goes first.
    forget_session(importer, server)
    status = importer.login({"url": server.url, "login": plan()})
    assert (status["login_open"], status["login_result"]) == (True, None)
    with pytest.raises(UserError, match="로그인 창"):
        importer.start({"url": server.url, "recursive": False, "analyze": False})
    wait(lambda: importer.status()["login_result"] is not None)
    assert importer.status()["login_result"] == {
        "method": "generic",
        "label": SSO_FORM,
        "detected": True,
        "hosts": ["localhost"],
    }
    assert importer.login_close()["login_open"] is False
    assert len(server.logins) == 2 and importer.status()["login_error"] == ""

    # A saved account the SSO refuses leaves the window to the user, who is told so (the session the window before
    # left would sign in by itself, so it goes first).
    forget_session(importer, server)
    importer.login(
        {"url": server.url, "login": plan({"id": "tester", "password": "wrong"}, "fixed", [METHODS["generic"]])}
    )
    wait(lambda: importer.status()["login_error"] != "")
    assert importer.status()["login_error"].startswith(
        "자동 입력하지 못했습니다. 창에서 직접 로그인해 주세요. 로그인하지 못했습니다."
    )
    assert importer.status()["login_open"] is True
    assert importer.login_close()["login_open"] is False


def test_real_browser_with_the_llm_a_failed_login_and_a_page_that_does_not_open_come_with_an_analysis(browser):
    server, service, importer, captures = browser
    llm = service.llm = AnalyzingLLM()
    service.preferences_save({"web_assist": True})  # Off until the user turns it on.
    analysis = {**ANALYSIS, "label": "ID·비밀번호", "attempt": ""}
    server.sso = True
    job = run(importer, server.url, login=plan({"id": "tester", "password": "wrong"}, "fixed", [METHODS["generic"]]))
    assert job["status"] == "error" and job["error"].startswith("로그인하지 못했습니다."), str(job)
    assert (job["diagnosis"], job["notice"]) == (analysis, "")
    assert [llm.asked[0][key] for key in ("site", "stage", "message")] == ["Confluence", "로그인", job["error"]]
    # Neither the password nor the account reached the LLM.
    assert not re.search(r"wrong|correct horse|tester", json.dumps(llm.asked, ensure_ascii=False))

    # Without a login, the first page that does not open is analyzed; later pages are not.
    server.sso, server.auth = False, True
    job = run(importer, server.url, recursive=True)
    assert (job["failed"], job["diagnosis"]) == (1, analysis), str(job)
    assert (llm.asked[-1]["stage"], llm.asked[-1]["message"]) == (
        "페이지 열기",
        "페이지에 접근하지 못했습니다 (HTTP 401).",
    )

    # A login check returns the failure with the analysis.
    server.auth, server.sso = False, True
    checked = importer.check(
        {"url": server.site, "login": plan({"id": "tester", "password": "wrong"}, "fixed", [METHODS["generic"]])}
    )
    assert (checked["ok"], checked["diagnosis"], llm.asked[-1]["stage"]) == (False, analysis, "로그인 확인")

    # An AI analysis that fails keeps the saved page and says so.
    server.sso = False
    service.llm = FailingLLM()
    job = run(importer, server.url, analyze=True)
    assert (job["status"], job["imported"], job["analysis_failed"]) == ("completed", 1, 1), str(job)
    assert "AI 분석에 실패" in job["results"][0]["analysis_error"]


# ---------------------------------------------------------------------------
# Requests within the site's rate, and routines that read changed pages only
# ---------------------------------------------------------------------------


def test_requests_keep_to_a_rate_per_host_and_wait_as_long_as_a_site_asks(monkeypatch):
    clock = [100.0]
    throttle = browser_import.Throttle(rate=4, burst=4, clock=lambda: clock[0])
    assert [throttle.reserve("https://wiki.test/a") for _ in range(4)] == [0, 0, 0, 0]
    assert throttle.reserve("https://wiki.test/b") == pytest.approx(0.25)
    assert throttle.reserve("https://other.test/") == 0  # Each host has its own.
    clock[0] += 1.25
    assert throttle.reserve("https://WIKI.test/c") == 0
    # A refusal as one too many holds every request to the host as long as Retry-After asks: seconds or a date.
    assert throttle.pause("https://wiki.test/x", 1, "3") is True
    assert throttle.reserve("https://wiki.test/y") == pytest.approx(3)
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    assert 25 < browser_import.retry_seconds(date) <= 30
    assert browser_import.retry_seconds("soon") is None and browser_import.retry_seconds("") is None
    # More than a job waits for is not waited for; without Retry-After the wait doubles at each try.
    assert throttle.pause("https://wiki.test/x", 1, str(int(browser_import.MAX_WAIT) + 1)) is False
    monkeypatch.setattr(browser_import.random, "random", lambda: 0)
    throttle.pause("https://late.test/", 3)
    assert throttle.reserve("https://late.test/") == pytest.approx(4)

    fast = browser_import.Throttle(rate=1000, burst=1000)

    def answers(*values):
        sent = list(values)

        async def send():
            return sent.pop(0)

        return send

    # Too many: tried again once the site's wait is over; another refusal (503 without Retry-After) is the answer.
    assert asyncio.run(
        browser_import.polite(
            "https://wiki.test/", answers({"status": 429, "retry": "0"}, {"status": 200, "text": "{}"}), fast
        )
    ) == {"status": 200, "text": "{}"}
    assert asyncio.run(
        browser_import.polite("https://wiki.test/", answers({"status": 503, "retry": ""}, {"status": 200}), fast)
    ) == {"status": 503, "retry": ""}
    refused = [{"status": 429, "retry": "0"}] * browser_import.ATTEMPTS + [{"status": 200}]
    send = answers(*refused)
    assert asyncio.run(browser_import.polite("https://wiki.test/", send, fast)) == {"status": 429, "retry": "0"}


def test_a_subpage_list_refused_as_too_many_is_asked_again_and_gives_the_versions(monkeypatch):
    monkeypatch.setattr(browser_import, "THROTTLE", browser_import.Throttle(rate=1000, burst=1000))

    class Page:
        def __init__(self, *answers):
            self.answers = list(answers)

        async def evaluate(self, script, params):
            return self.answers.pop(0)

    listed = json.dumps({"results": [{"id": "2", "version": {"number": 7}}, {"id": "3"}]})
    page = Page({"status": 429, "link": "", "text": "", "retry": "0"}, {"status": 200, "link": "", "text": listed})
    found = []
    asyncio.run(
        children(
            confluence_import.page_request(page), {"base": "https://wiki.test/confluence", "id": "1"}, found.append
        )
    )
    assert [(item["id"], item["version"]) for item in found] == [("2", "7"), ("3", "")]
    # A site that keeps refusing says so, and the job tells which list it could not read.
    page = Page(*[{"status": 429, "link": "", "text": "", "retry": "0"}] * browser_import.ATTEMPTS)
    with pytest.raises(UserError, match="HTTP 429"):
        asyncio.run(
            children(
                confluence_import.page_request(page), {"base": "https://wiki.test/confluence", "id": "1"}, found.append
            )
        )
    # A link that names its page by space and title is looked up by them.
    assert confluence_import.title_of("https://wiki.test/confluence/display/SPACE/Page+1%2B2") == (
        "https://wiki.test/confluence",
        "SPACE",
        "Page 1+2",
    )
    assert confluence_import.title_of("https://wiki.test/pages/viewpage.action?spaceKey=S&title=A+B") == (
        "https://wiki.test",
        "S",
        "A B",
    )
    assert confluence_import.title_of(PAGE) is None and confluence_import.title_of("https://wiki.test/x/Ab") is None


def test_real_browser_a_routine_reads_only_the_pages_whose_version_changed(browser):
    server, service, importer, captures = browser
    job = run(importer, server.url, recursive=True, track_changes=True)
    assert (job["status"], job["imported"]) == ("completed", 4), str(job)
    # Each page's version goes with its import: the subpage list gave the subpages', the content API the first page's.
    with service.db.connect() as db:
        versions = {row[0]: row[1] for row in db.execute("SELECT page_id, version FROM confluence_sources")}
    assert versions == {ident: str(server.version(ident)) for ident in "1234"}
    # Nothing changed: no page is read again, only the versions are asked for.
    server.requests.clear()
    job = run(importer, server.url, recursive=True, track_changes=True)
    assert (job["skipped"], job["processed"]) == (4, 4), str(job)
    assert {item["message"] for item in job["results"]} == {sources.UNMODIFIED}
    assert not [path for path in server.requests if "pageId=" in path], server.requests
    # A new version of one page, and a page last read in full more than a day ago: those two alone are read.
    server.versions["3"] = 77
    with service.db.connect() as db:
        db.execute("UPDATE confluence_sources SET read_at='2020-01-01T00:00:00+00:00' WHERE page_id='4'")
    server.requests.clear()
    job = run(importer, server.url, recursive=True, track_changes=True)
    assert (job["skipped"], job["processed"]) == (4, 4), str(job)
    assert sorted(path.rsplit("=", 1)[1] for path in server.requests if "pageId=" in path) == ["3", "4"]
    with service.db.connect() as db:
        assert db.execute("SELECT version FROM confluence_sources WHERE page_id='3'").fetchone()[0] == "77"
    # A link that names the page by space and title is found by them, without opening the page.
    server.requests.clear()
    job = run(importer, f"{server.origin}/confluence/display/SPACE/Page+1", track_changes=True)
    assert (job["skipped"], job["results"][0]["message"]) == (1, sources.UNMODIFIED), str(job)
    assert not [path for path in server.requests if "pageId=" in path or "/display/" in path], server.requests
    # The user's own import reads every page, as before.
    server.requests.clear()
    job = run(importer, server.url, recursive=True)
    assert len([path for path in server.requests if "pageId=" in path]) >= 4 and job["skipped"] == 4


def test_real_browser_a_site_that_asks_to_slow_down_is_waited_for(browser):
    server, service, importer, captures = browser
    # The subpage list refused twice as too many requests: asked again, no page below is lost.
    server.throttled = 2
    job = run(importer, server.url, recursive=True)
    assert (job["processed"], job["imported"], job["discovery_failed"], job["failed"]) == (4, 4, 0, 0), str(job)
    assert len([path for path in server.requests if "/rest/" in path]) == 5 + 2
    # A page refused once is opened again.
    server.page_throttled = 1
    job = run(importer, server.url)
    assert (job["status"], job["skipped"], job["failed"]) == ("completed", 1, 0), str(job)
    # A site that asks for a longer wait than a job takes is not waited for: the list it refused is told.
    server.throttled, server.retry_after = 1, "3600"
    job = run(importer, server.url, recursive=True)
    assert job["discovery_failed"] == 1 and "HTTP 429" in job["results"][0]["discovery_error"], str(job)
