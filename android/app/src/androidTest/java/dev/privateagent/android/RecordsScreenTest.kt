package dev.privateagent.android

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.data.NewCapture
import dev.privateagent.android.ui.RecordsView
import dev.privateagent.android.ui.records.RecordsScreen
import dev.privateagent.android.ui.theme.PrivateAgentTheme
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureResult
import dev.privateagent.android.web.CapturedItem
import dev.privateagent.android.web.ItemKind
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID

/**
 * 기록 → 기록한 페이지: one recorded page goes, with its items, once the user confirms, and a question the user
 * cancels deletes nothing. The app's own database on the test device is used; the test's page goes either way.
 */
@RunWith(AndroidJUnit4::class)
class RecordsScreenTest {
    @get:Rule
    val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
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
            compose.onNodeWithText("항목 1건을 지웁니다", substring = true).assertIsDisplayed()
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
        } finally {
            graph.records.deleteCapture(id)
            graph.navigator.recordsView.value = RecordsView.ITEMS
        }
    }
}
