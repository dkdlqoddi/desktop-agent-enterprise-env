package dev.privateagent.android.data

import android.content.ContentValues
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.util.cleanText
import dev.privateagent.android.util.text
import dev.privateagent.android.util.textOrNull
import dev.privateagent.android.util.wholeOrNull
import org.json.JSONException
import org.json.JSONObject

/** What an entry of the trash was. */
enum class TrashKind(val key: String) {
    /** The conversation, cleared whole (대화 지우기): its messages with what each turn did. */
    CHAT("chat"),

    /** A recorded page with its items. */
    CAPTURE("capture"),

    /** A scheduled capture. */
    SCHEDULE("schedule"),
    ;

    companion object {
        fun of(key: String?) = entries.firstOrNull { it.key == key }
    }
}

/**
 * An entry of the trash as its list shows it: what was deleted and when, and what tells it from another of its kind:
 * the conversation's number of messages, the page as it was recorded, the schedule as it was set.
 */
data class TrashEntry(
    val id: Long,
    val kind: TrashKind,
    /** A page by what was searched for (or its title, or its address), a schedule by its search words, the conversation by the first thing the user asked. */
    val title: String,
    val deletedAt: Long,
    /** Moved here by the retention policy of the recorded pages, not by the user. */
    val retired: Boolean,
    val messages: Int = 0,
    val capture: CaptureRow? = null,
    val schedule: ScheduleRow? = null,
) {
    companion object {
        /** The entry of the trash's row: [payload] is the row deleted, or the cleared conversation's summary. */
        fun of(id: Long, kind: TrashKind, title: String, payload: JSONObject, retired: Boolean, deletedAt: Long) =
            TrashEntry(
                id = id,
                kind = kind,
                title = title,
                deletedAt = deletedAt,
                retired = retired,
                messages = if (kind == TrashKind.CHAT) (payload.wholeOrNull("messages") ?: 0).toInt() else 0,
                capture = if (kind == TrashKind.CAPTURE) captureOf(payload) else null,
                schedule = if (kind == TrashKind.SCHEDULE) scheduleOf(payload) else null,
            )

        private fun captureOf(row: JSONObject) = CaptureRow(
            id = row.wholeOrNull("id") ?: 0,
            site = row.text("site"),
            query = row.text("query"),
            url = row.text("url"),
            title = row.text("title"),
            capturedAt = row.wholeOrNull("captured_at") ?: 0,
            adCount = (row.wholeOrNull("ad_count") ?: 0).toInt(),
            productCount = (row.wholeOrNull("product_count") ?: 0).toInt(),
            resultCount = (row.wholeOrNull("result_count") ?: 0).toInt(),
            source = CaptureSource.of(row.textOrNull("source")),
        )

        private fun scheduleOf(row: JSONObject) = ScheduleRow(
            id = row.wholeOrNull("id") ?: 0,
            site = row.text("site"),
            query = row.text("query"),
            time = row.text("time"),
            days = ScheduleDays.of(row.textOrNull("days")) ?: ScheduleDays.DAILY,
            enabled = (row.wholeOrNull("enabled") ?: 1L) != 0L,
            createdAt = row.wholeOrNull("created_at") ?: 0,
            nextRunAt = row.wholeOrNull("next_run_at"),
        )
    }
}

/** A page of the trash's list, and whether more entries follow it. */
data class TrashPage(val entries: List<TrashEntry>, val more: Boolean)

/**
 * What a restore brought back: how many entries, of which kinds, and where a conversation went beside the one the chat
 * has now ([chat], null when the chat was empty or none came back).
 */
data class Restored(val count: Int, val kinds: Set<TrashKind>, val chat: ChatPlace? = null)

/**
 * Where restored messages went beside the conversation there is now: before it, by their numbers, or after it when
 * those were in use.
 */
enum class ChatPlace { BEFORE, AFTER }

/**
 * The trash, as the desktop's (trash.py): what the user deletes waits here [DAYS] days to be restored. That is the
 * conversation cleared (one entry: every message with what its turn did), a recorded page with its items, a schedule,
 * and the recorded pages the retention policy moves here. The store that deletes moves the row and the rows that went
 * with it here in its own transaction ([discard], [discardChat]) and takes them out of their tables, so nothing that
 * reads those tables meets them: a schedule in the trash never reminds, and a page in it is in no list, export or
 * answer of list_records.
 *
 * A restore puts the rows back by the columns their tables have now, with their numbers (AUTOINCREMENT gives none of
 * them out again), so a chat's card still opens its page; rows whose numbers are in use come back with new ones. A
 * conversation comes back as its messages and their activity, nothing more: what was allowed for it ended when it was
 * cleared (ChatAgent.clear), and each Hermes turn is a session of its own. Only [purge] and [purgeAll], which the trash
 * screen asks about first, and [expire] after [DAYS] delete an entry for good.
 */
class TrashStore(private val db: AppDatabase) {

    /**
     * A page of the entries, the last deleted first: of [kind], or of every kind, after [after] (the last entry of the
     * page before), [limit] at most.
     */
    suspend fun page(kind: TrashKind? = null, after: TrashEntry? = null, limit: Int = PAGE): TrashPage = withDatabase {
        val clauses = mutableListOf<String>()
        val args = mutableListOf<String>()
        if (kind != null) {
            clauses += "kind = ?"
            args += kind.key
        }
        if (after != null) {
            // After the cursor's entry in the list's order, written so that the index is searched from it.
            clauses += "deleted_at <= ? AND (deleted_at < ? OR id < ?)"
            args += listOf(after.deletedAt, after.deletedAt, after.id).map(Long::toString)
        }
        val size = limit.coerceIn(1, MAX_PAGE)
        val where = if (clauses.isEmpty()) "" else "WHERE " + clauses.joinToString(" AND ")
        val found = db.readableDatabase.rawQuery(
            "SELECT id, kind, title, payload, retired, deleted_at FROM trash $where " +
                "ORDER BY deleted_at DESC, id DESC LIMIT ${size + 1}",
            args.toTypedArray(),
        ).use { cursor -> cursor.entries() }
        TrashPage(found.take(size), found.size > size)
    }

    /** How many entries there are of each kind. */
    suspend fun counts(): Map<TrashKind, Int> = withDatabase {
        db.readableDatabase.rawQuery("SELECT kind, COUNT(*) FROM trash GROUP BY kind", null).use { cursor ->
            buildMap {
                while (cursor.moveToNext()) TrashKind.of(cursor.getString(0))?.let { put(it, cursor.getInt(1)) }
            }
        }
    }

    /** The entries of [kind] (of every kind when null), in the list's order, [limit] at most: what 모두 선택 chooses. */
    suspend fun ids(kind: TrashKind? = null, limit: Int = MAX_SELECTED): List<Long> = withDatabase {
        val where = if (kind == null) "" else "WHERE kind = ?"
        db.readableDatabase.rawQuery(
            "SELECT id FROM trash $where ORDER BY deleted_at DESC, id DESC LIMIT ${limit.coerceAtLeast(1)}",
            if (kind == null) null else arrayOf(kind.key),
        ).use { cursor -> cursor.ids() }
    }

    /**
     * Brings the entries [ids] back, each with the rows that went with it, and takes them out of the trash: CHUNK at a
     * time, each chunk all or none. An entry no longer in the trash is passed over. A schedule comes back with no next
     * run: its reminder is queued again from now on (AppGraph.restore), so none comes for a time that passed meanwhile.
     */
    suspend fun restore(ids: Collection<Long>): Restored = withDatabase {
        val database = db.writableDatabase
        val columns = HashMap<String, Set<String>>()
        val kinds = mutableSetOf<TrashKind>()
        var count = 0
        var chat: ChatPlace? = null
        try {
            for (chunk in ids.distinct().chunked(CHUNK)) {
                val done = database.inTransaction {
                    var restored = 0
                    for (id in chunk) {
                        val (kind, payload) = database.entry(id) ?: continue
                        database.putBack(id, kind, payload) { table ->
                            columns.getOrPut(table) { database.columns(table) }
                        }?.let { chat = it }
                        database.delete("trash", "id = ?", arrayOf(id.toString()))
                        kinds += kind
                        restored++
                    }
                    restored
                }
                count += done
            }
        } catch (unreadable: JSONException) {
            // What the app wrote here itself, damaged: nothing of that chunk came back.
            throw StoreException(StoreProblem.FAILED, unreadable)
        } finally {
            if (count > 0) db.changed()
        }
        Restored(count, kinds, chat)
    }

    /** Deletes the entries [ids] for good, with the rows that went with each: how many there were. */
    suspend fun purge(ids: Collection<Long>): Int = withDatabase {
        val database = db.writableDatabase
        val purged = database.inTransaction {
            // IDs are numbers: they go into the statement as they are.
            ids.distinct().chunked(CHUNK).sumOf { chunk ->
                database.delete("trash", "id IN (${chunk.joinToString(",")})", null)
            }
        }
        if (purged > 0) db.changed()
        purged
    }

    /** Deletes every entry for good (휴지통 비우기): how many there were. */
    suspend fun purgeAll(): Int = withDatabase {
        val purged = db.writableDatabase.delete("trash", "1", null)
        if (purged > 0) db.changed()
        purged
    }

    /** Deletes for good what has waited [DAYS] days by [now]: how many entries. */
    suspend fun expire(now: Long = System.currentTimeMillis()): Int = withDatabase {
        val expired = db.writableDatabase.delete("trash", "deleted_at < ?", arrayOf((now - DAYS * DAY_MS).toString()))
        if (expired > 0) db.changed()
        expired
    }

    private fun Cursor.entries(): List<TrashEntry> {
        val list = ArrayList<TrashEntry>(count)
        while (moveToNext()) {
            // A kind only a later version knows waits for its days; this one does not list it.
            val kind = TrashKind.of(string("kind")) ?: continue
            val payload = try {
                JSONObject(string("payload") ?: "{}")
            } catch (_: JSONException) {
                JSONObject()
            }
            list += TrashEntry.of(long("id"), kind, string("title").orEmpty(), payload, int("retired") != 0, long("deleted_at"))
        }
        return list
    }

    companion object {
        /** How long an entry waits before it is deleted for good, as on the desktop (service.TRASH_DAYS). */
        const val DAYS = 30
        const val DAY_MS = 24 * 60 * 60 * 1000L

        /** The entries a page of the list holds, and the most one read may ask for. */
        const val PAGE = 100
        const val MAX_PAGE = 5000

        /** The most entries 모두 선택 chooses. */
        const val MAX_SELECTED = 10_000

        /** Entries one transaction of a restore or a purge takes. */
        private const val CHUNK = 500
    }
}

/** What the list calls a row put in the trash, as [TrashEntry.title] says, at most this long. */
private const val TITLE_CHARS = 200

/** A page by what was searched for (or its title, or its address), a schedule by its search words. */
internal fun trashTitle(kind: TrashKind, row: JSONObject): String = cleanText(
    when (kind) {
        TrashKind.CAPTURE -> row.text("query").ifBlank { row.text("title") }.ifBlank { row.text("url") }
        TrashKind.SCHEDULE -> row.text("query")
        TrashKind.CHAT -> ""
    },
    TITLE_CHARS,
)

/** The table a row of [kind] lives in, and the rows that go with it: (table, the column of its owner's ID). */
private fun tableOf(kind: TrashKind): String = when (kind) {
    TrashKind.CAPTURE -> "captures"
    TrashKind.SCHEDULE -> "schedules"
    TrashKind.CHAT -> "messages"
}

private fun relatedOf(kind: TrashKind): List<Pair<String, String>> = when (kind) {
    TrashKind.CAPTURE -> listOf("items" to "capture_id")
    TrashKind.SCHEDULE, TrashKind.CHAT -> emptyList()
}

/**
 * Moves the rows [ids] of [kind] (pages, schedules) to the trash within the caller's transaction: each to an entry of
 * its own, the rows that go with it kept one by one, then deleted from their tables (a page's items with it, by their
 * foreign key). The entries made, in the order of [ids]; an ID whose row is not there makes none. [retired]: the
 * retention policy moved them, not the user.
 */
internal fun SQLiteDatabase.discard(kind: TrashKind, ids: Collection<Long>, now: Long, retired: Boolean = false): List<Long> {
    require(kind != TrashKind.CHAT) { "The conversation goes whole: discardChat" }
    val table = tableOf(kind)
    val made = ArrayList<Long>()
    for (id in ids.distinct()) {
        val key = arrayOf(id.toString())
        val row = rawQuery("SELECT * FROM $table WHERE id = ?", key).use { if (it.moveToFirst()) it.json() else null }
            ?: continue
        val entry = insertOrThrow("trash", null, entryValues(kind, trashTitle(kind, row), row, retired, now))
        for ((related, owner) in relatedOf(kind)) keep(entry, related, "SELECT * FROM $related WHERE $owner = ?", key)
        delete(table, "id = ?", key)
        made += entry
    }
    return made
}

/**
 * Moves the conversation to the trash within the caller's transaction, as one entry named by the first thing the user
 * asked: every message, with what each turn did. The entry made, or null when there is no message.
 */
internal fun SQLiteDatabase.discardChat(now: Long): Long? {
    val (count, first, last) = rawQuery("SELECT COUNT(*), MIN(created_at), MAX(created_at) FROM messages", null).use {
        it.moveToFirst()
        Triple(it.getInt(0), it.getLong(1), it.getLong(2))
    }
    if (count == 0) return null
    val asked = rawQuery(
        "SELECT text FROM messages WHERE role = ? AND trim(text, char(32, 9, 10, 13)) != '' ORDER BY id LIMIT 1",
        arrayOf(MessageRole.USER.key),
    ).use { if (it.moveToFirst()) it.getString(0) else "" }
    val summary = JSONObject().put("messages", count).put("first_at", first).put("last_at", last)
    val entry = insertOrThrow("trash", null, entryValues(TrashKind.CHAT, cleanText(asked, TITLE_CHARS), summary, false, now))
    keep(entry, "messages", "SELECT * FROM messages", null)
    keep(entry, "activity", "SELECT * FROM activity", null)
    // What each turn did goes with its message (its foreign key).
    delete("messages", null, null)
    return entry
}

private fun entryValues(kind: TrashKind, title: String, payload: JSONObject, retired: Boolean, now: Long) =
    ContentValues().apply {
        put("kind", kind.key)
        put("title", title)
        put("payload", payload.toString())
        put("retired", if (retired) 1 else 0)
        put("deleted_at", now)
    }

/** Keeps the rows [query] finds with the trash's [entry], one by one under their table's name ([source]). */
private fun SQLiteDatabase.keep(entry: Long, source: String, query: String, args: Array<String>?) {
    rawQuery(query, args).use { cursor ->
        while (cursor.moveToNext()) {
            val row = cursor.json()
            insertOrThrow(
                "trash_rows",
                null,
                ContentValues().apply {
                    put("entry_id", entry)
                    put("source", source)
                    put("row_id", rowId(source, row))
                    put("data", row.toString())
                },
            )
        }
    }
}

/** A kept row's number in its table: a message's or an item's ID, the message a turn's activity belongs to. */
private fun rowId(source: String, row: JSONObject): Long =
    row.wholeOrNull(if (source == "activity") "message_id" else "id") ?: error("A $source row without its number")

/** The row the cursor is at, by column name. */
private fun Cursor.json(): JSONObject {
    val row = JSONObject()
    for (index in 0 until columnCount) {
        val value: Any = when (getType(index)) {
            Cursor.FIELD_TYPE_INTEGER -> getLong(index)
            Cursor.FIELD_TYPE_FLOAT -> getDouble(index)
            Cursor.FIELD_TYPE_STRING -> getString(index)
            // NULL, and a blob, which none of the tables holds.
            else -> JSONObject.NULL
        }
        row.put(getColumnName(index), value)
    }
    return row
}

/** The kind and payload of the entry [id], or null when it is not in the trash (any more). */
private fun SQLiteDatabase.entry(id: Long): Pair<TrashKind, JSONObject>? =
    rawQuery("SELECT kind, payload FROM trash WHERE id = ?", arrayOf(id.toString())).use { cursor ->
        if (!cursor.moveToFirst()) return null
        val kind = TrashKind.of(cursor.getString(0)) ?: return null
        kind to JSONObject(cursor.getString(1))
    }

/**
 * Puts the entry [entry] back: its row and the rows that went with it, by the columns their tables have now
 * ([columns]). For a conversation, where its messages went beside the one there is now.
 */
private fun SQLiteDatabase.putBack(entry: Long, kind: TrashKind, payload: JSONObject, columns: (String) -> Set<String>): ChatPlace? {
    when (kind) {
        TrashKind.CHAT -> {
            val there = rawQuery("SELECT 1 FROM messages LIMIT 1", null).use { it.moveToFirst() }
            val renumbered = inUse(entry, "messages")
            val moved = HashMap<Long, Long>()
            eachKept(entry, "messages") { old, row ->
                moved[old] = insertOrThrow("messages", null, row.values(columns("messages"), renumbered))
            }
            eachKept(entry, "activity") { old, row ->
                val message = moved[old] ?: return@eachKept
                insertOrThrow("activity", null, row.put("message_id", message).values(columns("activity"), false))
            }
            return if (!there) null else if (renumbered) ChatPlace.AFTER else ChatPlace.BEFORE
        }
        TrashKind.CAPTURE -> {
            val taken = taken("captures", payload)
            val capture = insertOrThrow("captures", null, payload.values(columns("captures"), taken))
            val renumbered = inUse(entry, "items")
            eachKept(entry, "items") { _, row ->
                insertOrThrow("items", null, row.put("capture_id", capture).values(columns("items"), renumbered))
            }
        }
        TrashKind.SCHEDULE -> {
            // Its next reminder is worked out anew when it is queued again.
            payload.put("next_run_at", JSONObject.NULL)
            insertOrThrow("schedules", null, payload.values(columns("schedules"), taken("schedules", payload)))
        }
    }
    return null
}

/** Whether the number of [row] is in use in [table] again. */
private fun SQLiteDatabase.taken(table: String, row: JSONObject): Boolean {
    val id = row.wholeOrNull("id") ?: return false
    return rawQuery("SELECT 1 FROM $table WHERE id = ?", arrayOf(id.toString())).use { it.moveToFirst() }
}

/** Whether a number of the rows of [source] kept with [entry] is in use in its table again: they all take new ones then. */
private fun SQLiteDatabase.inUse(entry: Long, source: String): Boolean = rawQuery(
    "SELECT 1 FROM trash_rows r JOIN $source t ON t.id = r.row_id WHERE r.entry_id = ? AND r.source = ? LIMIT 1",
    arrayOf(entry.toString(), source),
).use { it.moveToFirst() }

/** Each row of [source] kept with [entry], in the order of their numbers. */
private inline fun SQLiteDatabase.eachKept(entry: Long, source: String, each: (Long, JSONObject) -> Unit) {
    rawQuery(
        "SELECT row_id, data FROM trash_rows WHERE entry_id = ? AND source = ? ORDER BY row_id",
        arrayOf(entry.toString(), source),
    ).use { cursor ->
        while (cursor.moveToNext()) each(cursor.getLong(0), JSONObject(cursor.getString(1)))
    }
}

/** The columns [table] has now. */
private fun SQLiteDatabase.columns(table: String): Set<String> =
    rawQuery("PRAGMA table_info($table)", null).use { cursor ->
        buildSet { while (cursor.moveToNext()) add(cursor.string("name").orEmpty()) }
    }

/**
 * The row's values for [columns], the table's columns now: a column the row lacks takes its default, and a value whose
 * column is gone is left out, so what an earlier version deleted still comes back. [renumbered]: without its ID, which
 * the table then gives anew.
 */
private fun JSONObject.values(columns: Set<String>, renumbered: Boolean): ContentValues {
    val values = ContentValues()
    for (column in columns) {
        if (!has(column) || (renumbered && column == "id")) continue
        when (val value = opt(column)) {
            null, JSONObject.NULL -> values.putNull(column)
            is String -> values.put(column, value)
            is Boolean -> values.put(column, if (value) 1 else 0)
            is Int, is Long, is Short, is Byte -> values.put(column, (value as Number).toLong())
            is Number -> values.put(column, value.toDouble())
            else -> values.put(column, value.toString())
        }
    }
    return values
}

private fun Cursor.ids(): List<Long> {
    val list = ArrayList<Long>(count)
    while (moveToNext()) list += getLong(0)
    return list
}
