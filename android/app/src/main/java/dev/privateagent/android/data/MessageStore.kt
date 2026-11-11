package dev.privateagent.android.data

import android.content.ContentValues
import android.database.Cursor
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

/** The one conversation of the chat screen. */
class MessageStore(private val db: AppDatabase) {

    suspend fun all(limit: Int = 500): List<ChatMessage> = withContext(Dispatchers.IO) {
        // The latest [limit] messages, oldest first.
        db.readableDatabase.rawQuery(
            "SELECT * FROM (SELECT * FROM messages ORDER BY id DESC LIMIT $limit) ORDER BY id ASC",
            null,
        ).use { cursor -> cursor.messages() }
    }

    suspend fun get(id: Long): ChatMessage? = withContext(Dispatchers.IO) {
        db.readableDatabase.rawQuery("SELECT * FROM messages WHERE id = ?", arrayOf(id.toString()))
            .use { cursor -> cursor.messages().firstOrNull() }
    }

    suspend fun addUser(text: String): Long = insert(MessageRole.USER, text)

    suspend fun addAssistant(text: String): Long = insert(MessageRole.ASSISTANT, text)

    suspend fun addNotice(kind: NoticeKind, text: String): Long = insert(MessageRole.NOTICE, text) {
        put("notice", kind.key)
    }

    suspend fun addAction(
        tool: String,
        args: String,
        state: ActionState,
        result: String? = null,
        captureId: Long? = null,
    ): Long = insert(MessageRole.ACTION, "") {
        put("tool", tool)
        put("args", args)
        put("state", state.key)
        if (result != null) put("result", result)
        if (captureId != null) put("capture_id", captureId)
    }

    /**
     * Moves an action from [from] to [state]; false when it was not in [from] (it ran or was cancelled already), so
     * one action never runs twice.
     */
    suspend fun moveAction(
        id: Long,
        from: ActionState,
        state: ActionState,
        result: String? = null,
        captureId: Long? = null,
    ): Boolean = withContext(Dispatchers.IO) {
        val values = ContentValues().apply {
            put("state", state.key)
            if (result != null) put("result", result)
            if (captureId != null) put("capture_id", captureId)
        }
        val moved = db.writableDatabase.update(
            "messages",
            values,
            "id = ? AND role = ? AND state = ?",
            arrayOf(id.toString(), MessageRole.ACTION.key, from.key),
        ) == 1
        if (moved) db.changed()
        moved
    }

    /** Actions that were running when the app stopped can no longer finish: they end as failed. */
    suspend fun failInterrupted(result: String) = withContext(Dispatchers.IO) {
        val values = ContentValues().apply {
            put("state", ActionState.FAILED.key)
            put("result", result)
        }
        val count = db.writableDatabase.update(
            "messages",
            values,
            "role = ? AND state = ?",
            arrayOf(MessageRole.ACTION.key, ActionState.RUNNING.key),
        )
        if (count > 0) db.changed()
    }

    suspend fun clear() = withContext(Dispatchers.IO) {
        db.writableDatabase.delete("messages", null, null)
        db.changed()
    }

    private suspend fun insert(role: MessageRole, text: String, extra: ContentValues.() -> Unit = {}): Long =
        withContext(Dispatchers.IO) {
            val values = ContentValues().apply {
                put("role", role.key)
                put("text", text)
                put("created_at", System.currentTimeMillis())
                extra()
            }
            val id = db.writableDatabase.insertOrThrow("messages", null, values)
            db.changed()
            id
        }

    private fun Cursor.messages(): List<ChatMessage> {
        val list = ArrayList<ChatMessage>(count)
        while (moveToNext()) {
            list += ChatMessage(
                id = long("id"),
                role = MessageRole.of(string("role")),
                text = string("text").orEmpty(),
                createdAt = long("created_at"),
                tool = string("tool"),
                args = string("args"),
                state = ActionState.of(string("state")),
                result = string("result"),
                captureId = longOrNull("capture_id"),
                notice = NoticeKind.of(string("notice")),
            )
        }
        return list
    }
}

internal fun Cursor.long(column: String): Long = getLong(getColumnIndexOrThrow(column))

internal fun Cursor.int(column: String): Int = getInt(getColumnIndexOrThrow(column))

internal fun Cursor.longOrNull(column: String): Long? {
    val index = getColumnIndexOrThrow(column)
    return if (isNull(index)) null else getLong(index)
}

internal fun Cursor.string(column: String): String? {
    val index = getColumnIndexOrThrow(column)
    return if (isNull(index)) null else getString(index)
}
