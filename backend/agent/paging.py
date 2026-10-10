"""Pages of the document list, the search results and the conversations, and the filters the documents share.

wiki.list, wiki.search and assistant.conversations with `limit` (or a `cursor`) answer one page and next_cursor, an
opaque string the next request sends back as `cursor` to go on after the page's last entry; it is null after the last
page. The cursor holds the keys the list is ordered by (keyset paging), so a document saved meanwhile neither repeats an
entry nor drops one. Without `limit` and `cursor` each answers as it always did. A page of the document list or of a
search also says how many documents its filters keep in all (`total`), which a screen shows rather than counting the
pages it has.

On a page an import too long for one document (parts.py) is one entry: the first of its documents in the order of the
page, with the import's live documents in `parts`, as a document window gets them (Service.document)."""

import base64
import binascii
import json

from .db import SOURCE_TABLES, search_text
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
# The columns of the source tables that say where a document came from, which the document list finds it by: a file's
# path, a page's address, a ticket's key.
WHERE_FROM = ("source_path", "source_url", "issue_key")
# The longest text the document list is asked to find (`query`).
QUERY_CHARS = 200


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
        if any(
            isinstance(key, bool) or not isinstance(key, expected)
            for key, expected in zip(found[1:], types, strict=True)
        ):
            raise ValueError()
    except (ValueError, TypeError, RecursionError, binascii.Error):
        raise UserError(CURSOR) from None
    return found[1:]


def period(p):
    """The update time range a request filters by: updated_from (included) and updated_to (left out), as the UTC
    times the Wiki keeps (service.now), or None for an end it leaves open."""
    from .assistant import timestamp

    return (
        timestamp(p.get("updated_from"), "수정 시각 시작(updated_from)"),
        timestamp(p.get("updated_to"), "수정 시각 끝(updated_to)"),
    )


class Filters:
    """What a document list or search keeps: documents of one import source (or written by hand, `manual`), in one
    state of AI analysis, and updated within a time range. The document list (wiki.list) also keeps only the documents
    holding a text (`query`) in their title, summary, keywords or the keywords' aliases, or in where they came from (a
    file's path, a page's address, a ticket's key), and only those with a keyword (`keyword`), or with the keyword an
    alias names: a search's own query is what it looks for, never one of these."""

    def __init__(self, source, analysis, since, until, text="", keyword=""):
        self.source, self.analysis, self.since, self.until = source, analysis, since, until
        self.text, self.keyword = text, keyword

    @classmethod
    def of(cls, p, listing=False):
        """The filters a request names, or None for none; `listing`: those of the document list, `query` and `keyword`
        among them."""
        source, analysis = p.get("source"), p.get("analysis")
        if source is not None and source not in SOURCE_KINDS:
            raise UserError(f"출처(source)는 {', '.join(SOURCE_KINDS)} 중 하나로 입력해 주세요.")
        if analysis is not None and analysis not in ANALYSIS:
            raise UserError(f"AI 분석 여부(analysis)는 {', '.join(ANALYSIS)} 중 하나로 입력해 주세요.")
        since, until = period(p)
        text = keyword = ""
        if listing:
            text, keyword = found_text(p.get("query")), found_keyword(p.get("keyword"))
        if source or analysis or since or until or text or keyword:
            return cls(source, analysis, since, until, text, keyword)
        return None

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
        if self.text:
            clause, found = self.holding(d)
            clauses.append(clause)
            args += found
        if self.keyword:
            from .search import fold

            # An alias whose keyword a program not keeping the foreign keys took away names nothing (search.ALIASES).
            clauses.append(
                f"""EXISTS (SELECT 1 FROM keywords WHERE doc_id={d}.id AND keyword=?) OR EXISTS (SELECT 1 FROM
                keyword_aliases a JOIN keywords k ON k.doc_id=a.doc_id AND k.keyword=a.keyword
                WHERE a.doc_id={d}.id AND a.normalized=?)"""
            )
            args += [self.keyword, fold(self.keyword)]
        return " AND ".join(f"({clause})" for clause in clauses) or "1", args

    def holding(self, d):
        """The condition of `query`: the text within the title or summary as search compares text (search_text: case,
        full width and decomposed Hangul do not count), within a keyword or an alias of one, or within where the
        document came from, as typed (LIKE, which leaves the case of Latin letters out; the backslashes of a Windows
        path stay what they are)."""
        from .service import SOURCES, like

        folded, pattern = search_text(self.text), like(self.text)
        clauses = [
            f"instr(search_text({d}.title), ?)",
            f"instr(search_text({d}.summary), ?)",
            f"EXISTS (SELECT 1 FROM keywords WHERE doc_id={d}.id AND instr(keyword, ?))",
            f"""EXISTS (SELECT 1 FROM keyword_aliases a JOIN keywords k ON k.doc_id=a.doc_id AND k.keyword=a.keyword
                WHERE a.doc_id={d}.id AND instr(a.normalized, ?))""",
        ]
        args = [folded] * len(clauses)
        for table, _, _ in SOURCES:
            columns = [c for c in WHERE_FROM if c in SOURCE_TABLES[table][0]]
            matches = " OR ".join(f"{column} LIKE ? ESCAPE '\\'" for column in columns)
            clauses.append(f"EXISTS (SELECT 1 FROM {table} WHERE doc_id={d}.id AND ({matches}))")
            args += [pattern] * len(columns)
        return " OR ".join(clauses), args


def found_text(value):
    """The text a document list is asked to find (`query`), trimmed; "" for none."""
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > QUERY_CHARS:
        raise UserError(f"목록에서 찾을 글(query)은 {QUERY_CHARS}자 이내로 입력해 주세요.")
    return value.strip()


def found_keyword(value):
    """The keyword a document list keeps (`keyword`) as the Wiki keeps keywords; "" for none."""
    from .service import keyword

    if value is None or (isinstance(value, str) and not value.strip()):
        return ""
    if not isinstance(value, str):
        raise UserError("키워드(keyword)는 글로 입력해 주세요.")
    return keyword(value)


def imports(db, ids):
    """The imports saved in parts that the documents `ids` belong to: {id: (source table, the values of its key)}."""
    from .search import chunks
    from .service import SOURCES

    found = {}
    for table, _, _ in SOURCES:
        columns = SOURCE_TABLES[table][1]
        for chunk in chunks(ids):
            for row in db.execute(
                f"SELECT doc_id,{columns} FROM {table} WHERE doc_id IN ({','.join('?' * len(chunk))}) AND parts>1",
                chunk,
            ):
                found.setdefault(row[0], (table, tuple(row)[1:]))
    return found


def members(db, table, key, filters=None):
    """The live documents of an import, by part: id, part, title, updated_at, and whether each passes `filters`. They
    are found by the import's key; the + keeps SQLite from finding the live documents first (search.LIVE_ROW)."""
    where = " AND ".join(f"s.{column.strip()}=?" for column in SOURCE_TABLES[table][1].split(","))
    test, args = filters.sql("d") if filters else ("1", [])
    return db.execute(
        f"""SELECT s.doc_id AS id, s.part, d.title, d.updated_at, ({test}) AS passes FROM {table} s
        JOIN documents d ON d.id=s.doc_id WHERE {where} AND +d.deleted_at IS NULL ORDER BY s.part""",
        [*args, *key],
    ).fetchall()


def parts(found):
    """The `parts` of a page entry, as Service.document lists them."""
    return [{"id": row["id"], "title": row["title"], "part": row["part"]} for row in found]
