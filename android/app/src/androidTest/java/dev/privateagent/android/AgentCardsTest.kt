package dev.privateagent.android

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.privateagent.android.agent.Activity
import dev.privateagent.android.agent.ActivityStep
import dev.privateagent.android.agent.StepKind
import dev.privateagent.android.ui.chat.ActivityNote
import dev.privateagent.android.ui.theme.PrivateAgentTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** The list of what a turn did, under the message that ended it. */
@RunWith(AndroidJUnit4::class)
class AgentCardsTest {
    @get:Rule
    val compose = createComposeRule()

    @Test
    fun whatATurnDidIsListedWhenAskedFor() {
        val activity = Activity(
            listOf(
                ActivityStep(StepKind.TOOL, "웹 검색: 노트북", "", "", 0),
                ActivityStep(StepKind.ACTION, "광고·상품 기록", "광고 5건을 기록했습니다.", "done", 0),
                ActivityStep(StepKind.APPROVAL, "git push", "원격에 올립니다", "session", 0),
                ActivityStep(StepKind.QUESTION, "어느 사이트에서 찾을까요?", "", "timeout", 0),
            ),
            more = 2,
        )
        compose.setContent { PrivateAgentTheme { ActivityNote(activity) } }
        // Closed until asked for: only how many things the turn did.
        compose.onNodeWithText("한 일 6건").assertIsDisplayed()
        compose.onNodeWithText("git push").assertDoesNotExist()
        compose.onNodeWithText("한 일 6건").performClick()
        val shown = listOf(
            "도구", "웹 검색: 노트북", "앱 작업", "광고·상품 기록", "광고 5건을 기록했습니다.", "완료", "명령 확인", "git push",
            "원격에 올립니다", "이 대화에서 허용", "질문", "어느 사이트에서 찾을까요?", "응답 없음", "그 뒤의 2건은 적지 않았습니다.",
        )
        for (text in shown) compose.onNodeWithText(text).assertIsDisplayed()
        compose.onNodeWithText("한 일 6건").performClick()
        compose.onNodeWithText("git push").assertDoesNotExist()
    }
}
