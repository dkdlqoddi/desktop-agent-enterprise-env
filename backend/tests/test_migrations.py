"""The Wiki's schema version (agent/migrations.py): PRAGMA user_version, numbered migrations run in order after db.py's
CREATE TABLE IF NOT EXISTS block, and a Wiki of a later version refused before anything is written to it."""

import contextlib
import sqlite3

import pytest

from agent import db as database_module, migrations
from agent.db import FTS_DONE, Database
from agent.service import Service
from agent.storage import Refused


def user_version(path):
    with contextlib.closing(sqlite3.connect(path)) as db:
        return db.execute("PRAGMA user_version").fetchone()[0]


def tables(path):
    with contextlib.closing(sqlite3.connect(path)) as db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def steps(monkeypatch, *functions):
    """Migrations as another version of this code would have them; none as version 1 of it had, before the first."""
    monkeypatch.setattr(migrations, "MIGRATIONS", functions)
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 1 + len(functions))


def test_a_new_wiki_and_one_from_before_versions_are_at_the_newest_version(tmp_path):
    fresh = tmp_path / "new" / "wiki.db"
    Database(fresh)
    assert user_version(fresh) == migrations.SCHEMA_VERSION == 2
    # A Wiki of an earlier version: the block's compatibility steps bring it to version 1, the migrations the rest.
    old = tmp_path / "old.db"
    with contextlib.closing(sqlite3.connect(old)) as db:
        db.execute("""CREATE TABLE documents (id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
            revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        db.execute(
            "INSERT INTO documents (id,title,content,created_at,updated_at) VALUES ('d1','예전 문서','글','t','t')"
        )
        db.commit()
    service = Service(Database(old), None)
    assert user_version(old) == migrations.SCHEMA_VERSION
    assert service.wiki_get({"id": "d1"})["deleted_at"] is None  # The column of the trash, added by the block.
    Database(old)
    assert user_version(old) == migrations.SCHEMA_VERSION


def test_migrations_run_once_each_in_order_after_the_block(tmp_path, monkeypatch):
    path = tmp_path / "wiki.db"
    steps(monkeypatch)
    Database(path)
    ran = []

    def second(db):
        # The block has run: its tables are there, and the version is still the one before.
        ran.append(
            (
                "second",
                migrations.version(db),
                "documents" in {r[0] for r in db.execute("SELECT name FROM sqlite_master")},
            )
        )
        db.execute("CREATE TABLE labels (name TEXT PRIMARY KEY)")
        db.execute("INSERT INTO labels VALUES ('중요')")

    def third(db):
        ran.append(("third", migrations.version(db), True))
        db.execute("ALTER TABLE labels ADD COLUMN color TEXT NOT NULL DEFAULT 'red'")

    steps(monkeypatch, second, third)
    Database(path)
    assert ran == [("second", 1, True), ("third", 2, True)] and user_version(path) == 3
    with contextlib.closing(sqlite3.connect(path)) as db:
        assert db.execute("SELECT name, color FROM labels").fetchall() == [("중요", "red")]
    Database(path)
    assert len(ran) == 2
    # A Wiki at version 2 takes only the third; a new one none, as the block made its tables in their newest form.
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("DROP TABLE labels")
        db.execute("CREATE TABLE labels (name TEXT PRIMARY KEY)")
        db.execute("PRAGMA user_version=2")
        db.commit()
    Database(path)
    assert [name for name, _, _ in ran] == ["second", "third", "third"] and user_version(path) == 3
    fresh = tmp_path / "new.db"
    Database(fresh)
    assert user_version(fresh) == 3 and len(ran) == 3 and "labels" not in tables(fresh)


def test_a_wiki_from_before_versions_takes_every_migration(tmp_path, monkeypatch):
    path = tmp_path / "wiki.db"
    Database(path)
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version=0")
    ran = []
    steps(monkeypatch, lambda db: ran.append(2), lambda db: ran.append(3))
    Database(path)
    assert ran == [2, 3] and user_version(path) == 3


def test_a_migration_that_fails_leaves_the_wiki_at_the_version_before_it(tmp_path, monkeypatch):
    path = tmp_path / "wiki.db"
    steps(monkeypatch)
    Database(path)

    def second(db):
        db.execute("CREATE TABLE labels (name TEXT)")

    def third(db):
        db.execute("CREATE TABLE half (name TEXT)")
        raise RuntimeError("디스크 오류")

    steps(monkeypatch, second, third)
    with pytest.raises(RuntimeError, match="디스크 오류"):
        Database(path)
    assert user_version(path) == 2 and "labels" in tables(path) and "half" not in tables(path)
    # The next start tries the third again.
    steps(monkeypatch, second, lambda db: db.execute("CREATE TABLE half (name TEXT)"))
    Database(path)
    assert user_version(path) == 3 and "half" in tables(path)
    steps(monkeypatch, second)
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 5)
    with contextlib.closing(sqlite3.connect(path)) as db, pytest.raises(RuntimeError, match="SCHEMA_VERSION"):
        migrations.upgrade(db)


def test_a_first_start_stopped_midway_is_never_taken_for_an_earlier_version(tmp_path, monkeypatch):
    """A new Wiki is numbered before the block makes its tables: stopped before the migrations' turn, the next start
    finds the tables in their newest form under the newest number, not under 0 as a Wiki from before versions."""
    ran = []
    steps(monkeypatch, lambda db: ran.append(2))
    real = migrations.upgrade

    def stopped(db):
        raise RuntimeError("앱이 닫힘")

    monkeypatch.setattr(migrations, "upgrade", stopped)
    path = tmp_path / "wiki.db"
    with pytest.raises(RuntimeError):
        Database(path)
    assert user_version(path) == 2 and "documents" in tables(path)
    monkeypatch.setattr(migrations, "upgrade", real)
    Database(path)
    assert ran == [] and user_version(path) == 2


def test_a_migration_rebuilds_a_table_without_taking_the_rows_that_refer_to_it(tmp_path, monkeypatch):
    path = tmp_path / "wiki.db"
    steps(monkeypatch)
    service = Service(Database(path), None)
    doc = service.wiki_save({"title": "문서", "content": "글"})
    with service.db.connect() as db:
        db.execute("INSERT INTO keywords VALUES (?, 'api')", (doc["id"],))

    def rebuild(db):
        # SQLite's way to change a table as ALTER TABLE cannot: a new one, the rows copied, the old one dropped.
        schema = db.execute("SELECT sql FROM sqlite_master WHERE name='documents'").fetchone()[0]
        db.execute(schema.replace("documents", "documents_new", 1))
        db.execute("INSERT INTO documents_new SELECT * FROM documents")
        db.execute("DROP TABLE documents")
        db.execute("ALTER TABLE documents_new RENAME TO documents")

    steps(monkeypatch, rebuild)
    reopened = Service(Database(path), None)
    assert reopened.wiki_get({"id": doc["id"]})["keywords"] == ["api"]
    with reopened.db.connect() as db:  # Foreign keys are on again for everything else.
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_a_wiki_of_a_later_version_is_refused_before_anything_is_written(tmp_path):
    path = tmp_path / "wiki.db"
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE documents (id TEXT PRIMARY KEY, meaning TEXT)")
        db.execute(f"PRAGMA user_version={migrations.SCHEMA_VERSION + 1}")
        db.commit()
    written = path.read_bytes()
    with pytest.raises(Refused) as refused:
        Database(path)
    message = str(refused.value)
    assert f"(버전 {migrations.SCHEMA_VERSION + 1})" in message and f"버전 {migrations.SCHEMA_VERSION}까지" in message
    assert str(path) in message and "최신 버전의 Private Agent로 열어 주세요" in message
    assert path.read_bytes() == written and tables(path) == {"documents"}


# -- Version 2: the search text made anew -----------------------------------------------------------------------------


def as_version_1(path):
    """The Wiki at `path` as version 1 left it: the search text of every document with the line breaks and spaces of
    its text (version 1's search_text, which these documents' plain text casefolded is), the trigram index following
    it by its triggers, and the version number 1."""
    with contextlib.closing(sqlite3.connect(path)) as db, db:
        for ident, title, summary, content in db.execute("SELECT id,title,summary,content FROM documents").fetchall():
            db.execute(
                "UPDATE document_search SET title=?,summary=?,content=? WHERE doc_id=?",
                (title.casefold(), summary.casefold(), content.casefold(), ident),
            )
        db.execute("PRAGMA user_version=1")


def found(service, query, mode="word"):
    return [doc["id"] for doc in service.wiki_search({"query": query, "mode": mode})["documents"]]


def index_whole(service):
    """Whether the trigram index covers every row of the search text and holds exactly what they say (FTS5 checks its
    entries against document_search)."""
    with service.db.connect() as db:
        db.execute("INSERT INTO document_fts(document_fts, rank) VALUES('integrity-check', 1)")
        return db.execute("SELECT value FROM metadata WHERE key='search_fts'").fetchone()[0] == str(FTS_DONE)


@pytest.mark.parametrize("fts", [True, False], ids=["trigram index", "without FTS5 at the upgrade"])
def test_version_2_makes_the_search_text_anew_so_a_phrase_is_found_across_a_line_break(tmp_path, monkeypatch, fts):
    """Version 1 kept the search text with the document's line breaks and double spaces, where a quoted phrase, with
    one space, was not found. Version 2 drops it, and what the trigram index holds of it, and makes it anew."""
    if fts and not database_module.fts_supported():
        pytest.skip("SQLite without the FTS5 trigram tokenizer")
    path = tmp_path / "wiki.db"
    service = Service(Database(path), None)
    broken = service.wiki_save({"title": "회의록", "content": "다음 분기에는 캐시\n전략을 바꿉니다."})["id"]
    spaced = service.wiki_save({"title": "메모", "content": "Redis  클러스터 도입\r\n\t검토"})["id"]
    service.wiki_save({"title": "기타", "content": "캐시와 전략은 따로 봅니다."})
    as_version_1(path)
    # As version 1 found them: the phrases are in no search text, only their words.
    assert found(service, '"캐시 전략"') == found(service, '"redis 클러스터 도입 검토"') == []
    assert len(found(service, "캐시 전략")) == 2

    if not fts:  # A start with an SQLite without FTS5, which cannot touch the index the earlier starts made.
        monkeypatch.setattr(database_module, "fts_supported", lambda: False)
    Database(path)
    assert user_version(path) == 2
    assert found(service, '"캐시 전략"') == found(service, '"캐시 전략"', "auto") == [broken]
    assert found(service, '"Redis 클러스터 도입 검토"') == [spaced]
    with service.db.connect() as db:
        stored = db.execute("SELECT content FROM document_search WHERE doc_id=?", (spaced,)).fetchone()[0]
    assert stored == "redis 클러스터 도입 검토"
    # The passage shown is the document's own text, where the screen marks the phrase across the line break.
    result = service.wiki_search({"query": '"캐시 전략"', "mode": "word"})
    snippet = result["documents"][0]["match"]["snippets"][0]["text"]
    assert result["highlights"] == ["캐시 전략"] and "캐시 전략을 바꿉니다" in snippet
    if fts:
        assert index_whole(service)
    else:
        monkeypatch.undo()
        reopened = Service(Database(path), None)
        assert reopened.db.fts == database_module.fts_supported() and found(reopened, '"캐시 전략"') == [broken]
        assert not reopened.db.fts or index_whole(reopened)
    # Version 1 of the app, which this Wiki is now newer than, refuses it rather than misread its search text.
    steps(monkeypatch)
    with pytest.raises(Refused, match="버전 2"):
        Database(path)


def test_while_the_search_text_is_made_anew_a_search_reads_the_documents_themselves(tmp_path, monkeypatch):
    """A large Wiki's start answers before its search text is made anew (db.Database._index); until then a search
    finds what the documents hold, phrases across a line break included."""
    path = tmp_path / "wiki.db"
    service = Service(Database(path), None)
    ident = service.wiki_save({"title": "보고서", "content": "지연 시간을\n30% 줄였습니다."})["id"]
    as_version_1(path)
    made = Database._index
    monkeypatch.setattr(Database, "_index", lambda self, budget=None, chunk=200: False)
    monkeypatch.setattr(Database, "_index_later", lambda self: None)
    Database(path)
    with service.db.connect() as db:
        assert db.execute("SELECT count(*) FROM document_search").fetchone()[0] == 0
    assert found(service, '"시간을 30%"') == [ident] and found(service, "줄였습니다", "auto") == [ident]
    monkeypatch.setattr(Database, "_index", made)
    assert service.db._index() is True
    assert found(service, '"시간을 30%"') == [ident]
    assert not service.db.fts or index_whole(service)
