package dev.privateagent.android.llm

import dev.privateagent.android.util.arrayOrNull
import dev.privateagent.android.util.objectOrNull
import dev.privateagent.android.util.objects
import dev.privateagent.android.util.text
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

enum class ApiFormat(val key: String) {
    OPENAI("openai"),
    ANTHROPIC("anthropic"),
    ;

    companion object {
        fun of(key: String?): ApiFormat = entries.firstOrNull { it.key == key } ?: OPENAI
    }
}

/** One message of the conversation as the LLM reads it: plain text from the user or the assistant. */
data class ChatTurn(val role: Role, val text: String) {
    enum class Role { USER, ASSISTANT }
}

data class ToolDefinition(val name: String, val description: String, val schema: JSONObject)

/** A tool call of a reply. [input] is null when the arguments are not a JSON object; [rawArguments] is as received. */
data class ToolCall(val id: String, val name: String, val input: JSONObject?, val rawArguments: String)

/**
 * A reply: its text and tool calls. [planned] marks calls read from a JSON plan in the text (a model or server without
 * tool calls); [raw] is the text as the model wrote it, which a follow-up request repeats.
 */
data class LlmReply(
    val text: String,
    val toolCalls: List<ToolCall>,
    val planned: Boolean = false,
    val truncated: Boolean = false,
    val raw: String = text,
)

/** The reply whose tool calls ran, and what each returned by call id: the request after it carries both. */
data class ToolRound(val reply: LlmReply, val results: Map<String, String>)

/** The server answered, but not with a reply of the configured format. */
class LlmFormatException : Exception("unexpected LLM response")

/** The model declined to answer (Anthropic stop_reason "refusal", or a content filter). */
class LlmRefusedException : Exception("LLM refused")

/** The request bodies and reply readers of the two API formats; no I/O here. */
object LlmProtocol {
    const val ANTHROPIC_VERSION = "2023-06-01"
    const val MAX_TOKENS = 2048
    private const val PING_TOKENS = 16

    private val THINKING = Regex("(?is)<think>.*?(?:</think>|\\z)")
    private val UNOPENED_THINKING = Regex("(?is)\\A(?:(?!<think>).)*?</think>")

    /** The request headers; the key goes in its format's own header and nowhere else. */
    fun headers(format: ApiFormat, apiKey: String?): Map<String, String> = buildMap {
        put("Content-Type", "application/json; charset=utf-8")
        put("Accept", "application/json")
        when (format) {
            ApiFormat.OPENAI -> if (!apiKey.isNullOrEmpty()) put("Authorization", "Bearer $apiKey")
            ApiFormat.ANTHROPIC -> {
                put("anthropic-version", ANTHROPIC_VERSION)
                if (!apiKey.isNullOrEmpty()) put("x-api-key", apiKey)
            }
        }
    }

    /**
     * The request for the conversation [turns] with [tools]. With [round], it also carries the tool calls of the
     * previous reply and their results, so the model can answer with what they found.
     */
    fun requestBody(
        format: ApiFormat,
        model: String,
        system: String,
        turns: List<ChatTurn>,
        tools: List<ToolDefinition>,
        round: ToolRound? = null,
    ): JSONObject {
        var conversation = normalizeTurns(turns)
        var toolRound = round
        if (round != null && round.reply.planned) {
            // A JSON plan came as text, so its results go back as text too: the server may not know tool messages.
            conversation = normalizeTurns(
                conversation + ChatTurn(ChatTurn.Role.ASSISTANT, round.reply.raw) +
                    ChatTurn(ChatTurn.Role.USER, planResults(round)),
            )
            toolRound = null
        }
        return when (format) {
            ApiFormat.OPENAI -> openAiBody(model, system, conversation, tools, toolRound)
            ApiFormat.ANTHROPIC -> anthropicBody(model, system, conversation, tools, toolRound)
        }
    }

    /** The smallest request that shows the endpoint, key and model work. */
    fun pingBody(format: ApiFormat, model: String): JSONObject {
        val messages = JSONArray().put(message("user", "Reply with the word OK."))
        return when (format) {
            // No max_tokens: OpenAI's reasoning models refuse it and take max_completion_tokens instead.
            ApiFormat.OPENAI -> JSONObject().put("model", model).put("messages", messages)
            ApiFormat.ANTHROPIC -> JSONObject().put("model", model).put("max_tokens", PING_TOKENS).put("messages", messages)
        }
    }

    /** The reply of a response body; throws LlmFormatException when it is not one of [format]. */
    fun parseReply(format: ApiFormat, body: JSONObject): LlmReply {
        val parsed = when (format) {
            ApiFormat.OPENAI -> parseOpenAi(body)
            ApiFormat.ANTHROPIC -> parseAnthropic(body)
        }
        val text = stripThinking(parsed.text)
        if (parsed.toolCalls.isEmpty()) {
            JsonPlan.parse(text)?.let { plan ->
                return LlmReply(plan.message, plan.calls, planned = true, truncated = parsed.truncated, raw = text)
            }
        }
        return parsed.copy(text = text, raw = text)
    }

    /** Text without a reasoning model's thinking: <think> blocks, and all before a </think> that nothing opened. */
    fun stripThinking(text: String): String = THINKING.replace(UNOPENED_THINKING.replaceFirst(text, ""), "").trim()

    /**
     * Turns as both APIs take them: no empty messages, one message per speaker in a row (Anthropic alternates roles),
     * and the user first.
     */
    fun normalizeTurns(turns: List<ChatTurn>): List<ChatTurn> {
        val merged = mutableListOf<ChatTurn>()
        for (turn in turns) {
            val text = turn.text.trim()
            if (text.isEmpty()) continue
            val last = merged.lastOrNull()
            if (last != null && last.role == turn.role) {
                merged[merged.lastIndex] = last.copy(text = last.text + "\n\n" + text)
            } else if (last != null || turn.role == ChatTurn.Role.USER) {
                merged += ChatTurn(turn.role, text)
            }
        }
        return merged
    }

    private fun openAiBody(
        model: String,
        system: String,
        turns: List<ChatTurn>,
        tools: List<ToolDefinition>,
        round: ToolRound?,
    ): JSONObject {
        val messages = JSONArray().put(message("system", system))
        for (turn in turns) messages.put(message(roleName(turn.role), turn.text))
        if (round != null) {
            val calls = JSONArray()
            for (call in round.reply.toolCalls) {
                calls.put(
                    JSONObject().put("id", call.id).put("type", "function").put(
                        "function",
                        JSONObject().put("name", call.name).put("arguments", call.rawArguments.ifEmpty { "{}" }),
                    ),
                )
            }
            messages.put(
                JSONObject().put("role", "assistant")
                    .put("content", round.reply.raw.ifEmpty { null } ?: JSONObject.NULL)
                    .put("tool_calls", calls),
            )
            for (call in round.reply.toolCalls) {
                messages.put(
                    JSONObject().put("role", "tool").put("tool_call_id", call.id)
                        .put("content", round.results[call.id] ?: NOT_RUN),
                )
            }
        }
        val body = JSONObject().put("model", model).put("messages", messages)
        if (tools.isNotEmpty()) {
            body.put(
                "tools",
                JSONArray(
                    tools.map { tool ->
                        JSONObject().put("type", "function").put(
                            "function",
                            JSONObject().put("name", tool.name).put("description", tool.description)
                                .put("parameters", tool.schema),
                        )
                    },
                ),
            )
        }
        return body
    }

    private fun anthropicBody(
        model: String,
        system: String,
        turns: List<ChatTurn>,
        tools: List<ToolDefinition>,
        round: ToolRound?,
    ): JSONObject {
        val messages = JSONArray()
        for (turn in turns) messages.put(message(roleName(turn.role), turn.text))
        if (round != null) {
            val content = JSONArray()
            if (round.reply.raw.isNotEmpty()) content.put(JSONObject().put("type", "text").put("text", round.reply.raw))
            for (call in round.reply.toolCalls) {
                content.put(
                    JSONObject().put("type", "tool_use").put("id", call.id).put("name", call.name)
                        .put("input", call.input ?: JSONObject()),
                )
            }
            messages.put(JSONObject().put("role", "assistant").put("content", content))
            val results = JSONArray()
            for (call in round.reply.toolCalls) {
                results.put(
                    JSONObject().put("type", "tool_result").put("tool_use_id", call.id)
                        .put("content", round.results[call.id] ?: NOT_RUN),
                )
            }
            messages.put(JSONObject().put("role", "user").put("content", results))
        }
        val body = JSONObject().put("model", model).put("max_tokens", MAX_TOKENS).put("system", system)
            .put("messages", messages)
        if (tools.isNotEmpty()) {
            body.put(
                "tools",
                JSONArray(
                    tools.map { tool ->
                        JSONObject().put("name", tool.name).put("description", tool.description)
                            .put("input_schema", tool.schema)
                    },
                ),
            )
        }
        return body
    }

    private fun parseOpenAi(body: JSONObject): LlmReply {
        val choice = body.arrayOrNull("choices")?.optJSONObject(0) ?: throw LlmFormatException()
        val message = choice.objectOrNull("message") ?: throw LlmFormatException()
        val text = when (val content = message.opt("content")) {
            is String -> content
            // Some servers answer with content parts, as the request may send them.
            is JSONArray -> content.objects().filter { it.text("type") == "text" }.joinToString("") { it.text("text") }
            else -> ""
        }
        val calls = mutableListOf<ToolCall>()
        message.arrayOrNull("tool_calls")?.objects()?.forEachIndexed { index, call ->
            val function = call.objectOrNull("function") ?: return@forEachIndexed
            val (input, raw) = arguments(function.opt("arguments"))
            calls += ToolCall(call.text("id").ifEmpty { "call_$index" }, function.text("name"), input, raw)
        }
        if (calls.isEmpty()) {
            // The function_call of servers that still speak the API's first form of tool calls.
            message.objectOrNull("function_call")?.let { function ->
                val (input, raw) = arguments(function.opt("arguments"))
                calls += ToolCall("call_0", function.text("name"), input, raw)
            }
        }
        val finish = choice.text("finish_reason")
        if (finish == "content_filter" && text.isBlank() && calls.isEmpty()) throw LlmRefusedException()
        return LlmReply(text, calls, truncated = finish == "length")
    }

    private fun parseAnthropic(body: JSONObject): LlmReply {
        val content = body.arrayOrNull("content") ?: throw LlmFormatException()
        val stop = body.text("stop_reason")
        if (stop == "refusal") throw LlmRefusedException()
        val text = StringBuilder()
        val calls = mutableListOf<ToolCall>()
        for (block in content.objects()) {
            when (block.text("type")) {
                "text" -> text.append(block.text("text"))
                "tool_use" -> {
                    val input = block.objectOrNull("input")
                    calls += ToolCall(
                        block.text("id").ifEmpty { "toolu_${calls.size}" },
                        block.text("name"),
                        input,
                        input?.toString().orEmpty(),
                    )
                }
            }
        }
        return LlmReply(text.toString(), calls, truncated = stop == "max_tokens")
    }

    /** Tool arguments as an object and as the text they came in: a JSON string (OpenAI) or an object (some servers). */
    private fun arguments(value: Any?): Pair<JSONObject?, String> = when (value) {
        is JSONObject -> value to value.toString()
        is String -> {
            val input = if (value.isBlank()) {
                JSONObject()
            } else {
                try {
                    JSONObject(value)
                } catch (_: JSONException) {
                    null
                }
            }
            input to value
        }
        null, JSONObject.NULL -> JSONObject() to ""
        else -> null to value.toString()
    }

    private fun planResults(round: ToolRound): String = buildString {
        append("[도구 실행 결과]\n")
        for (call in round.reply.toolCalls) {
            append(call.name).append(": ").append(round.results[call.id] ?: NOT_RUN).append('\n')
        }
        append("위 결과만 근거로 사용자에게 한국어 문장으로 답하세요. JSON은 쓰지 마세요.")
    }

    private fun message(role: String, content: String) = JSONObject().put("role", role).put("content", content)

    private fun roleName(role: ChatTurn.Role) = if (role == ChatTurn.Role.USER) "user" else "assistant"

    /** The result of a call that is not run now: an action that waits for the user's approval on the screen. */
    const val NOT_RUN = "사용자의 승인을 기다리는 중입니다. 아직 실행하지 않았습니다."
}

/** A reply that holds its tool calls as a JSON object in the text: {"message": "...", "actions": [{"tool", "args"}]}. */
data class JsonPlan(val message: String, val calls: List<ToolCall>) {
    companion object {
        private const val MAX_STARTS = 50

        /**
         * The plan in [text], with what models put around it (code fences, a sentence before or after), or null. The
         * first object that reads is taken, so an object inside another is never taken for the plan.
         */
        fun parse(text: String): JsonPlan? {
            var start = text.indexOf('{')
            var tries = 0
            while (start >= 0 && tries < MAX_STARTS) {
                tries += 1
                val end = matchingBrace(text, start)
                val value = if (end > start) {
                    try {
                        JSONObject(text.substring(start, end + 1))
                    } catch (_: JSONException) {
                        null
                    }
                } else {
                    null
                }
                if (value != null) {
                    planOf(value)?.let { return it }
                    start = text.indexOf('{', end + 1)
                } else {
                    start = text.indexOf('{', start + 1)
                }
            }
            return null
        }

        private fun planOf(value: JSONObject): JsonPlan? {
            if (!value.has("actions") && !value.has("message")) return null
            val message = value.opt("message")
            if (message != null && message != JSONObject.NULL && message !is String) return null
            val actions = value.opt("actions")
            if (actions != null && actions != JSONObject.NULL && actions !is JSONArray) return null
            val calls = (actions as? JSONArray)?.objects().orEmpty().mapIndexed { index, action ->
                val name = action.text("tool").ifEmpty { action.text("name") }
                val args = when {
                    action.has("args") -> action.opt("args") as? JSONObject
                    action.has("arguments") -> action.opt("arguments") as? JSONObject
                    action.has("input") -> action.opt("input") as? JSONObject
                    else -> JSONObject()
                }
                ToolCall("plan-$index", name, args, args?.toString().orEmpty())
            }
            return JsonPlan((message as? String).orEmpty().trim(), calls)
        }

        /** The index of the brace that closes the one at [start], minding strings and escapes, or -1. */
        private fun matchingBrace(text: String, start: Int): Int {
            var depth = 0
            var inString = false
            var escaped = false
            for (index in start until text.length) {
                val char = text[index]
                if (inString) {
                    when {
                        escaped -> escaped = false
                        char == '\\' -> escaped = true
                        char == '"' -> inString = false
                    }
                    continue
                }
                when (char) {
                    '"' -> inString = true
                    '{' -> depth += 1
                    '}' -> {
                        depth -= 1
                        if (depth == 0) return index
                    }
                }
            }
            return -1
        }
    }
}
