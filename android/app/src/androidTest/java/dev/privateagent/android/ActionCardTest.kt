package dev.privateagent.android

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.agent.Texts
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.ChatMessage
import dev.privateagent.android.data.MessageRole
import dev.privateagent.android.ui.chat.ActionCard
import dev.privateagent.android.ui.theme.PrivateAgentTheme
import dev.privateagent.android.web.Sites
import dev.privateagent.android.web.WebScripts
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** The approval card of a tool call: it asks before it runs and says what happened only once it happened. */
@RunWith(AndroidJUnit4::class)
class ActionCardTest {
    @get:Rule
    val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val sites = Sites.parse(WebScripts.read(context.assets, "sites.json"))
    private val texts = Texts(context) { sites }

    private fun message(state: ActionState, result: String? = null, captureId: Long? = null) = ChatMessage(
        id = 1,
        role = MessageRole.ACTION,
        text = "",
        createdAt = 0,
        tool = "web_capture",
        args = "{\"site\":\"coupang\",\"query\":\"노트북\"}",
        state = state,
        result = result,
        captureId = captureId,
    )

    @Test
    fun aPendingCardAsksBeforeItRuns() {
        var approved = 0
        var cancelled = 0
        compose.setContent {
            PrivateAgentTheme {
                ActionCard(message(ActionState.PENDING), texts, { approved++ }, { cancelled++ }, {}, {}, {})
            }
        }
        compose.onNodeWithText("승인 대기").assertIsDisplayed()
        compose.onNodeWithText("쿠팡에서 '노트북' 검색 결과를 열고, 보이는 광고와 상품을 기록합니다.").assertIsDisplayed()
        compose.onNodeWithText("취소").assertIsDisplayed()
        compose.onNodeWithText("확인 후 실행").assertIsDisplayed().performClick()
        assertEquals(1, approved)
        assertEquals(0, cancelled)
    }

    @Test
    fun aDoneCardShowsItsResultAndOpensTheRecords() {
        var opened: Long? = null
        compose.setContent {
            PrivateAgentTheme {
                ActionCard(
                    message(ActionState.DONE, result = "광고 5건, 상품 32건을 기록했습니다.", captureId = 42),
                    texts,
                    {},
                    {},
                    { opened = it },
                    {},
                    {},
                )
            }
        }
        compose.onNodeWithText("완료").assertIsDisplayed()
        compose.onNodeWithText("광고 5건, 상품 32건을 기록했습니다.").assertIsDisplayed()
        compose.onNodeWithText("확인 후 실행").assertDoesNotExist()
        compose.onNodeWithText("기록 보기").performClick()
        assertEquals(42L, opened)
    }

    @Test
    fun aFailedCardSaysWhyAndOffersTheBrowser() {
        var browser = false
        compose.setContent {
            PrivateAgentTheme {
                ActionCard(
                    message(ActionState.FAILED, result = context.getString(R.string.result_blocked)),
                    texts,
                    {},
                    {},
                    {},
                    {},
                    { browser = true },
                )
            }
        }
        compose.onNodeWithText("실패").assertIsDisplayed()
        compose.onNodeWithText(context.getString(R.string.result_blocked)).assertIsDisplayed()
        compose.onNodeWithText("브라우저 보기").performClick()
        assertTrue(browser)
    }
}
