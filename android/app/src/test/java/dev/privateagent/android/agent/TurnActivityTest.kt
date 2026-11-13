package dev.privateagent.android.agent

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class TurnActivityTest {
    @Test
    fun secretsAreWrittenAsRedacted() {
        assertEquals("export API_KEY=[REDACTED]", Redact.text("export API_KEY=abc123"))
        assertEquals("curl -H 'Authorization: [REDACTED]", Redact.text("curl -H 'Authorization: Bearer abcdef123456' https://example.test"))
        assertEquals("key sk-[REDACTED] and pa_[REDACTED]", Redact.text("key sk-abcdef123456 and pa_abcdef123456"))
        assertEquals("bearer [REDACTED]", Redact.text("bearer abcdefgh"))
        assertEquals("{'password': [REDACTED]}", Redact.text("{'password': 'hunter2'}"))
        // What only looks like one stays: a count of tokens, an ordinary command.
        for (plain in listOf("max_tokens=100", "ls -la /tmp", "git push origin main", "노트북 광고 기록")) {
            assertEquals(plain, Redact.text(plain))
        }
    }

    @Test
    fun aTurnsStepsAreKeptShortInOrderAndWithoutSecrets() {
        var clock = 1_000L
        val recorder = ActivityRecorder { clock++ }
        assertNull(recorder.snapshot())
        recorder.step(StepKind.TOOL, "웹 검색: 노트북")
        recorder.step(StepKind.APPROVAL, "curl -H 'Authorization: Bearer abcdef123456'", "token=abc123 필요", "once")
        recorder.step(StepKind.ACTION, "가".repeat(400), result = "done")
        val activity = recorder.snapshot()!!
        assertEquals(listOf(StepKind.TOOL, StepKind.APPROVAL, StepKind.ACTION), activity.steps.map { it.kind })
        assertEquals(listOf(1_000L, 1_001L, 1_002L), activity.steps.map { it.at })
        assertEquals("curl -H 'Authorization: [REDACTED]", activity.steps[1].text)
        assertEquals("token=[REDACTED] 필요", activity.steps[1].note)
        assertEquals("once", activity.steps[1].result)
        assertEquals(ActivityRecorder.STEP_CHARS, activity.steps[2].text.length)
        assertEquals(3, activity.count)
    }

    @Test
    fun stepsBeyondTheLimitAreOnlyCounted() {
        val recorder = ActivityRecorder { 0 }
        repeat(ActivityRecorder.MAX_STEPS + 5) { recorder.step(StepKind.TOOL, "step $it") }
        val activity = recorder.snapshot()!!
        assertEquals(ActivityRecorder.MAX_STEPS, activity.steps.size)
        assertEquals(5, activity.more)
        assertEquals(ActivityRecorder.MAX_STEPS + 5, activity.count)
        assertEquals("step ${ActivityRecorder.MAX_STEPS - 1}", activity.steps.last().text)
    }

    @Test
    fun anActivityIsReadBackAsItWasStored() {
        val recorder = ActivityRecorder { 42 }
        recorder.step(StepKind.QUESTION, "어느 사이트에서 찾을까요?", result = "submitted")
        recorder.step(StepKind.APPROVAL, "git push", "원격에 올립니다", "always")
        val stored = recorder.snapshot()!!
        assertEquals(stored, Activity.parse(stored.stepsJson(), 0))
        assertEquals(stored.copy(more = 3), Activity.parse(stored.stepsJson(), 3))
        // Nothing to show is no activity; a step of a kind this version does not know is left out.
        assertNull(Activity.parse(null, 0))
        assertNull(Activity.parse("not json", 0))
        assertNull(Activity.parse("[]", 0))
        val mixed = JSONArray().put(JSONObject().put("kind", "later").put("text", "x"))
            .put(JSONObject().put("kind", "tool").put("text", "웹 검색").put("at", 7))
        val parsed = Activity.parse(mixed.toString(), 0)!!
        assertEquals(listOf(ActivityStep(StepKind.TOOL, "웹 검색", "", "", 7)), parsed.steps)
        assertTrue(Activity.parse("[]", 2)!!.steps.isEmpty())
        assertFalse(Activity.parse("[]", 2)!!.count == 0)
    }

    @Test
    fun aQuestionStepSaysTheFirstQuestionAndHowManyMore() {
        val one = JSONArray().put(JSONObject().put("question", "어느 사이트에서 찾을까요?"))
        assertEquals("어느 사이트에서 찾을까요?", ActivityRecorder.questionText(one))
        val three = JSONArray(one.toString()).put(JSONObject().put("question", "검색어는?")).put(JSONObject().put("question", "기간은?"))
        assertEquals("어느 사이트에서 찾을까요? 외 2개", ActivityRecorder.questionText(three))
        assertEquals("", ActivityRecorder.questionText(null))
        assertEquals("", ActivityRecorder.questionText(JSONArray().put("words")))
    }
}
