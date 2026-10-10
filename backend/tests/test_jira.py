import asyncio
import json
import time
from pathlib import Path

import pytest

import jira_server
from agent import browser_import, diagnostics, jira_import, sources
from agent.db import Database
from agent.jira import build_html, comment_item, import_issue, parse_issue_url, search_jql
from agent.jira_import import JiraApi
from agent.llm import UserError
from agent.service import Service
from jira_server import JiraServer

# The login method catalog Electron sends with each job; the auto plan tries its methods in this order.
CATALOG = json.loads(
    (Path(__file__).resolve().parents[2] / "electron" / "login-methods.json").read_text(encoding="utf-8")
)["methods"]


def plan(credentials, mode="auto", methods=None):
    return {
        "site_id": "site-1",
        "site_name": "JIRA",
        "mode": mode,
        "methods": methods or CATALOG,
        "credentials": dict(credentials),
    }


@pytest.fixture
def service(tmp_path):
    return Service(Database(tmp_path / "wiki.db"), None)


def capture(service, body="<table><tr><td>한국어 표</td></tr></table><pre>if x:\n  return 1</pre>"):
    return {
        "source_url": "https://jira.test/jira/browse/TEAM-123",
        "issue_key": "TEAM-123",
        "html": f"<html><head><title>JIRA 테스트</title></head><body>{body}</body></html>",
    }


def test_import_search_source_persistence_duplicate_edits_and_delete(service):
    params = capture(service)
    result = import_issue(service, params)
    assert result["status"] == "imported"
    doc = service.wiki_get({"id": result["doc_id"]})
    assert "| 한국어 표 |" in doc["content"]
    assert "```\nif x:" in doc["content"]
    assert not (Path(service.db.path).parent / "jira").exists()
    assert "  return 1" in doc["content"]
    assert doc["source"]["kind"] == "jira"
    assert doc["source"]["source_url"] == params["source_url"]
    assert len(service.wiki_search({"query": "한국어 표", "mode": "word"})["documents"]) == 1
    markdown = service.wiki_source_markdown({"id": doc["id"]})["markdown"]
    service.wiki_save({"id": doc["id"], "title": "수정", "content": "개인 메모"})
    assert import_issue(service, params)["status"] == "skipped"
    assert service.wiki_source_markdown({"id": doc["id"]})["markdown"] == markdown
    assert service.wiki_get({"id": doc["id"]})["content"] == "개인 메모"
    changed = capture(service, "<p>변경된 페이지</p>")
    # The ticket changed, and so did the document: the user's text stays unless the update replaces it.
    skipped = import_issue(service, changed)
    assert (skipped["status"], skipped["doc_id"]) == ("skipped", doc["id"]) and "‘원본에서 업데이트’" in skipped[
        "message"
    ]
    restarted = Service(Database(service.db.path), None)
    assert restarted.wiki_source_markdown({"id": doc["id"]})["markdown"] == markdown
    updated = import_issue(service, {**changed, "doc_id": doc["id"], "replace": True})
    assert (updated["status"], updated["doc_ids"]) == ("updated", [doc["id"]])
    replaced = service.wiki_get({"id": doc["id"]})
    assert (replaced["title"], replaced["content"], replaced["revision"]) == ("수정", "변경된 페이지", 3)
    assert len(service.wiki_list({})) == 1
    service.wiki_delete({"id": doc["id"]})
    service.wiki_purge({"ids": [doc["id"]]})
    with service.db.connect() as db:
        assert not db.execute("SELECT 1 FROM jira_sources WHERE doc_id=?", (doc["id"],)).fetchone()


def test_limits_and_in_memory_payload_validation(service, tmp_path):
    params = capture(service)
    with pytest.raises(UserError, match="원문"):
        import_issue(service, {**params, "html": None, "html_path": "/outside.html"})
    with pytest.raises(UserError, match="HTTP"):
        import_issue(service, {**params, "source_url": "file:///etc/passwd"})
    with pytest.raises(UserError, match="티켓 키"):
        import_issue(service, {**params, "issue_key": "../123"})
    with pytest.raises(UserError, match="10MB"):
        import_issue(service, capture(service, "a" * (10 * 1024 * 1024)))
    assert service.wiki_list({}) == []
    # The browser importer calls import_issue directly; the old internal RPC is gone.
    with pytest.raises(UserError, match="지원하지 않는"):
        service.dispatch("jira.import", params)


def test_a_ticket_longer_than_a_document_is_saved_in_parts_and_keeps_its_first_comments(service, monkeypatch):
    comments = "".join(f"<h3>김개발 · 2026-09-{i % 28 + 1:02d}</h3><p>{'댓글 ' * 400}</p>" for i in range(120))
    result = import_issue(service, capture(service, comments))
    docs = [service.wiki_get({"id": ident}) for ident in result["doc_ids"]]
    assert result["message"] == f"글이 80,000자를 넘어 Markdown 문서 {len(docs)}개로 나누어 Wiki에 저장했습니다."
    assert len(docs) == 2 and [doc["title"] for doc in docs] == ["JIRA 테스트 (1/2)", "JIRA 테스트 (2/2)"]
    assert all(doc["source"]["issue_key"] == "TEAM-123" and len(doc["content"]) <= 80000 for doc in docs)
    assert import_issue(service, capture(service, comments))["status"] == "skipped"

    # Comments beyond the limit are left out, and the ticket says how many it shows.
    monkeypatch.setattr(jira_import, "MAX_COMMENTS", 2)
    api = JiraApi(None, "/jira")

    async def get(resource, params):
        start = params["startAt"]
        return {"startAt": start, "total": 5, "comments": [{"id": str(start), "body": f"댓글 {start}"}]}

    api.get = get
    found, total = asyncio.run(api.comments({"key": "TEAM-1", "fields": {"comment": {"comments": [], "total": 5}}}))
    assert ([comment["html"] for comment in found], total) == (["<p>댓글 0</p>", "<p>댓글 1</p>"], 5)
    _, document = build_html({"key": "TEAM-1", "fields": {"summary": "제목"}}, "https://jira.test", found, total)
    assert "전체 댓글 5개 중 2개만 가져왔습니다." in document


def test_existing_database_migration(service):
    doc = service.wiki_save({"title": "기존 문서", "content": "보존"})
    with service.db.connect() as db:
        db.execute("DROP TABLE jira_sources")
    upgraded = Service(Database(service.db.path), None)
    assert upgraded.wiki_get({"id": doc["id"]})["content"] == "보존"
    assert import_issue(upgraded, capture(upgraded))["status"] == "imported"


def test_ticket_links_only():
    for url, expected in [
        ("https://jira.test/browse/TEAM-123", ("https://jira.test", "", "TEAM-123")),
        ("https://jira.test/jira/browse/team_2-7/", ("https://jira.test", "/jira", "TEAM_2-7")),
        (
            "http://JIRA.test:8080/jira/projects/TEAM/issues/TEAM-9?filter=allopenissues",
            ("http://jira.test:8080", "/jira", "TEAM-9"),
        ),
        ("https://jira.test:443/browse/TEAM-1?focusedCommentId=5#comment-5", ("https://jira.test", "", "TEAM-1")),
        ("  https://jira.test/browse/TEAM-1  ", ("https://jira.test", "", "TEAM-1")),
    ]:
        assert parse_issue_url(url) == expected, url
    for url in [
        "https://jira.test/",
        "https://jira.test/browse/TEAM",
        "https://jira.test/browse/TEAM-1/extra",
        "https://jira.test/issues/?jql=project%3DTEAM",
        "https://jira.test/projects/TEAM",
        "https://jira.test/secure/RapidBoard.jspa?rapidView=7",
        "https://jira.test/browse/1TEAM-1",
        "ftp://jira.test/browse/TEAM-1",
        "https://user:pw@jira.test/browse/TEAM-1",
        "https://@jira.test/browse/TEAM-1",
        "javascript:alert(1)",
        "not a url",
        "https://jira.test:99999/browse/TEAM-1",
        "https://jira.test/a/../browse/TEAM-1",
        "https://jira.test/%2e%2e/browse/TEAM-1",
        "https:///browse/TEAM-1",
        "https://jira.test/" + "a" * 4096 + "/browse/TEAM-1",
        None,
    ]:
        assert parse_issue_url(url) is None, url


def test_project_search_queries():
    assert (
        search_jql("TEAM", "keyword", ' C++ "로그인"  -오류 [a]\\b ')
        == 'project = "TEAM" AND text ~ "C 로그인 오류 a b" ORDER BY key ASC'
    )
    with pytest.raises(UserError, match="키워드"):
        search_jql("TEAM", "keyword", '+-"*')
    assert (
        search_jql("TEAM", "user", user='a"b\\c')
        == 'project = "TEAM" AND (assignee = "a\\"b\\\\c" OR reporter = "a\\"b\\\\c") ORDER BY key ASC'
    )
    assert (
        search_jql("TEAM", "user", user="홍길동", role="assignee")
        == 'project = "TEAM" AND assignee = "홍길동" ORDER BY key ASC'
    )
    assert search_jql("TEAM", "user", user="tester", role="reporter").endswith(
        'AND reporter = "tester" ORDER BY key ASC'
    )


def test_ticket_document_sections(service):
    issue = {
        "key": "TEAM-5",
        "names": {
            "customfield_1": "스프린트",
            "customfield_2": "점수",
            "customfield_3": "고객",
            "customfield_4": "Rank",
            "customfield_5": "분류",
            "customfield_6": "태그",
        },
        "fields": {
            "summary": "제목 <b>",
            "description": "원문 *설명*\n둘째 줄",
            "assignee": None,
            "customfield_1": ["com.atlassian.greenhopper.service.sprint.Sprint@1a2b[id=1,name=S1]"],
            "customfield_2": 3.0,
            "customfield_3": {"displayName": "홍길동"},
            "customfield_4": "0|i0001:",
            "customfield_5": {"value": "하드웨어", "child": {"value": "센서"}},
            "customfield_6": ["a", "b"],
            "subtasks": [{"key": "TEAM-6", "fields": {"summary": "하위"}}],
            "parent": {"key": "bad key"},
            "attachment": [{"filename": "a.png", "content": "javascript:alert(1)", "size": 3 * 1024 * 1024}],
        },
        "renderedFields": {"environment": "<h1>Windows</h1>"},
    }
    comments = [
        comment_item(
            {"author": {"displayName": "김개발"}, "body": "원문 댓글", "created": "2026-09-01T12:00:00.000+0900"}, None
        )
    ]
    source, document = build_html(issue, "https://jira.test/jira", comments, total=4)
    assert source == "https://jira.test/jira/browse/TEAM-5"
    result = import_issue(service, {"source_url": source, "issue_key": "TEAM-5", "html": document})
    content = service.wiki_get({"id": result["doc_id"]})["content"]
    assert service.wiki_get({"id": result["doc_id"]})["title"] == "[TEAM-5] 제목 <b>"
    assert "| 담당자 | 미지정 |" in content
    assert "원문 \\*설명\\*  \n둘째 줄" in content  # Plain text is kept as text, not as markup.
    assert "## 환경\n\n### Windows" in content  # Rendered headings stay below the section heading.
    assert "### 점수\n\n3\n" in content and "### 고객\n\n홍길동" in content
    assert "하드웨어 › 센서" in content and "### 태그\n\na, b" in content
    assert "스프린트" not in content and "0|i0001" not in content and "Rank" not in content
    assert "- 하위 작업: [TEAM-6](https://jira.test/jira/browse/TEAM-6) 하위" in content and "bad key" not in content
    assert "- a.png (3.0MB)" in content and "javascript" not in content
    assert "### 김개발 · 2026-09-01 12:00\n\n원문 댓글" in content
    assert "전체 댓글 4개 중 1개만 가져왔습니다." in content
    long = build_html({**issue, "fields": {**issue["fields"], "summary": "가" * 300}}, "https://jira.test", [])[1]
    assert f"<title>[TEAM-5] {'가' * 191}</title>" in long


def test_start_validates_before_opening_a_browser(service):
    importer = service.jira
    url = "https://jira.test/browse/TEAM-1"
    base = {"url": url, "scope": "issue", "analyze": False}
    for params, message in [
        ({**base, "url": "https://jira.test/issues/?jql=project%3DTEAM"}, "티켓 링크 형식"),
        ({**base, "url": 3}, "JIRA 티켓 링크"),
        ({**base, "scope": "project"}, "가져올 범위"),
        ({**base, "scope": "keyword", "keyword": " "}, "검색 키워드"),
        ({**base, "scope": "keyword", "keyword": "*?"}, "기호만으로는"),
        ({**base, "scope": "keyword", "keyword": "a\nb"}, "제어 문자"),
        ({**base, "scope": "user"}, "사용자"),
        ({**base, "scope": "user", "user": "tester", "role": "watcher"}, "검색 대상"),
        ({**base, "analyze": "yes"}, "AI 분석 옵션"),
        ({**base, "login": {"mode": "auto"}}, "로그인 정보 형식"),
        ({**base, "login": plan({"id": "tester"})}, "로그인 정보 형식"),
        ({**base, "login": plan({"id": "", "password": "x"})}, "로그인 정보 형식"),
        ({**base, "login": {**plan({"id": "a", "password": "b"}), "mode": "manual"}}, "로그인 정보 형식"),
        (
            {
                **base,
                "login": plan(
                    {"id": "a", "password": "b"}, methods=[{"id": "x", "label": "x", "detect": [], "steps": []}]
                ),
            },
            "로그인 정보 형식",
        ),
    ]:
        with pytest.raises(UserError, match=message):
            importer.start(params)
    # A login window or check opens any page of the site, not only tickets.
    for method in (importer.login, importer.check):
        with pytest.raises(UserError, match="JIRA 주소"):
            method({"url": "ftp://jira.test/", "login": plan({"id": "a", "password": "b"})})
    with pytest.raises(UserError, match="로그인 정보를 저장한 뒤"):
        importer.check({"url": "https://jira.test/jira"})
    with pytest.raises(UserError, match="로그인 정보 형식"):
        importer.login({"url": "https://jira.test/jira", "login": {"mode": "fixed", "methods": []}})

    class Unconfigured:
        def public(self):
            return {"configured": False}

    service.llm = Unconfigured()
    with pytest.raises(UserError, match="LLM"):
        importer.start({**base, "analyze": True})
    assert importer.status() == {"job": None, "login_open": False, "login_error": "", "login_result": None}
    assert not importer.profile_root.exists() or not any(importer.profile_root.iterdir())


def test_a_browser_that_never_starts_leaves_the_importer_free(service, monkeypatch):
    # Without an event loop (Windows builds it on a local socket pair, which security software may refuse) the work
    # never runs, so it cannot report: the start says so, and the next start is not refused as busy.
    def refused(main):
        main.close()
        raise OSError(10106, "The requested service provider could not be loaded or initialized")

    monkeypatch.setattr(asyncio, "run", refused)
    importer = service.jira
    for _ in range(2):
        with pytest.raises(UserError, match="브라우저 작업을 시작하지 못했습니다.*10106"):
            importer.start({"url": "https://jira.test/browse/TEAM-1", "scope": "issue", "analyze": False})
        assert importer.status()["job"] is None
    with pytest.raises(UserError, match="브라우저 작업을 시작하지 못했습니다"):
        importer.login({"url": "https://jira.test/jira", "login": None})
    assert importer.status()["login_open"] is False
    with pytest.raises(UserError, match="브라우저 작업을 시작하지 못했습니다"):
        importer.check({"url": "https://jira.test/jira", "login": plan({"id": "a", "password": "b"})})
    assert importer.checking is False


def test_a_browser_start_that_fails_is_tried_once_more(service, tmp_path, monkeypatch):
    # On some PCs a start now and then fails between starts that work: the failed attempt's driver stops, the
    # failure goes to the log, and the next attempt opens the browser.
    events, failures, logged = [], [], []

    class Driver:  # async_playwright()
        async def start(self):
            events.append("driver")
            return self

        async def stop(self):
            events.append("stop")

    class Context:
        async def close(self):
            events.append("close")

    async def launch(playwright, profile, headless):
        events.append("launch")
        if failures:
            raise failures.pop(0)
        return Context()

    monkeypatch.setattr("playwright.async_api.async_playwright", Driver)
    monkeypatch.setattr(browser_import, "START_PAUSE", 0)
    monkeypatch.setattr(diagnostics, "logged", lambda context, trace: logged.append((context, trace)) or "id")
    importer = service.jira
    importer.profile_root, importer._launch = tmp_path / "profiles", launch

    async def use():
        async with importer._browser("https://jira.test", True):
            events.append("use")

    failures.append(RuntimeError("Browser closed."))
    asyncio.run(use())
    assert events == ["driver", "launch", "stop", "driver", "launch", "use", "close", "stop"]
    assert len(logged) == 1 and logged[0][0] == "JIRA 브라우저 시작 (다시 시도함)" and "Browser closed." in logged[0][1]

    # Two failures in a row report the last one; a cancelled start is not tried again.
    events.clear()
    failures.extend([RuntimeError("first"), RuntimeError("second")])
    with pytest.raises(UserError, match="JIRA 브라우저를 시작하지 못했습니다: second"):
        asyncio.run(use())
    assert events == ["driver", "launch", "stop", "driver", "launch", "stop"]
    events.clear()
    failures.append(asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(use())
    assert events == ["driver", "launch", "stop"]


class FailingLLM:
    def public(self):
        return {"configured": True}

    def structured(self, system, content, timeout=None):
        raise UserError("LLM 오류")


@pytest.fixture
def browser(tmp_path):
    server = JiraServer()
    service = Service(Database(tmp_path / "data" / "wiki.db"), FailingLLM())
    importer = service.jira
    importer.profile_root = tmp_path / "profiles"
    importer.optional_seconds, importer.form_seconds, importer.login_seconds = 1, 15, 20
    importer.login_headless = True
    yield server, service, importer
    importer.shutdown()
    server.close()


def wait(condition, seconds=60):
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.05)


def forget_session(importer, server):
    """The session the last job or window left in the site's profile is gone, as after the site signed the user out:
    the next job signs in with the saved account again."""

    async def clear():
        async with importer._browser(server.origin, headless=True) as context:
            await context.clear_cookies()

    asyncio.run(clear())


def run(server, importer, **params):
    """The finished job. Assertions show it as str(job): pytest shortens a dict to its first keys, without the error."""
    importer.start({"url": server.url, "scope": "issue", "analyze": False, "login": plan(server.credentials), **params})
    wait(lambda: importer.status()["job"]["status"] not in ("scanning", "login", "running", "cancelling"))
    return importer.status()["job"]


def test_real_browser_sso_login_scopes_and_no_files(browser, tmp_path):
    server, service, importer = browser
    job = run(server, importer)
    assert job["status"] == "completed", str(job)
    assert (job["imported"], job["total"], job["auto_login"], job["project"]) == (1, 1, True, "TEAM")
    # Background login follows the SSO Login link and fills the SSO form: the auto plan
    # detects that method and reports it, so Electron can save it for the site.
    assert server.logins == [server.credentials]
    assert job["login"] == {
        "site": "JIRA",
        "method": "sso-link",
        "label": "SSO 링크 · 표준 로그인 폼",
        "detected": True,
        "error": "",
        "hosts": ["localhost"],
    }
    assert "correct horse" not in json.dumps(importer.status())
    doc = service.wiki_get({"id": job["results"][0]["doc_id"]})
    assert doc["title"] == "[TEAM-1] 로그인 화면 오류"
    assert doc["source"]["source_url"] == f"{server.origin}/jira/browse/TEAM-1"
    content = doc["content"]
    for expected in [
        "| 담당자 | 김개발 |",
        "### 재현 절차",
        "| Chrome | 실패 |",
        "```java\nint answer = 1;\n```",
        "### 인수 조건\n\n로그인 후 목록이 보여야 한다",
        "### 심각도\n\n심각",
        "- blocks: [TEAM-2]",
        f"[설계.txt]({server.origin}/jira/secure/attachment/1/design.txt) (2KB, 김개발",
        "## 댓글 (3)",
        "재현 **확인**",
        "마지막 댓글 검색어",
        "화면 캡처",
    ]:
        assert expected in content, expected
    assert not any(word in content for word in ("secret", "javascript", "0|i0001", "lastViewed"))
    # The third comment came from the paginated comment API; attachments and images are never fetched.
    assert any("/comment?startAt=2" in path for path in server.requests)
    assert not any("/secure/attachment/" in path for path in server.requests)

    # The session the first job left in the profile is still signed in: the next job types no password (a site with a
    # second factor is signed in to once, in the login window, and the background keeps using that session).
    job = run(server, importer, login=plan(server.credentials, "fixed", [CATALOG[0]]))
    assert (job["skipped"], len(server.logins)) == (1, 1)
    assert (job["login"]["method"], job["login"]["detected"]) == (None, False)
    # A fixed method (the one saved for the site) signs in the same way without detection, once the session is gone.
    forget_session(importer, server)
    job = run(server, importer, login=plan(server.credentials, "fixed", [CATALOG[0]]))
    assert (job["skipped"], len(server.logins)) == (1, 2)
    assert (job["login"]["method"], job["login"]["detected"]) == ("sso-link", False)

    job = run(server, importer, scope="keyword", keyword="로그인")
    assert (job["status"], job["total"], job["imported"], job["skipped"]) == ("completed", 2, 1, 1)
    assert job["jql"] == server.jql[-1] == 'project = "TEAM" AND text ~ "로그인" ORDER BY key ASC'
    assert [item["key"] for item in job["results"]] == ["TEAM-1", "TEAM-3"]
    assert any("search?" in path and "startAt=1" in path for path in server.requests)

    server.forbidden = {"TEAM-3"}
    job = run(server, importer, scope="user", user="tester", role="any")
    assert (job["total"], job["processed"], job["imported"], job["skipped"], job["failed"]) == (3, 3, 1, 1, 1)
    assert "403" in next(item["message"] for item in job["results"] if item["status"] == "failed")
    server.forbidden = set()
    job = run(server, importer, scope="user", user="tester", role="assignee")
    assert [item["key"] for item in job["results"]] == ["TEAM-2"]

    job = run(server, importer, scope="user", user="nobody", role="reporter")
    assert job["status"] == "error" and "The value 'nobody' does not exist" in job["error"]
    assert job["processed"] == 0

    # Only the database is written; no HTML, Markdown or attachment files.
    assert sorted(p.name for p in (tmp_path / "data").iterdir() if not p.name.startswith("wiki.db")) == []
    assert not [
        p for p in tmp_path.rglob("*") if p.suffix in (".md", ".html", ".txt", ".png") and "profiles" not in p.parts
    ]
    assert len(service.wiki_list({})) == 3


def test_real_browser_session_login_window_errors_analysis_and_cancel(browser):
    server, service, importer = browser
    job = run(server, importer, login=None)
    assert job["status"] == "error" and "로그인이 필요합니다" in job["error"]
    assert (job["auto_login"], server.logins) == (False, [])
    assert job["login"] == {"site": "", "method": None, "label": "", "detected": False, "error": ""}
    # A cleared job stays for Electron, which may still read the login it detected; only that job is cleared.
    assert "dismissed" not in service.dispatch("jira.dismiss", {"id": "older job"})["job"]
    cleared = service.dispatch("jira.dismiss", {"id": job["id"]})["job"]
    assert cleared == {**job, "dismissed": True}

    # The login window fills the saved login too, so a sign-in prompt can be approved once. It opens the
    # ticket or any other page of the site, and reports the method that signed in.
    importer.login({"url": server.url, "login": plan(server.credentials)})
    assert importer.status()["login_open"] is True
    with pytest.raises(UserError, match="로그인 창"):
        importer.start({"url": server.url, "scope": "issue", "analyze": False})
    wait(lambda: importer.status()["login_result"] is not None)
    assert importer.status()["login_result"] == {
        "method": "sso-link",
        "label": "SSO 링크 · 표준 로그인 폼",
        "detected": True,
        "hosts": ["localhost"],
    }
    assert importer.login_close()["login_open"] is False
    assert server.logins == [server.credentials]
    assert importer.status()["login_result"]["method"] == "sso-link"  # Kept for Electron after the window closes.

    # Without a saved login, the session left in the profile reaches the ticket.
    job = run(server, importer, login=None, analyze=True)
    assert (job["status"], job["imported"], job["analysis_failed"]) == ("completed", 1, 1), str(job)
    assert "AI 분석에 실패" in job["results"][0]["analysis_error"]
    assert len(server.logins) == 1

    # A rejected password is recognized as soon as the SSO shows its form again, long before the timeout. The session
    # the window left would sign in by itself, so it goes first.
    forget_session(importer, server)
    started = time.monotonic()
    job = run(server, importer, login=plan({"id": "tester", "password": "wrong"}))
    assert job["status"] == "error" and "로그인하지 못했습니다" in job["error"], str(job)
    assert "SSO 링크 · 표준 로그인 폼: 로그인 화면이 다시 표시됨" in job["error"]
    assert job["login"]["error"] == job["error"] and job["login"]["method"] is None
    assert time.monotonic() - started < importer.login_seconds
    assert server.logins[-1]["password"] == "wrong" and "wrong" not in json.dumps(importer.status())
    assert len(server.logins) == 2  # A refused login ends the sign-in: one submission only.

    # An SSO that accepts the login but never returns to JIRA ends with the timeout message.
    importer.login_seconds = 2
    server.pending = True
    job = run(server, importer)
    assert job["status"] == "error" and "2초 안에 JIRA 티켓 페이지로 이동하지 않았습니다" in job["error"], str(job)
    server.pending = False

    server.slow = True
    importer.start({"url": server.url, "scope": "issue", "analyze": False, "login": plan(server.credentials)})
    with pytest.raises(UserError, match="진행 중인 가져오기"):
        importer.start({"url": server.url, "scope": "issue", "analyze": False})
    wait(
        lambda: (
            importer.status()["job"]["status"] == "scanning" and any("/rest/" in path for path in server.requests[-1:])
        )
    )
    # A running job keeps showing its progress.
    assert "dismissed" not in importer.dismiss({"id": importer.status()["job"]["id"]})["job"]
    assert importer.cancel()["job"]["status"] == "cancelling"
    wait(lambda: importer.status()["job"]["status"] == "cancelled", 20)
    assert importer.status()["job"]["imported"] == 0
    importer.job_worker.thread.join(20)
    assert not importer.job_worker.thread.is_alive()


def test_real_browser_that_cannot_start_says_why(browser, tmp_path, monkeypatch):
    server, service, importer = browser
    # No browser where Playwright looks: the job ends with Playwright's own reason instead of a bare failure.
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "no-browsers"))
    job = run(server, importer)
    assert job["status"] == "error" and job["error"].startswith("JIRA 브라우저를 시작하지 못했습니다: "), str(job)
    assert "Executable doesn't exist" in job["error"], str(job)
    assert server.logins == []


def test_a_request_jira_refuses_as_too_many_is_asked_again(monkeypatch):
    monkeypatch.setattr(browser_import, "THROTTLE", browser_import.Throttle(rate=1000, burst=1000))

    class Page:
        url = "https://jira.test/jira/browse/TEAM-1"

        def __init__(self, *answers):
            self.answers, self.asked = list(answers), []

        async def evaluate(self, script, params):
            self.asked.append(params["url"])
            return self.answers.pop(0)

    page = Page({"status": 429, "text": "", "retry": "0"}, {"status": 200, "text": '{"key": "TEAM-1"}', "retry": ""})
    assert asyncio.run(JiraApi(page, "/jira").get("issue/TEAM-1", {"fields": "updated"})) == {"key": "TEAM-1"}
    assert page.asked == ["/jira/rest/api/2/issue/TEAM-1?fields=updated"] * 2
    page = Page(*[{"status": 429, "text": "", "retry": "0"}] * browser_import.ATTEMPTS)
    with pytest.raises(UserError, match=r"HTTP 429\). 요청이 너무 많다며 JIRA가 거절했습니다"):
        asyncio.run(JiraApi(page, "/jira").get("issue/TEAM-1"))


def test_real_browser_a_routine_reads_only_the_tickets_updated_since(browser, monkeypatch):
    server, service, importer = browser

    def watch():
        return run(server, importer, scope="list", keys=["TEAM-1", "TEAM-2", "TEAM-3"], track_changes=True)

    job = watch()
    assert (job["status"], job["imported"]) == ("completed", 3), str(job)
    with service.db.connect() as db:
        assert {row[0] for row in db.execute("SELECT updated FROM jira_sources")} == {jira_server.UPDATED}
    # Nothing updated: the first ticket, which the job opens with, is read; the others are asked for their updated
    # time alone, and none is saved again.
    server.requests.clear()
    job = watch()
    assert (job["skipped"], {item["message"] for item in job["results"]}) == (3, {sources.UNMODIFIED}), str(job)
    full = [path for path in server.requests if "expand=renderedFields" in path]
    assert [path.split("?")[0].rsplit("/", 1)[1] for path in full] == ["TEAM-1"]
    assert sorted(path.split("?")[0].rsplit("/", 1)[1] for path in server.requests if "fields=updated" in path) == [
        "TEAM-2",
        "TEAM-3",
    ]
    assert not [path for path in server.requests if "/comment" in path]
    # One ticket updated: that one is read in full and saved.
    monkeypatch.setitem(
        jira_server.ISSUES,
        "TEAM-3",
        {
            **jira_server.ISSUES["TEAM-3"],
            "text": "세션이 60분 뒤 만료됩니다",
            "updated": "2026-10-06T11:00:00.000+0900",
        },
    )
    server.requests.clear()
    job = watch()
    assert [item["status"] for item in job["results"]] == ["skipped", "skipped", "updated"], str(job)
    assert sum("TEAM-3?expand=renderedFields" in path for path in server.requests) == 1
    # JIRA asks the job to slow down: the requests it refused are asked again.
    server.throttled = 2
    job = run(server, importer)
    assert (job["status"], job["skipped"], job["failed"]) == ("completed", 1, 0), str(job)
