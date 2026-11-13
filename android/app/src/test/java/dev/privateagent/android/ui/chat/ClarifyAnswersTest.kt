package dev.privateagent.android.ui.chat

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Test

/** What a clarify card sends back: a choice, or the user's own words in its place, as Hermes takes them. */
class ClarifyAnswersTest {
    private val one = ClarifyQuestion("q0", "어느 사이트에서 찾을까요?", listOf("쿠팡 (Recommended)", "네이버"), multiple = false)
    private val several = ClarifyQuestion("q1", "어떤 종류를 기록할까요?", listOf("광고", "상품"), multiple = true)
    private val open = ClarifyQuestion("q2", "검색어는요?", emptyList(), multiple = false)

    @Test
    fun theQuestionsAreReadFromTheFrame() {
        val sites = JSONObject().put("qid", "q0").put("question", "어느 사이트?")
            .put("choices", JSONArray(listOf("쿠팡", "네이버")))
        val words = JSONObject().put("qid", "q1").put("question", "검색어는요?").put("choices", JSONObject.NULL)
        val payload = JSONObject().put("questions", JSONArray().put(sites).put(words))
        val questions = ClarifyQuestion.of(payload)
        assertEquals(listOf("q0", "q1"), questions.map { it.id })
        assertEquals(listOf("쿠팡", "네이버"), questions[0].choices)
        assertEquals(emptyList<String>(), questions[1].choices)
        assertFalse(questions[0].multiple)
    }

    @Test
    fun aChoiceIsTheAnswerAndTheUsersOwnWordsTakeItsPlace() {
        val questions = listOf(one)
        assertEquals("네이버", ClarifyAnswers.json(questions, mapOf("q0" to listOf("네이버")), emptyMap()).getString("q0"))
        assertEquals(
            "11번가",
            ClarifyAnswers.json(questions, mapOf("q0" to listOf("네이버")), mapOf("q0" to " 11번가 ")).getString("q0"),
        )
        // A box left blank is no answer: the choice stands.
        val blank = ClarifyAnswers.json(questions, mapOf("q0" to listOf("네이버")), mapOf("q0" to "  "))
        assertEquals("네이버", blank.getString("q0"))
    }

    @Test
    fun aQuestionThatTakesSeveralGetsTheChoicesAndTheUsersOwnWordsTogether() {
        val answer = ClarifyAnswers.json(listOf(several), mapOf("q1" to listOf("광고", "상품")), mapOf("q1" to "검색 결과"))
        assertEquals(JSONArray(listOf("광고", "상품", "검색 결과")).toString(), answer.getJSONArray("q1").toString())
        val written = ClarifyAnswers.json(listOf(several), emptyMap(), mapOf("q1" to "검색 결과"))
        assertEquals(JSONArray(listOf("검색 결과")).toString(), written.getJSONArray("q1").toString())
    }

    @Test
    fun anOpenQuestionTakesTheUsersWordsAndAnUnansweredOneIsLeftOut() {
        val answer = ClarifyAnswers.json(listOf(one, several, open), emptyMap(), mapOf("q2" to "노트북"))
        assertEquals(setOf("q2"), answer.keys().asSequence().toSet())
        assertEquals("노트북", answer.getString("q2"))
    }

    @Test
    fun theUsersOwnWordsAreAsLongAsTheDesktopTakes() {
        val answer = ClarifyAnswers.json(listOf(open), emptyMap(), mapOf("q2" to "가".repeat(3000)))
        assertEquals(ClarifyAnswers.MAX_WRITTEN, answer.getString("q2").length)
    }

    @Test
    fun theRecommendedChoiceIsToldWithoutHermesMark() {
        assertEquals("쿠팡", ClarifyAnswers.recommended("쿠팡 (Recommended)"))
        assertNull(ClarifyAnswers.recommended("네이버"))
        assertNull(ClarifyAnswers.recommended("(Recommended) 쿠팡"))
    }
}
