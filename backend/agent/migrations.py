"""The version of the Wiki's schema, kept as SQLite's PRAGMA user_version, and the numbered migrations that bring a Wiki
of an earlier version up to the one this code writes.

db.py's CREATE TABLE IF NOT EXISTS block stays what makes a Wiki: it creates the newest tables of a new Wiki, and its
compatibility steps (a column added when it is missing) bring the Wikis of earlier versions up to version 1, the schema
every Wiki had when versions began. What that block cannot do is a migration here: MIGRATIONS[n - 1] takes a Wiki from
version n to n + 1. Migrations run after the block (upgrade), in order, each in one transaction with its new version
number, so a Wiki is at one version or the next and never between them. A new Wiki takes SCHEMA_VERSION before the
block makes its tables in their newest form (stamp), so that a first start stopped midway never leaves tables a later
start would take for an earlier version's.

A migration therefore finds every table and index of the block already there (an existing table as the earlier version
left it, a new one empty) and changes what the block leaves alone: rows to move or rewrite, a table to rebuild, data to
fill in. It runs its statements with execute() alone, as executescript() would commit in the middle of it. Foreign keys
are off while migrations run, as SQLite's way to rebuild a table (a new one, the rows copied, the old one dropped)
needs: dropping the old one would otherwise delete the rows of other tables that refer to it.

A Wiki whose version is newer than SCHEMA_VERSION was changed by a later version of the app, which may have changed
what its rows mean; storage.prepare refuses to open it before anything is written to it (downgrade protection)."""


def drop_search_text(db):
    """Drop the search text (document_search, as versions 1 to 3 have it) and what the trigram index holds of it, to be
    made anew from the documents as for a Wiki that never had it: each start goes on with it a chunk at a time, for its
    budget before the backend answers and then on a thread of its own (db.Database._index), and a search meanwhile
    reads the documents that have none yet themselves. Dropping takes a moment whatever the size of the Wiki, where
    making it anew in the migration would keep a large Wiki's start waiting."""
    # Covering no row from here on, the index is left alone by the triggers that keep it while the rows go, and it is
    # emptied in one statement. A mark of -1 is an index this SQLite cannot read (without FTS5), which a later start
    # with FTS5 empties before it fills it; without a table there is nothing to empty.
    if db.execute(
        """SELECT 1 FROM metadata WHERE key='search_fts' AND value!='-1'
        AND EXISTS (SELECT 1 FROM sqlite_master WHERE name='document_fts')"""
    ).fetchone():
        db.execute("UPDATE metadata SET value='0' WHERE key='search_fts'")
        db.execute("INSERT INTO document_fts(document_fts) VALUES('delete-all')")
    db.execute("DELETE FROM document_search")


def search_text_anew(db):
    """Version 2: the search text holds every run of white space as one space (db.search_text), as a query does, so a
    quoted phrase is found across a line break or a double space in a document. What version 1 kept is made anew."""
    drop_search_text(db)


def search_text_ended(db):
    """Version 3: each column of the search text ends with a mark, so that a word of two characters is looked up in the
    trigram index, and its summary holds the summaries of the document's sections, which the index then finds as well
    (db.search_fields). What version 2 kept is made anew; a Wiki of version 1 takes both in one start, and makes its
    search text once."""
    drop_search_text(db)


# The migrations in order: the first takes a Wiki from version 1 to 2, the next from 2 to 3, and so on. Each is a
# function of the connection that must neither commit nor roll back. It writes its changes in SQL of its own, for the
# tables as its version has them, not through code that a later version may change.
MIGRATIONS = (search_text_anew, search_text_ended)
# The version this code writes and the newest it opens.
SCHEMA_VERSION = 1 + len(MIGRATIONS)


def version(db):
    """The schema version of the Wiki open as `db`: 0 for one made before versions began."""
    return db.execute("PRAGMA user_version").fetchone()[0]


def stamp(db):
    """Number a new Wiki, open as `db` and without tables yet, with the version whose tables db.py's block makes."""
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
    db.commit()


def upgrade(db):
    """Bring the Wiki open as `db` to SCHEMA_VERSION once db.py's block has run on it. A migration that fails is rolled
    back with its version number and raised, leaving the Wiki at the version before it."""
    if len(MIGRATIONS) + 1 != SCHEMA_VERSION:
        raise RuntimeError("SCHEMA_VERSION must be one more than the number of migrations")
    db.commit()  # Nothing the caller began stays open: each migration is a transaction of its own.
    # A Wiki from before versions began (0) is at version 1 now that the block has run on it.
    start = max(version(db), 1)
    db.execute("PRAGMA foreign_keys=OFF")  # Only outside a transaction does SQLite take it.
    try:
        for number in range(start, SCHEMA_VERSION):
            db.execute("BEGIN IMMEDIATE")
            try:
                MIGRATIONS[number - 1](db)
                db.execute(f"PRAGMA user_version={number + 1}")
                db.commit()
            except BaseException:
                db.rollback()
                raise
    finally:
        db.execute("PRAGMA foreign_keys=ON")
    if version(db) < start:  # From before versions, with nothing to migrate: only its number to write.
        db.execute(f"PRAGMA user_version={start}")
        db.commit()
