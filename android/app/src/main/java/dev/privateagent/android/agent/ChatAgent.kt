package dev.privateagent.android.agent

import android.content.Context
import dev.privateagent.android.R
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.ChatMessage
import dev.privateagent.android.data.MessageRole
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.NoticeKind
import dev.privateagent.android.data.ScheduleStore
import dev.privateagent.android.hermes.HermesModel
import dev.privateagent.android.hermes.HermesRuntime
import dev.privateagent.android.hermes.LlmHermesModel
import dev.privateagent.android.llm.ChatTurn
import dev.privateagent.android.llm.LlmClient
import dev.privateagent.android.llm.LlmException
import dev.privateagent.android.llm.LlmReply
import dev.privateagent.android.llm.LlmService
import dev.privateagent.android.llm.ToolDefinition
import dev.privateagent.android.llm.ToolRound
import dev.privateagent.android.security.LlmConnection
import dev.privateagent.android.security.SettingsStore
import dev.privateagent.android.ui.Navigator
import dev.privateagent.android.ui.Tab
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
import org.json.JSONException
import org.json.JSONObject
import java.time.ZonedDateTime

/**
 * The chat. With the bundled Hermes runtime (android/hermes), Hermes runs each turn with its own tools and the app's
 * actions, live ([HermesChat]), as on the desktop. A build without it plans with the LLM's own tool calls instead: the
 * calls become action cards, and an action runs only after the user approves it on its card (list_records, which only
 * reads, runs at once). A card says what happened only once it happened.
 */
class ChatAgent(
    private val context: Context,
    private val messages: MessageStore,
    private val schedules: ScheduleStore,
    private val settings: SettingsStore,
    private val llm: LlmService,
    private val actions: AppActions,
    private val navigator: Navigator,
    private val sites: () -> Sites,
    private val texts: Texts,
    private val runtime: HermesRuntime? = null,
    val hermes: HermesChat? = null,
    private val hermesModel: (LlmConnection) -> HermesModel = { LlmHermesModel(LlmClient(), it) },
) {
    /** Which planner the next message uses: the bundled Hermes agent, or the LLM's own tool calls (no runtime). */
    enum class Engine { HERMES, DIRECT }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val busyState = MutableStateFlow(false)
    val busy: StateFlow<Boolean> = busyState
    private var job: Job? = null

    val engine: Engine get() = if (hermes != null && runtime?.isBundled == true) Engine.HERMES else Engine.DIRECT

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

    /** The stop button: ends the request (Hermes' worker is stopped too); nothing of it is shown. */
    fun stop() {
        job?.cancel()
    }

    /** Waits for the current request to end (tests). */
    suspend fun join() {
        job?.join()
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
            val action = AgentAction.WebCapture(site.key, row.query)
            val id = messages.addAction(
                AgentTools.WEB_CAPTURE,
                action.toJson().put("schedule_id", row.id).toString(),
                ActionState.RUNNING,
            )
            navigator.show(Tab.BROWSER)
            finish(id, actions.run(action, CaptureSource.SCHEDULE))
        }
    }

    private suspend fun converse(message: String) {
        // The conversation before this message, for Hermes' context.
        val before = messages.all(HISTORY_MESSAGES)
        messages.addUser(message)
        // The key is opened with the Keystore, off the main thread, for this request only.
        val connection = withContext(Dispatchers.IO) { settings.connection() }
        if (connection == null) {
            messages.addNotice(NoticeKind.SETUP, context.getString(R.string.chat_setup_body))
            return
        }
        val chat = hermes
        if (chat != null && engine == Engine.HERMES) {
            chat.converse(hermesModel(connection), connection.model, message, before)
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
                        val found = actions.listRecords(action)
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
        finish(messageId, actions.run(action))
    }

    private suspend fun finish(messageId: Long, outcome: AppActions.Outcome) {
        messages.moveAction(messageId, ActionState.RUNNING, outcome.state, outcome.line, captureId = outcome.captureId)
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
    }
}
