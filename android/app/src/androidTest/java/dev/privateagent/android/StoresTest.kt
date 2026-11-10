package dev.privateagent.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.agent.RecordKindFilter
import dev.privateagent.android.agent.Texts
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.AppDatabase
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.NewCapture
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.export.CsvExporter
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureResult
import dev.privateagent.android.web.CapturedItem
import dev.privateagent.android.web.ItemKind
import dev.privateagent.android.web.Sites
import dev.privateagent.android.web.WebScripts
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** The SQL of the stores, on the device's SQLite, in a database that lives in memory only. */
@RunWith(AndroidJUnit4::class)
class StoresTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val database = AppDatabase(context, name = null)
    private val records = RecordStore(database)
    private val messages = MessageStore(database)

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
        items = listOf(
            item(0, ItemKind.AD, "노트북 A"),
            item(1, ItemKind.PRODUCT, "노트북 B, 100% 할인_특가"),
            item(2, ItemKind.AD, "의자 C"),
        ),
    )

    @Test
    fun capturesAreSavedFoundAndFiltered() = runBlocking {
        val token = "0f8fad5b-d9cb-469f-a165-70867728950e"
        val id = records.saveCapture(NewCapture("coupang", "노트북", CaptureSource.CHAT, token, capturedAt = 1_000L), result)
        assertEquals(id, records.captureIdForToken(token))
        assertNull(records.captureIdForToken("ffffffff-0000-0000-0000-000000000000"))
        assertEquals(3, records.items(ItemFilter()).size)
        assertEquals(CaptureCounts(ad = 2, product = 1), records.counts(ItemFilter()))
        assertEquals(listOf("노트북 A", "의자 C"), records.items(ItemFilter(kind = RecordKindFilter.AD)).map { it.title })
        assertEquals(1, records.items(ItemFilter(search = "의자")).size)
        assertEquals(3, records.items(ItemFilter(search = "노트북")).size) // the capture's query matches too
        // % and _ are letters in a search, not wildcards.
        assertEquals(listOf("노트북 B, 100% 할인_특가"), records.items(ItemFilter(search = "100% 할인_")).map { it.title })
        assertEquals(0, records.items(ItemFilter(search = "0%할")).size)
        assertEquals(0, records.items(ItemFilter(site = "amazon")).size)
        assertEquals(3, records.items(ItemFilter(captureId = id)).size)
        val capture = records.captures().single()
        assertEquals(2, capture.adCount)
        assertEquals(CaptureSource.CHAT, capture.source)
    }

    @Test
    fun aClickAndItsLandingPageAreRecorded() = runBlocking {
        val id = records.saveCapture(NewCapture("naver", "의자", CaptureSource.SCHEDULE, "11111111-2222-3333-4444-555555555555"), result)
        assertEquals(0, records.items(ItemFilter(kind = RecordKindFilter.CLICKED)).size)
        val itemId = records.markClicked(id, ref = 2, clickedAt = 5_000L, href = "https://shop.example.com/p/2")!!
        records.setLanding(itemId, "https://brand.example.com/landing", "브랜드 랜딩")
        val clicked = records.items(ItemFilter(kind = RecordKindFilter.CLICKED)).single()
        assertEquals("의자 C", clicked.title)
        assertEquals(5_000L, clicked.clickedAt)
        assertEquals("https://shop.example.com/p/2", clicked.clickedUrl)
        assertEquals("https://brand.example.com/landing", clicked.landingUrl)
        assertEquals("브랜드 랜딩", clicked.landingTitle)
        assertNull(records.markClicked(id, ref = 99, clickedAt = 1L, href = ""))
        // A product the user tapped is recorded, but is not a clicked ad.
        records.markClicked(id, ref = 1, clickedAt = 6_000L, href = "")
        assertEquals(1, records.items(ItemFilter(kind = RecordKindFilter.CLICKED)).size)
        records.clear()
        assertTrue(records.items(ItemFilter()).isEmpty())
    }

    @Test
    fun anActionRunsOnlyOnce() = runBlocking {
        val id = messages.addAction("web_capture", "{\"site\":\"coupang\",\"query\":\"노트북\"}", ActionState.PENDING)
        assertTrue(messages.moveAction(id, ActionState.PENDING, ActionState.RUNNING))
        assertFalse(messages.moveAction(id, ActionState.PENDING, ActionState.RUNNING))
        assertFalse(messages.moveAction(id, ActionState.PENDING, ActionState.CANCELLED))
        assertTrue(messages.moveAction(id, ActionState.RUNNING, ActionState.DONE, "광고 2건을 기록했습니다.", captureId = 7))
        val saved = messages.get(id)!!
        assertEquals(ActionState.DONE, saved.state)
        assertEquals(7L, saved.captureId)
        val running = messages.addAction("open_site", "{}", ActionState.RUNNING)
        messages.failInterrupted("중단")
        assertEquals(ActionState.FAILED, messages.get(running)!!.state)
    }

    @Test
    fun theCsvExportIsAUtf8FileWithAByteOrderMark() = runBlocking {
        records.saveCapture(NewCapture("coupang", "노트북", CaptureSource.MANUAL, "99999999-2222-3333-4444-555555555555"), result)
        val sites = Sites.parse(WebScripts.read(context.assets, "sites.json"))
        val uri = CsvExporter.export(context, records.items(ItemFilter()), Texts(context) { sites })
        val bytes = context.contentResolver.openInputStream(uri)!!.use { it.readBytes() }
        assertEquals(listOf(0xEF, 0xBB, 0xBF), bytes.take(3).map { it.toInt() and 0xFF })
        val lines = String(bytes, Charsets.UTF_8).removePrefix("\uFEFF").split("\r\n")
        assertEquals(context.resources.getStringArray(R.array.csv_header).joinToString(","), lines[0])
        assertEquals(4, lines.count { it.isNotEmpty() })
        assertTrue(lines.any { it.contains("\"노트북 B, 100% 할인_특가\"") })
        assertTrue(lines.drop(1).all { it.isEmpty() || it.contains("쿠팡") })
    }
}
