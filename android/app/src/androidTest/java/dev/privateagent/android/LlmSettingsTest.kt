package dev.privateagent.android

import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.performTextReplacement
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.ui.settings.SettingsScreen
import dev.privateagent.android.ui.theme.PrivateAgentTheme
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 설정 → LLM 연결: an http:// address typed in the endpoint field is warned about while it is there, unless it is on
 * this phone itself; an https address, or one without a scheme (which is saved as https), is not. Nothing is saved:
 * the app's own settings stay as they were.
 */
@RunWith(AndroidJUnit4::class)
class LlmSettingsTest {
    @get:Rule
    val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val graph = (context.applicationContext as PrivateAgentApp).graph

    @Test
    fun anHttpAddressOffThisPhoneIsWarnedAbout() {
        val saved = graph.settings.llm.value
        compose.setContent { PrivateAgentTheme { SettingsScreen(graph) } }
        val field = compose.onNode(hasSetTextAction() and hasText("전체 API 엔드포인트"))
        val cases = listOf(
            "http://192.0.2.10:11434/v1/chat/completions" to true,
            "HTTP://llm.test/v1/messages" to true,
            "https://llm.test/v1/chat/completions" to false,
            "192.0.2.10:11434/v1/chat/completions" to false,
            "http://127.0.0.1:11434/v1/chat/completions" to false,
            "http://localhost:11434/v1/chat/completions" to false,
        )
        for ((address, warned) in cases) {
            field.performTextReplacement(address)
            compose.waitForIdle()
            val shown = compose.onAllNodesWithText(WARNING, substring = true).fetchSemanticsNodes().isNotEmpty()
            assertEquals(address, warned, shown)
        }
        assertEquals(saved, graph.settings.llm.value)
    }

    private companion object {
        const val WARNING = "암호화되지 않아 API Key와"
    }
}
