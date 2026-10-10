"""Collect JIRA tickets with Python Playwright in the background.

A Chromium profile kept for each JIRA site signs in with the site's login method (web_login.py), then reads
tickets with REST requests made inside the JIRA page, so the browser's cookies and certificate trust apply. Each
ticket goes to jira.import_issue as an in-memory HTML document; nothing but the browser profile is written to disk.

The automatic routines (routines.py) also use it: a list of watched tickets is imported again with what each changed
(scope "list"), and a query job reads the issues of JQL searches with their status, people and dates, for a project's
progress report or a saved search (query), and may import the tickets the routine picks from them in the same session.
A routine's import asks for a ticket's updated time first, or takes it from the search that found the ticket: one not
updated since the Wiki took it, read in full within sources.RECHECK, is not read again.
"""
import asyncio
import contextlib
import json
import re
import uuid
from urllib.parse import quote, urlencode

from . import diagnostics, sources
from .browser_import import BUSY_ERROR, BrowserImporter, close_popup, open_page, polite
from .jira import (FORMAT_ERROR, KEY, PROJECT, brief, build_html, comment_item, import_issue, jql_string,
                   parse_issue_url, project_of, safe_prefix, search_jql, site_of, ticket_page)
from .llm import BusyError, UserError
from .service import routine_name, text
from .web_login import origin_of, sign_in, signed_in, validate_plan, with_scheme

MAX_ISSUES = 5000
MAX_COMMENTS = 20000
MAX_RESPONSE_CHARS = 20 * 1024 * 1024
# "list": the tickets a watching routine names (keys), on the site of the first.
SCOPES = ("issue", "keyword", "user", "list")
ROLES = ("any", "assignee", "reporter")
MAX_LIST = 50
# Query jobs: a project's progress ("report") or the user's own search ("jql").
MODES = ("report", "jql")
REPORT_FIELDS = "summary,status,assignee,priority,issuetype,updated,created,duedate,resolutiondate,project"
MAX_OPEN = 1000  # Unresolved issues a report reads, the latest updated first,
MAX_RESOLVED = 200  # and issues resolved in its period.
MAX_FOUND = 500  # Issues of a search.
MAX_PICKED = 50  # Tickets a routine may import from what a query found.
REPORT_DAYS = (7, 14, 30)
ORDERED = re.compile(r"\border\s+by\b", re.I)

CONTEXT_PATH = """() => document.querySelector('meta[name="ajs-context-path"]')?.getAttribute('content') ?? null"""

SITE_ERROR = "JIRA 주소를 확인해 주세요. 예) https://jira.example.com/jira, jira.example.com/jira"

# Runs in the signed-in JIRA page; a login redirect shows up as status 0.
FETCH_JSON = """async ({url, limit}) => {
    const response = await fetch(url, {
        credentials: 'same-origin', redirect: 'manual', cache: 'no-store',
        headers: {Accept: 'application/json'}, signal: AbortSignal.timeout(30000),
    });
    const text = await response.text();
    return {status: response.status, text: text.length > limit ? null : text,
            retry: response.headers.get('retry-after') || ''};
}"""


# ---------------------------------------------------------------------------
# REST inside the page
# ---------------------------------------------------------------------------


class ApiError(UserError):
    def __init__(self, status, detail=""):
        hint = {401: "로그인이 필요합니다.", 403: "접근 권한이 없습니다.", 404: "티켓이 없거나 볼 권한이 없습니다.",
                429: "요청이 너무 많다며 JIRA가 거절했습니다. 잠시 후 다시 시도해 주세요."}.get(
            status, "로그인·접근 권한·REST API 사용 가능 여부를 확인해 주세요.")
        super().__init__(f"JIRA 데이터를 읽지 못했습니다 (HTTP {status}). {detail or hint}")
        self.status = status


class JiraApi:
    """REST calls made inside the signed-in JIRA page (the browser's cookies and certificate trust)."""

    def __init__(self, page, base_path):
        self.page, self.base = page, base_path

    async def get(self, resource, params=None):
        """A REST answer of JIRA, within the site's rate; one JIRA refuses as too many is waited out and asked again
        (browser_import.polite). The address is the site's own path, so the rate is counted by the page's host."""
        url = f"{self.base}/rest/api/2/{resource}" + (f"?{urlencode(params, quote_via=quote)}" if params else "")
        result = await polite(self.page.url, lambda: self.page.evaluate(
            FETCH_JSON, {"url": url, "limit": MAX_RESPONSE_CHARS}))
        status, body = result.get("status"), result.get("text")
        if body is None:
            raise UserError("JIRA 응답이 20MB를 초과합니다.")
        if status == 0:
            raise UserError("JIRA 요청이 로그인 화면으로 이동했습니다. 로그인 정보를 확인해 주세요.")
        try:
            data = json.loads(body)
        except ValueError:
            data = None
        if status != 200:
            # JIRA explains rejected searches, e.g. an unknown user, in errorMessages/errors.
            reasons = []
            if isinstance(data, dict):
                reasons += data.get("errorMessages") if isinstance(data.get("errorMessages"), list) else []
                reasons += list(data["errors"].values()) if isinstance(data.get("errors"), dict) else []
            raise ApiError(status, " ".join(r for r in reasons if isinstance(r, str))[:300])
        if not isinstance(data, dict):
            raise UserError("JIRA 데이터 대신 로그인 화면 또는 잘못된 응답을 받았습니다. 로그인이 풀렸거나 권한이 없을 수 있습니다.")
        return data

    async def issue(self, key):
        data = await self.get(f"issue/{key}", {"expand": "renderedFields,names"})
        if not KEY.fullmatch(str(data.get("key") or "")) or not isinstance(data.get("fields"), dict):
            raise UserError("티켓 본문 형식을 확인하지 못했습니다.")
        return data

    async def comments(self, issue):
        """(comments, total). The ticket response can hold only the first page of comments."""
        embedded = issue["fields"].get("comment") if isinstance(issue["fields"].get("comment"), dict) else {}
        items = [c for c in embedded.get("comments") or [] if isinstance(c, dict)]
        total = embedded.get("total") if isinstance(embedded.get("total"), int) else len(items)
        if total <= len(items):
            rendered = ((issue.get("renderedFields") or {}).get("comment") or {}).get("comments") or []
            bodies = {c.get("id"): c.get("body") for c in rendered if isinstance(c, dict)}
            return [comment_item(c, bodies.get(c.get("id"))) for c in items], total
        found, start = [], 0
        while True:
            body = await self.get(f"issue/{issue['key']}/comment", {"startAt": start, "maxResults": 100, "expand": "renderedBody"})
            values = body.get("comments")
            if not isinstance(values, list) or body.get("startAt") != start or not isinstance(body.get("total"), int):
                raise UserError("댓글 목록의 페이지 정보를 확인하지 못했습니다.")
            found += [comment_item(c, c.get("renderedBody")) for c in values if isinstance(c, dict)]
            start += len(values)
            if start >= body["total"]:
                return found, body["total"]
            if not values:
                raise UserError("댓글 목록이 중간에 비어 있습니다.")
            if len(found) >= MAX_COMMENTS:
                # The rest are left out; the ticket document says how many it shows of how many.
                return found[:MAX_COMMENTS], body["total"]

    async def issues(self, jql, fields, limit, progress=None):
        """The first `limit` issues of a search with `fields`, and how many it found in all (None when JIRA does not
        say, as Cloud's cursor search), following Server/DC offsets or Cloud cursors."""
        found, start, token, tokens, cursor, total = [], 0, None, set(), False, None
        while len(found) < limit:
            size = min(100, limit - len(found))
            if cursor:
                body = await self.get("search/jql", {"jql": jql, "fields": fields, "maxResults": size,
                                                     **({"nextPageToken": token} if token else {})})
            else:
                try:
                    body = await self.get("search", {"jql": jql, "fields": fields, "startAt": start, "maxResults": size})
                except ApiError as error:
                    if start or error.status not in (404, 405, 410):
                        raise
                    cursor = True
                    continue
            issues = body.get("issues")
            if not isinstance(issues, list):
                raise UserError("JIRA 검색 결과 형식을 확인하지 못했습니다.")
            if isinstance(body.get("total"), int) and not isinstance(body.get("total"), bool):
                total = body["total"]
            for issue in issues:
                if not isinstance(issue, dict) or not KEY.fullmatch(str(issue.get("key") or "")):
                    raise UserError("검색 결과의 티켓 키가 올바르지 않습니다.")
                found.append(issue)
            if progress:
                progress(len(found))
            if cursor:
                token = body.get("nextPageToken")
                if body.get("isLast") is True or not token or not issues:
                    break
                if not isinstance(token, str) or token in tokens:
                    raise UserError("검색 결과 페이지가 반복됩니다. 일부 티켓이 누락될 수 있습니다.")
                tokens.add(token)
            else:
                start += len(issues)
                if not issues or (total is not None and start >= total):
                    break
        return found[:limit], total

    async def count(self, jql):
        """How many issues a search finds, or None when JIRA does not say (Cloud's search gives no total)."""
        try:
            body = await self.get("search", {"jql": jql, "fields": "summary", "maxResults": 0})
        except ApiError as error:
            if error.status in (404, 405, 410):
                return None
            raise
        total = body.get("total")
        return total if isinstance(total, int) and not isinstance(total, bool) else None

    async def search(self, jql, progress):
        """Every ticket key of the search, following Server/DC offsets or Cloud cursors."""
        found, start, token, tokens, cursor = {}, 0, None, set(), False
        while True:
            if cursor:
                body = await self.get("search/jql", {"jql": jql, "fields": "summary", "maxResults": 100,
                                                     **({"nextPageToken": token} if token else {})})
            else:
                try:
                    body = await self.get("search", {"jql": jql, "fields": "summary", "startAt": start, "maxResults": 100})
                except ApiError as error:
                    # Newer JIRA Cloud replaced /search with the cursor-based /search/jql.
                    if start or error.status not in (404, 405, 410):
                        raise
                    cursor = True
                    continue
            issues = body.get("issues")
            if not isinstance(issues, list):
                raise UserError("JIRA 검색 결과 형식을 확인하지 못했습니다.")
            total = body.get("total")
            if isinstance(total, int) and total > MAX_ISSUES:
                raise UserError(f"검색된 티켓이 {total:,}개로 최대 {MAX_ISSUES:,}개를 초과합니다. 키워드나 사용자를 더 구체적으로 입력해 주세요.")
            count = len(found)
            for issue in issues:
                key = issue.get("key") if isinstance(issue, dict) else None
                if not isinstance(key, str) or not KEY.fullmatch(key):
                    raise UserError("검색 결과의 티켓 키가 올바르지 않습니다.")
                found[key] = True
            if len(found) > MAX_ISSUES:
                raise UserError(f"검색된 티켓이 최대 {MAX_ISSUES:,}개를 초과합니다. 키워드나 사용자를 더 구체적으로 입력해 주세요.")
            progress(len(found))
            if cursor:
                token = body.get("nextPageToken")
                if body.get("isLast") is True or not token:
                    return list(found)
                if not isinstance(token, str) or token in tokens or len(found) == count:
                    raise UserError("검색 결과 페이지가 반복됩니다. 일부 티켓이 누락될 수 있습니다.")
                tokens.add(token)
            else:
                if not isinstance(total, int) or body.get("startAt") != start:
                    raise UserError("검색 결과의 페이지 정보를 확인하지 못했습니다.")
                start += len(issues)
                if start >= total:
                    return list(found)
                if len(found) == count:
                    raise UserError("검색 결과가 중간에 비거나 반복됩니다. 일부 티켓이 누락될 수 있습니다.")


# ---------------------------------------------------------------------------
# Background jobs
# ---------------------------------------------------------------------------


class JiraImporter(BrowserImporter):
    product = "JIRA"
    site_error = SITE_ERROR
    profiles_variable, profiles_folder = "PRIVATE_AGENT_JIRA_PROFILES", "jira-profiles"

    def start(self, params):
        url, target = self._target(params)
        login = validate_plan(params.get("login"))
        scope = params.get("scope")
        if scope not in SCOPES:
            raise UserError("가져올 범위(이 티켓만·키워드 검색·사용자 검색)를 선택해 주세요.")
        keys = []
        if scope == "list":
            keys = params.get("keys")
            if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_LIST or not all(
                    isinstance(key, str) and KEY.fullmatch(key.upper()) for key in keys):
                raise UserError(f"확인할 티켓은 1~{MAX_LIST}개의 티켓 키로 주세요.")
            keys = list(dict.fromkeys([target[2], *(key.upper() for key in keys)]))
        keyword = text(params.get("keyword", ""), "검색 키워드", 200, scope == "keyword")
        user = text(params.get("user", ""), "사용자", 256, scope == "user")
        role = params.get("role", "any")
        if role not in ROLES:
            raise UserError("사용자 검색 대상을 확인해 주세요.")
        if re.search(r"[\x00-\x1f\x7f]", keyword + user):
            raise UserError("검색어에는 줄바꿈이나 제어 문자를 사용할 수 없습니다.")
        if scope != "issue":
            search_jql("PROJECT", scope, keyword, user, role)  # Symbol-only keywords fail before the browser starts.
        analyze = params.get("analyze")
        if not isinstance(analyze, bool):
            raise UserError("AI 분석 옵션을 확인해 주세요.")
        if analyze and not self.service.llm.public()["configured"]:
            raise UserError("AI 분석을 사용하려면 먼저 LLM을 연결해 주세요.")
        doc_id, replace = sources.options(params)
        # An automatic routine (routines.py) that watches tickets learns what each import changed.
        options = {"scope": scope, "keyword": keyword if scope == "keyword" else "",
                   "user": user if scope == "user" else "", "role": role, "analyze": analyze,
                   "doc_id": doc_id, "replace": replace, "keys": keys,
                   "track_changes": params.get("track_changes") is True}
        routine = routine_name(params)
        with self.lock:
            if self._busy():
                raise BusyError(BUSY_ERROR)
            job = {"id": str(uuid.uuid4()), "url": url, **options, "routine": routine, "auto_login": bool(login),
                   "login": {"site": login["site_name"] if login else "", "method": None, "label": "",
                             "detected": False, "error": ""},
                   "status": "scanning", "project": target[2].split("-")[0], "jql": "",
                   "total": len(keys) if scope == "list" else 1 if scope == "issue" else 0, "processed": 0, "imported": 0, "updated": 0, "skipped": 0,
                   "failed": 0, "analysis_failed": 0, "current_url": "", "notice": "", "results": [], "error": "",
                   "diagnosis": None}
            self._begin(job, lambda: self._collect(job, url, target[0], options, login), "jira-browser")
            return self.status()

    @staticmethod
    def _target(params):
        url = with_scheme(text(params.get("url"), "JIRA 티켓 링크", 4096))
        target = parse_issue_url(url)
        if not target:
            raise UserError(FORMAT_ERROR)
        return url, target

    @staticmethod
    async def _at_ticket(page, origin):
        """The sign-in is complete: a ticket page of the site, or any of its pages with a signed-in user."""
        try:
            if ticket_page(page.url, origin):
                return True
        except Exception:
            return False
        return await signed_in(page, origin)

    async def _collect(self, job, url, origin, options, login):
        try:
            async with self._browser(origin, headless=True) as context:
                page = context.pages[0] if context.pages else await context.new_page()
                context.on("page", lambda popup: close_popup(page, popup))
                assist = self._assistant(page, login, lambda **values: self._update(job, **values))
                # Until the first ticket is read, a failure is one of opening JIRA or signing in.
                async with self._explaining(job, assist, page,
                                            lambda: "로그인" if job["status"] == "login" else "티켓 열기"):
                    if login:
                        self._update(job, status="login")
                        try:
                            result = await sign_in(context, page, url, login, product="JIRA 티켓", assist=assist,
                                                   arrived=lambda value: self._at_ticket(value, origin),
                                                   **self._timeouts())
                        except UserError as error:
                            with self.lock:
                                job["login"].update(error=str(error), sent=getattr(error, "sent", False))
                            raise
                        with self.lock:
                            job["login"].update(result)
                        # The SSO may return to another page of the site, e.g. the dashboard.
                        if not ticket_page(page.url, origin):
                            await open_page(page, url, self.product, assist)
                    else:
                        response = await open_page(page, url, self.product, assist)
                        if response and response.status >= 400:
                            raise UserError(f"티켓 페이지에 접근하지 못했습니다 (HTTP {response.status}).")
                    arrived = parse_issue_url(page.url)
                    if not arrived or arrived[0] != origin:
                        raise UserError("로그인이 필요합니다. 관리 → 사이트 계정에서 이 JIRA의 로그인 정보를 저장하거나 "
                                        "‘로그인 창 열기’에서 먼저 로그인해 주세요.")
                    self._update(job, status="scanning")
                    # A moved ticket redirects to its new key; the page also names the context path.
                    _, prefix, key = arrived
                    context_path = await page.evaluate(CONTEXT_PATH)
                    base_path = context_path if safe_prefix(context_path) else prefix
                    base_url = origin + base_path
                    api = JiraApi(page, base_path)
                    try:
                        first = await api.issue(key)
                    except ApiError as error:
                        # One watched ticket that is gone or closed to the user fails alone.
                        if options["scope"] != "list" or error.status not in (403, 404):
                            raise
                        first = None
                project = first["fields"].get("project") if first else None
                project = project["key"] if isinstance(project, dict) and isinstance(project.get("key"), str) \
                    else (first["key"] if first else key).split("-")[0]
                queue = [first["key"] if first else key]
                if options["scope"] == "list":
                    queue = list(dict.fromkeys([*queue, *options["keys"]]))
                elif options["scope"] != "issue":
                    if not PROJECT.fullmatch(project):
                        raise UserError("티켓의 프로젝트를 확인하지 못했습니다.")
                    jql = search_jql(project, options["scope"], options["keyword"], options["user"], options["role"])
                    self._update(job, project=project, jql=jql)
                    queue = await api.search(jql, lambda count: self._update(job, total=count))
                self._update(job, status="running", project=project, total=len(queue))
                for key in queue:
                    if page.is_closed():
                        raise UserError("JIRA 브라우저가 닫혀 가져오기를 중단했습니다.")
                    self._update(job, current_url=f"{base_url}/browse/{key}")
                    result = await self._import(api, base_url, first if first and key == first["key"] else key,
                                                options["analyze"], job)
                    with self.lock:
                        job["results"].append({"key": key, "url": f"{base_url}/browse/{key}", **result})
                        job[result["status"]] += 1
                        job["processed"] += 1
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self._update(job, error=diagnostics.report(
                error, "JIRA 가져오기를 완료하지 못했습니다. 링크·네트워크·로그인 상태를 확인해 주세요.", "JIRA 가져오기"))
        finally:
            with self.lock:
                job["status"] = "cancelled" if job["status"] == "cancelling" else "error" if job["error"] else "completed"
                job["current_url"] = ""

    async def _import(self, api, base_url, issue, analyze, job, updated=None):
        """Import the ticket `issue` (its key, or the ticket read already): a routine's import skips one it need not
        read again (_unchanged), with its `updated` time when a search gave it."""
        try:
            if job.get("track_changes"):
                skipped = await self._unchanged(api, base_url, issue, updated)
                if skipped:
                    return skipped
            if isinstance(issue, str):
                issue = await api.issue(issue)
            comments, total = await api.comments(issue)
            source_url, document = build_html(issue, base_url, comments, total)
            stamp = issue["fields"].get("updated")
            result = import_issue(self.service, {"source_url": source_url, "issue_key": issue["key"], "html": document,
                                                 "doc_id": job.get("doc_id"), "replace": job.get("replace", False),
                                                 "updated": stamp if isinstance(stamp, str) else "",
                                                 "track_changes": job.get("track_changes", False)})
        except Exception as error:
            return {"status": "failed", "message": diagnostics.report(
                error, "티켓을 읽거나 저장하지 못했습니다. 네트워크·로그인 상태를 확인해 주세요.", "JIRA 티켓 가져오기")}
        if analyze and result["status"] in ("imported", "updated"):
            for ident in result["doc_ids"]:
                try:
                    await asyncio.to_thread(self.service.wiki_analyze, {"id": ident})
                except Exception:
                    result["analysis_error"] = "Markdown 문서는 저장되었지만 AI 분석에 실패했습니다. 문서에서 재시도해 주세요."
            if "analysis_error" in result:
                with self.lock:
                    job["analysis_failed"] += 1
        return result

    async def _unchanged(self, api, base_url, issue, updated=None):
        """For a routine's import: the result of the ticket `issue` (key, or the ticket read) when it need not be read
        again, or None to read it. One whose documents were all purged is left out (sources.ignored); one JIRA says was
        not updated since the Wiki took it, read in full within sources.RECHECK, is skipped (sources.fresh). Its updated
        time is asked for alone unless known: given (a search found it) or in the ticket read."""
        key = issue if isinstance(issue, str) else issue["key"]
        link = f"{base_url}/browse/{key}"
        with self.service.db.connect() as db:
            if sources.ignored(db, "jira_sources", link):
                return sources.skipped(sources.Plan("skip", [], "ignored"), "")
            # Nothing recorded to compare with (a ticket new to the Wiki): it is read without asking first.
            if not sources.recorded(db, "jira_sources", link, "updated"):
                return None
        if updated is None:
            if isinstance(issue, str):
                light = await api.get(f"issue/{key}", {"fields": "updated"})
                if light.get("key") != key:  # Moved to another key: read in full as it is now.
                    return None
                issue = light
            fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
            updated = fields.get("updated")
        if not isinstance(updated, str) or not updated:
            return None
        with self.service.db.connect() as db:
            found = sources.fresh(db, "jira_sources", link, "updated", updated)
        return sources.unmodified(found) if found else None

    # -- Query jobs of the automatic routines ------------------------------------------------------------------------

    def query(self, params, pick=None):
        """A background job for an automatic routine (routines.py): sign in to the JIRA site of `url` (any of its links,
        such as a project, a board or a ticket) and read the issues of JQL searches with REPORT_FIELDS into
        job["report"]. Mode "report" reads a project's progress: its unresolved issues, the latest updated first, those
        resolved and the count of those created in the last `days`, and its total, narrowed by `jql` when given. Mode
        "jql" runs the user's own search `jql`. `pick(report)`, called in the job once the report is read, may name
        tickets to import in the same session, as a "list" job imports them."""
        url = with_scheme(text(params.get("url"), "JIRA 주소", 4096))
        site = site_of(url)
        if not site:
            raise UserError(SITE_ERROR)
        login = validate_plan(params.get("login"))
        mode = params.get("mode")
        if mode not in MODES:
            raise UserError("JIRA에서 읽을 내용(진행 현황·검색)을 확인해 주세요.")
        jql = text(params.get("jql", ""), "JQL", 2000, required=mode == "jql")
        if re.search(r"[\x00-\x1f\x7f]", jql):
            raise UserError("JQL에는 줄바꿈이나 제어 문자를 사용할 수 없습니다.")
        project = ""
        if mode == "report":
            project = (params.get("project") or project_of(url) or "").strip().upper()
            if not PROJECT.fullmatch(project):
                raise UserError("JIRA 프로젝트 키를 확인해 주세요. 예) TEAM (프로젝트 링크에 들어 있으면 비워도 됩니다)")
            if ORDERED.search(jql):
                raise UserError("추가 조건(JQL)에는 ORDER BY를 넣지 말아 주세요. 정렬은 앱이 정합니다.")
        days = params.get("days", 7)
        if days not in REPORT_DAYS or isinstance(days, bool):
            raise UserError("최근 변화를 볼 기간은 7일, 14일, 30일 중에서 골라 주세요.")
        analyze = params.get("analyze", False)
        if not isinstance(analyze, bool):
            raise UserError("AI 분석 옵션을 확인해 주세요.")
        if analyze and not self.service.llm.public()["configured"]:
            raise UserError("AI 분석을 사용하려면 먼저 LLM을 연결해 주세요.")
        options = {"mode": mode, "project": project, "jql": jql, "days": days, "analyze": analyze}
        with self.lock:
            if self._busy():
                raise BusyError(BUSY_ERROR)
            job = {"id": str(uuid.uuid4()), "url": url, "scope": mode, "keyword": "", "user": "", "role": "any",
                   "analyze": analyze, "doc_id": None, "replace": False, "keys": [], "track_changes": True,
                   "routine": routine_name(params), "auto_login": bool(login),
                   "login": {"site": login["site_name"] if login else "", "method": None, "label": "",
                             "detected": False, "error": ""},
                   "status": "scanning", "project": project, "jql": jql, "total": 0, "processed": 0, "imported": 0,
                   "updated": 0, "skipped": 0, "failed": 0, "analysis_failed": 0, "current_url": "", "notice": "",
                   "results": [], "error": "", "diagnosis": None, "report": None}
            self._begin(job, lambda: self._query(job, url, site, options, login, pick), "jira-browser")
            return self.status()

    async def _query(self, job, url, site, options, login, pick):
        origin, prefix = site
        try:
            async with self._browser(origin, headless=True) as context:
                page = context.pages[0] if context.pages else await context.new_page()
                context.on("page", lambda popup: close_popup(page, popup))
                assist = self._assistant(page, login, lambda **values: self._update(job, **values))
                # Until JIRA's page shows, a failure is one of opening JIRA or signing in.
                async with self._explaining(job, assist, page,
                                            lambda: "로그인" if job["status"] == "login" else "JIRA 열기"):
                    if login:
                        self._update(job, status="login")
                        try:
                            result = await sign_in(context, page, url, login, product="JIRA", assist=assist,
                                                   arrived=lambda value: signed_in(value, origin), **self._timeouts())
                        except UserError as error:
                            with self.lock:
                                job["login"].update(error=str(error), sent=getattr(error, "sent", False))
                            raise
                        with self.lock:
                            job["login"].update(result)
                    else:
                        response = await open_page(page, url, self.product, assist)
                        if response and response.status >= 400:
                            raise UserError(f"JIRA 페이지에 접근하지 못했습니다 (HTTP {response.status}).")
                    if origin_of(page.url) != origin:
                        raise UserError("로그인이 필요합니다. 관리 → 사이트 계정에서 이 JIRA의 로그인 정보를 저장하거나 "
                                        "‘로그인 창 열기’에서 먼저 로그인해 주세요.")
                    context_path = await page.evaluate(CONTEXT_PATH)
                base_path = context_path if safe_prefix(context_path) else prefix
                api, base_url = JiraApi(page, base_path), origin + base_path
                self._update(job, status="scanning")
                report = await (self._report(api, options, job) if options["mode"] == "report"
                                else self._search(api, options, job))
                report["base_url"] = base_url
                with self.lock:
                    job["report"] = report
                keys = list(dict.fromkeys(key for key in (pick(report) if pick else []) if KEY.fullmatch(key)))
                # The search gave each ticket's updated time: a ticket picked is not asked for it again.
                stamps = {issue["key"]: issue["updated"] for name in ("issues", "open", "resolved")
                          for issue in report.get(name) or []}
                self._update(job, status="running", total=len(keys[:MAX_PICKED]), processed=0)
                for key in keys[:MAX_PICKED]:
                    if page.is_closed():
                        raise UserError("JIRA 브라우저가 닫혀 가져오기를 중단했습니다.")
                    self._update(job, current_url=f"{base_url}/browse/{key}")
                    result = await self._import(api, base_url, key, options["analyze"], job, stamps.get(key))
                    with self.lock:
                        job["results"].append({"key": key, "url": f"{base_url}/browse/{key}", **result})
                        job[result["status"]] += 1
                        job["processed"] += 1
        except asyncio.CancelledError:
            pass
        except Exception as error:
            self._update(job, error=diagnostics.report(
                error, "JIRA를 조회하지 못했습니다. 주소·JQL·네트워크·로그인 상태를 확인해 주세요.", "JIRA 조회"))
        finally:
            with self.lock:
                job["status"] = "cancelled" if job["status"] == "cancelling" else "error" if job["error"] else "completed"
                job["current_url"] = ""

    async def _report(self, api, options, job):
        """A project's progress: its unresolved issues (the latest updated first), the issues resolved and the number
        created in the last `days`, and how many it has in all."""
        base = f"project = {jql_string(options['project'])}" + (f" AND ({options['jql']})" if options["jql"] else "")
        days = options["days"]
        self._update(job, jql=base)
        found, open_total = await api.issues(f"{base} AND resolution = Unresolved ORDER BY updated DESC",
                                             REPORT_FIELDS, MAX_OPEN, lambda count: self._update(job, processed=count))
        resolved, resolved_total = await api.issues(f"{base} AND resolutiondate >= -{days}d ORDER BY resolutiondate DESC",
                                                    REPORT_FIELDS, MAX_RESOLVED)
        name = ""
        for issue in found + resolved:
            project = (issue.get("fields") or {}).get("project")
            if isinstance(project, dict) and isinstance(project.get("name"), str):
                name = project["name"][:120]
                break
        return {"mode": "report", "project": options["project"], "name": name, "jql": base, "days": days,
                "total": await api.count(base), "created": await api.count(f"{base} AND created >= -{days}d"),
                "open_total": open_total if open_total is not None else len(found),
                "open": [brief(issue) for issue in found],
                "resolved_total": resolved_total if resolved_total is not None else len(resolved),
                "resolved": [brief(issue) for issue in resolved]}

    async def _search(self, api, options, job):
        """The issues of the user's own search, the latest updated first unless it says how to order them."""
        jql = options["jql"] if ORDERED.search(options["jql"]) else f"{options['jql']} ORDER BY updated DESC"
        found, total = await api.issues(jql, REPORT_FIELDS, MAX_FOUND, lambda count: self._update(job, processed=count))
        return {"mode": "jql", "jql": options["jql"], "total": total if total is not None else len(found),
                "issues": [brief(issue) for issue in found]}

    async def _prepare_login(self, context, page, url, origin, login):
        if not login:
            await open_page(page, url, self.product)
            return None
        # The user watches the automatic login and can approve a sign-in prompt once, or take over
        # by hand when it fails.
        return asyncio.ensure_future(self._login_helper(context, page, url, origin, login))

    async def _login_helper(self, context, page, url, origin, login):
        with contextlib.suppress(Exception):  # A failed automatic login leaves the window to the user.
            result = await sign_in(context, page, url, login, product="JIRA", watched=True,
                                   arrived=lambda value: self._at_ticket(value, origin), **self._timeouts())
            with self.lock:
                self.login_result = result

    async def _verify(self, context, page, url, origin, login, assist):
        # A check tells whether the account and the method sign in: never the session a run or the window left.
        return await sign_in(context, page, url, login, product="JIRA", assist=assist, fresh=True,
                             arrived=lambda value: signed_in(value, origin), **self._timeouts())
