package dev.privateagent.android

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.ui.settings.AgentSection
import dev.privateagent.android.ui.theme.PrivateAgentTheme
import org.junit.After
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * 설정 → 채팅 에이전트: a command allowed for good is listed and can only be taken off, and what was allowed for the
 * conversation is asked about again. The app's own settings on the test device are used, and left as they were found.
 */
@RunWith(AndroidJUnit4::class)
class AgentSectionTest {
    @get:Rule
    val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val graph = (context.applicationContext as PrivateAgentApp).graph
    private val command = "echo private-agent-section-test"

    @After
    fun putBack() {
        graph.grants.revoke(command)
        graph.grants.forgetSession()
    }

    private fun show() {
        compose.setContent {
            PrivateAgentTheme { Column(Modifier.verticalScroll(rememberScrollState())) { AgentSection(graph) } }
        }
        compose.waitForIdle()
    }

    @Test
    fun aCommandAllowedForGoodIsListedAndTakenOffAndTheConversationsAreAskedAboutAgain() {
        assertTrue(graph.grants.allow(command))
        graph.grants.grant(listOf("recursive delete"))
        show()
        compose.onNodeWithText(command).performScrollTo().assertIsDisplayed()
        compose.onNodeWithText("명령 종류 1가지를 이 대화에서 묻지 않습니다.", substring = true).assertExists()
        compose.onNodeWithText("다시 묻기").performScrollTo().performClick()
        compose.onNodeWithText("다시 묻기").assertDoesNotExist()
        assertTrue(graph.grants.session.value.isEmpty())
        compose.onNodeWithContentDescription("허용 취소: $command").performScrollTo().performClick()
        compose.onNodeWithText(command).assertDoesNotExist()
        assertFalse(command in graph.grants.allowed.value)
        // What is closed to the agent is said, and has no switch.
        compose.onNodeWithText("끌 수 없습니다.", substring = true).assertExists()
    }
}
