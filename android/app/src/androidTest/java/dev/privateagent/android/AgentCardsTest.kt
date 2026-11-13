package dev.privateagent.android

import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.privateagent.android.agent.Activity
import dev.privateagent.android.agent.ActivityStep
import dev.privateagent.android.agent.HermesChat
import dev.privateagent.android.agent.StepKind
import dev.privateagent.android.hermes.HermesSession
import dev.privateagent.android.ui.chat.ActivityNote
import dev.privateagent.android.ui.chat.ApprovalPrompt
import dev.privateagent.android.ui.chat.ClarifyPrompt
import dev.privateagent.android.ui.theme.PrivateAgentTheme
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The card that asks about a command, with the scopes the question offers, the card of Hermes' questions, and the list
 * of what a turn did under the message that ended it.
 */
@RunWith(AndroidJUnit4::class)
class AgentCardsTest {
    @get:Rule
    val compose = createComposeRule()

    private fun asked(session: Boolean, permanent: Boolean, refused: Boolean = false) = HermesChat.Interaction(
        "question-1",
        HermesSession.InteractionKind.APPROVAL,
        JSONObject().put("command", "rm -rf build").put("description", "폴더를 지웁니다").put("allow_session", session)
            .put("allow_permanent", permanent).put("smart_denied", refused),
    )

    @Test
    fun everyScopeTheQuestionOffersHasItsAnswerAndSaysWhatItMeans() {
        val decisions = mutableListOf<Pair<String, String>>()
        compose.setContent {
            PrivateAgentTheme { ApprovalPrompt(asked(session = true, permanent = true)) { id, decision -> decisions += id to decision } }
        }
        compose.onNodeWithText("rm -rf build").assertIsDisplayed()
        compose.onNodeWithText("폴더를 지웁니다").assertIsDisplayed()
        for (answer in listOf("이번만 실행", "이 대화에서 허용", "항상 허용", "거부")) compose.onNodeWithText(answer).assertIsDisplayed()
        compose.onNodeWithText("이 대화에서 허용: 대화를 지우거나", substring = true).assertIsDisplayed()
        compose.onNodeWithText("항상 허용: 글자까지 같은 이 명령만", substring = true).assertIsDisplayed()
        compose.onNodeWithText("이 대화에서 허용").performClick()
        compose.onNodeWithText("항상 허용").performClick()
        compose.onNodeWithText("이번만 실행").performClick()
        assertEquals(listOf("session", "always", "once"), decisions.map { it.second })
        assertEquals(setOf("question-1"), decisions.map { it.first }.toSet())
    }

    @Test
    fun aCommandTheSafetyCheckRefusedCanOnlyBeRunOnceOrRefused() {
        val decisions = mutableListOf<String>()
        compose.setContent {
            PrivateAgentTheme {
                ApprovalPrompt(asked(session = false, permanent = false, refused = true)) { _, decision -> decisions += decision }
            }
        }
        compose.onNodeWithText("이번만 실행").assertIsDisplayed()
        compose.onNodeWithText("이 대화에서 허용").assertDoesNotExist()
        compose.onNodeWithText("항상 허용").assertDoesNotExist()
        compose.onNodeWithText("이 대화에서 허용: 대화를 지우거나", substring = true).assertDoesNotExist()
        compose.onNodeWithText("안전 검사가 위험하다고 본 명령입니다. 실행하더라도 이번 한 번뿐입니다.").assertIsDisplayed()
        compose.onNodeWithText("거부").performClick()
        assertEquals(listOf("deny"), decisions)
    }

    @Test
    fun aQuestionWithChoicesTakesAnAnswerInTheUsersOwnWordsToo() {
        val answers = mutableListOf<JSONObject>()
        val question = JSONObject().put("qid", "q0").put("question", "어느 사이트에서 찾을까요?")
            .put("choices", JSONArray(listOf("쿠팡 (Recommended)", "네이버")))
        val asked = HermesChat.Interaction(
            "question-2",
            HermesSession.InteractionKind.CLARIFY,
            JSONObject().put("questions", JSONArray().put(question)),
        )
        compose.setContent { PrivateAgentTheme { ClarifyPrompt(asked) { _, answer -> answers += answer } } }
        // Hermes' mark of the choice it recommends is said in the card's words.
        compose.onNodeWithText("쿠팡 (추천)").assertIsDisplayed()
        compose.onNodeWithText("보기에 없으면 직접 적어 주세요").assertIsDisplayed()
        // The user's own words take the place of the choice picked before them...
        compose.onNodeWithText("네이버").performClick()
        compose.onNode(hasSetTextAction()).performTextInput("11번가")
        compose.onNodeWithText("보내기").performClick()
        // ...and a choice picked after them takes theirs.
        compose.onNodeWithText("네이버").performClick()
        compose.onNodeWithText("보내기").performClick()
        assertEquals(listOf("11번가", "네이버"), answers.map { it.getString("q0") })
    }

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
