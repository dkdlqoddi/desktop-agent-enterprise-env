"""Pages of the document list, the search results and the conversations, and the filters the documents share.

wiki.list, wiki.search and assistant.conversations with `limit` (or a `cursor`) answer one page and next_cursor, an
opaque string the next request sends back as `cursor` to go on after the page's last entry; it is null after the last
page. The cursor holds the keys the list is ordered by (keyset paging), so a document saved meanwhile neither repeats an
entry nor drops one. Without `limit` and `cursor` each answers as it always did.

On a page an import too long for one document (parts.py) is one entry: the first of its documents in the order of the
page, with the import's live documents in `parts`, as a document window gets them (Service.document)."""
import base64
import binascii
import json

from .db import SOURCE_TABLES
from .llm import UserError

# The import sources a document list filters by (service.SOURCES), and the documents without one.
SOURCE_KINDS = ("manual", "rtf", "office", "image", "text", "markdown", "confluence", "jira")
# A document's AI analysis as the document list shows it (electron/api-catalog.cjs analysisState).
ANALYSIS = {
    "running": "{d}.status='indexing'",
    "analyzed": "{d}.status!='indexing' AND {d}.index_mode!=''",
    "failed": "{d}.status='error' AND {d}.index_mode=''",
    "not_analyzed": "{d}.status NOT IN ('indexing','error') AND {d}.index_mode=''",
}
CURSOR = "목록을 이어 받을 위치(cursor)가 올바르지 않습니다. 처음부터 다시 불러와 주세요."


def wanted(p):
    """Whether a request asks for a page: it names a `limit` or a `cursor`."""
    return p.get("limit") is not None or p.get("cursor") is not None


def size(p, what, maximum, default=50):
    """How many entries a page holds: `limit`, or `default` when only a cursor is given."""
    value = p.get("limit")
    if value is None:
        return default
    if type(value) is not int or not 1 <= value <= maximum:
        raise UserError(f"{what} 한 번에 1~{maximum:,}개까지 불러올 수 있습니다.")
    return value


def encode(kind, *keys):
    """The cursor after an entry with these sort keys, of a list of `kind`."""
    raw = json.dumps([kind, *keys], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(p, kind, types):
    """The sort keys a request's cursor continues after, checked against the list's `kind` and key types; None
    without a cursor. A cursor of another list, or one changed by hand, is refused rather than guessed at."""
    value = p.get("cursor")
    if value is None:
        return None
    try:
        if not isinstance(value, str) or not 0 < len(value) <= 2000:
            raise ValueError()
        found = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8"))
        if not isinstance(found, list) or len(found) != len(types) + 1 or found[0] != kind:
            raise ValueError()
        if any(isinstance(key, bool) or not isinstance(key, expected) for key, expected in zip(found[1:], types)):
            raise ValueError()
    except (ValueError, TypeError, RecursionError, binascii.Error):
        raise UserError(CURSOR) from None
    return found[1:]


def period(p):
    """The update time range a request filters by: updated_from (included) and updated_to (left out), as the UTC
    times the Wiki keeps (service.now), or None for an end it leaves open."""
    from .assistant import timestamp
    return (timestamp(p.get("updated_from"), "수정 시각 시작(updated_from)"),
            timestamp(p.get("updated_to"), "수정 시각 끝(updated_to)"))


class Filters:
    """What a document list or search keeps: documents of one import source (or written by hand, `manual`), in one
    state of AI analysis, and updated within a time range."""

    def __init__(self, source, analysis, since, until):
        self.source, self.analysis, self.since, self.until = source, analysis, since, until

    @classmethod
    def of(cls, p):
        """The filters a request names, or None for none."""
        source, analysis = p.get("source"), p.get("analysis")
        if source is not None and source not in SOURCE_KINDS:
            raise UserError(f"출처(source)는 {', '.join(SOURCE_KINDS)} 중 하나로 입력해 주세요.")
        if analysis is not None and analysis not in ANALYSIS:
            raise UserError(f"AI 분석 여부(analysis)는 {', '.join(ANALYSIS)} 중 하나로 입력해 주세요.")
        since, until = period(p)
        return cls(source, analysis, since, until) if source or analysis or since or until else None

    def sql(self, d="d"):
        """An SQL condition on the documents aliased `d`, with its arguments."""
        from .service import SOURCES
        clauses, args = [], []
        if self.source:
            # The source a document shows is the first it has, in the order of service.SOURCES (Service.attach).
            tables = [table for table, _, _ in SOURCES]
            kinds = [kind for _, kind, _ in SOURCES]
            index = len(tables) if self.source == "manual" else kinds.index(self.source)
            clauses += [f"NOT EXISTS (SELECT 1 FROM {table} WHERE doc_id={d}.id)" for table in tables[:index]]
            if self.source != "manual":
                clauses.append(f"EXISTS (SELECT 1 FROM {tables[index]} WHERE doc_id={d}.id)")
        if self.analysis:
            clauses.append(ANALYSIS[self.analysis].format(d=d))
        if self.since:
            clauses.append(f"{d}.updated_at>=?")
            args.append(self.since)
        if self.until:
            clauses.append(f"{d}.updated_at<?")
            args.append(self.until)
        return " AND ".join(f"({clause})" for clause in clauses) or "1", args


def imports(db, ids):
    """The imports saved in parts that the documents `ids` belong to: {id: (source table, the values of its key)}."""
    from .search import chunks
    from .service import SOURCES
    found = {}
    for table, _, _ in SOURCES:
        columns = SOURCE_TABLES[table][1]
        for chunk in chunks(ids):
            for row in db.execute(f"SELECT doc_id,{columns} FROM {table} WHERE doc_id IN ({','.join('?' * len(chunk))}) "
                                  "AND parts>1", chunk):
                found.setdefault(row[0], (table, tuple(row)[1:]))
    return found


def members(db, table, key, filters=None):
    """The live documents of an import, by part: id, part, title, updated_at, and whether each passes `filters`. They
    are found by the import's key; the + keeps SQLite from finding the live documents first (search.LIVE_ROW)."""
    where = " AND ".join(f"s.{column.strip()}=?" for column in SOURCE_TABLES[table][1].split(","))
    test, args = filters.sql("d") if filters else ("1", [])
    return db.execute(f"""SELECT s.doc_id AS id, s.part, d.title, d.updated_at, ({test}) AS passes FROM {table} s
        JOIN documents d ON d.id=s.doc_id WHERE {where} AND +d.deleted_at IS NULL ORDER BY s.part""",
                      [*args, *key]).fetchall()


def parts(found):
    """The `parts` of a page entry, as Service.document lists them."""
    return [{"id": row["id"], "title": row["title"], "part": row["part"]} for row in found]
