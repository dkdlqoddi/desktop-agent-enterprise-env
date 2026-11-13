package dev.privateagent.android

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.isToggleable
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.data.NewCapture
import dev.privateagent.android.data.TrashKind
import dev.privateagent.android.ui.ShowNotices
import dev.privateagent.android.ui.settings.SettingsScreen
import dev.privateagent.android.ui.settings.TrashScreen
import dev.privateagent.android.ui.theme.PrivateAgentTheme
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureResult
import dev.privateagent.android.web.CapturedItem
import dev.privateagent.android.web.ItemKind
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID

/**
 * 설정 → 데이터 → 휴지통: what the test deleted is listed, one kind or all; an entry comes back with its own button and
 * several chosen come back together; one is deleted for good only once the question is answered; and 되돌리기 on the
 * message of a delete brings it back. The app's own database on the test device is used, whose trash may hold more:
 * the test looks only at its own entries, and what it made goes for good either way.
 */
@RunWith(AndroidJUnit4::class)
class TrashScreenTest {
    @get:Rule
    val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val graph = (context.applicationContext as PrivateAgentApp).graph
    private val tag = UUID.randomUUID().toString().take(8)

    private suspend fun save(query: String): Long = graph.records.saveCapture(
        NewCapture("coupang", query, CaptureSource.MANUAL, UUID.randomUUID().toString()),
        CaptureResult(
            url = "https://shop.example.com/search",
            title = "검색",
            blocked = false,
            counts = CaptureCounts(ad = 1),
            items = listOf(
                CapturedItem(0, ItemKind.AD, 1, "테스트 광고", "1,000원", "shop.example.com", "https://shop.example.com/p/1", "", "", 0),
            ),
        ),
    )

    private fun waitFor(text: String) = compose.waitUntil(10_000) {
        compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty()
    }

    private fun waitGone(text: String) = compose.waitUntil(10_000) {
        compose.onAllNodesWithText(text).fetchSemanticsNodes().isEmpty()
    }

    /** A button of the bar above the list, which names no entry (each entry's own buttons do). */
    private fun barButton(text: String) =
        compose.onNode(hasText(text) and SemanticsMatcher.keyNotDefined(SemanticsProperties.ContentDescription))

    @Test
    fun whatWasDeletedIsListedByKindAndGoesForGoodOnlyOnceAsked(): Unit = runBlocking {
        val first = "휴지통 기록 A $tag"
        val second = "휴지통 기록 B $tag"
        val pages = listOf(save(first), save(second))
        val schedule = graph.scheduler.create("coupang", "휴지통 예약 $tag", "09:00", ScheduleDays.DAILY)
        try {
            graph.records.deleteCaptures(pages)
            graph.scheduler.delete(schedule.id)
            val counts = graph.trash.counts()
            compose.setContent { PrivateAgentTheme { TrashScreen(graph) {} } }
            val scheduled = "매일 09:00 · 쿠팡 '휴지통 예약 $tag'"
            waitFor(first)
            compose.onNodeWithText(second).assertIsDisplayed()
            compose.onNodeWithText(scheduled).assertIsDisplayed()

            // One kind: the schedules alone, then all again.
            compose.onNodeWithText("예약 ${counts[TrashKind.SCHEDULE]}").performClick()
            waitGone(first)
            compose.onNodeWithText(scheduled).assertIsDisplayed()
            compose.onNodeWithText("전체 ${counts.values.sum()}").performClick()
            waitFor(first)

            // One comes back with its own button, and its reminder is queued again.
            compose.onNodeWithContentDescription("복원: $scheduled").performClick()
            waitGone(scheduled)
            compose.waitUntil(10_000) { runBlocking { graph.schedules.get(schedule.id) }?.nextRunAt != null }
            assertTrue(graph.schedules.get(schedule.id)!!.enabled)

            // Deleted for good only once the question is answered.
            compose.onNodeWithContentDescription("영구 삭제: $second").performClick()
            compose.onNodeWithText("영구 삭제할까요?").assertIsDisplayed()
            compose.onNodeWithText("‘$second’을(를) 영구 삭제합니다", substring = true).assertIsDisplayed()
            compose.onNodeWithText("취소").performClick()
            compose.waitForIdle()
            compose.onNodeWithText(second).assertIsDisplayed()
            compose.onNodeWithContentDescription("영구 삭제: $second").performClick()
            compose.onNodeWithText("영구 삭제").performClick()
            waitGone(second)
            assertTrue(graph.trash.page(TrashKind.CAPTURE, limit = 5000).entries.none { it.capture?.id == pages[1] })
            assertNull(graph.records.capture(pages[1]))
            compose.onNodeWithText(first).assertIsDisplayed()
        } finally {
            pages.forEach { graph.forgetCapture(it) }
            graph.forgetSchedule(schedule.id)
        }
    }

    @Test
    fun severalChosenComeBackTogether(): Unit = runBlocking {
        val names = listOf("함께 복원 A $tag", "함께 복원 B $tag")
        val pages = names.map { save(it) }
        try {
            graph.records.deleteCaptures(pages)
            compose.setContent { PrivateAgentTheme { TrashScreen(graph) {} } }
            waitFor(names[0])
            for (name in names) compose.onNode(hasText(name, substring = true) and isToggleable()).performClick()
            compose.onNodeWithText("2개 선택").assertIsDisplayed()
            barButton("복원").performClick()
            waitGone(names[0])
            waitGone(names[1])
            for (page in pages) assertEquals("테스트 광고", graph.records.items(ItemFilter(captureId = page)).single().title)
        } finally {
            pages.forEach { graph.forgetCapture(it) }
        }
    }

    @Test
    fun theTrashOpensFromTheSettingsAndGoesBackToThem() {
        compose.setContent { PrivateAgentTheme { SettingsScreen(graph) } }
        compose.onNodeWithText("휴지통 열기").performScrollTo().performClick()
        compose.onNodeWithText("30일 동안 여기에 있다가 자동으로 지워집니다", substring = true).assertIsDisplayed()
        compose.onNodeWithContentDescription("뒤로").performClick()
        compose.onNodeWithText("휴지통 열기").assertExists()
    }

    @Test
    fun undoOnTheMessageOfADeleteBringsItBack(): Unit = runBlocking {
        val page = save("되돌리기 확인 $tag")
        try {
            compose.setContent {
                PrivateAgentTheme {
                    val snackbar = remember { SnackbarHostState() }
                    Box(Modifier.fillMaxSize()) {
                        ShowNotices(graph, snackbar)
                        SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter))
                    }
                }
            }
            compose.waitForIdle()
            val entry = graph.records.deleteCapture(page)!!
            assertNull(graph.records.capture(page))
            graph.sayMoved("기록을 휴지통으로 옮겼습니다.", listOf(entry))
            waitFor("되돌리기")
            compose.onNodeWithText("기록을 휴지통으로 옮겼습니다.").assertIsDisplayed()
            compose.onNodeWithText("되돌리기").performClick()
            compose.waitUntil(10_000) { runBlocking { graph.records.capture(page) } != null }
            assertTrue(graph.trash.page(TrashKind.CAPTURE, limit = 5000).entries.none { it.id == entry })
        } finally {
            graph.forgetCapture(page)
        }
    }
}
