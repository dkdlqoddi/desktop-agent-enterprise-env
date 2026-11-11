package dev.privateagent.android.hermes

import dev.privateagent.android.agent.HermesChat
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.ChatMessage
import dev.privateagent.android.data.MessageRole
import dev.privateagent.android.llm.ApiFormat
import dev.privateagent.android.llm.LlmException
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.time.ZoneId
import java.time.ZonedDateTime

class HermesMessagesTest {
    private val tool = JSONObject().put("type", "function").put(
        "function",
        JSONObject().put("name", "list_records").put("description", "Search the records")
            .put("parameters", JSONObject().put("type", "object")),
    )

    private fun request(vararg messages: JSONObject) = JSONObject().put("model", "hermes-picked")
        .put("messages", JSONArray(messages.toList())).put("tools", JSONArray().put(tool)).put("stream", true)
        .put("max_tokens", 8192)

    private fun message(role: String, content: Any?) = JSONObject().put("role", role).put("content", content ?: JSONObject.NULL)

    private val call = JSONObject().put("id", "x").put("type", "function")
        .put("function", JSONObject().put("name", "list_records").put("arguments", "{\"query\":\"노트북\"}"))

    @Test
    fun anOpenAiRequestKeepsTheMessagesAndToolsWithTheConnectionsModel() {
        val sent = request(message("system", "rules"), message("user", "hi"))
        val body = HermesMessages.requestBody(ApiFormat.OPENAI, "my-model", sent)
        assertEquals("my-model", body.getString("model"))
        assertEquals(sent.getJSONArray("messages").toString(), body.getJSONArray("messages").toString())
        assertEquals("list_records", body.getJSONArray("tools").getJSONObject(0).getJSONObject("function").getString("name"))
        // Only messages and tools go on: the app's client reads one whole answer, and the server's own cap applies.
        assertFalse(body.has("stream"))
        assertFalse(body.has("max_tokens"))
    }

    @Test
    fun toolMessagesConvertToAnthropicWithoutLosingIds() {
        val (system, messages) = HermesMessages.anthropicMessages(
            JSONArray()
                .put(message("system", "rules"))
                .put(message("user", "hello"))
                .put(message("assistant", null).put("tool_calls", JSONArray().put(call)))
                .put(JSONObject().put("role", "tool").put("tool_call_id", "x").put("content", "found 3")),
        )
        assertEquals("rules", system)
        assertEquals(3, messages.length())
        val use = messages.getJSONObject(1).getJSONArray("content").getJSONObject(0)
        assertEquals("tool_use", use.getString("type"))
        assertEquals("x", use.getString("id"))
        assertEquals("노트북", use.getJSONObject("input").getString("query"))
        val result = messages.getJSONObject(2)
        assertEquals("user", result.getString("role"))
        assertEquals("x", result.getJSONArray("content").getJSONObject(0).getString("tool_use_id"))
    }

    @Test
    fun anAnthropicRequestHasItsSystemToolsAndCap() {
        val body = HermesMessages.requestBody(
            ApiFormat.ANTHROPIC,
            "claude-model",
            request(message("system", "rules"), message("user", "hi"), message("user", "again")),
        )
        assertEquals("rules", body.getString("system"))
        assertEquals(HermesMessages.ANTHROPIC_MAX_TOKENS, body.getInt("max_tokens"))
        // Turns of one speaker in a row become one turn: the API takes alternating roles.
        assertEquals(1, body.getJSONArray("messages").length())
        assertEquals(2, body.getJSONArray("messages").getJSONObject(0).getJSONArray("content").length())
        val schema = body.getJSONArray("tools").getJSONObject(0)
        assertEquals("list_records", schema.getString("name"))
        assertEquals("object", schema.getJSONObject("input_schema").getString("type"))
    }

    @Test
    fun anAnthropicAnswerComesBackAsAChatCompletion() {
        val reply = JSONObject()
            .put(
                "content",
                JSONArray().put(JSONObject().put("type", "text").put("text", "찾을게요"))
                    .put(JSONObject().put("type", "tool_use").put("id", "toolu_1").put("name", "list_records")
                        .put("input", JSONObject().put("query", "노트북"))),
            )
            .put("stop_reason", "tool_use")
            .put("usage", JSONObject().put("input_tokens", 10).put("output_tokens", 5))
        val completion = HermesMessages.completion(ApiFormat.ANTHROPIC, "claude-model", reply)
        val choice = completion.getJSONArray("choices").getJSONObject(0)
        assertEquals("tool_calls", choice.getString("finish_reason"))
        val message = choice.getJSONObject("message")
        assertEquals("assistant", message.getString("role"))
        assertEquals("찾을게요", message.getString("content"))
        val function = message.getJSONArray("tool_calls").getJSONObject(0).getJSONObject("function")
        assertEquals("list_records", function.getString("name"))
        assertEquals("노트북", JSONObject(function.getString("arguments")).getString("query"))
        assertEquals(15, completion.getJSONObject("usage").getInt("total_tokens"))
    }

    @Test
    fun anOpenAiAnswerKeepsItsMessageAndReason() {
        val reply = JSONObject().put(
            "choices",
            JSONArray().put(
                JSONObject().put("index", 0).put("finish_reason", "length")
                    .put("message", JSONObject().put("role", "assistant").put("content", "긴 답")),
            ),
        )
        val choice = HermesMessages.completion(ApiFormat.OPENAI, "my-model", reply).getJSONArray("choices").getJSONObject(0)
        assertEquals("length", choice.getString("finish_reason"))
        assertEquals("긴 답", choice.getJSONObject("message").getString("content"))
        try {
            HermesMessages.completion(ApiFormat.OPENAI, "my-model", JSONObject().put("error", "nope"))
            fail("An answer without choices is not a completion")
        } catch (error: LlmException) {
            assertEquals(LlmException.Kind.FORMAT, error.kind)
        }
    }

    @Test
    fun imagesGoToAnthropicAsImageBlocks() {
        val parts = JSONArray()
            .put(JSONObject().put("type", "text").put("text", "what is this?"))
            .put(JSONObject().put("type", "image_url").put("image_url", JSONObject().put("url", "data:image/png;base64,AAAA")))
            .put(JSONObject().put("type", "image_url").put("image_url", JSONObject().put("url", "http://insecure.example.test/a.png")))
        val (_, messages) = HermesMessages.anthropicMessages(JSONArray().put(message("user", parts)))
        val blocks = messages.getJSONObject(0).getJSONArray("content")
        assertEquals(2, blocks.length())
        val source = blocks.getJSONObject(1).getJSONObject("source")
        assertEquals("base64", source.getString("type"))
        assertEquals("image/png", source.getString("media_type"))
    }

    @Test
    fun theTurnFrameOffersNoBrowserAndTheRepliesFailClosed() {
        val frame = HermesSession.turnFrame(
            HermesSession.Turn("system", "context", "my-model", HermesChat.TOOLSETS, JSONArray(), HermesChat.SETTINGS, 240),
        )
        assertEquals("turn", frame.getString("type"))
        assertEquals("", frame.getString("chromium"))
        assertTrue(frame.getString("session_id").startsWith("pa_"))
        val toolsets = (0 until frame.getJSONArray("toolsets").length()).map { frame.getJSONArray("toolsets").getString(it) }
        for (missing in listOf("terminal", "code_execution", "file", "browser", "computer_use")) {
            assertFalse(missing, missing in toolsets)
        }
        assertFalse(frame.getJSONObject("settings").getBoolean("computer_use"))

        val unknown = HermesSession.reply(HermesSession.InteractionKind.APPROVAL, "a1", JSONObject().put("decision", "yes"))
        assertEquals("deny", unknown.getString("decision"))
        assertEquals("deny", HermesSession.reply(HermesSession.InteractionKind.APPROVAL, "a1", null).getString("decision"))
        val once = HermesSession.reply(HermesSession.InteractionKind.APPROVAL, "a1", JSONObject().put("decision", "once"))
        assertEquals("once", once.getString("decision"))
        val timedOut = HermesSession.reply(HermesSession.InteractionKind.CLARIFY, "c1", null)
        assertEquals("cancelled", timedOut.getString("outcome"))
        val answered = HermesSession.reply(
            HermesSession.InteractionKind.CLARIFY,
            "c1",
            JSONObject().put("answers", JSONObject().put("q0", "쿠팡")).put("outcome", "submitted"),
        )
        assertEquals("submitted", answered.getString("outcome"))
        assertEquals("쿠팡", answered.getJSONObject("answers").getString("q0"))
    }

    @Test
    fun theContextEndsWithTheNewMessageAndHermesWordsAreCleaned() {
        val now = ZonedDateTime.of(2026, 11, 11, 9, 0, 0, 0, ZoneId.of("Asia/Seoul"))
        val history = listOf(
            ChatMessage(1, MessageRole.USER, "노트북 기록해 줘", 0),
            ChatMessage(2, MessageRole.ACTION, "", 0, tool = "web_capture", state = ActionState.DONE, result = "광고 3건"),
            ChatMessage(3, MessageRole.NOTICE, "알림", 0),
        )
        val context = HermesChat.context(history, "요약해 줘", now)
        assertTrue(context.startsWith("Today is 2026-11-11 (Wednesday); tomorrow is 2026-11-12."))
        assertTrue(context.contains("사용자: 노트북 기록해 줘\n에이전트: [작업] web_capture → 광고 3건"))
        assertFalse(context.contains("알림"))
        assertTrue(context.endsWith("New message from the user:\n요약해 줘"))

        assertEquals("답입니다", HermesChat.spoken("<think>plan</think>답입니다"))
        assertEquals("", HermesChat.spoken("{\"actions\": []}"))
        assertEquals("", HermesChat.visibleDraft("```json"))
        assertEquals("쓰는 중", HermesChat.visibleDraft("쓰는 중"))
    }
}
