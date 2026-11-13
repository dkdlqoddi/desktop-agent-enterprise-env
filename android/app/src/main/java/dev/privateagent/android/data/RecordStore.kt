package dev.privateagent.android.data

import android.content.ContentValues
import android.database.Cursor
import dev.privateagent.android.agent.RecordKindFilter
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureResult
import dev.privateagent.android.web.ItemKind

/** The captures (pages the app read) and their items (ads, products, results), with the user's clicks. */
class RecordStore(private val db: AppDatabase) {

    /** Saves a capture with its items in one transaction; returns the capture's id. */
    suspend fun saveCapture(capture: NewCapture, result: CaptureResult): Long = withDatabase {
        val database = db.writableDatabase
        val id = database.inTransaction {
            val counts = result.counts
            val captureId = database.insertOrThrow(
                "captures",
                null,
                ContentValues().apply {
                    put("site", capture.site)
                    put("query", capture.query)
                    put("url", result.url)
                    put("title", result.title)
                    put("captured_at", capture.capturedAt)
                    put("ad_count", counts.ad)
                    put("product_count", counts.product)
                    put("result_count", counts.result)
                    put("source", capture.source.key)
                    put("token", capture.token)
                },
            )
            for (item in result.items) {
                database.insertOrThrow(
                    "items",
                    null,
                    ContentValues().apply {
                        put("capture_id", captureId)
                        put("ref", item.ref)
                        put("kind", item.kind.key)
                        put("position", item.position)
                        put("title", item.title)
                        put("price", item.price)
                        put("display_url", item.displayUrl)
                        put("link_url", item.link)
                        put("image_url", item.image)
                        put("text", item.text)
                    },
                )
            }
            captureId
        }
        db.changed()
        id
    }

    suspend fun captureIdForToken(token: String): Long? = withDatabase {
        db.readableDatabase.rawQuery("SELECT id FROM captures WHERE token = ?", arrayOf(token)).use { cursor ->
            if (cursor.moveToFirst()) cursor.getLong(0) else null
        }
    }

    /** Records the user's click on an item; returns the item's id, or null when the capture has no such item. */
    suspend fun markClicked(captureId: Long, ref: Int, clickedAt: Long, href: String): Long? =
        withDatabase {
            val database = db.writableDatabase
            val itemId = database.rawQuery(
                "SELECT id FROM items WHERE capture_id = ? AND ref = ?",
                arrayOf(captureId.toString(), ref.toString()),
            ).use { cursor -> if (cursor.moveToFirst()) cursor.getLong(0) else null } ?: return@withDatabase null
            database.update(
                "items",
                ContentValues().apply {
                    put("clicked_at", clickedAt)
                    put("clicked_url", href)
                    putNull("landing_url")
                    putNull("landing_title")
                },
                "id = ?",
                arrayOf(itemId.toString()),
            )
            db.changed()
            itemId
        }

    suspend fun setLanding(itemId: Long, url: String, title: String) = withDatabase {
        db.writableDatabase.update(
            "items",
            ContentValues().apply {
                put("landing_url", url)
                put("landing_title", title)
            },
            "id = ?",
            arrayOf(itemId.toString()),
        )
        db.changed()
    }

    /** Items, newest capture first (newest click first for clicked ads). */
    suspend fun items(filter: ItemFilter, limit: Int = 500): List<ItemRow> = withDatabase {
        val (where, args) = where(filter)
        db.readableDatabase.rawQuery(
            "$ITEMS WHERE $where ORDER BY ${order(filter)} LIMIT ${limit.coerceIn(1, 5000)}",
            args,
        ).use { cursor -> cursor.items() }
    }

    /**
     * Every item the filter finds, in the order of [items], handed to [each] one at a time: an export is of all of
     * them, which may be more than memory should hold at once. Returns how many there were.
     */
    suspend fun eachItem(filter: ItemFilter, each: (ItemRow) -> Unit): Int = withDatabase {
        val (where, args) = where(filter)
        db.readableDatabase.rawQuery("$ITEMS WHERE $where ORDER BY ${order(filter)}", args).use { cursor ->
            var count = 0
            while (cursor.moveToNext()) {
                each(cursor.item())
                count++
            }
            count
        }
    }

    private fun order(filter: ItemFilter) = if (filter.kind == RecordKindFilter.CLICKED) {
        "i.clicked_at DESC"
    } else {
        "c.captured_at DESC, c.id DESC, i.position ASC"
    }

    /** How many items of each kind the filter finds, without a limit. */
    suspend fun counts(filter: ItemFilter): CaptureCounts = withDatabase {
        val (where, args) = where(filter)
        var counts = CaptureCounts()
        db.readableDatabase.rawQuery(
            "SELECT i.kind, COUNT(*) FROM items i JOIN captures c ON c.id = i.capture_id WHERE $where GROUP BY i.kind",
            args,
        ).use { cursor ->
            while (cursor.moveToNext()) {
                val count = cursor.getInt(1)
                counts = when (ItemKind.of(cursor.getString(0))) {
                    ItemKind.AD -> counts.copy(ad = count)
                    ItemKind.PRODUCT -> counts.copy(product = count)
                    ItemKind.RESULT -> counts.copy(result = count)
                    null -> counts
                }
            }
        }
        counts
    }

    suspend fun item(id: Long): ItemRow? = withDatabase {
        db.readableDatabase.rawQuery("$ITEMS WHERE i.id = ?", arrayOf(id.toString()))
            .use { cursor -> cursor.items().firstOrNull() }
    }

    suspend fun captures(limit: Int = 300): List<CaptureRow> = withDatabase {
        db.readableDatabase.rawQuery(
            "SELECT * FROM captures ORDER BY captured_at DESC, id DESC LIMIT ${limit.coerceIn(1, 5000)}",
            null,
        ).use { cursor -> cursor.captures() }
    }

    suspend fun capture(id: Long): CaptureRow? = withDatabase {
        db.readableDatabase.rawQuery("SELECT * FROM captures WHERE id = ?", arrayOf(id.toString()))
            .use { cursor -> cursor.captures().firstOrNull() }
    }

    /** 기록 모두 지우기: every recorded page goes to the trash with its items, each an entry of its own. The entries made. */
    suspend fun clear(): List<Long> = withDatabase {
        val database = db.writableDatabase
        val made = database.inTransaction {
            val ids = database.rawQuery("SELECT id FROM captures ORDER BY captured_at, id", null).use { cursor ->
                buildList { while (cursor.moveToNext()) add(cursor.getLong(0)) }
            }
            database.discard(TrashKind.CAPTURE, ids, System.currentTimeMillis())
        }
        if (made.isNotEmpty()) db.changed()
        made
    }

    /** Moves one recorded page to the trash with its items: the entry made, or null when the page is gone already. */
    suspend fun deleteCapture(id: Long): Long? = deleteCaptures(listOf(id)).firstOrNull()

    /**
     * Moves the recorded pages [ids] to the trash at once, each with its items to an entry of its own; a page gone
     * meanwhile is passed over. The entries made.
     */
    suspend fun deleteCaptures(ids: Collection<Long>): List<Long> = withDatabase {
        val database = db.writableDatabase
        val made = database.inTransaction { database.discard(TrashKind.CAPTURE, ids, System.currentTimeMillis()) }
        if (made.isNotEmpty()) db.changed()
        made
    }

    /**
     * The retention policy (Upkeep): the recorded pages captured before [before] go to the trash, the oldest first,
     * each an entry of its own marked as moved by the policy; RETIRE_AT_ONCE a transaction, so that a page being
     * recorded meanwhile waits little. How many went.
     */
    suspend fun retire(before: Long, now: Long = System.currentTimeMillis()): Int = withDatabase {
        val database = db.writableDatabase
        var moved = 0
        do {
            val made = database.inTransaction {
                val ids = database.rawQuery(
                    "SELECT id FROM captures WHERE captured_at < ? ORDER BY captured_at, id LIMIT $RETIRE_AT_ONCE",
                    arrayOf(before.toString()),
                ).use { cursor -> buildList { while (cursor.moveToNext()) add(cursor.getLong(0)) } }
                database.discard(TrashKind.CAPTURE, ids, now, retired = true)
            }
            if (made.isNotEmpty()) db.changed()
            moved += made.size
        } while (made.size == RETIRE_AT_ONCE)
        moved
    }

    private fun where(filter: ItemFilter): Pair<String, Array<String>> {
        val clauses = mutableListOf("1 = 1")
        val args = mutableListOf<String>()
        filter.site?.let {
            clauses += "c.site = ?"
            args += it
        }
        filter.captureId?.let {
            clauses += "c.id = ?"
            args += it.toString()
        }
        when (filter.kind) {
            RecordKindFilter.AD -> clauses += "i.kind = 'ad'"
            RecordKindFilter.PRODUCT -> clauses += "i.kind = 'product'"
            RecordKindFilter.RESULT -> clauses += "i.kind = 'result'"
            RecordKindFilter.CLICKED -> clauses += "i.kind = 'ad' AND i.clicked_at IS NOT NULL"
            null -> Unit
        }
        val search = filter.search.trim()
        if (search.isNotEmpty()) {
            val pattern = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            val columns = listOf(
                "i.title", "i.text", "i.price", "i.display_url", "i.link_url", "i.landing_url", "i.landing_title",
                "c.query",
            )
            clauses += "(" + columns.joinToString(" OR ") { "$it LIKE ? ESCAPE '\\'" } + ")"
            repeat(columns.size) { args += pattern }
        }
        return clauses.joinToString(" AND ") to args.toTypedArray()
    }

    private fun Cursor.items(): List<ItemRow> {
        val list = ArrayList<ItemRow>(count)
        while (moveToNext()) list += item()
        return list
    }

    /** The item of the row the cursor is at. */
    private fun Cursor.item() = ItemRow(
        id = long("id"),
        captureId = long("capture_id"),
        ref = int("ref"),
        kind = ItemKind.of(string("kind")) ?: ItemKind.RESULT,
        position = int("position"),
        title = string("title").orEmpty(),
        price = string("price").orEmpty(),
        displayUrl = string("display_url").orEmpty(),
        linkUrl = string("link_url").orEmpty(),
        imageUrl = string("image_url").orEmpty(),
        text = string("text").orEmpty(),
        clickedAt = longOrNull("clicked_at"),
        clickedUrl = string("clicked_url"),
        landingUrl = string("landing_url"),
        landingTitle = string("landing_title"),
        site = string("c_site").orEmpty(),
        query = string("c_query").orEmpty(),
        capturedAt = long("c_captured_at"),
        pageUrl = string("c_url").orEmpty(),
    )

    private fun Cursor.captures(): List<CaptureRow> {
        val list = ArrayList<CaptureRow>(count)
        while (moveToNext()) {
            list += CaptureRow(
                id = long("id"),
                site = string("site").orEmpty(),
                query = string("query").orEmpty(),
                url = string("url").orEmpty(),
                title = string("title").orEmpty(),
                capturedAt = long("captured_at"),
                adCount = int("ad_count"),
                productCount = int("product_count"),
                resultCount = int("result_count"),
                source = CaptureSource.of(string("source")),
            )
        }
        return list
    }

    private companion object {
        /** The pages one transaction of the retention policy moves to the trash. */
        const val RETIRE_AT_ONCE = 200

        /** The items with the capture each belongs to, as [Cursor.item] reads them. */
        const val ITEMS = "SELECT i.*, c.site AS c_site, c.query AS c_query, c.captured_at AS c_captured_at, " +
            "c.url AS c_url FROM items i JOIN captures c ON c.id = i.capture_id"
    }
}
