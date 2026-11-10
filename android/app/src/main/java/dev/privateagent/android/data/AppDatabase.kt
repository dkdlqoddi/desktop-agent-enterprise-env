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
        // Version 1 is the first; later versions add their migrations here.
    }

    fun changed() {
        changeCount.update { it + 1 }
    }

    companion object {
        const val NAME = "private-agent.db"
        const val VERSION = 1

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
        )
    }
}
