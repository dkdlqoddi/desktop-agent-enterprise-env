package dev.privateagent.android.agent

import android.content.Context
import dev.privateagent.android.R
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.ChatMessage
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.data.MessageRole
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.NoticeKind
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.data.ScheduleStore
import dev.privateagent.android.llm.ChatTurn
import dev.privateagent.android.llm.Endpoint
import dev.privateagent.android.llm.LlmException
import dev.privateagent.android.llm.LlmReply
import dev.privateagent.android.llm.LlmService
import dev.privateagent.android.llm.ToolDefinition
import dev.privateagent.android.llm.ToolRound
import dev.privateagent.android.schedule.Notifications
import dev.privateagent.android.schedule.ScheduleManager
import dev.privateagent.android.security.LlmConnection
import dev.privateagent.android.security.SettingsStore
import dev.privateagent.android.ui.Navigator
import dev.privateagent.android.ui.Tab
import dev.privateagent.android.web.BrowserController
import dev.privateagent.android.web.CaptureOutcome
import dev.privateagent.android.web.Sites
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

/**
 * The chat: sends the conversation to the LLM, turns its tool calls into action cards, and runs an action only after
 * the user approves it on its card (list_records, which only reads, runs at once). A card says what happened only once
 * it happened.
 */
class ChatAgent(
    private val context: Context,
    private val messages: MessageStore,
    private val records: RecordStore,
    private val schedules: ScheduleStore,
    private val settings: SettingsStore,
    private val llm: LlmService,
    private val browser: BrowserController,
    private val scheduler: ScheduleManager,
    private val notifications: Notifications,
    private val navigator: Navigator,
    private val sites: () -> Sites,
    private val texts: Texts,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val busyState = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = busyState
    private var job: Job? = null

    /** At app start: actions that were running when the app stopped end as failed. */
    fun start() {
        scope.launch { messages.failInterrupted(context.getString(R.string.result_interrupted)) }
    }

    fun send(text: String) {
        val message = text.trim()
        if (message.isEmpty() || busyState.value) return
        busyState.value = true
        job = scope.launch {
            try {
                converse(message)
            } catch (stopped: CancellationException) {
                withContext(NonCancellable) { messages.addNotice(NoticeKind.INFO, context.getString(R.string.chat_stopped)) }
                throw stopped
            } catch (_: Exception) {
                // Never a stack trace on the screen; the message says the request did not finish.
                withContext(NonCancellable) {
                    messages.addNotice(NoticeKind.ERROR, context.getString(R.string.llm_error_unexpected))
                }
            } finally {
                busyState.value = false
                job = null
            }
        }
    }

    /** The stop button: ends the request; nothing of it is shown. */
    fun stop() {
        job?.cancel()
    }

    fun approve(messageId: Long) {
        scope.launch { runAction(messageId) }
    }

    fun cancel(messageId: Long) {
        scope.launch {
            messages.moveAction(
                messageId,
                ActionState.PENDING,
                ActionState.CANCELLED,
                result = context.getString(R.string.result_cancelled),
            )
        }
    }

    /** The user tapped a scheduled capture's notification: the capture runs in the visible browser now. */
    fun runSchedule(scheduleId: Long) {
        scope.launch {
            val row = schedules.get(scheduleId)
            val site = row?.let { sites().byKey(it.site) }
            if (row == null || site == null) {
                messages.addNotice(NoticeKind.INFO, context.getString(R.string.schedule_run_missing))
                return@launch
            }
            val args = AgentAction.WebCapture(site.key, row.query).toJson().put("schedule_id", row.id)
            val id = messages.addAction(AgentTools.WEB_CAPTURE, args.toString(), ActionState.RUNNING)
            navigator.show(Tab.BROWSER)
            val outcome = runCatchingCapture {
                browser.capture(site, row.query, CaptureSource.SCHEDULE, sites().searchUrl(site, row.query))
            }
            finishCapture(id, outcome)
        }
    }

    private suspend fun converse(message: String) {
        messages.addUser(message)
        // The key is opened with the Keystore, off the main thread, for this request only.
        val connection = withContext(Dispatchers.IO) { settings.connection() }
        if (connection == null) {
            messages.addNotice(NoticeKind.SETUP, context.getString(R.string.chat_setup_body))
            return
        }
        val siteList = sites()
        val tools = AgentTools.definitions(siteList)
        val system = SystemPrompt.build(siteList, ZonedDateTime.now())
        val turns = history(messages.all(HISTORY_MESSAGES))
        val reply = try {
            llm.complete(connection, system, turns, tools)
        } catch (error: LlmException) {
            messages.addNotice(NoticeKind.ERROR, texts.llmError(error))
            return
        }
        handleReply(connection, system, turns, tools, reply, ToolValidator(siteList))
    }

    private suspend fun handleReply(
        connection: LlmConnection,
        system: String,
        turns: List<ChatTurn>,
        tools: List<ToolDefinition>,
        reply: LlmReply,
        validator: ToolValidator,
    ) {
        if (reply.text.isNotBlank()) messages.addAssistant(reply.text.trim())
        val calls = reply.toolCalls.take(MAX_ACTIONS)
        if (reply.toolCalls.size > MAX_ACTIONS) {
            messages.addNotice(NoticeKind.INFO, context.getString(R.string.chat_too_many_actions, MAX_ACTIONS))
        }
        val results = HashMap<String, String>()
        val summaries = mutableListOf<String>()
        for (call in calls) {
            when (val validation = validator.validate(call.name, call.input)) {
                is Validation.Invalid -> {
                    val problem = texts.problem(validation.problem)
                    messages.addAssistant(problem)
                    results[call.id] = "실행하지 않음: $problem"
                }
                is Validation.Valid -> when (val action = validation.action) {
                    is AgentAction.ListRecords -> {
                        val found = listRecords(action)
                        messages.addAction(action.tool, action.toJson().toString(), ActionState.DONE, found.line)
                        results[call.id] = found.forLlm
                        summaries += found.plain
                    }
                    else -> messages.addAction(action.tool, action.toJson().toString(), ActionState.PENDING)
                }
            }
        }
        if (summaries.isNotEmpty()) {
            // One more request, so the LLM answers with what the records hold; the plain summary if it fails.
            val answer = try {
                llm.complete(connection, system, turns, tools, ToolRound(reply, results)).text
            } catch (_: LlmException) {
                ""
            }
            messages.addAssistant(answer.trim().ifEmpty { summaries.joinToString("\n\n") })
        }
        if (reply.text.isBlank() && calls.isEmpty()) {
            messages.addNotice(NoticeKind.ERROR, context.getString(R.string.chat_empty_reply))
        } else if (reply.truncated) {
            messages.addNotice(NoticeKind.INFO, context.getString(R.string.chat_truncated))
        }
    }

    private suspend fun runAction(messageId: Long) {
        val message = messages.get(messageId) ?: return
        if (message.role != MessageRole.ACTION || message.state != ActionState.PENDING) return
        val input = try {
            JSONObject(message.args ?: "{}")
        } catch (_: JSONException) {
            null
        }
        // The saved arguments are checked again: nothing runs that the validator would not let through.
        val action = when (val validation = ToolValidator(sites()).validate(message.tool.orEmpty(), input)) {
            is Validation.Valid -> validation.action
            is Validation.Invalid -> {
                messages.moveAction(messageId, ActionState.PENDING, ActionState.FAILED, texts.problem(validation.problem))
                return
            }
        }
        if (!messages.moveAction(messageId, ActionState.PENDING, ActionState.RUNNING)) return
        try {
            when (action) {
                is AgentAction.WebCapture -> {
                    val site = sites().byKey(action.site)!!
                    navigator.show(Tab.BROWSER)
                    val outcome = runCatchingCapture {
                        browser.capture(site, action.query, CaptureSource.CHAT, sites().searchUrl(site, action.query))
                    }
                    finishCapture(messageId, outcome)
                }
                is AgentAction.OpenSite -> {
                    navigator.show(Tab.BROWSER)
                    val failure = browser.open(action.url)
                    if (failure == null) {
                        val name = action.site?.let { sites().byKey(it)?.name } ?: Endpoint.host(action.url)
                        messages.moveAction(
                            messageId,
                            ActionState.RUNNING,
                            ActionState.DONE,
                            context.getString(R.string.result_opened, name),
                        )
                    } else {
                        messages.moveAction(messageId, ActionState.RUNNING, ActionState.FAILED, texts.failure(failure))
                    }
                }
                is AgentAction.ScheduleCapture -> {
                    val row = scheduler.create(action.site, action.query, action.time, action.days)
                    val next = row.nextRunAt?.let { texts.time(it) }.orEmpty()
                    var result = context.getString(R.string.result_schedule_created, next)
                    if (!notifications.canPost()) {
                        result += " " + context.getString(R.string.result_schedule_no_notifications)
                    }
                    messages.moveAction(messageId, ActionState.RUNNING, ActionState.DONE, result)
                }
                is AgentAction.ListRecords -> {
                    val found = listRecords(action)
                    messages.moveAction(messageId, ActionState.RUNNING, ActionState.DONE, found.line)
                }
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (_: Exception) {
            messages.moveAction(
                messageId,
                ActionState.RUNNING,
                ActionState.FAILED,
                context.getString(R.string.llm_error_unexpected),
            )
        }
    }

    private suspend fun runCatchingCapture(block: suspend () -> CaptureOutcome): CaptureOutcome? = try {
        block()
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (_: Exception) {
        null
    }

    private suspend fun finishCapture(messageId: Long, outcome: CaptureOutcome?) {
        when (outcome) {
            is CaptureOutcome.Saved -> messages.moveAction(
                messageId,
                ActionState.RUNNING,
                ActionState.DONE,
                texts.outcome(outcome),
                captureId = outcome.captureId,
            )
            null -> messages.moveAction(
                messageId,
                ActionState.RUNNING,
                ActionState.FAILED,
                context.getString(R.string.llm_error_unexpected),
            )
            else -> messages.moveAction(messageId, ActionState.RUNNING, ActionState.FAILED, texts.outcome(outcome))
        }
    }

    private class Found(val line: String, val forLlm: String, val plain: String)

    /** The records list_records asked for: a line for the card, a short JSON for the LLM, and a plain summary. */
    private suspend fun listRecords(action: AgentAction.ListRecords): Found {
        val filter = ItemFilter(site = action.site, kind = action.kind, search = action.query.orEmpty())
        val counts = records.counts(filter)
        val items = records.items(filter, action.limit)
        val zone = ZoneId.systemDefault()
        val forLlm = JSONObject()
            .put("now", STAMP.format(ZonedDateTime.now(zone)))
            .put("total", counts.total)
            .put("counts", JSONObject().put("ad", counts.ad).put("product", counts.product).put("result", counts.result))
            .put(
                "items",
                JSONArray(
                    items.map { item ->
                        JSONObject()
                            .put("site", texts.siteName(item.site))
                            .put("query", item.query)
                            .put("kind", item.kind.key)
                            .put("title", item.title.take(80))
                            .put("price", item.price)
                            .put("captured_at", STAMP.format(Instant.ofEpochMilli(item.capturedAt).atZone(zone)))
                            .apply { if (item.clickedAt != null) put("clicked", true) }
                    },
                ),
            )
            .toString()
        val line = if (counts.total == 0) {
            context.getString(R.string.result_list_none)
        } else {
            context.getString(R.string.result_list, counts.total)
        }
        val plain = buildString {
            append(line)
            if (counts.total > 0) {
                append(" (").append(texts.countList(counts)).append(")")
                for (item in items.take(PLAIN_ITEMS)) {
                    append("\n• ").append(texts.siteName(item.site)).append(" · ").append(texts.kind(item.kind))
                        .append(" · ").append(item.title.take(60))
                    if (item.price.isNotEmpty()) append(" · ").append(item.price)
                }
            }
        }
        return Found(line, forLlm, plain)
    }

    /** The recent conversation as the LLM reads it: user and assistant text, and one line per action card. */
    private fun history(all: List<ChatMessage>): List<ChatTurn> {
        val turns = ArrayList<ChatTurn>()
        for (message in all) {
            when (message.role) {
                MessageRole.USER -> turns += ChatTurn(ChatTurn.Role.USER, message.text.take(MAX_MESSAGE_CHARS))
                MessageRole.ASSISTANT -> turns += ChatTurn(ChatTurn.Role.ASSISTANT, message.text.take(MAX_MESSAGE_CHARS))
                MessageRole.ACTION -> turns += ChatTurn(ChatTurn.Role.ASSISTANT, actionLine(message))
                MessageRole.NOTICE -> Unit
            }
        }
        var total = turns.sumOf { it.text.length }
        while (turns.size > 1 && total > MAX_HISTORY_CHARS) total -= turns.removeAt(0).text.length
        return turns
    }

    private fun actionLine(message: ChatMessage): String {
        val state = when (message.state) {
            ActionState.PENDING -> "승인 대기"
            ActionState.RUNNING -> "실행 중"
            ActionState.DONE -> "완료"
            ActionState.FAILED -> "실패"
            ActionState.CANCELLED -> "사용자가 취소"
            null -> "?"
        }
        val result = message.result?.let { ": ${it.take(200)}" }.orEmpty()
        return "[도구 ${message.tool} ${message.args.orEmpty().take(200)} · $state$result]"
    }

    private companion object {
        const val MAX_ACTIONS = 4
        const val HISTORY_MESSAGES = 30
        const val MAX_MESSAGE_CHARS = 2_000
        const val MAX_HISTORY_CHARS = 12_000
        const val PLAIN_ITEMS = 5
        val STAMP: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
    }
}
