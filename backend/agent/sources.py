"""Where imported documents came from, and bringing them up to date with it.

Every import keeps a row per document in its source table (db.SOURCE_TABLES): the file path or page link it came from,
what the content was (source_hash), and the text and title it saved. An import of the same path or link again updates
the documents of that earlier import in place: they keep their ids, which links, tasks and answers refer to, and take the
new text, page and title, losing the AI analysis of the old text. The same content is saved once, so an unchanged source
changes nothing; nor does a file whose bytes changed but not its text, which only records its new hash. Documents whose
text the user changed since are left as they are unless the request says to replace it (`replace`, which the document
window asks before sending); a title the user gave a document stays either way."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from .db import SOURCE_TABLES, identity, index_text
from .llm import UserError
from .parts import imported, insert, joined, part_title, saved_message

EDITED = ("가져온 뒤 본문을 고친 문서라 원본의 내용으로 바꾸지 않았습니다. 바꾸려면 문서 창에서 ‘원본에서 업데이트’를 "
          "눌러 주세요.")
# An automatic routine never brings back a document the user put in the trash (Plan "skip", reason "trashed").
TRASHED = "휴지통에 있는 문서라 자동 작업이 가져오지 않았습니다. 휴지통에서 복원하거나 비운 뒤에는 다시 가져옵니다."
# A file whose bytes changed but not its text (Plan "keep"), by whether its HTML page changed.
KEPT = {"same": "원본 파일의 글이 그대로라 문서와 AI 분석을 그대로 두었습니다.",
        "format": "원본 파일의 글이 그대로라 문서와 AI 분석은 그대로 두고, 바뀐 서식·그림만 HTML 문서에 반영했습니다."}


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


def rows(db, table, content):
    """The documents saved with `content` (values of the key's columns), by part: the title the import gave and the one
    they have now, and whether their text is still the one imported. The texts are compared where they are, as a long
    import holds up to 100 documents of them."""
    where = " AND ".join(f"s.{column}=?" for column in content)
    return db.execute(f"""SELECT s.doc_id, s.part, s.parts, s.title, d.title AS now_title, d.deleted_at,
        d.content != s.markdown AS changed FROM {table} s JOIN documents d ON d.id=s.doc_id
        WHERE {where} ORDER BY s.part""", list(content.values())).fetchall()


def group(db, table, ident):
    """The documents of the import the document `ident` belongs to, by part: itself, or every part of a long one."""
    columns = key(table)
    row = db.execute(f"SELECT {','.join(columns)} FROM {table} WHERE doc_id=?", (ident,)).fetchone()
    return rows(db, table, dict(zip(columns, row))) if row else []


def earlier(db, table, source, target=None):
    """The import of `source` (a path or a link) that another import of it updates: that of the document `target` when
    it came from there, else the latest."""
    column = identity(table)
    if target:
        row = db.execute(f"SELECT {column} FROM {table} WHERE doc_id=?", (target,)).fetchone()
        if row is not None and row[0] == source:
            return group(db, table, target)
    row = db.execute(f"SELECT doc_id FROM {table} WHERE {column}=? ORDER BY imported_at DESC, part LIMIT 1",
                     (source,)).fetchone()
    return group(db, table, row[0]) if row else []


def edited(found):
    """Whether the user changed the text of any of the documents since it was imported."""
    return any(row["changed"] for row in found)


def texts(db, table, found):
    """The texts the documents `found` (rows by part) were imported with, by part."""
    ids = [row["doc_id"] for row in found]
    return [row[0] for row in db.execute(
        f"SELECT markdown FROM {table} WHERE doc_id IN ({','.join('?' * len(ids))}) ORDER BY part", ids)] if ids else []


def base(db, table, found, track):
    """The texts, by part, that the documents the import planned as `found` were imported with, when `track` asks what
    the source changed in them (change) and the source changed: an update of the documents, or one skipped because the
    user changed their text. None otherwise. Read in the import's transaction; the comparison, which takes a while for
    a long page, waits until that is committed."""
    if not track or not (found.action == "update" or found.reason == "edited") or not found.rows:
        return None
    return texts(db, table, found.rows) or None


def change(before, split):
    """What the source's new text (`split`) changed in the texts `before` (base) its documents were imported with
    (changes.compare), both as one text without what each part repeats; None when nothing changed."""
    if before is None:
        return None
    from .changes import compare
    return compare(joined(before), joined([text for _, text in split.parts]))


class Plan(NamedTuple):
    action: str  # "insert", "update", "skip" or "keep"
    rows: list  # the documents an update replaces, or those a skipped or kept import's content is saved as
    # skip: "same" (the content is saved), "edited" (the user changed the text) or "trashed" (the documents are in the
    # trash, and an automatic routine leaves them there). update: "restore" (the source is as it was and its text
    # replaces the user's), "page" (a file an earlier version saved as Markdown) or "" (it changed).
    # keep (a file's bytes changed but not its text): "same", or "format" when its HTML page changed.
    reason: str = ""


def unchanged(db, table, previous, split):
    """Whether the new content `split` of a source is the text its documents `previous` were imported with: "same",
    or "format" when only the HTML page of a part changed (a file's formatting or pictures). None when the text changed,
    or a document of it is still the Markdown an earlier version saved and becomes its page by an update."""
    if not previous or split is None or texts(db, table, previous) != [text for _, text in split.parts]:
        return None
    reason = "same"
    for row, (page, _) in zip(previous, split.parts):
        if page is not None:
            stored = db.execute("SELECT html=? FROM document_html WHERE doc_id=?", (page, row["doc_id"])).fetchone()
            if stored is None:
                return None
            reason = reason if stored[0] else "format"
    return reason


def plan(db, table, source, content, target=None, replace=False, split=None, automatic=False):
    """What an import of `content` (values of the table's key) from `source` does. The same content is skipped, unless
    it is the import being updated and `replace` brings back its text the user changed. Other content updates the
    earlier import of the same source, unless the user changed its text and `replace` is not set, or is new. A source
    keyed by its bytes whose new content `split` (when given) has the text it was imported with (a file saved again
    as it was, other line breaks, a DRM header) keeps its documents and their analysis and only records the new hash.
    Documents of it that are in the trash come back when the user imports it again; an `automatic` import (a routine)
    leaves them there and is skipped."""
    previous = earlier(db, table, source, target)
    same = rows(db, table, content)
    trashed = [row for row in (same or previous) if row["deleted_at"]]
    if trashed:
        if automatic:
            return Plan("skip", trashed, "trashed")
        ids = [row["doc_id"] for row in trashed]
        db.execute(f"UPDATE documents SET deleted_at=NULL WHERE id IN ({','.join('?' * len(ids))})", ids)
        previous = earlier(db, table, source, target)
        same = rows(db, table, content)
    if same:
        if replace and previous and same[0]["doc_id"] in {row["doc_id"] for row in previous} and edited(previous):
            return Plan("update", previous, "restore")
        return Plan("skip", same, "same")
    kept = unchanged(db, table, previous, split)
    if kept:
        return Plan("update", previous, "restore") if replace and edited(previous) else Plan("keep", previous, kept)
    if previous and edited(previous) and not replace:
        return Plan("skip", previous, "edited")
    return Plan("update", previous) if previous else Plan("insert", [])


def skipped(found, same):
    """The result of an import planned to be skipped, with why (`reason`, as Plan has it); `same` says that its content
    is saved already."""
    return {"status": "skipped", "doc_id": found.rows[0]["doc_id"],
            "message": {"edited": EDITED, "trashed": TRASHED}.get(found.reason, same), "reason": found.reason}


def save(db, table, found, split, title, timestamp, values):
    """Save `split` as `found` plans: as new documents, or in place of the documents of the earlier import, part by
    part, adding or deleting documents when the number of parts changed. `values` are the source's own columns (path
    or link, hash, …). Returns the ids of the documents saved, by part, and how many were deleted. A kept import saves
    no document: its sources take `values` (the new hash) and, when they changed, its parts their HTML pages."""
    count = len(split.parts)
    if found.action == "keep":
        for row, (page, _) in zip(found.rows, split.parts):
            db.execute(f"UPDATE {table} SET {','.join(f'{column}=?' for column in values)} WHERE doc_id=?",
                       [*values.values(), row["doc_id"]])
            if page is not None and found.reason == "format":
                db.execute("UPDATE document_html SET html=? WHERE doc_id=?", (page, row["doc_id"]))
        return [], 0

    def source(ident, number, text):
        row = {"doc_id": ident, **values, "markdown": text, "imported_at": timestamp, "part": number, "parts": count,
               "title": part_title(title, number, count)}
        db.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))

    previous = found.rows if found.action == "update" else []
    ids = []
    for row, (number, (page, text)) in zip(previous, enumerate(split.parts, 1)):
        ident = row["doc_id"]
        # A title the user gave the document stays. Before the import kept its title, it was the one this import
        # gives, as long as the source's title is the same.
        given = row["title"] or part_title(title, row["part"], row["parts"])
        named = row["now_title"] if row["now_title"] != given else part_title(title, number, count)
        db.execute("""UPDATE documents SET title=?,content=?,format=?,summary='',status='pending',error='',
            index_mode='',revision=revision+1,updated_at=? WHERE id=?""",
                   (named, text, "markdown" if page is None else "html", timestamp, ident))
        for name in ("keywords", "relations", "doc_nodes"):
            db.execute(f"DELETE FROM {name} WHERE doc_id=?", (ident,))
        if page is None:
            db.execute("DELETE FROM document_html WHERE doc_id=?", (ident,))
        else:
            db.execute("INSERT INTO document_html(doc_id,html) VALUES(?,?) "
                       "ON CONFLICT(doc_id) DO UPDATE SET html=excluded.html", (ident, page))
        db.execute(f"DELETE FROM {table} WHERE doc_id=?", (ident,))
        source(ident, number, text)
        ids.append(ident)
    ids += insert(db, split, title, timestamp, source, first=len(ids) + 1)
    removed = [row["doc_id"] for row in previous[count:]]
    for ident in removed:
        db.execute("DELETE FROM documents WHERE id=?", (ident,))
    index_text(db, ids)
    return ids, len(removed)


def outcome(found, split, ids, removed, saved, kind):
    """What an import saved as `ids` tells: `saved` for one new document; for updated ones how they changed. `kind` is
    the kind of document (HTML, Markdown). A kept import is skipped, its content being the same."""
    if found.action == "keep":
        return {"status": "skipped", "doc_id": found.rows[0]["doc_id"], "message": KEPT[found.reason], "reason": "same"}
    if found.action == "insert":
        return imported(split, ids, saved, kind)
    message = {"restore": "고친 본문 대신 원본의 내용으로 되돌렸습니다.",
               "page": "Markdown으로 가져왔던 문서를 HTML 문서로 바꿨습니다."}.get(
        found.reason, "원본이 바뀌어 문서를 새 내용으로 업데이트했습니다.")
    if len(ids) > 1:
        message += " " + saved_message(split, kind)
    if removed:
        message += f" 글이 줄어 남는 부분 문서 {removed}개는 지웠습니다."
    return {"status": "updated", "doc_id": ids[0], "doc_ids": ids, "message": message}


def file_state(path, checksum, limit=50 * 1024 * 1024):
    """Whether the file at `path` still holds the bytes `checksum` it was imported with: 'same', 'changed', 'missing'
    or 'unreadable', and when it was last modified. A file larger than `limit`, the most an import reads, changed."""
    file = Path(path)
    try:
        if not file.is_file():
            return {"state": "missing"}
        modified = datetime.fromtimestamp(file.stat().st_mtime, timezone.utc).isoformat()
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
