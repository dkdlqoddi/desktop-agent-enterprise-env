"""Where imported documents came from, and bringing them up to date with it.

Every import is one document with a row in its source table (db.SOURCE_TABLES): the file path or page link it came
from, what the content was (source_hash), and the text and title it saved. An import of the same path or link again
updates the document of that earlier import in place: it keeps its id, which links, tasks and answers refer to, and
takes the new text, page and title, losing the AI analysis of the old text. The same content is saved once, so an
unchanged source changes nothing; nor does a file whose bytes changed but not its text, which only records its new hash.
A document whose text the user changed since is left as it is unless the request says to replace it (`replace`, which
the document window asks before sending); a title the user gave a document stays either way. An import an earlier
version saved as several documents is made one before anything here reads it (held, parts.merge).

A file whose size and modification time are the ones its import recorded is not read again to tell it unchanged
(stored_hash); a file moved or renamed takes its documents along (relink). A page or ticket whose version the site gives
(Confluence's version number, JIRA's updated time) is not read again by a routine while that version stays (fresh). A
source whose document was purged from the trash is left out of automatic imports until the user imports it again
(purge, ignored_sources)."""

import hashlib
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

from .db import SOURCE_TABLES, identity, index_text
from .llm import UserError
from .parts import CUT_MESSAGE, merge

EDITED = (
    "가져온 뒤 본문을 고친 문서라 원본의 내용으로 바꾸지 않았습니다. 바꾸려면 문서 창에서 ‘원본에서 업데이트’를 "
    "눌러 주세요."
)
# An automatic routine never brings back a document the user put in the trash (Plan "skip", reason "trashed").
TRASHED = "휴지통에 있는 문서라 자동 작업이 가져오지 않았습니다. 휴지통에서 복원하거나 비운 뒤에는 다시 가져옵니다."
# A file whose bytes changed but not its text (Plan "keep"), by whether its HTML page changed.
KEPT = {
    "same": "원본 파일의 글이 그대로라 문서와 AI 분석을 그대로 두었습니다.",
    "format": "원본 파일의 글이 그대로라 문서와 AI 분석은 그대로 두고, 바뀐 서식·그림만 HTML 문서에 반영했습니다.",
}
# An automatic routine never brings back a source whose documents the user purged from the trash (reason "ignored").
IGNORED = "휴지통에서 영구 삭제한 문서의 원본이라 자동 작업이 가져오지 않았습니다. 다시 가져오려면 직접 가져와 주세요."
# A file whose bytes an earlier import of a path where no file is any more holds (reason "moved").
MOVED = "옮기거나 이름을 바꾼 파일이라 가져온 문서가 새 경로를 따르게 했습니다. 문서는 그대로입니다."
# A page or ticket a routine did not read again: the site says it did not change since (fresh).
UNMODIFIED = "지난번에 가져온 뒤 사이트에서 바뀌지 않아(버전·수정 시각이 같음) 다시 읽지 않았습니다."
# A page or ticket whose version stays is still read in full once in this long: what it shows can change without a new
# version (an included page, a list of tickets, the status of a linked ticket).
RECHECK = timedelta(hours=24)
# A file changed less than this long ago keeps no modification time (file_stat): file systems count time in steps of up
# to two seconds (FAT), so a change right after it could leave the time as it is.
RACY_NS = 3_000_000_000


def options(params):
    """What an import request says about updating: the document to update (`doc_id`; else the latest import of the
    same path or link) and whether text the user changed is replaced (`replace`)."""
    target = params.get("doc_id")
    if target is not None and (not isinstance(target, str) or not 0 < len(target) <= 80):
        raise UserError("업데이트할 문서(doc_id)를 확인해 주세요.")
    replace = params.get("replace", False)
    if not isinstance(replace, bool):
        raise UserError("고친 본문을 원본으로 바꿀지(replace) 확인해 주세요.")
    return target, replace


def key(table):
    """The columns that tell the same content apart: a file's bytes, or a page's text at its link."""
    return [column.strip() for column in SOURCE_TABLES[table][1].split(",")]


def held(db, table, content):
    """The source row of the document saved with `content` (values of the key's columns), or None: the title the import
    gave and the one the document has now, whether it is in the trash, and whether its text is still the one imported.
    The texts are compared where they are, as an import holds up to parts.MAX_DOCUMENT_CHARS of them. An import that an
    earlier version saved as several documents is made one first (parts.merge), so there is one row or none."""
    where = " AND ".join(f"s.{column}=?" for column in content)
    query = f"""SELECT s.doc_id, s.part, s.parts, s.title, d.title AS now_title, d.deleted_at,
        d.content != s.markdown AS changed FROM {table} s JOIN documents d ON d.id=s.doc_id
        WHERE {where} ORDER BY s.part"""
    found = db.execute(query, list(content.values())).fetchall()
    if len(found) > 1 or (found and (found[0]["part"], found[0]["parts"]) != (1, 1)):
        merge(db, table, tuple(content[column] for column in key(table)))
        found = db.execute(query, list(content.values())).fetchall()
    return found[0] if found else None


def of(db, table, ident):
    """The source row (held) of the import the document `ident` came from, or None. For a document that was a part of
    a long import of an earlier version, it is the row of the document the parts became."""
    columns = key(table)
    row = db.execute(f"SELECT {','.join(columns)} FROM {table} WHERE doc_id=?", (ident,)).fetchone()
    return held(db, table, dict(zip(columns, row, strict=True))) if row else None


def whole(db, ident):
    """The id of the document to read for `ident`: itself, or the document it became part of. A part of a long import
    of an earlier version is merged with the others first (parts.merge); one merged before is found by the note the
    merge left (merged_documents)."""
    for table in SOURCE_TABLES:
        # By the index of the rows saved in parts (db.py), which is empty in a Wiki without any.
        parted = f"SELECT 1 FROM {table} INDEXED BY idx_{table}_parted WHERE parts>1 AND doc_id=?"
        if db.execute(parted, (ident,)).fetchone():
            row = of(db, table, ident)
            if row is not None:
                return row["doc_id"]
    row = db.execute("SELECT doc_id FROM merged_documents WHERE id=?", (ident,)).fetchone()
    return row[0] if row else ident


def earlier(db, table, source, target=None):
    """The source row of the import of `source` (a path or a link) that another import of it updates: that of the
    document `target` when it came from there, else the latest. None when it was not imported before."""
    column = identity(table)
    if target:
        row = db.execute(f"SELECT {column} FROM {table} WHERE doc_id=?", (target,)).fetchone()
        if row is not None and row[0] == source:
            return of(db, table, target)
    row = db.execute(
        f"SELECT doc_id FROM {table} WHERE {column}=? ORDER BY imported_at DESC, part LIMIT 1", (source,)
    ).fetchone()
    return of(db, table, row[0]) if row else None


def edited(found):
    """Whether the user changed the text of the document (a source row, or None) since it was imported."""
    return found is not None and bool(found["changed"])


def imported_text(db, table, found):
    """The text the document `found` (a source row) was imported with."""
    return db.execute(f"SELECT markdown FROM {table} WHERE doc_id=?", (found["doc_id"],)).fetchone()[0]


def base(db, table, found, track):
    """The text that the document the import planned as `found` was imported with, when `track` asks what the source
    changed in it (change) and the source changed: an update of the document, or one skipped because the user changed
    its text. None otherwise. Read in the import's transaction; the comparison, which takes a while for a long page,
    waits until that is committed."""
    if not track or not (found.action == "update" or found.reason == "edited") or found.row is None:
        return None
    return imported_text(db, table, found.row)


def change(before, fitted):
    """What the source's new text (`fitted`) changed in the text `before` (base) its document was imported with
    (changes.compare); None when nothing changed."""
    if before is None:
        return None
    from .changes import compare

    return compare(before, fitted.text)


class Plan(NamedTuple):
    action: str  # "insert", "update", "skip" or "keep"
    row: object  # the source row of the document an update replaces, or a skipped or kept import is saved as; or None
    # skip: "same" (the content is saved), "edited" (the user changed the text) or "trashed" (the document is in the
    # trash, and an automatic routine leaves it there). update: "restore" (the source is as it was and its text
    # replaces the user's), "page" (a file an earlier version saved as Markdown) or "" (it changed).
    # keep (a file's bytes changed but not its text): "same", or "format" when its HTML page changed.
    reason: str = ""


def unchanged(db, table, previous, fitted):
    """Whether the new content `fitted` of a source is the text its document `previous` was imported with: "same", or
    "format" when only its HTML page changed (a file's formatting or pictures). None when the text changed, or the
    document is still the Markdown an earlier version saved and becomes its page by an update."""
    if previous is None or fitted is None or imported_text(db, table, previous) != fitted.text:
        return None
    if fitted.page is None:
        return "same"
    stored = db.execute("SELECT html=? FROM document_html WHERE doc_id=?", (fitted.page, previous["doc_id"])).fetchone()
    if stored is None:
        return None
    return "same" if stored[0] else "format"


def plan(db, table, source, content, target=None, replace=False, fitted=None, automatic=False, stat=None):
    """What an import of `content` (values of the table's key) from `source` does. The same content is skipped, unless
    it is the import being updated and `replace` brings back its text the user changed. Other content updates the
    earlier import of the same source, unless the user changed its text and `replace` is not set, or is new. A source
    keyed by its bytes whose new content `fitted` (when given) has the text it was imported with (a file saved again
    as it was, other line breaks, a DRM header) keeps its document and analysis and only records the new hash.
    A document of it that is in the trash comes back when the user imports it again; an `automatic` import (a routine)
    leaves it there and is skipped. So is a source whose document was purged (ignored), until the user imports it
    again. A file whose bytes an earlier import of another path holds, where no file is any more, was moved or
    renamed: that import's document follows it to `source` (relink, with the file's `stat`) and the import is skipped
    as "moved", or goes on as an update of it."""
    if automatic and ignored(db, table, source):
        return Plan("skip", None, "ignored")
    if not automatic:
        db.execute("DELETE FROM ignored_sources WHERE source_table=? AND source=?", (table, source))
    previous = earlier(db, table, source, target)
    same = held(db, table, content)
    moved = same is not None and previous is None and relink(db, table, source, same, stat)
    if moved:
        previous = earlier(db, table, source, target)
    there = same if same is not None else previous
    if there is not None and there["deleted_at"]:
        if automatic:
            return Plan("skip", there, "trashed")
        db.execute("UPDATE documents SET deleted_at=NULL WHERE id=?", (there["doc_id"],))
        previous = earlier(db, table, source, target)
        same = held(db, table, content)
    if same is not None:
        if replace and previous is not None and same["doc_id"] == previous["doc_id"] and edited(previous):
            return Plan("update", previous, "restore")
        return Plan("skip", same, "moved" if moved else "same")
    kept = unchanged(db, table, previous, fitted)
    if kept:
        return Plan("update", previous, "restore") if replace and edited(previous) else Plan("keep", previous, kept)
    if previous is not None and edited(previous) and not replace:
        return Plan("skip", previous, "edited")
    return Plan("update", previous) if previous is not None else Plan("insert", None)


def skipped(found, same):
    """The result of an import planned to be skipped, with why (`reason`, as Plan has it); `same` says that its content
    is saved already. An ignored source has no document left to name (doc_id None)."""
    messages = {"edited": EDITED, "trashed": TRASHED, "ignored": IGNORED, "moved": MOVED}
    return {
        "status": "skipped",
        "doc_id": found.row["doc_id"] if found.row is not None else None,
        "message": messages.get(found.reason, same),
        "reason": found.reason,
    }


def ignored(db, table, source):
    """Whether the document imported from `source` (a path or link) was purged from the trash (purge)."""
    return bool(
        db.execute("SELECT 1 FROM ignored_sources WHERE source_table=? AND source=?", (table, source)).fetchone()
    )


def purge(db, ids):
    """Delete the documents `ids` for good, as emptying the trash does. A source none of whose documents is left, the
    file or page they were imported from, goes into ignored_sources: automatic imports (routines) do not bring it back,
    which the purge meant to prevent; the user's own import of it does (plan). The number of documents deleted."""
    from .service import now

    ids, found, deleted = list(dict.fromkeys(ids)), set(), 0
    chunks = [ids[start : start + 500] for start in range(0, len(ids), 500)]
    for chunk in chunks:
        marks = ",".join("?" * len(chunk))
        for table in SOURCE_TABLES:
            column = identity(table)
            found.update(
                (table, row[0])
                for row in db.execute(f"SELECT DISTINCT {column} FROM {table} WHERE doc_id IN ({marks})", chunk)
            )
    for chunk in chunks:
        deleted += db.execute(f"DELETE FROM documents WHERE id IN ({','.join('?' * len(chunk))})", chunk).rowcount
    stamp = now()
    for table, source in sorted(found):
        # The source rows went with their documents; another import of the same source may still have its own.
        if not db.execute(f"SELECT 1 FROM {table} WHERE {identity(table)}=? LIMIT 1", (source,)).fetchone():
            db.execute(
                "INSERT OR REPLACE INTO ignored_sources(source_table,source,ignored_at) VALUES(?,?,?)",
                (table, source, stamp),
            )
    return deleted


def gone(path):
    """Whether no file is at `path` though its drive or network share answers: the file was moved, renamed or deleted,
    rather than kept on a drive that is away now."""
    try:
        file = Path(path)
        return not file.exists() and Path(file.anchor or os.sep).exists()
    except (OSError, ValueError):
        return False


def relink(db, table, source, found, stat=None):
    """Make the document `found` (the source row of a file's import) follow its file to `source` when no file is at
    the path it came from any more: it was moved or renamed, and its next change then updates the document instead of
    making a new one. The copies that write-backs made of the file follow too. Whether it did."""
    if identity(table) != "source_path":
        return False
    ident = found["doc_id"]
    old = db.execute(f"SELECT source_path FROM {table} WHERE doc_id=?", (ident,)).fetchone()[0]
    if old == source or not gone(old):
        return False
    values = {"source_path": source, **({"source_size": stat[0], "source_mtime": stat[1]} if stat else {})}
    db.execute(
        f"UPDATE {table} SET {','.join(f'{column}=?' for column in values)} WHERE doc_id=?", [*values.values(), ident]
    )
    db.execute("UPDATE writeback_backups SET source_path=? WHERE source_path=?", (source, old))
    return True


def touch(db, table, found, values, source):
    """Record `values` (columns of the source table: a file's size and time, a page's version and when it was read)
    with the document `found` (a source row, or None) of an import whose content was found to be the same, when it is
    that of `source`: the same bytes may have been imported from another path."""
    if found is not None and values:
        db.execute(
            f"UPDATE {table} SET {','.join(f'{column}=?' for column in values)} WHERE doc_id=? AND {identity(table)}=?",
            [*values.values(), found["doc_id"], source],
        )


def file_stat(file):
    """(size, modification time in ns) of the file at `file`, as an import records them; the time is None for a file
    changed less than RACY_NS ago, which a change right after could leave with the same time."""
    info = os.stat(file)
    return info.st_size, info.st_mtime_ns if time.time_ns() - info.st_mtime_ns > RACY_NS else None


def stored_hash(db, table, path, stat):
    """The hash the latest import of the file at `path` recorded, when the file's size and modification time (`stat`,
    as file_stat gives them) are still the ones recorded with it: the file need not be read again to be known the same.
    None otherwise, and for a file whose time file_stat does not keep."""
    size, modified = stat
    if modified is None:
        return None
    row = db.execute(
        f"""SELECT source_hash, source_size, source_mtime FROM {table} WHERE source_path=?
        ORDER BY imported_at DESC, part LIMIT 1""",
        (path,),
    ).fetchone()
    return row["source_hash"] if row and (row["source_size"], row["source_mtime"]) == (size, modified) else None


def recorded(db, table, source, column):
    """The version (Confluence's version number, JIRA's updated time) the latest import of `source` (a page's or
    ticket's link) recorded in `column`, when it read the source in full within RECHECK; '' otherwise: a routine's
    import then reads it anyway, without asking the site for its version first."""
    found = earlier(db, table, source)
    if found is None:
        return ""
    row = db.execute(f"SELECT {column}, read_at FROM {table} WHERE doc_id=?", (found["doc_id"],)).fetchone()
    return row[column] if row["read_at"] > (datetime.now(timezone.utc) - RECHECK).isoformat() else ""


def fresh(db, table, source, column, version):
    """The source row of the latest import of `source`, when the site says the source is still at the `version` that
    import recorded (recorded): a routine's import need not read it again. None otherwise."""
    return earlier(db, table, source) if version and recorded(db, table, source, column) == version else None


def unmodified(found):
    """The result of a routine's import that fresh() spared reading `found` again: skipped as the same, or as in the
    trash when its document is there (as plan has an automatic import leave it)."""
    if found["deleted_at"]:
        return skipped(Plan("skip", found, "trashed"), "")
    return {"status": "skipped", "doc_id": found["doc_id"], "message": UNMODIFIED, "reason": "same"}


def save(db, table, found, fitted, title, timestamp, values):
    """Save `fitted` as `found` plans: as a new document, or in place of the document of the earlier import. `values`
    are the source's own columns (path or link, hash, …). Returns the id of the document saved. A kept import saves no
    document (None): its source takes `values` (the new hash) and, when it changed, its HTML page."""
    if found.action == "keep":
        ident = found.row["doc_id"]
        db.execute(
            f"UPDATE {table} SET {','.join(f'{column}=?' for column in values)} WHERE doc_id=?",
            [*values.values(), ident],
        )
        if fitted.page is not None and found.reason == "format":
            db.execute("UPDATE document_html SET html=? WHERE doc_id=?", (fitted.page, ident))
        return None
    title, shown = title[:200], "markdown" if fitted.page is None else "html"
    previous = found.row if found.action == "update" else None
    if previous is None:
        ident = str(uuid.uuid4())
        db.execute(
            "INSERT INTO documents(id,title,content,format,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (ident, title, fitted.text, shown, timestamp, timestamp),
        )
        if fitted.page is not None:
            db.execute("INSERT INTO document_html(doc_id,html) VALUES(?,?)", (ident, fitted.page))
    else:
        ident = previous["doc_id"]
        # A title the user gave the document stays. Before the import kept its title, it was the one this import
        # gives, as long as the source's title is the same.
        given = previous["title"] or title
        named = previous["now_title"] if previous["now_title"] != given else title
        db.execute(
            """UPDATE documents SET title=?,content=?,format=?,summary='',status='pending',error='',
            index_mode='',revision=revision+1,updated_at=? WHERE id=?""",
            (named, fitted.text, shown, timestamp, ident),
        )
        for name in ("keywords", "relations", "doc_nodes"):
            db.execute(f"DELETE FROM {name} WHERE doc_id=?", (ident,))
        if fitted.page is None:
            db.execute("DELETE FROM document_html WHERE doc_id=?", (ident,))
        else:
            db.execute(
                "INSERT INTO document_html(doc_id,html) VALUES(?,?) "
                "ON CONFLICT(doc_id) DO UPDATE SET html=excluded.html",
                (ident, fitted.page),
            )
        db.execute(f"DELETE FROM {table} WHERE doc_id=?", (ident,))
    row = {"doc_id": ident, **values, "markdown": fitted.text, "imported_at": timestamp, "title": title}
    db.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
    index_text(db, [ident])
    return ident


def outcome(found, fitted, ident, saved):
    """What an import saved as the document `ident` tells: `saved` for a new document; for an updated one how it
    changed; and that the end of a text longer than a document holds was left out. A kept import is skipped, its
    content being the same."""
    if found.action == "keep":
        return {"status": "skipped", "doc_id": found.row["doc_id"], "message": KEPT[found.reason], "reason": "same"}
    if found.action == "insert":
        status, message = "imported", saved
    else:
        status = "updated"
        message = {
            "restore": "고친 본문 대신 원본의 내용으로 되돌렸습니다.",
            "page": "Markdown으로 가져왔던 문서를 HTML 문서로 바꿨습니다.",
        }.get(found.reason, "원본이 바뀌어 문서를 새 내용으로 업데이트했습니다.")
    if fitted.cut:
        message += " " + CUT_MESSAGE
    return {"status": status, "doc_id": ident, "message": message}


def file_state(path, checksum, limit=50 * 1024 * 1024, stat=None):
    """Whether the file at `path` still holds the bytes `checksum` it was imported with: 'same', 'changed', 'missing'
    or 'unreadable', and when it was last modified. A file larger than `limit`, the most an import reads, changed.
    `stat`, the size and modification time recorded with the import (stored_hash), spares reading a file that still
    has both."""
    file = Path(path)
    try:
        if not file.is_file():
            return {"state": "missing"}
        info = file.stat()
        modified = datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat()
        if stat and stat[1] is not None and (info.st_size, info.st_mtime_ns) == tuple(stat):
            return {"state": "same", "modified_at": modified}
        digest, size = hashlib.sha256(), 0
        with file.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    return {"state": "changed", "modified_at": modified}
                digest.update(chunk)
    except OSError:
        return {"state": "unreadable"}
    return {"state": "same" if digest.hexdigest() == checksum else "changed", "modified_at": modified}
