package dev.privateagent.android

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.test.performTextReplacement
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.ui.settings.AgentSection
import dev.privateagent.android.ui.theme.PrivateAgentTheme
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * 설정 → 채팅 에이전트: a command allowed for good is listed and can only be taken off, what was allowed for the
 * conversation is asked about again, and the agent's memory is added to, corrected and deleted entry by entry. The
 * app's own settings and the agent's home on the test device are used: its two memory files are put back as they were
 * found, and the records of earlier turns go, as the test asks for.
 */
@RunWith(AndroidJUnit4::class)
class AgentSectionTest {
    @get:Rule
    val compose = createComposeRule()

    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val graph = (context.applicationContext as PrivateAgentApp).graph
    private val home = graph.hermesRuntime.home
    private val files = listOf("memories/MEMORY.md", "memories/USER.md").map { File(home, it) }
    private val kept = HashMap<File, ByteArray?>()
    private val command = "echo private-agent-section-test"

    @Before
    fun setAside() {
        for (file in files) {
            kept[file] = file.takeIf { it.isFile }?.readBytes()
            file.delete()
        }
    }

    @After
    fun putBack() {
        for ((file, bytes) in kept) {
            if (bytes == null) {
                file.delete()
            } else {
                file.parentFile!!.mkdirs()
                file.writeBytes(bytes)
            }
        }
        graph.grants.revoke(command)
        graph.grants.forgetSession()
    }

    private fun show() {
        compose.setContent {
            PrivateAgentTheme { Column(Modifier.verticalScroll(rememberScrollState())) { AgentSection(graph) } }
        }
        // The memory is read off the main thread: its two lists are there, with their sizes, once it is.
        compose.waitUntil(10_000) { compose.onAllNodesWithText("0/2200자").fetchSemanticsNodes().isNotEmpty() }
    }

    private fun memory() = File(home, "memories/MEMORY.md")

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

    @Test
    fun aMemoryIsAddedCorrectedAndDeletedAndTheRecordsOfTheTurnsAreDeletedWhole() {
        File(home, "state.db").apply { parentFile!!.mkdirs() }.writeText("records of turns")
        show()
        compose.onNodeWithText("0/2200자").assertExists()
        compose.onNodeWithText("0/1375자").assertExists()

        // Added: the agent's own notes are the first of the two lists.
        compose.onAllNodesWithText("항목 추가")[0].performScrollTo().performClick()
        compose.onNodeWithText("저장").assertIsNotEnabled()
        compose.onNode(hasSetTextAction()).performTextInput("쿠팡은 로그인이 필요하다")
        compose.onNodeWithText("저장").performClick()
        compose.waitUntil(10_000) { memory().isFile && memory().readText() == "쿠팡은 로그인이 필요하다" }
        compose.onNodeWithText("쿠팡은 로그인이 필요하다").assertExists()
        compose.onNodeWithText("13/2200자").assertExists()

        // Corrected.
        compose.onNodeWithContentDescription("고치기: 쿠팡은 로그인이 필요하다").performScrollTo().performClick()
        compose.onNode(hasSetTextAction()).performTextReplacement("쿠팡은 앱에서 로그인해야 한다")
        compose.onNodeWithText("저장").performClick()
        compose.waitUntil(10_000) { memory().readText() == "쿠팡은 앱에서 로그인해야 한다" }
        compose.onNodeWithText("쿠팡은 로그인이 필요하다").assertDoesNotExist()

        // Deleted, after asking.
        compose.onNodeWithContentDescription("지우기: 쿠팡은 앱에서 로그인해야 한다").performScrollTo().performClick()
        compose.onNodeWithText("이 기억을 지울까요?").assertIsDisplayed()
        compose.onNodeWithText("지우기").performClick()
        compose.waitUntil(10_000) { memory().readText().isEmpty() }
        compose.onNodeWithText("쿠팡은 앱에서 로그인해야 한다").assertDoesNotExist()

        // The records of the turns: deleted whole, after asking; the button is then off.
        compose.onNodeWithText("작업 기록 지우기").performScrollTo().performClick()
        compose.onNodeWithText("작업 기록을 지울까요?").assertIsDisplayed()
        compose.onNodeWithText("지우기").performClick()
        compose.waitUntil(15_000) { !File(home, "state.db").exists() }
        compose.onNodeWithText("에이전트가 남긴 작업 기록이 없습니다.").assertExists()
        compose.onNodeWithText("작업 기록 지우기").assertIsNotEnabled()
        assertEquals("", memory().readText())
    }
}
