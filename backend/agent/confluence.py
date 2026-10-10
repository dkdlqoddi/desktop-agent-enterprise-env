"""Import browser captures as Markdown without intermediate files."""

import hashlib
from urllib.parse import urlsplit

from . import sources
from .llm import UserError
from .markdown_import import html_to_markdown
from .parts import markdown_parts
from .service import now, text


def import_page(service, params):
    source_url = text(params.get("source_url"), "Confluence URL", 4096)
    url = urlsplit(source_url)
    if url.scheme not in ("http", "https") or not url.netloc or url.username or url.password:
        raise UserError("올바른 Confluence HTTP(S) URL을 입력해 주세요.")
    page_id = text(params.get("page_id"), "페이지 ID", 80)
    if not page_id.isascii() or not page_id.isdigit():
        raise UserError("Confluence 페이지 ID가 올바르지 않습니다.")
    target, replace = sources.options(params)
    # The page's version number as Confluence gave it with this capture ('' when unknown), which a routine compares
    # before it reads the page again (confluence_import.ConfluenceImporter._unchanged).
    version = params.get("version") if isinstance(params.get("version"), str) else ""
    title, content = html_to_markdown(params.get("html"), source_url)
    # Confluence allows titles of 255 characters; the Wiki keeps 200 of them, as for JIRA tickets.
    title = text(title[:200], "Confluence 제목", 200)
    split = markdown_parts(content)
    checksum = hashlib.sha256(content.encode("utf-8")).hexdigest()
    timestamp = now()
    with service.db.connect() as db:
        # Lock before duplicate detection, including concurrent internal requests.
        db.execute("BEGIN IMMEDIATE")
        found = sources.plan(
            db,
            "confluence_sources",
            source_url,
            {"source_url": source_url, "source_hash": checksum},
            target,
            replace,
            automatic=params.get("track_changes") is True,
        )
        # A watching routine (routines.py) learns what the page changed in the text imported before.
        before = sources.base(db, "confluence_sources", found, params.get("track_changes") is True)
        if found.action == "skip":
            # The text saved is this version's: it is recorded with it, unless the user changed the document's text,
            # which a routine then still compares at every run.
            if found.reason == "same":
                sources.touch(
                    db,
                    "confluence_sources",
                    found.rows,
                    {"read_at": timestamp, **({"version": version[:40]} if version else {})},
                    source_url,
                )
            result = sources.skipped(found, "동일한 페이지 내용이 이미 저장되어 있습니다.")
        else:
            ids, removed = sources.save(
                db,
                "confluence_sources",
                found,
                split,
                title,
                timestamp,
                {
                    "source_url": source_url,
                    "page_id": page_id,
                    "source_hash": checksum,
                    "version": version[:40],
                    "read_at": timestamp,
                },
            )
            result = sources.outcome(found, split, ids, removed, "Markdown 문서를 Wiki에 저장했습니다.", "Markdown")
    # Compared once the import is committed: a long page takes a while, and other saves must not wait for it.
    change = sources.change(before, split)
    return {**result, "change": change} if change else result
