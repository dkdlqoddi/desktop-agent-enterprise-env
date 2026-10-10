"""JIRA ticket links, project searches and ticket documents.

jira_import.py reads tickets in the browser; this module turns a ticket into an HTML document in
memory and stores the converted Markdown, so no HTML, Markdown or attachment file is written.
"""

import hashlib
import html
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from . import sources
from .llm import UserError
from .markdown_import import html_to_markdown
from .parts import markdown_parts
from .service import now, text
from .web_login import origin_of

KEY = re.compile(r"[A-Z][A-Z0-9_]*-[0-9]+")
PROJECT = re.compile(r"[A-Z][A-Z0-9_]*")
# Ticket links, also below a context path such as /jira.
ISSUE_PATHS = [
    re.compile(rf"(?P<prefix>.*?)/browse/(?P<key>{KEY.pattern})/?", re.I),
    re.compile(rf"(?P<prefix>.*?)/projects/[^/]+/issues/(?P<key>{KEY.pattern})/?", re.I),
]
# Reserved in JIRA text search; the index drops them anyway, so they become spaces.
TEXT_RESERVED = re.compile(r'[+\-&|!(){}\[\]^~*?:\\/"]')
JAVA_OBJECT = re.compile(r"[\w.$]+@[0-9a-f]+\[")
FORMAT_ERROR = (
    "JIRA 티켓 링크 형식이 아닙니다. 예) https://<주소>/browse/PROJ-123, https://<주소>/projects/PROJ/issues/PROJ-123"
)


def import_issue(service, params):
    source_url = text(params.get("source_url"), "JIRA URL", 4096)
    url = urlsplit(source_url)
    if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password:
        raise UserError("올바른 JIRA HTTP(S) URL을 입력해 주세요.")
    issue_key = text(params.get("issue_key"), "티켓 키", 80)
    if not KEY.fullmatch(issue_key):
        raise UserError("JIRA 티켓 키가 올바르지 않습니다.")
    target, replace = sources.options(params)
    # The ticket's updated time as JIRA gave it with this read ('' when unknown), which a routine compares before it
    # reads the ticket again (jira_import.JiraImporter._unchanged).
    updated = params.get("updated") if isinstance(params.get("updated"), str) else ""
    title, content = html_to_markdown(params.get("html"), source_url)
    title = text(title, "JIRA 제목", 200)
    split = markdown_parts(content)
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    timestamp = now()
    with service.db.connect() as db:
        # Lock before duplicate detection, including concurrent internal requests.
        db.execute("BEGIN IMMEDIATE")
        found = sources.plan(
            db,
            "jira_sources",
            source_url,
            {"source_url": source_url, "source_hash": checksum},
            target,
            replace,
            automatic=params.get("track_changes") is True,
        )
        # A watching routine (routines.py) learns what the ticket changed in the text imported before.
        before = sources.base(db, "jira_sources", found, params.get("track_changes") is True)
        if found.action == "skip":
            # The text saved is this read's: its time is recorded with it, unless the user changed the document's
            # text, which a routine then still compares at every run.
            if found.reason == "same":
                sources.touch(
                    db,
                    "jira_sources",
                    found.rows,
                    {"read_at": timestamp, **({"updated": updated[:40]} if updated else {})},
                    source_url,
                )
            result = sources.skipped(found, "동일한 티켓 내용이 이미 저장되어 있습니다.")
        else:
            ids, removed = sources.save(
                db,
                "jira_sources",
                found,
                split,
                title,
                timestamp,
                {
                    "source_url": source_url,
                    "issue_key": issue_key,
                    "source_hash": checksum,
                    "updated": updated[:40],
                    "read_at": timestamp,
                },
            )
            result = sources.outcome(found, split, ids, removed, "Markdown 문서를 Wiki에 저장했습니다.", "Markdown")
    # Compared once the import is committed: a long ticket takes a while, and other saves must not wait for it.
    change = sources.change(before, split)
    return {**result, "change": change} if change else result


# ---------------------------------------------------------------------------
# Links and searches
# ---------------------------------------------------------------------------


def safe_prefix(value):
    """A JIRA context path such as /jira, used to build same-origin REST paths."""
    return (
        isinstance(value, str)
        and (value == "" or bool(re.fullmatch(r"(?:/[\w.~-]+)+", value)))
        and not re.search(r"(?:^|/)\.{1,2}(?=/|$)", value)
    )


def parse_issue_url(value):
    """(origin, context path, ticket key) of a JIRA ticket link, or None."""
    try:
        url = urlsplit(value.strip()) if isinstance(value, str) and len(value) <= 4096 else None
        port = url.port if url else None
    except ValueError:
        return None
    scheme = url.scheme.lower() if url else ""
    if scheme not in ("http", "https") or not url.hostname or "@" in url.netloc:
        return None
    host = f"[{url.hostname}]" if ":" in url.hostname else url.hostname
    origin = f"{scheme}://{host}" + (f":{port}" if port and port != {"http": 80, "https": 443}[scheme] else "")
    for pattern in ISSUE_PATHS:
        match = pattern.fullmatch(url.path)
        if match and safe_prefix(match["prefix"]):
            return origin, match["prefix"], match["key"].upper()
    return None


def ticket_page(value, origin):
    target = parse_issue_url(value)
    return target is not None and target[0] == origin


def jql_string(value):
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def project_of(value):
    """The project key a JIRA link names: /projects/KEY…, /browse/KEY, a ticket's /browse/KEY-123 or ?projectKey=KEY;
    '' for one that names none."""
    try:
        url = urlsplit(value.strip()) if isinstance(value, str) and len(value) <= 4096 else None
    except ValueError:
        return ""
    if url is None:
        return ""
    for pattern in (rf"/projects/({PROJECT.pattern})(?:/|$)", rf"/browse/({PROJECT.pattern})(?:-[0-9]+)?/?$"):
        if match := re.search(pattern, url.path, re.I):
            return match[1].upper()
    found = re.search(rf"(?:^|&)projectKey=({PROJECT.pattern})(?:&|$)", url.query, re.I)
    return found[1].upper() if found else ""


# The routes of JIRA's own pages, which follow the site's base path (context path), as APP_ROUTE in electron/sites.cjs.
APP_ROUTE = re.compile(r"/(?:browse|projects|issues|secure|rest|plugins|login\.jsp|dashboard\.jspa)(?:/|$)", re.I)


def site_of(value):
    """(origin, context path) of any link of a JIRA site, such as a project, a board or the site itself; None for one
    that is not a plain http(s) link."""
    origin = origin_of(value)
    if not origin:
        return None
    prefix = APP_ROUTE.split(urlsplit(value.strip()).path, 1)[0].rstrip("/")
    return origin, prefix if safe_prefix(prefix) else ""


# What a status is when JIRA gives it no category: names of JIRA's own workflows and common Korean ones.
STATUS_CATEGORIES = ("new", "indeterminate", "done")
NEW_STATUSES = {
    "open",
    "to do",
    "todo",
    "backlog",
    "new",
    "reopened",
    "열림",
    "할 일",
    "해야 할 일",
    "백로그",
    "신규",
    "다시 열림",
}
DONE_STATUSES = {"done", "closed", "resolved", "완료", "닫힘", "해결됨", "종료"}


def category_of(key, status):
    """The status category of an issue: new (not started), indeterminate (in progress) or done."""
    if key in STATUS_CATEGORIES:
        return key
    name = " ".join(status.split()).lower()
    return "new" if name in NEW_STATUSES else "done" if name in DONE_STATUSES else "indeterminate"


def brief(issue):
    """What the automatic routines keep of an issue a search found with REPORT_FIELDS (jira_import.py)."""
    fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
    status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
    category = status.get("statusCategory") if isinstance(status.get("statusCategory"), dict) else {}

    def stamp(name):
        return fields.get(name) if isinstance(fields.get(name), str) else ""

    return {
        "key": issue["key"],
        "summary": " ".join(str(fields.get("summary") or "").split())[:300],
        "status": name_of(status),
        "category": category_of(category.get("key"), name_of(status)),
        "assignee": person(fields.get("assignee")),
        "priority": name_of(fields.get("priority")),
        "type": name_of(fields.get("issuetype")),
        "updated": stamp("updated"),
        "created": stamp("created"),
        "due": stamp("duedate"),
        "resolved": stamp("resolutiondate"),
    }


def search_jql(project, scope, keyword="", user="", role="any"):
    """Project search for the keyword or user scope."""
    if scope == "keyword":
        words = " ".join(TEXT_RESERVED.sub(" ", keyword).split())
        if not words:
            raise UserError("검색할 키워드를 입력해 주세요. 기호만으로는 검색할 수 없습니다.")
        condition = f"text ~ {jql_string(words)}"
    else:
        name = jql_string(user)
        condition = {"assignee": f"assignee = {name}", "reporter": f"reporter = {name}"}.get(
            role, f"(assignee = {name} OR reporter = {name})"
        )
    return f"project = {jql_string(project)} AND {condition} ORDER BY key ASC"


# ---------------------------------------------------------------------------
# Ticket HTML (built in memory)
# ---------------------------------------------------------------------------


def name_of(value):
    return str(value.get("name") or "") if isinstance(value, dict) else ""


def person(value):
    return str(value.get("displayName") or value.get("name") or "") if isinstance(value, dict) else ""


def fmt_date(value):
    """2024-01-15T10:23:45.000+0900 → 2024-01-15 10:23"""
    return value[:16].replace("T", " ") if isinstance(value, str) else ""


def listing(values):
    """Labels, components and versions as one comma-separated line."""
    return (
        ", ".join(filter(None, (v if isinstance(v, str) else name_of(v) for v in values or [])))
        if isinstance(values, list)
        else ""
    )


def size_text(value):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return ""
    return f"{value / 1024 / 1024:.1f}MB" if value >= 1024 * 1024 else f"{max(1, round(value / 1024))}KB"


def display(value, nested=False):
    """Readable text of option, user, version and number custom fields; '' for other values.
    Top-level strings without a rendered form are system values such as ranks."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if isinstance(value, str):
        return value if nested and not JAVA_OBJECT.match(value) else ""
    if isinstance(value, dict):
        label = value.get("displayName") or value.get("name") or value.get("value") or ""
        label = label if isinstance(label, str) else ""
        child = display(value.get("child"), True) if isinstance(value.get("child"), dict) else ""
        return f"{label} › {child}" if label and child else label
    if isinstance(value, list):
        return ", ".join(filter(None, (display(item, True) for item in value)))
    return ""


def fragment(markup, offset):
    """JIRA-rendered HTML with headings kept below the document's own section headings."""
    soup = BeautifulSoup(markup, "html.parser")
    for level in range(6, 0, -1):
        for tag in soup.find_all(f"h{level}"):
            tag.name = f"h{min(level + offset, 6)}"
    return str(soup)


def filled(value):
    return isinstance(value, str) and bool(value.strip())


def rich(rendered, raw, offset, empty):
    """Rendered HTML when JIRA provides it, otherwise the original text."""
    if filled(rendered):
        return fragment(rendered, offset)
    if filled(raw):
        return "<p>" + html.escape(raw).replace("\n", "<br>") + "</p>"
    return f"<p>{empty}</p>"


def custom_fields(fields, rendered, names):
    items = []
    for field_id, raw in fields.items():
        if not field_id.startswith("customfield_") or raw in (None, "", [], {}):
            continue
        value = rendered.get(field_id)
        if filled(value):
            body = fragment(value, 3)
        elif label := display(raw):
            body = f"<p>{html.escape(label)}</p>"
        else:
            continue
        items.append((str(names.get(field_id) or field_id), body))
    return sorted(items)


def linked_issues(fields, base_url):
    def item(label, target):
        if not isinstance(target, dict) or not KEY.fullmatch(str(target.get("key") or "")):
            return None
        info = target.get("fields") if isinstance(target.get("fields"), dict) else {}
        status = name_of(info.get("status"))
        return (
            f'{html.escape(label)}: <a href="{html.escape(base_url)}/browse/{target["key"]}">{target["key"]}</a> '
            + html.escape(str(info.get("summary") or ""))
            + (f" ({html.escape(status)})" if status else "")
        )

    items = [item("상위 티켓", fields.get("parent"))]
    items += [item("하위 작업", sub) for sub in fields.get("subtasks") or []]
    for link in fields.get("issuelinks") or []:
        kind = link.get("type") if isinstance(link, dict) and isinstance(link.get("type"), dict) else {}
        for side, name in (("outwardIssue", "outward"), ("inwardIssue", "inward")):
            if isinstance(link, dict) and side in link:
                items.append(item(str(kind.get(name) or kind.get("name") or "연결"), link[side]))
    return [entry for entry in items if entry]


def attachment_item(attachment):
    name = html.escape(str(attachment.get("filename") or "첨부 파일"))
    url = attachment.get("content") if isinstance(attachment.get("content"), str) else ""
    about = ", ".join(
        filter(
            None,
            (size_text(attachment.get("size")), person(attachment.get("author")), fmt_date(attachment.get("created"))),
        )
    )
    link = f'<a href="{html.escape(url)}">{name}</a>' if url.startswith(("http://", "https://")) else name
    return f"<li>{link}{f' ({html.escape(about)})' if about else ''}</li>"


def comment_item(comment, rendered_body):
    return {
        "author": person(comment.get("author")) or "(알 수 없음)",
        "created": fmt_date(comment.get("created")),
        "html": rich(rendered_body, comment.get("body"), 3, "(내용 없음)"),
    }


def build_html(issue, base_url, comments, total=None):
    """Ticket document for the Wiki; attachments stay links to JIRA and are never downloaded."""
    e = html.escape
    key = issue["key"]
    fields = issue.get("fields") or {}
    rendered = issue.get("renderedFields") if isinstance(issue.get("renderedFields"), dict) else {}
    source = f"{base_url}/browse/{key}"
    title = f"[{key}] {fields.get('summary') or ''}".strip()
    project = fields.get("project") if isinstance(fields.get("project"), dict) else {}
    info = [
        ("프로젝트", " · ".join(filter(None, (str(project.get("key") or ""), str(project.get("name") or ""))))),
        ("유형", name_of(fields.get("issuetype"))),
        ("상태", name_of(fields.get("status"))),
        ("우선순위", name_of(fields.get("priority"))),
        ("해결 상태", name_of(fields.get("resolution"))),
        ("담당자", person(fields.get("assignee")) or "미지정"),
        ("보고자", person(fields.get("reporter"))),
        ("생성일", fmt_date(fields.get("created"))),
        ("수정일", fmt_date(fields.get("updated"))),
        ("해결일", fmt_date(fields.get("resolutiondate"))),
        ("기한", fields.get("duedate") if isinstance(fields.get("duedate"), str) else ""),
        ("레이블", listing(fields.get("labels"))),
        ("컴포넌트", listing(fields.get("components"))),
        ("영향 버전", listing(fields.get("versions"))),
        ("수정 버전", listing(fields.get("fixVersions"))),
    ]
    parts = [
        f"<h1>{e(title)}</h1>",
        f'<p>출처: <a href="{e(source)}">{e(source)}</a></p>',
        "<h2>기본 정보</h2><table><tr><th>항목</th><th>내용</th></tr>"
        + "".join(f"<tr><td>{e(label)}</td><td>{e(value)}</td></tr>" for label, value in info if value)
        + "</table>",
        "<h2>설명</h2>" + rich(rendered.get("description"), fields.get("description"), 2, "(설명 없음)"),
    ]
    if filled(rendered.get("environment")) or filled(fields.get("environment")):
        parts.append("<h2>환경</h2>" + rich(rendered.get("environment"), fields.get("environment"), 2, ""))
    if extra := custom_fields(fields, rendered, issue.get("names") if isinstance(issue.get("names"), dict) else {}):
        parts.append("<h2>추가 필드</h2>" + "".join(f"<h3>{e(name)}</h3>{body}" for name, body in extra))
    if links := linked_issues(fields, base_url):
        parts.append("<h2>연결된 티켓</h2><ul>" + "".join(f"<li>{item}</li>" for item in links) + "</ul>")
    attachments = [a for a in fields.get("attachment") or [] if isinstance(a, dict)]
    if attachments:
        parts.append(
            f"<h2>첨부 파일 ({len(attachments)})</h2><ul>" + "".join(map(attachment_item, attachments)) + "</ul>"
        )
    parts.append(
        f"<h2>댓글 ({len(comments)})</h2>"
        + (
            "".join(
                f"<h3>{e(c['author'])}{' · ' + e(c['created']) if c['created'] else ''}</h3>{c['html']}"
                for c in comments
            )
            or "<p>(댓글 없음)</p>"
        )
    )
    if isinstance(total, int) and total > len(comments):
        parts.append(f"<blockquote>전체 댓글 {total:,}개 중 {len(comments):,}개만 가져왔습니다.</blockquote>")
    document = (
        f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>{e(title[:200])}</title>'
        f"</head><body>{''.join(parts)}</body></html>"
    )
    return source, document
