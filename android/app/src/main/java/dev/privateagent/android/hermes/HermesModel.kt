package dev.privateagent.android.hermes

import dev.privateagent.android.llm.ApiFormat
import dev.privateagent.android.llm.LlmClient
import dev.privateagent.android.llm.LlmException
import dev.privateagent.android.llm.LlmProtocol
import dev.privateagent.android.security.LlmConnection
import dev.privateagent.android.util.text
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.util.UUID

/**
 * One model request of the Hermes worker: the main turn, and its auxiliary tasks and subagents. The worker holds no
 * credential; every request comes back to the app as an inference frame and is answered here.
 */
fun interface HermesModel {
    /** [request] is an OpenAI chat-completions request as Hermes writes it; the answer is a whole chat.completion. */
    suspend fun complete(request: JSONObject): JSONObject
}

/**
 * The user's LLM connection as Hermes' model, like the desktop's hermes_llm.py: an OpenAI-compatible API takes the
 * messages and tools as they are, the Anthropic Messages API gets them converted and its answer converted back. The
 * connection's model always replaces the one Hermes names. HTTPS, no redirects, the platform's certificate checks and
 * one deadline per request come with [LlmClient].
 */
class LlmHermesModel(private val client: LlmClient, private val connection: LlmConnection) : HermesModel {
    override suspend fun complete(request: JSONObject): JSONObject {
        val body = HermesMessages.requestBody(connection.format, connection.model, request)
        val response = client.post(connection.endpoint, LlmProtocol.headers(connection.format, connection.apiKey), body)
        return HermesMessages.completion(connection.format, connection.model, response)
    }
}

/** The conversions between Hermes' OpenAI-form requests and the two API formats; no I/O here. */
object HermesMessages {
    /** The Messages API needs a cap on the answer: the desktop's ANTHROPIC_MAX_TOKENS. */
    const val ANTHROPIC_MAX_TOKENS = 16_000

    /** The request to send for Hermes' [request]: only its messages and tools, with the connection's [model]. */
    fun requestBody(format: ApiFormat, model: String, request: JSONObject): JSONObject {
        val messages = request.optJSONArray("messages") ?: JSONArray()
        val tools = request.optJSONArray("tools")?.takeIf { it.length() > 0 }
        return when (format) {
            ApiFormat.OPENAI -> JSONObject().put("model", model).put("messages", messages)
                .apply { if (tools != null) put("tools", tools) }
            ApiFormat.ANTHROPIC -> {
                val (system, converted) = anthropicMessages(messages)
                JSONObject().put("model", model).put("max_tokens", ANTHROPIC_MAX_TOKENS).put("messages", converted).apply {
                    if (system.isNotEmpty()) put("system", system)
                    if (tools != null) put("tools", anthropicTools(tools))
                }
            }
        }
    }

    /**
     * OpenAI-form messages as the Messages API takes them: the system prompt apart, tool calls as tool_use blocks and
     * tool results as tool_result blocks of a user turn, one turn per speaker in a row.
     */
    fun anthropicMessages(messages: JSONArray): Pair<String, JSONArray> {
        val system = mutableListOf<String>()
        val converted = JSONArray()
        var lastRole = ""
        for (index in 0 until messages.length()) {
            val message = messages.optJSONObject(index) ?: continue
            var role = message.text("role")
            val blocks = JSONArray()
            when (role) {
                "system", "developer" -> {
                    plainText(message.opt("content")).takeIf { it.isNotEmpty() }?.let(system::add)
                    continue
                }
                "tool" -> {
                    role = "user"
                    val result = JSONObject().put("type", "tool_result")
                        .put("tool_use_id", message.text("tool_call_id"))
                    val content = contentBlocks(message.opt("content"))
                    result.put("content", if (content.length() == 0) "" else content)
                    blocks.put(result)
                }
                else -> {
                    if (role != "assistant") role = "user"
                    val content = contentBlocks(message.opt("content"))
                    for (block in 0 until content.length()) blocks.put(content.get(block))
                    val calls = message.optJSONArray("tool_calls") ?: JSONArray()
                    for (call in 0 until calls.length()) {
                        val item = calls.optJSONObject(call) ?: continue
                        val function = item.optJSONObject("function") ?: JSONObject()
                        blocks.put(
                            JSONObject().put("type", "tool_use").put("id", item.text("id"))
                                .put("name", function.text("name")).put("input", arguments(function.opt("arguments"))),
                        )
                    }
                }
            }
            if (blocks.length() == 0) continue
            if (role == lastRole) {
                val previous = converted.getJSONObject(converted.length() - 1).getJSONArray("content")
                for (block in 0 until blocks.length()) previous.put(blocks.get(block))
            } else {
                converted.put(JSONObject().put("role", role).put("content", blocks))
                lastRole = role
            }
        }
        return system.joinToString("\n\n") to converted
    }

    /** A whole chat.completion of the response, as the worker hands it to Hermes; LlmException(FORMAT) otherwise. */
    fun completion(format: ApiFormat, model: String, response: JSONObject): JSONObject = try {
        when (format) {
            ApiFormat.OPENAI -> {
                val choice = response.getJSONArray("choices").getJSONObject(0)
                val message = choice.getJSONObject("message")
                completion(message, model, response.optJSONObject("usage"), choice.text("finish_reason"))
            }
            ApiFormat.ANTHROPIC -> anthropicCompletion(model, response)
        }
    } catch (_: JSONException) {
        throw LlmException(LlmException.Kind.FORMAT)
    }

    /** A chat.completion of one assistant [message]; [reason] blank names it from the message (tool_calls or stop). */
    fun completion(message: JSONObject, model: String, usage: JSONObject?, reason: String?): JSONObject {
        val assistant = JSONObject(message.toString()).put("role", "assistant")
        val calls = assistant.optJSONArray("tool_calls")
        val finish = reason?.takeIf { it.isNotBlank() } ?: if (calls != null && calls.length() > 0) "tool_calls" else "stop"
        return JSONObject()
            .put("id", "chatcmpl-" + UUID.randomUUID().toString().replace("-", ""))
            .put("object", "chat.completion")
            .put("created", System.currentTimeMillis() / 1000)
            .put("model", model)
            .put("choices", JSONArray().put(JSONObject().put("index", 0).put("message", assistant).put("finish_reason", finish)))
            .put("usage", usage ?: JSONObject().put("prompt_tokens", 0).put("completion_tokens", 0).put("total_tokens", 0))
    }

    private fun anthropicCompletion(model: String, response: JSONObject): JSONObject {
        val content = StringBuilder()
        val calls = JSONArray()
        val blocks = response.getJSONArray("content")
        for (index in 0 until blocks.length()) {
            val block = blocks.optJSONObject(index) ?: continue
            when (block.text("type")) {
                "text" -> content.append(block.text("text"))
                "tool_use" -> calls.put(
                    JSONObject().put("id", block.text("id")).put("type", "function").put(
                        "function",
                        JSONObject().put("name", block.text("name"))
                            .put("arguments", (block.optJSONObject("input") ?: JSONObject()).toString()),
                    ),
                )
            }
        }
        val message = JSONObject().put("content", content.toString())
        if (calls.length() > 0) message.put("tool_calls", calls)
        val usage = response.optJSONObject("usage") ?: JSONObject()
        val prompt = usage.optInt("input_tokens")
        val completionTokens = usage.optInt("output_tokens")
        val tokens = JSONObject().put("prompt_tokens", prompt).put("completion_tokens", completionTokens)
            .put("total_tokens", prompt + completionTokens)
        // A cut-off answer says so, as the OpenAI form would, so Hermes knows to go on.
        val reason = if (calls.length() == 0 && response.text("stop_reason") == "max_tokens") "length" else null
        return completion(message, model, tokens, reason)
    }

    private fun anthropicTools(tools: JSONArray): JSONArray {
        val converted = JSONArray()
        for (index in 0 until tools.length()) {
            val function = tools.optJSONObject(index)?.optJSONObject("function") ?: continue
            converted.put(
                JSONObject().put("name", function.text("name")).put("description", function.text("description"))
                    .put("input_schema", function.optJSONObject("parameters") ?: JSONObject().put("type", "object")),
            )
        }
        return converted
    }

    /** Content (a string, or OpenAI content parts) as Messages API blocks: text, and images given as data or https. */
    private fun contentBlocks(content: Any?): JSONArray {
        val blocks = JSONArray()
        when (content) {
            is String -> if (content.isNotEmpty()) blocks.put(JSONObject().put("type", "text").put("text", content))
            is JSONArray -> for (index in 0 until content.length()) {
                val part = content.optJSONObject(index) ?: continue
                when (part.text("type")) {
                    "text" -> part.text("text").takeIf { it.isNotEmpty() }?.let {
                        blocks.put(JSONObject().put("type", "text").put("text", it))
                    }
                    "image_url" -> image(part.optJSONObject("image_url")?.text("url").orEmpty())?.let(blocks::put)
                }
            }
        }
        return blocks
    }

    private fun image(url: String): JSONObject? {
        val data = Regex("^data:(image/[a-z+.-]+);base64,(.+)$", RegexOption.DOT_MATCHES_ALL).matchEntire(url)
        val source = when {
            data != null -> JSONObject().put("type", "base64").put("media_type", data.groupValues[1])
                .put("data", data.groupValues[2])
            url.startsWith("https://") -> JSONObject().put("type", "url").put("url", url)
            else -> return null
        }
        return JSONObject().put("type", "image").put("source", source)
    }

    private fun plainText(content: Any?): String = when (content) {
        is String -> content
        is JSONArray -> (0 until content.length()).mapNotNull { content.optJSONObject(it) }
            .filter { it.text("type") == "text" }.joinToString("") { it.text("text") }
        else -> ""
    }

    /** Tool call arguments (a JSON string, or an object) as an object; {} when they are no object. */
    private fun arguments(value: Any?): JSONObject = when (value) {
        is JSONObject -> value
        is String -> try {
            if (value.isBlank()) JSONObject() else JSONObject(value)
        } catch (_: JSONException) {
            JSONObject()
        }
        else -> JSONObject()
    }
}
