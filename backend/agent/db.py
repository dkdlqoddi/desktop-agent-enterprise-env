import contextlib
import logging
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path

# A backslash before an ASCII punctuation mark is a Markdown escape (CommonMark), which the screen does not show. The
# text of an imported page has many: markdownify writes R\&D, API\_SPEC and C\+\+.
MARKDOWN_ESCAPE = re.compile(r"\\([!-/:-@\[-`{-~])")


def search_text(value):
    """Text as search compares it (search.py), queries and documents alike: without Markdown escapes, with
    compatibility forms (full-width letters, ①, ℃) and decomposed Hangul as their plain composed text (NFKC), and
    case folded (Ärger, ΔT). Registered as an SQL function of every connection."""
    if not isinstance(value, str):
        return value
    if "\\" in value:
        value = MARKDOWN_ESCAPE.sub(r"\1", value)
    return unicodedata.normalize("NFKC", value).casefold()


def index_text(db, ids):
    """Keep the search text of the documents `ids` (search_text of their title, summary and content, and the length of
    that content) with their revision. Every write of a document's content, title or summary calls it in the same
    transaction; a search reads a document whose row is missing, of an older revision or written by an earlier version
    (no title) from the document itself instead, so a write that does not call it is slower to search, never wrong.
    The upsert keeps the row's id, by which the trigram index (document_fts) knows it, and the triggers of
    document_search change the index with it: INSERT OR REPLACE would delete the row without them."""
    ids = list(ids)
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        rows = []
        for ident, revision, title, summary, content in db.execute(
                f"SELECT id,revision,title,summary,content FROM documents WHERE id IN ({','.join('?' * len(chunk))})",
                chunk):
            body = search_text(content)
            rows.append((ident, revision, search_text(title), search_text(summary), len(body), body))
        db.executemany("""INSERT INTO document_search(doc_id,revision,title,summary,length,content) VALUES (?,?,?,?,?,?)
            ON CONFLICT(doc_id) DO UPDATE SET revision=excluded.revision,title=excluded.title,
            summary=excluded.summary,length=excluded.length,content=excluded.content""", rows)


def fts_supported():
    """Whether this SQLite can keep the trigram index of the search text: FTS5 built in and SQLite 3.34 or later, whose
    trigram tokenizer finds any run of three or more characters. Without it every search reads the search text."""
    if sqlite3.sqlite_version_info < (3, 34, 0):
        return False
    try:
        with contextlib.closing(sqlite3.connect(":memory:")) as db:
            if "ENABLE_FTS5" not in {row[0] for row in db.execute("PRAGMA compile_options")}:
                return False
            db.execute("CREATE VIRTUAL TABLE probe USING fts5(text, tokenize='trigram case_sensitive 1', detail=none)")
    except sqlite3.Error:
        return False
    return True


# The trigram index of the search text (document_fts) covers the rows of document_search up to this id, kept in the
# metadata row search_fts: the backfill (Database._index) raises it a chunk at a time, and the triggers change the index
# only for the rows it covers, so a row is never put in twice. FTS_DONE covers every row, those to come included; -1
# marks an index that a start without FTS5 left behind, which is emptied before it is filled again.
FTS_DONE = 9223372036854775807
FTS_COVERED = "(SELECT CAST(value AS INTEGER) FROM metadata WHERE key='search_fts')"
FTS_TRIGGERS = {
    "document_fts_insert": f"""AFTER INSERT ON document_search WHEN new.id <= {FTS_COVERED} BEGIN
        INSERT INTO document_fts(rowid,title,summary,content) VALUES (new.id,new.title,new.summary,new.content);
        END""",
    "document_fts_delete": f"""AFTER DELETE ON document_search WHEN old.id <= {FTS_COVERED} BEGIN
        INSERT INTO document_fts(document_fts,rowid,title,summary,content)
            VALUES ('delete',old.id,old.title,old.summary,old.content);
        END""",
    "document_fts_update": f"""AFTER UPDATE OF title,summary,content ON document_search
        WHEN old.id <= {FTS_COVERED} BEGIN
        INSERT INTO document_fts(document_fts,rowid,title,summary,content)
            VALUES ('delete',old.id,old.title,old.summary,old.content);
        INSERT INTO document_fts(rowid,title,summary,content) VALUES (new.id,new.title,new.summary,new.content);
        END""",
}
# Seconds a start spends on the search text and the trigram index before the backend answers; a thread of its own goes
# on with what is left, a chunk at a time beside the requests.
INDEX_BUDGET = 2.0
LOG = logging.getLogger("private_agent")

# Where each imported document came from (the file path or the link), and the text and title the last import of it
# saved: (columns, the key that tells a repeated import apart). An import of the same path or link again updates its
# documents (sources.py). An import too long for one document is saved as several (parts.py); each has a row of its
# own, numbered `part` of `parts`, so the same file or page content is held once per part.
SOURCE_TABLES = {
    "rtf_sources": ("source_path TEXT NOT NULL, source_hash TEXT NOT NULL", "source_hash"),
    "office_sources": ("source_path TEXT NOT NULL, source_hash TEXT NOT NULL, application TEXT NOT NULL", "source_hash"),
    "image_sources": ("source_path TEXT NOT NULL, source_hash TEXT NOT NULL", "source_hash"),
    "text_sources": ("source_path TEXT NOT NULL, source_hash TEXT NOT NULL", "source_hash"),
    # A Markdown (.md) file, saved as the text it holds.
    "markdown_sources": ("source_path TEXT NOT NULL, source_hash TEXT NOT NULL", "source_hash"),
    "confluence_sources": ("source_url TEXT NOT NULL, page_id TEXT NOT NULL, source_hash TEXT NOT NULL",
                           "source_url, source_hash"),
    "jira_sources": ("source_url TEXT NOT NULL, issue_key TEXT NOT NULL, source_hash TEXT NOT NULL",
                     "source_url, source_hash"),
}


def source_table(name, columns, key):
    # title: the title the import gave the document, which tells a title the user changed apart ('' before it was kept).
    return (f"CREATE TABLE IF NOT EXISTS {name} (doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE, "
            f"{columns}, markdown TEXT NOT NULL, imported_at TEXT NOT NULL, part INTEGER NOT NULL DEFAULT 1, "
            f"parts INTEGER NOT NULL DEFAULT 1, title TEXT NOT NULL DEFAULT '', UNIQUE({key}, part))")


def identity(name):
    """The column that names where a source table's documents came from: the link of a page, the path of a file."""
    return "source_url" if "source_url" in SOURCE_TABLES[name][0] else "source_path"


class Database:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            # The search text of an earlier version keeps no title, summary or length, and numbers its rows implicitly,
            # which a VACUUM may change under the trigram index: it is made anew from the documents (_index).
            search_columns = {r[1] for r in db.execute("PRAGMA table_info(document_search)")}
            if search_columns and "length" not in search_columns:
                db.execute("DROP TABLE document_search")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS prompts (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    category TEXT NOT NULL, system_prompt TEXT NOT NULL, template TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, prompt_id TEXT, title TEXT NOT NULL,
                    input TEXT NOT NULL, output TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS schedules (
                    id TEXT PRIMARY KEY,
                    prompt_id TEXT NOT NULL REFERENCES prompts(id) ON DELETE CASCADE,
                    name TEXT NOT NULL, variables TEXT NOT NULL DEFAULT '{}',
                    frequency TEXT NOT NULL
                        CHECK(frequency IN ('once','hourly','daily','weekdays','weekly','monthly')),
                    run_time TEXT NOT NULL DEFAULT '09:00', weekday INTEGER NOT NULL DEFAULT 0,
                    month_day INTEGER NOT NULL DEFAULT 1, run_at TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1, notify INTEGER NOT NULL DEFAULT 1,
                    next_run_at TEXT, last_run_at TEXT, last_status TEXT,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(enabled,next_run_at);
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
                    error TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                -- The order of the document list, which its pages continue from (a cursor of updated_at and id).
                CREATE INDEX IF NOT EXISTS idx_documents_updated ON documents(updated_at, id);
                CREATE TABLE IF NOT EXISTS keywords (
                    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    keyword TEXT NOT NULL, PRIMARY KEY(doc_id, keyword)
                );
                CREATE INDEX IF NOT EXISTS idx_keyword ON keywords(keyword);
                CREATE TABLE IF NOT EXISTS relations (
                    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    source TEXT NOT NULL, target TEXT NOT NULL, label TEXT NOT NULL,
                    PRIMARY KEY(doc_id, source, target, label)
                );
                -- PageIndex table of contents of a long document, valid for one revision of its content.
                CREATE TABLE IF NOT EXISTS doc_nodes (
                    doc_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL, node_id TEXT NOT NULL, parent_id TEXT,
                    position INTEGER NOT NULL, depth INTEGER NOT NULL, title TEXT NOT NULL,
                    kind TEXT NOT NULL, summary TEXT NOT NULL,
                    start_offset INTEGER NOT NULL, end_offset INTEGER NOT NULL,
                    PRIMARY KEY(doc_id, node_id)
                );
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                    assignee TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open'
                        CHECK(status IN ('open','done','paused')),
                    due_at TEXT, remind_at TEXT, notified_at TEXT, completed_at TEXT,
                    repeat TEXT NOT NULL DEFAULT 'none' CHECK(repeat IN ('none','daily','weekly')),
                    source_id TEXT REFERENCES documents(id) ON DELETE SET NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_tasks_reminder ON tasks(status,remind_at);
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL CHECK(role IN ('user','assistant')), content TEXT NOT NULL,
                    citations TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id,seq);
                -- The HTML page an imported file became: the document's body while its format is 'html',
                -- and its original after the text was edited. Kept apart so lists and searches never load it.
                CREATE TABLE IF NOT EXISTS document_html (
                    doc_id TEXT PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
                    html TEXT NOT NULL
                );
                -- The text search compares (search_text) of each document's title, summary and content, as of its
                -- revision. Kept apart like the pages, so lists never load it; a row of another revision, or without a
                -- title (written by an earlier version), is not used. id is the rowid of the trigram index
                -- (document_fts), explicit so a VACUUM keeps it; the content comes last, so reading the other columns
                -- never goes through the pages it overflows to.
                CREATE TABLE IF NOT EXISTS document_search (
                    id INTEGER PRIMARY KEY,
                    doc_id TEXT NOT NULL UNIQUE REFERENCES documents(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL, title TEXT, summary TEXT, length INTEGER,
                    content TEXT NOT NULL
                );
                -- A title or summary changed without index_text marks the search text out of date, so search reads the
                -- document itself rather than the old words. Every program that writes the Wiki runs it.
                CREATE TRIGGER IF NOT EXISTS documents_search_stale AFTER UPDATE OF title, summary ON documents
                    WHEN old.title IS NOT new.title OR old.summary IS NOT new.summary BEGIN
                    UPDATE document_search SET revision=-1 WHERE doc_id=new.id;
                END;
                -- Automatic routines (routines.py): a ready-made job of the catalog (kind) as the user set it up (config),
                -- its schedule, what it keeps from one run for the next (state) and how its last run went; and its runs.
                CREATE TABLE IF NOT EXISTS routines (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, config TEXT NOT NULL DEFAULT '{}',
                    frequency TEXT NOT NULL, interval INTEGER NOT NULL DEFAULT 60,
                    run_time TEXT NOT NULL DEFAULT '09:00', weekday INTEGER NOT NULL DEFAULT 0,
                    month_day INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
                    notify INTEGER NOT NULL DEFAULT 1, next_run_at TEXT, last_run_at TEXT, last_status TEXT,
                    last_summary TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT '',
                    last_changed INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_routines_due ON routines(enabled,next_run_at);
                CREATE TABLE IF NOT EXISTS routine_runs (
                    id TEXT PRIMARY KEY, routine_id TEXT NOT NULL REFERENCES routines(id) ON DELETE CASCADE,
                    started_by TEXT NOT NULL, status TEXT NOT NULL, changed INTEGER NOT NULL DEFAULT 0,
                    summary TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_routine_runs ON routine_runs(routine_id,created_at);
                CREATE INDEX IF NOT EXISTS idx_routine_runs_finished ON routine_runs(finished_at);
            """)
            for name, (columns, key) in SOURCE_TABLES.items():
                db.execute(source_table(name, columns, key))
            self._migrate_markdown(db)
            self._migrate_parts(db)
            for name in SOURCE_TABLES:
                if "title" not in {r[1] for r in db.execute(f"PRAGMA table_info({name})")}:
                    db.execute(f"ALTER TABLE {name} ADD COLUMN title TEXT NOT NULL DEFAULT ''")
                # An import finds the earlier one of the same file or page by it.
                db.execute(f"CREATE INDEX IF NOT EXISTS idx_{name}_identity ON {name}({identity(name)})")
            # What write-backs put into the file since the import, as a JSON list of texts (writeback.remember); an
            # import again leaves it '' as it saves the row anew.
            if "written" not in {r[1] for r in db.execute("PRAGMA table_info(office_sources)")}:
                db.execute("ALTER TABLE office_sources ADD COLUMN written TEXT NOT NULL DEFAULT ''")
            # Runs recorded before scheduling carry no outcome or timing; they were successful manual runs.
            columns = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
            for column, definition in (("status", "TEXT NOT NULL DEFAULT 'success'"),
                                       ("error", "TEXT NOT NULL DEFAULT ''"),
                                       ("kind", "TEXT NOT NULL DEFAULT 'manual'"),
                                       ("schedule_id", "TEXT"), ("started_at", "TEXT"),
                                       ("duration_ms", "INTEGER")):
                if column not in columns:
                    db.execute(f"ALTER TABLE runs ADD COLUMN {column} {definition}")
            db.execute("CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at)")
            # How the last analysis indexed a document: '' (not analyzed), 'wiki' or 'pageindex'.
            if "index_mode" not in {r[1] for r in db.execute("PRAGMA table_info(documents)")}:
                db.execute("ALTER TABLE documents ADD COLUMN index_mode TEXT NOT NULL DEFAULT ''")
                db.execute("UPDATE documents SET index_mode='wiki' WHERE status='ready'")
            # The search steps behind an assistant answer, as JSON.
            if "trace" not in {r[1] for r in db.execute("PRAGMA table_info(messages)")}:
                db.execute("ALTER TABLE messages ADD COLUMN trace TEXT NOT NULL DEFAULT ''")
            # What the document window shows: the Markdown text, or the HTML page of an imported file.
            if "format" not in {r[1] for r in db.execute("PRAGMA table_info(documents)")}:
                db.execute("ALTER TABLE documents ADD COLUMN format TEXT NOT NULL DEFAULT 'markdown'")
            if "completed_at" not in {r[1] for r in db.execute("PRAGMA table_info(tasks)")}:
                db.execute("ALTER TABLE tasks ADD COLUMN completed_at TEXT")
                db.execute("UPDATE tasks SET completed_at=updated_at WHERE status='done'")
            # A snoozed reminder is due again at snoozed_until; remind_at keeps the time a repeat counts from.
            if "snoozed_until" not in {r[1] for r in db.execute("PRAGMA table_info(tasks)")}:
                db.execute("ALTER TABLE tasks ADD COLUMN snoozed_until TEXT")
            # A deleted document waits in the trash (deleted_at) until it is restored, purged or 30 days pass
            # (service.TRASH_DAYS): the lists, the search, the graph and the analysis leave it out, and everything that
            # refers to it (keywords, sources, tasks) comes back with it.
            if "deleted_at" not in {r[1] for r in db.execute("PRAGMA table_info(documents)")}:
                db.execute("ALTER TABLE documents ADD COLUMN deleted_at TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS idx_documents_deleted ON documents(deleted_at)")
            self.fts = self._full_text(db)
            if not db.execute("SELECT 1 FROM metadata WHERE key='seeded'").fetchone():
                from .service import now
                for index, prompt in enumerate(SEEDS):
                    db.execute("INSERT INTO prompts VALUES (?,?,?,?,?,?,?,?)", (f"starter-{index}", *prompt, now(), now()))
                db.execute("INSERT INTO metadata VALUES ('seeded', '1')")
        if not self._index(INDEX_BUDGET):
            threading.Thread(target=self._index_later, name="search-index", daemon=True).start()

    @staticmethod
    def _full_text(db):
        """Keep the trigram index of the search text (document_fts) where this SQLite has one, and say whether it does.
        Without one the triggers go, as every write of the search text would fail on them, and an index made before is
        marked to be emptied: a later start with FTS5 fills it from the start, as it does a new one."""
        made = db.execute("SELECT 1 FROM sqlite_master WHERE name='document_fts'").fetchone()
        if not fts_supported():
            for name in FTS_TRIGGERS:
                db.execute(f"DROP TRIGGER IF EXISTS {name}")
            if made:
                db.execute("INSERT INTO metadata VALUES ('search_fts','-1') ON CONFLICT(key) DO UPDATE SET value='-1'")
            return False
        # Without positions (detail=none) a term is looked up as all of its trigrams, which finds every document that
        # has it and some that only have its trigrams apart; search counts the terms in what it finds. The index is a
        # fifth of what one with positions takes, and fills twice as fast. Text is case folded before it is indexed.
        db.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS document_fts USING fts5(title, summary, content,
            content='document_search', content_rowid='id', tokenize='trigram case_sensitive 1', detail=none,
            columnsize=0)""")
        mark = db.execute("SELECT value FROM metadata WHERE key='search_fts'").fetchone()
        triggers = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
        if not made or mark is None or mark[0] == "-1" or not set(FTS_TRIGGERS) <= triggers:
            db.execute("INSERT INTO document_fts(document_fts) VALUES('delete-all')")
            db.execute("INSERT INTO metadata VALUES ('search_fts','0') ON CONFLICT(key) DO UPDATE SET value='0'")
        for name, body in FTS_TRIGGERS.items():
            db.execute(f"CREATE TRIGGER IF NOT EXISTS {name} {body}")
        return True

    def _index(self, budget=None, chunk=200):
        """Bring the search text up to date, `chunk` rows per transaction for as long as `budget` seconds last (None:
        until done): the rows of the documents an earlier version saved, or that changed without it, then the trigram
        index of the rows it does not cover yet. A large Wiki's first start after the upgrade must still answer in time,
        and searches read what is left from the documents meanwhile. Returns whether everything is done; what a Wiki
        another program holds locked keeps is left for later."""
        deadline = None if budget is None else time.monotonic() + budget

        def more():
            return deadline is None or time.monotonic() < deadline

        try:
            with self.connect() as db:
                # The search text of documents that a program not keeping the foreign keys deleted.
                db.execute("DELETE FROM document_search WHERE doc_id NOT IN (SELECT id FROM documents)")
                db.commit()
                last = 0
                while True:
                    db.execute("BEGIN IMMEDIATE")
                    rows = db.execute("""SELECT d.rowid, d.id FROM documents d LEFT JOIN document_search s
                        ON s.doc_id=d.id WHERE d.rowid>? AND (s.revision IS NOT d.revision OR s.title IS NULL)
                        ORDER BY d.rowid LIMIT ?""", (last, chunk)).fetchall()
                    if rows:
                        index_text(db, [row[1] for row in rows])
                        last = rows[-1][0]
                    db.commit()
                    if len(rows) < chunk:
                        break
                    if not more():
                        return False
                while self.fts:
                    db.execute("BEGIN IMMEDIATE")
                    covered = db.execute(f"SELECT {FTS_COVERED}").fetchone()[0]
                    if covered is None or covered >= FTS_DONE:
                        db.commit()
                        break
                    rows = db.execute("SELECT id FROM document_search WHERE id>? ORDER BY id LIMIT ?",
                                      (covered, chunk)).fetchall()
                    # A short chunk is the last: within this transaction no row comes after it, so the index covers
                    # every row from here on, those to come included.
                    upto = rows[-1][0] if len(rows) == chunk else FTS_DONE
                    db.execute("""INSERT INTO document_fts(rowid,title,summary,content)
                        SELECT id,title,summary,content FROM document_search WHERE id>? AND id<=?""", (covered, upto))
                    db.execute("UPDATE metadata SET value=? WHERE key='search_fts'", (str(upto),))
                    db.commit()
                    if upto >= FTS_DONE:
                        break
                    if not more():
                        return False
        except sqlite3.OperationalError:
            return False
        except sqlite3.DatabaseError:
            # A damaged trigram index stays as it is rather than stop the start: a search that cannot read it compares
            # the search text instead (Search.texts).
            LOG.exception("search index could not be brought up to date")
        return True

    def _index_later(self):
        """Go on with what a start left of _index, half a second at a time beside the requests, until it is done."""
        while True:
            time.sleep(0.5)
            try:
                if self._index(budget=0.5):
                    return
            except Exception:
                LOG.exception("search index stopped")
                return

    @staticmethod
    def _migrate_markdown(db):
        """Upgrade legacy snapshots atomically, preserving user edits and source metadata."""
        import hashlib
        import re
        from .markdown_import import html_to_markdown

        db.execute("BEGIN IMMEDIATE")
        for table in ("rtf_sources", "office_sources", "confluence_sources", "jira_sources"):
            columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
            if "html" not in columns:
                continue
            schema = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            schema = schema.replace(table, table + "_markdown", 1)
            schema = re.sub(r"\bhtml\b", "markdown", schema)
            schema = re.sub(r"html_path TEXT NOT NULL,?", "", schema)
            db.execute(schema)
            new_columns = ["markdown" if c == "html" else c for c in columns if c != "html_path"]
            hashes = set()
            for row in db.execute(f"SELECT * FROM {table}"):
                values = dict(row)
                _, markdown = html_to_markdown(values.pop("html"), values.get("source_url", ""), validate=False)
                values.pop("html_path", None)
                values["markdown"] = markdown
                if "source_url" in values:
                    checksum = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
                    key = (values["source_url"], checksum)
                    # Two historical captures can differ only in markup. Keep both documents.
                    if key not in hashes:
                        values["source_hash"] = checksum
                        hashes.add(key)
                db.execute(f"INSERT INTO {table}_markdown ({','.join(new_columns)}) VALUES ({','.join('?' for _ in new_columns)})",
                           [values[c] for c in new_columns])
                if markdown:
                    db.execute("UPDATE documents SET content=?, revision=revision+1 WHERE id=? AND revision=1",
                               (markdown, row["doc_id"]))
            db.execute(f"DROP TABLE {table}")
            db.execute(f"ALTER TABLE {table}_markdown RENAME TO {table}")

    @staticmethod
    def _migrate_parts(db):
        """Number the sources of earlier versions as part 1 of 1, so the same content may have a row per part. Runs
        in the transaction _migrate_markdown began, so a table is rebuilt whole or not at all."""
        for name, (columns, key) in SOURCE_TABLES.items():
            existing = [row[1] for row in db.execute(f"PRAGMA table_info({name})")]
            if "part" in existing:
                continue
            listed = ",".join(existing)
            db.execute(source_table(f"{name}_parts", columns, key))
            db.execute(f"INSERT INTO {name}_parts ({listed}) SELECT {listed} FROM {name}")
            db.execute(f"DROP TABLE {name}")
            db.execute(f"ALTER TABLE {name}_parts RENAME TO {name}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.create_function("search_text", 1, search_text, deterministic=True)
        try:
            with db:
                yield db
        finally:
            db.close()


SEEDS = [
    ("회의록 정리", "복잡한 논의를 명확한 결정과 다음 할 일로", "업무", "당신은 꼼꼼한 업무 비서입니다. 한국어로 답하고, 제공되지 않은 내용을 만들어내지 마세요.", "다음 회의 내용을 핵심 요약, 결정 사항, 할 일(담당자·기한), 미결 사항으로 정리해 주세요.\n\n{{회의 내용}}"),
    ("코드 리뷰", "놓치기 쉬운 버그부터 더 나은 설계까지", "개발", "당신은 시니어 소프트웨어 엔지니어입니다. 근거와 구체적인 개선 코드를 제시하세요.", "다음 코드를 정확성, 보안, 성능, 유지보수성 관점에서 검토해 주세요. 심각도 순으로 설명해 주세요.\n\n{{코드}}"),
    ("업무 메일 작성", "의도를 정확히 전하는 간결한 비즈니스 메일", "업무", "당신은 한국어 비즈니스 커뮤니케이션 전문가입니다. 정중하고 간결하게 작성하세요.", "다음 내용을 바탕으로 제목과 본문이 있는 업무 메일을 작성해 주세요.\n\n{{전달할 내용}}"),
    ("아이디어 구체화", "떠오른 생각을 실행 가능한 계획으로", "기획", "당신은 실용적인 제품 기획 파트너입니다. 가정과 확인된 사실을 구분하세요.", "다음 아이디어를 문제 정의, 대상 사용자, 핵심 기능, 첫 실행 단계, 검증 지표로 구체화해 주세요.\n\n{{아이디어}}"),
]
