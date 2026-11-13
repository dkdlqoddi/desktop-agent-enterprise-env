package dev.privateagent.android.data

import android.content.Context
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update

/**
 * The app's one database, in the app's private storage. [name] null keeps it in memory (the instrumented tests).
 * Every write bumps [changes], which the screens watch to read again.
 */
class AppDatabase(context: Context, name: String? = NAME) :
    SQLiteOpenHelper(context.applicationContext, name, null, VERSION) {

    private val changeCount = MutableStateFlow(0L)
    val changes: StateFlow<Long> = changeCount

    init {
        setWriteAheadLoggingEnabled(true)
    }

    override fun onConfigure(db: SQLiteDatabase) {
        db.setForeignKeyConstraintsEnabled(true)
    }

    override fun onCreate(db: SQLiteDatabase) {
        for (statement in SCHEMA) db.execSQL(statement)
    }

    override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) {
        // Version 2: what a turn did on the way, kept with the message that ended it.
        if (oldVersion < 2) db.execSQL(ACTIVITY)
        // Version 3: the trash, where what the user deletes waits before it is gone for good.
        if (oldVersion < 3) for (statement in TRASH) db.execSQL(statement)
    }

    fun changed() {
        changeCount.update { it + 1 }
    }

    companion object {
        const val NAME = "private-agent.db"
        const val VERSION = 3

        /**
         * What the turn a message ended did on the way (agent/TurnActivity.kt): the tools it ran, the app's actions,
         * the commands it asked about with how each was decided, and its questions (steps: JSON, in order; more: the
         * steps beyond those kept). It goes with its message.
         */
        private const val ACTIVITY = """
            CREATE TABLE activity (
                message_id INTEGER PRIMARY KEY REFERENCES messages (id) ON DELETE CASCADE,
                steps TEXT NOT NULL DEFAULT '[]',
                more INTEGER NOT NULL DEFAULT 0
            )
            """

        /**
         * What the user deleted, kept TrashStore.DAYS days for them to restore (TrashStore.kt): what it was (kind), what
         * the list calls it (title), the row itself as JSON by column name (payload; the cleared conversation's is a
         * summary), and whether the retention policy of the recorded pages moved it rather than the user (retired). The
         * rows that went with it (a page's items, the conversation's messages and what each turn did) are kept one by
         * one in trash_rows, by the table they came from (source) and their number there (row_id), and go with it.
         */
        private val TRASH = listOf(
            """
            CREATE TABLE trash (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                payload TEXT NOT NULL DEFAULT '{}',
                retired INTEGER NOT NULL DEFAULT 0,
                deleted_at INTEGER NOT NULL
            )
            """,
            "CREATE INDEX trash_time ON trash (deleted_at, id)",
            "CREATE INDEX trash_kind ON trash (kind, deleted_at, id)",
            """
            CREATE TABLE trash_rows (
                entry_id INTEGER NOT NULL REFERENCES trash (id) ON DELETE CASCADE,
                source TEXT NOT NULL,
                row_id INTEGER NOT NULL,
                data TEXT NOT NULL,
                PRIMARY KEY (entry_id, source, row_id)
            )
            """,
        )

        private val SCHEMA = listOf(
            """
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                tool TEXT,
                args TEXT,
                state TEXT,
                result TEXT,
                capture_id INTEGER,
                notice TEXT
            )
            """,
            """
            CREATE TABLE captures (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site TEXT NOT NULL,
                query TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                captured_at INTEGER NOT NULL,
                ad_count INTEGER NOT NULL DEFAULT 0,
                product_count INTEGER NOT NULL DEFAULT 0,
                result_count INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE
            )
            """,
            "CREATE INDEX captures_time ON captures (captured_at)",
            """
            CREATE TABLE items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                capture_id INTEGER NOT NULL REFERENCES captures (id) ON DELETE CASCADE,
                ref INTEGER NOT NULL,
                kind TEXT NOT NULL,
                position INTEGER NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                price TEXT NOT NULL DEFAULT '',
                display_url TEXT NOT NULL DEFAULT '',
                link_url TEXT NOT NULL DEFAULT '',
                image_url TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                clicked_at INTEGER,
                clicked_url TEXT,
                landing_url TEXT,
                landing_title TEXT,
                UNIQUE (capture_id, ref)
            )
            """,
            "CREATE INDEX items_capture ON items (capture_id)",
            "CREATE INDEX items_clicked ON items (clicked_at)",
            """
            CREATE TABLE schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site TEXT NOT NULL,
                query TEXT NOT NULL,
                time TEXT NOT NULL,
                days TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at INTEGER NOT NULL,
                next_run_at INTEGER
            )
            """,
            ACTIVITY,
        ) + TRASH
    }
}
