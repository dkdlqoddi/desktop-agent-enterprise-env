package dev.privateagent.android

import android.database.sqlite.SQLiteDatabase
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.agent.Activity
import dev.privateagent.android.agent.ActivityStep
import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.agent.StepKind
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.AppDatabase
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.ChatPlace
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.NewCapture
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.data.Restored
import dev.privateagent.android.data.ScheduleStore
import dev.privateagent.android.data.TrashKind
import dev.privateagent.android.data.TrashStore
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureResult
import dev.privateagent.android.web.CapturedItem
import dev.privateagent.android.web.ItemKind
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID

/**
 * The trash on the device's SQLite, in a database that lives in memory only: what each delete moves there, how it comes
 * back, and what deletes it for good. And a database of the second version, which gains the trash and keeps its data.
 */
@RunWith(AndroidJUnit4::class)
class TrashTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val database = AppDatabase(context, name = null)
    private val messages = MessageStore(database)
    private val records = RecordStore(database)
    private val schedules = ScheduleStore(database)
    private val trash = TrashStore(database)

    @After
    fun close() = database.close()

    private fun item(ref: Int, kind: ItemKind, title: String) = CapturedItem(
        ref = ref,
        kind = kind,
        position = ref + 1,
        title = title,
        price = "1,000원",
        displayUrl = "shop.example.com",
        link = "https://shop.example.com/p/$ref",
        image = "",
        text = "$title 1,000원",
        top = ref * 100,
    )

    private val result = CaptureResult(
        url = "https://shop.example.com/search?q=x",
        title = "검색",
        blocked = false,
        counts = CaptureCounts(ad = 2, product = 1),
        items = listOf(item(0, ItemKind.AD, "노트북 A"), item(1, ItemKind.PRODUCT, "노트북 B"), item(2, ItemKind.AD, "의자 C")),
    )

    private suspend fun save(query: String, at: Long, token: String = UUID.randomUUID().toString()) =
        records.saveCapture(NewCapture("coupang", query, CaptureSource.CHAT, token, capturedAt = at), result)

    private fun count(table: String, where: String = "1"): Int =
        database.readableDatabase.rawQuery("SELECT count(*) FROM $table WHERE $where", null).use {
            it.moveToFirst()
            it.getInt(0)
        }

    @Test
    fun aRecordedPageGoesToTheTrashWithItsItemsAndComesBackAsItWas() = runBlocking {
        val token = "0f8fad5b-d9cb-469f-a165-70867728950e"
        val id = save("노트북", 1_000L, token)
        val clicked = records.markClicked(id, ref = 2, clickedAt = 2_000L, href = "https://shop.example.com/p/2")!!
        records.setLanding(clicked, "https://brand.example.com/landing", "브랜드 랜딩")
        val before = records.items(ItemFilter(captureId = id))

        val entry = records.deleteCapture(id)!!
        assertNull(records.capture(id))
        assertTrue(records.items(ItemFilter()).isEmpty())
        // Gone already: no second entry.
        assertNull(records.deleteCapture(id))
        val listed = trash.page().entries.single()
        assertEquals(entry, listed.id)
        assertEquals(TrashKind.CAPTURE, listed.kind)
        assertEquals("노트북", listed.title)
        assertEquals(2, listed.capture!!.adCount)
        assertEquals("coupang", listed.capture.site)
        assertFalse(listed.retired)

        assertEquals(Restored(1, setOf(TrashKind.CAPTURE)), trash.restore(listOf(entry)))
        // Its items with their numbers, the click and its landing page; a click on its open page still finds it.
        assertEquals(before, records.items(ItemFilter(captureId = id)))
        assertEquals(id, records.captureIdForToken(token))
        assertTrue(trash.page().entries.isEmpty())
        assertEquals(0, count("trash_rows"))
        // Restored once: there is nothing left to restore.
        assertEquals(0, trash.restore(listOf(entry)).count)
    }

    @Test
    fun severalPagesOrAllGoAsAnEntryEachAndComeBackTogether() = runBlocking {
        val first = save("노트북", 1_000L)
        val second = save("의자", 2_000L)
        save(" ", 3_000L)
        assertEquals(2, records.deleteCaptures(listOf(first, second, first)).size)
        assertEquals(1, records.clear().size)
        assertTrue(records.clear().isEmpty())
        assertTrue(records.captures().isEmpty())
        // The last deleted first; a page searched for nothing goes by its title.
        assertEquals(listOf("검색", "의자", "노트북"), trash.page().entries.map { it.title })
        assertEquals(mapOf(TrashKind.CAPTURE to 3), trash.counts())

        assertEquals(3, trash.restore(trash.ids()).count)
        assertEquals(3, records.captures().size)
        assertEquals(9, records.items(ItemFilter()).size)
    }

    @Test
    fun theClearedConversationIsOneEntryAndComesBackBeforeTheOneThereIsNow() = runBlocking {
        messages.addUser("  쿠팡에서\n노트북 광고   기록해 줘 ")
        val answer = messages.addAssistant("광고 2건을 기록했습니다.")
        messages.addAction("web_capture", "{\"site\":\"coupang\"}", ActionState.DONE, "광고 2건", captureId = 7)
        val activity = Activity(listOf(ActivityStep(StepKind.APPROVAL, "rm -rf build", "", "session", 1_000)), more = 1)
        messages.setActivity(answer, activity)
        val before = messages.all()

        val entry = messages.clear()!!
        assertTrue(messages.all().isEmpty())
        assertEquals(0, count("activity"))
        assertNull(messages.clear())
        val listed = trash.page().entries.single()
        assertEquals(TrashKind.CHAT, listed.kind)
        assertEquals("쿠팡에서 노트북 광고 기록해 줘", listed.title)
        assertEquals(3, listed.messages)

        // A new conversation, then the cleared one back: before it, as it was, with what its turn did.
        val now = messages.addUser("새 대화")
        val restored = trash.restore(listOf(entry))
        assertEquals(Restored(1, setOf(TrashKind.CHAT), ChatPlace.BEFORE), restored)
        val all = messages.all()
        assertEquals(before, all.dropLast(1))
        assertEquals(now, all.last().id)
        assertEquals(activity, messages.get(answer)!!.activity)
    }

    @Test
    fun aScheduleComesBackWithoutTheTimeItWasToRemindAt() = runBlocking {
        val id = schedules.add("coupang", "노트북", "09:00", ScheduleDays.WEEKDAYS)
        schedules.setNextRun(id, 5_000L)
        val entry = schedules.delete(id)!!
        assertNull(schedules.get(id))
        assertNull(schedules.delete(id))
        val listed = trash.page(TrashKind.SCHEDULE).entries.single()
        assertEquals("노트북", listed.title)
        assertEquals("09:00", listed.schedule!!.time)
        assertEquals(ScheduleDays.WEEKDAYS, listed.schedule.days)

        trash.restore(listOf(entry))
        val back = schedules.get(id)!!
        assertEquals(ScheduleDays.WEEKDAYS, back.days)
        assertTrue(back.enabled)
        // Queued again from now on (ScheduleManager.restoreAll), never for a time that passed in the trash.
        assertNull(back.nextRunAt)
    }

    @Test
    fun theListComesInPagesOfOneKindOrOfAll() = runBlocking {
        val made = mutableListOf<Long>()
        repeat(4) { made += schedules.delete(schedules.add("coupang", "예약 $it", "09:00", ScheduleDays.DAILY))!! }
        repeat(3) { made += records.deleteCapture(save("기록 $it", it.toLong()))!! }
        val newestFirst = made.reversed()

        val pages = mutableListOf(trash.page(limit = 3))
        while (pages.last().more) pages += trash.page(after = pages.last().entries.last(), limit = 3)
        assertEquals(listOf(3, 3, 1), pages.map { it.entries.size })
        assertEquals(newestFirst, pages.flatMap { it.entries }.map { it.id })
        assertEquals(newestFirst, trash.ids())
        assertEquals(newestFirst.take(2), trash.ids(limit = 2))

        val captures = trash.page(TrashKind.CAPTURE, limit = 2)
        assertTrue(captures.more)
        assertEquals(newestFirst.take(2), captures.entries.map { it.id })
        assertEquals(newestFirst.subList(2, 3), trash.page(TrashKind.CAPTURE, after = captures.entries.last()).entries.map { it.id })
        assertEquals(newestFirst.drop(3), trash.ids(TrashKind.SCHEDULE))
        assertEquals(mapOf(TrashKind.SCHEDULE to 4, TrashKind.CAPTURE to 3), trash.counts())
    }

    @Test
    fun anEntryGoesForGoodOnlyWhenPurgedOrAfterItsDays() = runBlocking {
        val page = records.deleteCapture(save("노트북", 1_000L))!!
        val schedule = schedules.delete(schedules.add("coupang", "의자", "09:00", ScheduleDays.DAILY))!!
        messages.addUser("안녕")
        val chat = messages.clear()!!

        assertEquals(1, trash.purge(listOf(page, page)))
        assertEquals(0, trash.restore(listOf(page)).count)
        // Its items went with it.
        assertEquals(0, count("trash_rows", "entry_id = $page"))

        val now = System.currentTimeMillis()
        assertEquals(0, trash.expire(now))
        assertEquals(2, trash.expire(now + (TrashStore.DAYS + 1) * TrashStore.DAY_MS))
        assertTrue(trash.page().entries.isEmpty())
        assertEquals(0, count("trash_rows"))
        assertEquals(0, trash.restore(listOf(schedule, chat)).count)

        records.deleteCapture(save("의자", 2_000L))
        schedules.delete(schedules.add("coupang", "책상", "10:00", ScheduleDays.DAILY))
        assertEquals(2, trash.purgeAll())
        assertEquals(0, count("trash"))
        assertEquals(0, count("trash_rows"))
    }

    @Test
    fun aDatabaseOfTheSecondVersionGainsTheTrashAndKeepsWhatItHad() = runBlocking {
        val name = "migration-v2-test.db"
        context.deleteDatabase(name)
        val file = context.getDatabasePath(name).also { it.parentFile!!.mkdirs() }
        SQLiteDatabase.openOrCreateDatabase(file, null).use { old ->
            for (statement in VERSION_2) old.execSQL(statement)
            old.execSQL("INSERT INTO messages (id, role, text, created_at) VALUES (1, 'user', '예전 질문', 1)")
            old.execSQL("INSERT INTO messages (id, role, text, created_at) VALUES (2, 'assistant', '예전 답', 2)")
            old.execSQL(
                "INSERT INTO activity (message_id, steps, more) VALUES " +
                    "(2, '[{\"kind\":\"tool\",\"text\":\"웹 검색\",\"note\":\"\",\"result\":\"\",\"at\":1}]', 0)",
            )
            old.execSQL(
                "INSERT INTO captures (id, site, query, url, title, captured_at, ad_count, source, token) VALUES " +
                    "(5, 'coupang', '노트북', 'https://shop.example.com/', '검색', 3, 1, 'chat', 'token-v2')",
            )
            old.execSQL("INSERT INTO items (capture_id, ref, kind, position, title) VALUES (5, 0, 'ad', 1, '예전 광고')")
            old.execSQL(
                "INSERT INTO schedules (site, query, time, days, enabled, created_at, next_run_at) VALUES " +
                    "('coupang', '의자', '09:00', 'daily', 1, 4, 5)",
            )
            old.version = 2
        }
        val upgraded = AppDatabase(context, name)
        try {
            assertEquals(3, upgraded.readableDatabase.version)
            val messages = MessageStore(upgraded)
            val records = RecordStore(upgraded)
            val schedules = ScheduleStore(upgraded)
            val trash = TrashStore(upgraded)
            // What the second version had is all there.
            assertEquals(listOf("예전 질문", "예전 답"), messages.all().map { it.text })
            assertEquals(1, messages.get(2)!!.activity!!.count)
            assertEquals("예전 광고", records.items(ItemFilter(captureId = 5)).single().title)
            assertEquals(5L, records.captureIdForToken("token-v2"))
            assertEquals(5L, schedules.all().single().nextRunAt)
            assertTrue(trash.page().entries.isEmpty())

            // The trash it gained takes what that version kept, and gives it back.
            val chat = messages.clear()!!
            val page = records.deleteCapture(5)!!
            val schedule = schedules.delete(schedules.all().single().id)!!
            assertEquals(mapOf(TrashKind.CHAT to 1, TrashKind.CAPTURE to 1, TrashKind.SCHEDULE to 1), trash.counts())
            assertEquals(3, trash.restore(listOf(chat, page, schedule)).count)
            assertEquals(listOf("예전 질문", "예전 답"), messages.all().map { it.text })
            assertNotNull(messages.get(2)!!.activity)
            assertEquals("예전 광고", records.items(ItemFilter(captureId = 5)).single().title)
            assertEquals("의자", schedules.all().single().query)
        } finally {
            upgraded.close()
            context.deleteDatabase(name)
        }
    }

    private companion object {
        /** The schema as the second version made it (AppDatabase.VERSION 2), statement by statement. */
        val VERSION_2 = listOf(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, " +
                "text TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, tool TEXT, args TEXT, state TEXT, " +
                "result TEXT, capture_id INTEGER, notice TEXT)",
            "CREATE TABLE captures (id INTEGER PRIMARY KEY AUTOINCREMENT, site TEXT NOT NULL, " +
                "query TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '', " +
                "captured_at INTEGER NOT NULL, ad_count INTEGER NOT NULL DEFAULT 0, " +
                "product_count INTEGER NOT NULL DEFAULT 0, result_count INTEGER NOT NULL DEFAULT 0, " +
                "source TEXT NOT NULL, token TEXT NOT NULL UNIQUE)",
            "CREATE INDEX captures_time ON captures (captured_at)",
            "CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, " +
                "capture_id INTEGER NOT NULL REFERENCES captures (id) ON DELETE CASCADE, ref INTEGER NOT NULL, " +
                "kind TEXT NOT NULL, position INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', " +
                "price TEXT NOT NULL DEFAULT '', display_url TEXT NOT NULL DEFAULT '', " +
                "link_url TEXT NOT NULL DEFAULT '', image_url TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '', " +
                "clicked_at INTEGER, clicked_url TEXT, landing_url TEXT, landing_title TEXT, UNIQUE (capture_id, ref))",
            "CREATE INDEX items_capture ON items (capture_id)",
            "CREATE INDEX items_clicked ON items (clicked_at)",
            "CREATE TABLE schedules (id INTEGER PRIMARY KEY AUTOINCREMENT, site TEXT NOT NULL, query TEXT NOT NULL, " +
                "time TEXT NOT NULL, days TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, " +
                "created_at INTEGER NOT NULL, next_run_at INTEGER)",
            "CREATE TABLE activity (message_id INTEGER PRIMARY KEY REFERENCES messages (id) ON DELETE CASCADE, " +
                "steps TEXT NOT NULL DEFAULT '[]', more INTEGER NOT NULL DEFAULT 0)",
        )
    }
}
