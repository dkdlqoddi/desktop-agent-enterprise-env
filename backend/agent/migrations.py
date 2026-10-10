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

# The migrations in order: the first takes a Wiki from version 1 to 2, the next from 2 to 3, and so on. Each is a
# function of the connection that must neither commit nor roll back. None yet: version 1 is the schema db.py makes.
MIGRATIONS = ()
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
