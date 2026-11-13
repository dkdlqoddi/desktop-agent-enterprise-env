package dev.privateagent.android

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasAnyAncestor
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isDialog
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.data.NewCapture
import dev.privateagent.android.data.TrashKind
import dev.privateagent.android.ui.RecordsView
import dev.privateagent.android.ui.records.RecordsScreen
import dev.privateagent.android.ui.settings.SettingsScreen
import dev.privateagent.android.ui.theme.PrivateAgentTheme
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureResult
import dev.privateagent.android.web.CapturedItem
import dev.privateagent.android.web.ItemKind
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File
import java.util.UUID
import java.util.regex.Pattern

/**
 * 기록 → 기록한 페이지: one recorded page goes to the trash, with its items, once the user confirms, and comes back from
 * there; a question the user cancels deletes nothing; several chosen go together. 기록 → CSV 내보내기 opens the
 * system's share sheet with the file of the items the filter finds. 설정 → 데이터 → 기록 보관 기간 is off until the user
 * chooses a number of days. The app's own database and settings on the test device are used; the test's pages go for
 * good either way, and the setting is put back.
 */
@RunWith(AndroidJUnit4::class)
class RecordsScreenTest {
    @get:Rule
    val compose = createComposeRule()

    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val graph = (context.applicationContext as PrivateAgentApp).graph

    private val ad = CapturedItem(
        ref = 0,
        kind = ItemKind.AD,
        position = 1,
        title = "테스트 광고",
        price = "1,000원",
        displayUrl = "shop.example.com",
        link = "https://shop.example.com/p/1",
        image = "",
        text = "테스트 광고 1,000원",
        top = 0,
    )

    @Test
    fun oneRecordedPageGoesWithItsItemsOnceTheUserConfirms(): Unit = runBlocking {
        val query = "지우기 확인 " + UUID.randomUUID().toString().take(8)
        val result = CaptureResult(
            url = "https://shop.example.com/search",
            title = "검색",
            blocked = false,
            counts = CaptureCounts(ad = 1),
            items = listOf(ad),
        )
        val id = graph.records.saveCapture(
            NewCapture("coupang", query, CaptureSource.MANUAL, UUID.randomUUID().toString()),
            result,
        )
        try {
            graph.navigator.recordsView.value = RecordsView.CAPTURES
            compose.setContent { PrivateAgentTheme { RecordsScreen(graph) } }
            val delete = "기록 지우기: 쿠팡 '$query'"
            compose.waitUntil(10_000) {
                compose.onAllNodesWithContentDescription(delete).fetchSemanticsNodes().isNotEmpty()
            }

            compose.onNodeWithContentDescription(delete).performClick()
            compose.onNodeWithText("이 기록을 지울까요?").assertIsDisplayed()
            compose.onNodeWithText("항목 1건을 휴지통으로 옮깁니다", substring = true).assertIsDisplayed()
            compose.onNodeWithText("취소").performClick()
            compose.waitForIdle()
            assertNotNull(graph.records.capture(id))

            compose.onNodeWithContentDescription(delete).performClick()
            compose.onNodeWithText("지우기").performClick()
            compose.waitUntil(10_000) {
                compose.onAllNodesWithContentDescription(delete).fetchSemanticsNodes().isEmpty()
            }
            assertNull(graph.records.capture(id))
            assertTrue(graph.records.items(ItemFilter(captureId = id)).isEmpty())

            // In the trash with its item, from where it comes back to the list.
            val entry = graph.trash.page(TrashKind.CAPTURE).entries.first { it.capture?.id == id }
            assertEquals(query, entry.title)
            assertEquals(1, graph.restore(listOf(entry.id)).count)
            assertEquals("테스트 광고", graph.records.items(ItemFilter(captureId = id)).single().title)
            compose.waitUntil(10_000) {
                compose.onAllNodesWithContentDescription(delete).fetchSemanticsNodes().isNotEmpty()
            }
        } finally {
            graph.forgetCapture(id)
            graph.navigator.recordsView.value = RecordsView.ITEMS
        }
    }

    @Test
    fun severalRecordedPagesChosenGoToTheTrashTogether(): Unit = runBlocking {
        val tag = UUID.randomUUID().toString().take(8)
        val queries = listOf("함께 지우기 A $tag", "함께 지우기 B $tag", "남기기 $tag")
        val result = CaptureResult(
            url = "https://shop.example.com/search",
            title = "검색",
            blocked = false,
            counts = CaptureCounts(ad = 1),
            items = listOf(ad),
        )
        val ids = queries.map { query ->
            graph.records.saveCapture(NewCapture("coupang", query, CaptureSource.MANUAL, UUID.randomUUID().toString()), result)
        }
        try {
            graph.navigator.recordsView.value = RecordsView.CAPTURES
            compose.setContent { PrivateAgentTheme { RecordsScreen(graph) } }
            compose.waitUntil(10_000) {
                compose.onAllNodesWithContentDescription("선택: 쿠팡 '${queries[0]}'").fetchSemanticsNodes().isNotEmpty()
            }
            compose.onNodeWithContentDescription("선택: 쿠팡 '${queries[0]}'").performClick()
            compose.onNodeWithContentDescription("선택: 쿠팡 '${queries[1]}'").performClick()
            compose.onNodeWithText("2개 선택").assertIsDisplayed()

            // Asked first: a cancel deletes nothing, then both go together, the third stays.
            compose.onNodeWithText("지우기").performClick()
            compose.onNodeWithText("선택한 기록 2개를 지울까요?").assertIsDisplayed()
            compose.onNodeWithText("취소").performClick()
            compose.waitForIdle()
            assertNotNull(graph.records.capture(ids[0]))
            compose.onNodeWithText("지우기").performClick()
            compose.onNode(hasText("지우기") and hasAnyAncestor(isDialog())).performClick()
            compose.waitUntil(10_000) {
                compose.onAllNodesWithContentDescription("선택: 쿠팡 '${queries[0]}'").fetchSemanticsNodes().isEmpty()
            }
            assertNull(graph.records.capture(ids[0]))
            assertNull(graph.records.capture(ids[1]))
            assertNotNull(graph.records.capture(ids[2]))
            compose.onNodeWithText("모두 선택").assertIsDisplayed()
            // Each an entry of its own in the trash, with its item.
            val entries = graph.trash.page(TrashKind.CAPTURE).entries.filter { it.capture?.id in ids }
            assertEquals(setOf(ids[0], ids[1]), entries.map { it.capture!!.id }.toSet())
            assertEquals(2, graph.restore(entries.map { it.id }).count)
            assertEquals("테스트 광고", graph.records.items(ItemFilter(captureId = ids[1])).single().title)
        } finally {
            ids.forEach { graph.forgetCapture(it) }
            graph.navigator.recordsView.value = RecordsView.ITEMS
        }
    }

    @Test
    fun howLongTheRecordsAreKeptIsChosenInTheSettings() {
        val before = graph.settings.recordsKept.value
        try {
            graph.settings.setRecordsKept(0)
            compose.setContent { PrivateAgentTheme { SettingsScreen(graph) } }
            compose.onNodeWithText("지울 때까지 보관합니다", substring = true).performScrollTo().assertIsDisplayed()
            compose.onNodeWithContentDescription("기록 보관 기간: 계속 보관").performScrollTo().performClick()
            compose.onNodeWithText("90일").performClick()
            compose.waitForIdle()
            assertEquals(90, graph.settings.recordsKept.value)
            compose.onNodeWithContentDescription("기록 보관 기간: 90일").assertExists()
            compose.onNodeWithText("90일이 지난 기록은", substring = true).assertExists()
            // The desktop's choices, the longest a year.
            compose.onNodeWithContentDescription("기록 보관 기간: 90일").performClick()
            for (choice in listOf("계속 보관", "7일", "30일", "180일", "1년")) compose.onNodeWithText(choice).assertExists()
            compose.onNodeWithText("1년").performClick()
            compose.waitForIdle()
            assertEquals(365, graph.settings.recordsKept.value)
        } finally {
            graph.settings.setRecordsKept(before)
        }
    }

    @Test
    fun theCsvExportOpensTheShareSheetWithTheFile(): Unit = runBlocking {
        val query = "CSV 공유 확인 " + UUID.randomUUID().toString().take(8)
        val result = CaptureResult(
            url = "https://shop.example.com/search",
            title = "검색",
            blocked = false,
            counts = CaptureCounts(ad = 1),
            items = listOf(ad),
        )
        val id = graph.records.saveCapture(
            NewCapture("coupang", query, CaptureSource.MANUAL, UUID.randomUUID().toString()),
            result,
        )
        val device = UiDevice.getInstance(instrumentation)
        try {
            graph.navigator.showRecords(ItemFilter(captureId = id))
            compose.setContent { PrivateAgentTheme { RecordsScreen(graph) } }
            compose.waitUntil(10_000) { compose.onAllNodesWithText("테스트 광고").fetchSemanticsNodes().isNotEmpty() }
            compose.onNodeWithContentDescription("CSV 내보내기").performClick()

            // The system's share sheet comes up (its own app from Android 14 on).
            val sheet = By.pkg(Pattern.compile("com\\.android\\.intentresolver|android"))
            assertTrue("the share sheet", device.wait(Until.hasObject(sheet), 15_000))
            // What it shares is the CSV of the page's item, with the byte order mark spreadsheets read UTF-8 by.
            val file = File(context.cacheDir, "exports").listFiles()!!.single()
            assertTrue(file.name, file.name.startsWith("records-") && file.name.endsWith(".csv"))
            val text = file.readText(Charsets.UTF_8)
            assertEquals(0xFEFF, text.first().code)
            assertTrue(text.contains(query) && text.contains("테스트 광고"))
            device.pressBack()
            assertTrue("the share sheet closes", device.wait(Until.gone(sheet), 10_000))
        } finally {
            graph.forgetCapture(id)
            graph.navigator.recordsFilter.value = ItemFilter()
        }
    }
}
