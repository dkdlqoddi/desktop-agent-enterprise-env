package dev.privateagent.android.llm

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test

class LlmProtocolTest {

    private fun openAi(message: JSONObject, finish: String = "stop") = JSONObject()
        .put("choices", JSONArray().put(JSONObject().put("index", 0).put("message", message).put("finish_reason", finish)))

    private fun anthropic(vararg blocks: JSONObject, stop: String = "end_turn") = JSONObject()
        .put("type", "message").put("role", "assistant").put("content", JSONArray(blocks.toList())).put("stop_reason", stop)

    @Test
    fun openAiToolCallsWithStringArgumentsAreRead() {
        val message = JSONObject().put("role", "assistant").put("content", JSONObject.NULL).put(
            "tool_calls",
            JSONArray().put(
                JSONObject().put("id", "call_1").put("type", "function").put(
                    "function",
                    JSONObject().put("name", "web_capture").put("arguments", "{\"site\":\"coupang\",\"query\":\"노트북\"}"),
                ),
            ),
        )
        val reply = LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(message, "tool_calls"))
        assertEquals("", reply.text)
        assertFalse(reply.planned)
        assertEquals(1, reply.toolCalls.size)
        val call = reply.toolCalls.single()
        assertEquals("call_1", call.id)
        assertEquals("web_capture", call.name)
        assertEquals("coupang", call.input!!.getString("site"))
        assertEquals("노트북", call.input.getString("query"))
        assertEquals("{\"site\":\"coupang\",\"query\":\"노트북\"}", call.rawArguments)
    }

    @Test
    fun openAiArgumentsAsAnObjectAreRead() {
        val message = JSONObject().put(
            "tool_calls",
            JSONArray().put(
                JSONObject().put(
                    "function",
                    JSONObject().put("name", "list_records").put("arguments", JSONObject().put("kind", "ad")),
                ),
            ),
        )
        val call = LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(message)).toolCalls.single()
        assertEquals("call_0", call.id)
        assertEquals("ad", call.input!!.getString("kind"))
    }

    @Test
    fun openAiMalformedArgumentsGiveNoInput() {
        val message = JSONObject().put(
            "tool_calls",
            JSONArray().put(
                JSONObject().put("id", "c").put(
                    "function",
                    JSONObject().put("name", "web_capture").put("arguments", "{\"site\": coupang"),
                ),
            ),
        )
        val call = LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(message)).toolCalls.single()
        assertNull(call.input)
        assertEquals("{\"site\": coupang", call.rawArguments)
    }

    @Test
    fun openAiPlainTextIsTheAnswer() {
        val reply = LlmProtocol.parseReply(
            ApiFormat.OPENAI,
            openAi(JSONObject().put("role", "assistant").put("content", "안녕하세요. 무엇을 기록할까요?")),
        )
        assertEquals("안녕하세요. 무엇을 기록할까요?", reply.text)
        assertTrue(reply.toolCalls.isEmpty())
        assertFalse(reply.planned)
    }

    @Test
    fun openAiContentPartsAreJoined() {
        val parts = JSONArray().put(JSONObject().put("type", "text").put("text", "가")).put(JSONObject().put("type", "text").put("text", "나"))
        assertEquals("가나", LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(JSONObject().put("content", parts))).text)
    }

    @Test
    fun openAiCutAnswerIsMarkedTruncated() {
        val reply = LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(JSONObject().put("content", "긴 답"), finish = "length"))
        assertTrue(reply.truncated)
    }

    @Test
    fun anthropicTextAndToolUseAreRead() {
        val reply = LlmProtocol.parseReply(
            ApiFormat.ANTHROPIC,
            anthropic(
                JSONObject().put("type", "text").put("text", "확인을 누르면 기록합니다."),
                JSONObject().put("type", "tool_use").put("id", "toolu_01").put("name", "web_capture")
                    .put("input", JSONObject().put("site", "amazon").put("query", "headphones")),
                stop = "tool_use",
            ),
        )
        assertEquals("확인을 누르면 기록합니다.", reply.text)
        val call = reply.toolCalls.single()
        assertEquals("toolu_01", call.id)
        assertEquals("web_capture", call.name)
        assertEquals("amazon", call.input!!.getString("site"))
        assertEquals("headphones", JSONObject(call.rawArguments).getString("query"))
    }

    @Test
    fun anthropicThinkingBlocksAreNotText() {
        val reply = LlmProtocol.parseReply(
            ApiFormat.ANTHROPIC,
            anthropic(
                JSONObject().put("type", "thinking").put("thinking", "생각"),
                JSONObject().put("type", "text").put("text", "답"),
            ),
        )
        assertEquals("답", reply.text)
    }

    @Test
    fun anthropicRefusalIsAnError() {
        assertThrows(LlmRefusedException::class.java) {
            LlmProtocol.parseReply(ApiFormat.ANTHROPIC, anthropic(stop = "refusal"))
        }
    }

    @Test
    fun aBodyOfTheOtherFormatIsAFormatError() {
        assertThrows(LlmFormatException::class.java) {
            LlmProtocol.parseReply(ApiFormat.OPENAI, anthropic(JSONObject().put("type", "text").put("text", "x")))
        }
        assertThrows(LlmFormatException::class.java) {
            LlmProtocol.parseReply(ApiFormat.ANTHROPIC, openAi(JSONObject().put("content", "x")))
        }
        assertThrows(LlmFormatException::class.java) {
            LlmProtocol.parseReply(ApiFormat.OPENAI, JSONObject().put("error", JSONObject().put("message", "bad")))
        }
    }

    @Test
    fun jsonPlanInCodeFencesBecomesToolCalls() {
        val content = "확인 후 진행할게요.\n```json\n{\"message\": \"쿠팡에서 노트북 광고를 기록할게요.\", " +
            "\"actions\": [{\"tool\": \"web_capture\", \"args\": {\"site\": \"coupang\", \"query\": \"노트북\"}}]}\n```"
        val reply = LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(JSONObject().put("content", content)))
        assertTrue(reply.planned)
        assertEquals("쿠팡에서 노트북 광고를 기록할게요.", reply.text)
        val call = reply.toolCalls.single()
        assertEquals("plan-0", call.id)
        assertEquals("web_capture", call.name)
        assertEquals("노트북", call.input!!.getString("query"))
        assertEquals(content, reply.raw)
    }

    @Test
    fun jsonPlanFromAnthropicTextWithSentenceAround() {
        val content = "다음과 같이 하겠습니다: {\"message\": \"예약을 만들게요\", \"actions\": [{\"tool\": " +
            "\"schedule_capture\", \"args\": {\"site\": \"coupang\", \"query\": \"노트북\", \"time\": \"09:00\", " +
            "\"days\": \"daily\"}}]} 확인해 주세요."
        val reply = LlmProtocol.parseReply(ApiFormat.ANTHROPIC, anthropic(JSONObject().put("type", "text").put("text", content)))
        assertTrue(reply.planned)
        assertEquals("예약을 만들게요", reply.text)
        assertEquals("schedule_capture", reply.toolCalls.single().name)
    }

    @Test
    fun planWithOnlyAMessageIsText() {
        val reply = LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(JSONObject().put("content", "{\"message\": \"무엇을 찾을까요?\"}")))
        assertTrue(reply.planned)
        assertEquals("무엇을 찾을까요?", reply.text)
        assertTrue(reply.toolCalls.isEmpty())
    }

    @Test
    fun jsonWithoutPlanKeysStaysPlainText() {
        val content = "설정 예시는 {\"site\": \"coupang\"} 입니다."
        val reply = LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(JSONObject().put("content", content)))
        assertFalse(reply.planned)
        assertEquals(content, reply.text)
        assertTrue(reply.toolCalls.isEmpty())
    }

    @Test
    fun braceInsideAStringDoesNotEndThePlan() {
        val content = "{\"message\": \"괄호 } 와 { 가 있는 말\", \"actions\": []}"
        val reply = LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(JSONObject().put("content", content)))
        assertTrue(reply.planned)
        assertEquals("괄호 } 와 { 가 있는 말", reply.text)
    }

    @Test
    fun anObjectInsideAnotherIsNotTakenForThePlan() {
        val content = "{\"data\": {\"message\": \"안쪽\", \"actions\": [{\"tool\": \"web_capture\", \"args\": {}}]}}"
        val reply = LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(JSONObject().put("content", content)))
        assertFalse(reply.planned)
        assertTrue(reply.toolCalls.isEmpty())
    }

    @Test
    fun planActionWithArgumentsThatAreNotAnObjectHasNoInput() {
        val content = "{\"message\": \"\", \"actions\": [{\"tool\": \"web_capture\", \"args\": \"coupang\"}]}"
        val call = LlmProtocol.parseReply(ApiFormat.OPENAI, openAi(JSONObject().put("content", content))).toolCalls.single()
        assertNull(call.input)
    }

    @Test
    fun reasoningIsRemovedFromTheText() {
        assertEquals("답입니다", LlmProtocol.stripThinking("<think>먼저 생각</think>답입니다"))
        assertEquals("답", LlmProtocol.stripThinking("열린 태그 없는 생각</think>답"))
        assertEquals("", LlmProtocol.stripThinking("<think>끝나지 않은 생각"))
    }

    @Test
    fun turnsAreMergedPerSpeakerAndStartWithTheUser() {
        val turns = LlmProtocol.normalizeTurns(
            listOf(
                ChatTurn(ChatTurn.Role.ASSISTANT, "앞선 안내"),
                ChatTurn(ChatTurn.Role.USER, "하나"),
                ChatTurn(ChatTurn.Role.USER, "둘"),
                ChatTurn(ChatTurn.Role.ASSISTANT, " "),
                ChatTurn(ChatTurn.Role.ASSISTANT, "답"),
            ),
        )
        assertEquals(listOf(ChatTurn(ChatTurn.Role.USER, "하나\n\n둘"), ChatTurn(ChatTurn.Role.ASSISTANT, "답")), turns)
    }

    private val tools = listOf(
        ToolDefinition("web_capture", "기록", JSONObject().put("type", "object").put("properties", JSONObject())),
    )

    @Test
    fun openAiRequestHasTheSystemMessageAndFunctionTools() {
        val body = LlmProtocol.requestBody(ApiFormat.OPENAI, "model-a", "시스템", listOf(ChatTurn(ChatTurn.Role.USER, "안녕")), tools)
        assertEquals("model-a", body.getString("model"))
        val messages = body.getJSONArray("messages")
        assertEquals("system", messages.getJSONObject(0).getString("role"))
        assertEquals("시스템", messages.getJSONObject(0).getString("content"))
        assertEquals("user", messages.getJSONObject(1).getString("role"))
        val tool = body.getJSONArray("tools").getJSONObject(0)
        assertEquals("function", tool.getString("type"))
        assertEquals("web_capture", tool.getJSONObject("function").getString("name"))
        assertTrue(tool.getJSONObject("function").has("parameters"))
        assertFalse(body.has("max_tokens"))
    }

    @Test
    fun anthropicRequestHasASystemFieldAndInputSchemas() {
        val body = LlmProtocol.requestBody(ApiFormat.ANTHROPIC, "model-b", "시스템", listOf(ChatTurn(ChatTurn.Role.USER, "안녕")), tools)
        assertEquals("시스템", body.getString("system"))
        assertEquals(LlmProtocol.MAX_TOKENS, body.getInt("max_tokens"))
        val messages = body.getJSONArray("messages")
        assertEquals(1, messages.length())
        assertEquals("user", messages.getJSONObject(0).getString("role"))
        val tool = body.getJSONArray("tools").getJSONObject(0)
        assertEquals("web_capture", tool.getString("name"))
        assertTrue(tool.has("input_schema"))
        assertFalse(tool.has("type"))
    }

    @Test
    fun noToolsMeansNoToolsField() {
        val body = LlmProtocol.requestBody(ApiFormat.OPENAI, "m", "s", listOf(ChatTurn(ChatTurn.Role.USER, "x")), emptyList())
        assertFalse(body.has("tools"))
    }

    private val listCall = ToolCall("call_9", "list_records", JSONObject().put("kind", "ad"), "{\"kind\":\"ad\"}")
    private val captureCall = ToolCall("call_10", "web_capture", JSONObject().put("site", "coupang"), "{\"site\":\"coupang\"}")

    @Test
    fun openAiFollowUpCarriesEveryCallAndItsResult() {
        val round = ToolRound(LlmReply("찾아볼게요", listOf(listCall, captureCall)), mapOf("call_9" to "{\"total\":3}"))
        val body = LlmProtocol.requestBody(ApiFormat.OPENAI, "m", "s", listOf(ChatTurn(ChatTurn.Role.USER, "요약")), tools, round)
        val messages = body.getJSONArray("messages")
        val assistant = messages.getJSONObject(2)
        assertEquals("assistant", assistant.getString("role"))
        assertEquals("찾아볼게요", assistant.getString("content"))
        val calls = assistant.getJSONArray("tool_calls")
        assertEquals(2, calls.length())
        assertEquals("{\"kind\":\"ad\"}", calls.getJSONObject(0).getJSONObject("function").getString("arguments"))
        val first = messages.getJSONObject(3)
        assertEquals("tool", first.getString("role"))
        assertEquals("call_9", first.getString("tool_call_id"))
        assertEquals("{\"total\":3}", first.getString("content"))
        // An action that waits for approval is answered too, saying it did not run.
        val second = messages.getJSONObject(4)
        assertEquals("call_10", second.getString("tool_call_id"))
        assertEquals(LlmProtocol.NOT_RUN, second.getString("content"))
    }

    @Test
    fun openAiFollowUpWithoutTextSendsNullContent() {
        val round = ToolRound(LlmReply("", listOf(listCall)), mapOf("call_9" to "{}"))
        val body = LlmProtocol.requestBody(ApiFormat.OPENAI, "m", "s", listOf(ChatTurn(ChatTurn.Role.USER, "요약")), tools, round)
        assertTrue(body.getJSONArray("messages").getJSONObject(2).isNull("content"))
    }

    @Test
    fun anthropicFollowUpCarriesToolUseAndToolResult() {
        val call = ToolCall("toolu_9", "list_records", JSONObject().put("kind", "ad"), "{\"kind\":\"ad\"}")
        val round = ToolRound(LlmReply("", listOf(call)), mapOf("toolu_9" to "{\"total\":3}"))
        val body = LlmProtocol.requestBody(ApiFormat.ANTHROPIC, "m", "s", listOf(ChatTurn(ChatTurn.Role.USER, "요약")), tools, round)
        val messages = body.getJSONArray("messages")
        assertEquals(3, messages.length())
        val use = messages.getJSONObject(1).getJSONArray("content").getJSONObject(0)
        assertEquals("tool_use", use.getString("type"))
        assertEquals("toolu_9", use.getString("id"))
        assertEquals("ad", use.getJSONObject("input").getString("kind"))
        val result = messages.getJSONObject(2)
        assertEquals("user", result.getString("role"))
        val block = result.getJSONArray("content").getJSONObject(0)
        assertEquals("tool_result", block.getString("type"))
        assertEquals("toolu_9", block.getString("tool_use_id"))
        assertEquals("{\"total\":3}", block.getString("content"))
    }

    @Test
    fun followUpOfAJsonPlanGoesBackAsText() {
        val planned = ToolCall("plan-0", "list_records", JSONObject(), "{}")
        val reply = LlmReply("", listOf(planned), planned = true, raw = "{\"actions\":[{\"tool\":\"list_records\"}]}")
        val body = LlmProtocol.requestBody(
            ApiFormat.OPENAI,
            "m",
            "s",
            listOf(ChatTurn(ChatTurn.Role.USER, "요약")),
            tools,
            ToolRound(reply, mapOf("plan-0" to "{\"total\":0}")),
        )
        val messages = body.getJSONArray("messages")
        assertEquals(4, messages.length())
        assertEquals("assistant", messages.getJSONObject(2).getString("role"))
        assertFalse(messages.getJSONObject(2).has("tool_calls"))
        val last = messages.getJSONObject(3)
        assertEquals("user", last.getString("role"))
        assertTrue(last.getString("content").contains("{\"total\":0}"))
    }

    @Test
    fun headersCarryTheKeyOnlyInTheFormatsOwnHeader() {
        val openAi = LlmProtocol.headers(ApiFormat.OPENAI, "sk-test")
        assertEquals("Bearer sk-test", openAi["Authorization"])
        assertFalse(openAi.containsKey("x-api-key"))
        val anthropic = LlmProtocol.headers(ApiFormat.ANTHROPIC, "sk-test")
        assertEquals("sk-test", anthropic["x-api-key"])
        assertEquals("2023-06-01", anthropic["anthropic-version"])
        assertFalse(anthropic.containsKey("Authorization"))
        assertFalse(LlmProtocol.headers(ApiFormat.OPENAI, null).containsKey("Authorization"))
    }

    @Test
    fun pingIsTiny() {
        val openAi = LlmProtocol.pingBody(ApiFormat.OPENAI, "m")
        assertEquals(1, openAi.getJSONArray("messages").length())
        assertFalse(openAi.has("tools"))
        assertEquals(16, LlmProtocol.pingBody(ApiFormat.ANTHROPIC, "m").getInt("max_tokens"))
    }

    @Test
    fun statusCodesMapToTheirKinds() {
        assertEquals(LlmException.Kind.AUTH, LlmClient.statusException(401).kind)
        assertEquals(LlmException.Kind.AUTH, LlmClient.statusException(403).kind)
        assertEquals(LlmException.Kind.REQUEST, LlmClient.statusException(404).kind)
        assertEquals(LlmException.Kind.REQUEST, LlmClient.statusException(400).kind)
        assertEquals(LlmException.Kind.RATE_LIMIT, LlmClient.statusException(429).kind)
        assertEquals(LlmException.Kind.REDIRECT, LlmClient.statusException(302).kind)
        assertEquals(LlmException.Kind.SERVER, LlmClient.statusException(503).kind)
        assertEquals(418, LlmClient.statusException(418).status)
    }
}
