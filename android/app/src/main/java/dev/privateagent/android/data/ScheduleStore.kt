package dev.privateagent.android.data

import android.content.ContentValues
import android.database.Cursor
import dev.privateagent.android.agent.ScheduleDays
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class ScheduleStore(private val db: AppDatabase) {

    suspend fun add(site: String, query: String, time: String, days: ScheduleDays): Long = withContext(Dispatchers.IO) {
        val id = db.writableDatabase.insertOrThrow(
            "schedules",
            null,
            ContentValues().apply {
                put("site", site)
                put("query", query)
                put("time", time)
                put("days", days.key)
                put("enabled", 1)
                put("created_at", System.currentTimeMillis())
            },
        )
        db.changed()
        id
    }

    suspend fun all(): List<ScheduleRow> = withContext(Dispatchers.IO) {
        db.readableDatabase.rawQuery("SELECT * FROM schedules ORDER BY time ASC, id ASC", null)
            .use { cursor -> cursor.schedules() }
    }

    suspend fun get(id: Long): ScheduleRow? = withContext(Dispatchers.IO) {
        db.readableDatabase.rawQuery("SELECT * FROM schedules WHERE id = ?", arrayOf(id.toString()))
            .use { cursor -> cursor.schedules().firstOrNull() }
    }

    suspend fun setEnabled(id: Long, enabled: Boolean) = update(id) { put("enabled", if (enabled) 1 else 0) }

    suspend fun setNextRun(id: Long, at: Long?) = update(id) {
        if (at == null) putNull("next_run_at") else put("next_run_at", at)
    }

    suspend fun delete(id: Long) = withContext(Dispatchers.IO) {
        db.writableDatabase.delete("schedules", "id = ?", arrayOf(id.toString()))
        db.changed()
    }

    private suspend fun update(id: Long, values: ContentValues.() -> Unit) = withContext(Dispatchers.IO) {
        db.writableDatabase.update("schedules", ContentValues().apply(values), "id = ?", arrayOf(id.toString()))
        db.changed()
    }

    private fun Cursor.schedules(): List<ScheduleRow> {
        val list = ArrayList<ScheduleRow>(count)
        while (moveToNext()) {
            list += ScheduleRow(
                id = long("id"),
                site = string("site").orEmpty(),
                query = string("query").orEmpty(),
                time = string("time").orEmpty(),
                days = ScheduleDays.of(string("days")) ?: ScheduleDays.DAILY,
                enabled = int("enabled") != 0,
                createdAt = long("created_at"),
                nextRunAt = longOrNull("next_run_at"),
            )
        }
        return list
    }
}
