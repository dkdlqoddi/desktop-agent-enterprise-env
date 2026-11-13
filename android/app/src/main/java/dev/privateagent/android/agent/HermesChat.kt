package dev.privateagent.android.agent

import android.content.Context
import dev.privateagent.android.R
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.ChatMessage
import dev.privateagent.android.data.MessageRole
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.NoticeKind
import dev.privateagent.android.hermes.HermesModel
import dev.privateagent.android.hermes.HermesSession
import dev.privateagent.android.llm.LlmException
import dev.privateagent.android.llm.LlmProtocol
import dev.privateagent.android.ui.Navigator
import dev.privateagent.android.ui.Tab
import dev.privateagent.android.util.cleanText
import dev.privateagent.android.util.text
import dev.privateagent.android.web.Sites
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import java.io.File
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale
import java.util.UUID

/**
 * The chat on the bundled Hermes agent, as the desktop's agent_chat.run_agent: Hermes runs the turn with its own tools
 * (web search, memory, skills, a todo list, past conversations, subagents, vision, clarify) and with the app's actions,
 * which run live when it calls them, each checked first ([ToolValidator]) and shown in the chat as a card with what it
 * did. A command Hermes' own guard escalates, and a question Hermes asks, wait in the chat for the user (bounded).
 * Every model request is answered with the user's LLM connection; the key never reaches the worker.
 *
 * What a turn did, the tools it ran, the app's actions, the commands asked about and how each was decided, is kept
 * with the message that ended it ([Activity]) and shown under it.
 */
class HermesChat(
    private val context: Context,
    private val messages: MessageStore,
    private val actions: AppActions,
    private val navigator: Navigator,
    private val sites: () -> Sites,
    private val texts: Texts,
    private val session: HermesSession,
) {
    /** What the chat shows while a turn runs: the steps Hermes took, its draft, and a question for the user. */
    data class TurnView(
        val steps: List<Step> = emptyList(),
        val draft: String = "",
        val interaction: Interaction? = null,
    )

    data class Step(val tool: String, val text: String, val done: Boolean)

    /** An approval (a command to allow) or a clarify (questions to answer) the turn waits on. */
    data class Interaction(val id: String, val kind: HermesSession.InteractionKind, val payload: JSONObject)

    private val viewState = MutableStateFlow<TurnView?>(null)
    val view: StateFlow<TurnView?> = viewState

    @Volatile
    private var waiting: Pair<String, CompletableDeferred<JSONObject>>? = null

    /** The activity of a turn that ended without a message of its own to keep it with ([claimActivity]). */
    @Volatile
    private var loose: Activity? = null

    /**
     * Runs one turn for the user's [message] with the conversation before it ([history]). The answer, the cards of
     * the actions Hermes ran and any failure go to the conversation, and what the turn did goes with the last of them.
     */
    suspend fun converse(model: HermesModel, modelName: String, message: String, history: List<ChatMessage>) {
        viewState.value = TurnView()
        loose = null
        val recorder = ActivityRecorder()
        var ran = 0
        // The last message this turn added: what the turn did on the way is kept with it.
        var last: Long? = null
        var stopped = false
        try {
            withContext(Dispatchers.IO) { introduce(session.home) }
            val turn = HermesSession.Turn(
                system = system(sites(), ZonedDateTime.now()),
                context = context(history, message, ZonedDateTime.now()),
                model = modelName,
                toolsets = TOOLSETS,
                appTools = AgentTools.hermesSchemas(sites()),
                settings = SETTINGS,
                budgetSeconds = BUDGET_SECONDS,
            )
            val answer = session.run(
                turn,
                object : HermesSession.Handler {
                    override suspend fun complete(request: JSONObject): JSONObject {
                        viewState.update { it?.copy(draft = "") }
                        return model.complete(request)
                    }

                    override suspend fun appTool(name: String, args: JSONObject): HermesSession.AppToolResult {
                        ran += 1
                        val (card, result) = runAppTool(name, args, recorder)
                        last = card
                        return result
                    }

                    override suspend fun ask(kind: HermesSession.InteractionKind, payload: JSONObject) =
                        askUser(kind, payload, recorder)

                    override fun progress(event: String, tool: String, preview: String) {
                        // The app's own tools show as cards in the chat, with the step below saying how they ended.
                        if (tool in APP_TOOLS) return
                        val name = label(tool)
                        if (event == "tool.started" && name.isNotEmpty()) {
                            recorder.step(StepKind.TOOL, if (preview.isEmpty()) name else "$name: $preview")
                        }
                        step(event, name, preview)
                    }

                    override fun draft(text: String) {
                        viewState.update { it?.copy(draft = (it.draft + text).takeLast(MAX_DRAFT)) }
                    }
                },
            )
            val reply = spoken(answer)
            when {
                reply.isNotEmpty() -> last = messages.addAssistant(reply)
                ran == 0 -> last = messages.addNotice(NoticeKind.ERROR, context.getString(R.string.hermes_no_reply))
            }
        } catch (failure: HermesSession.TurnException) {
            last = messages.addNotice(NoticeKind.ERROR, problem(failure))
        } catch (failure: LlmException) {
            last = messages.addNotice(NoticeKind.ERROR, texts.llmError(failure))
        } catch (stop: CancellationException) {
            stopped = true
            throw stop
        } finally {
            waiting?.second?.cancel()
            waiting = null
            viewState.value = null
            val activity = recorder.snapshot()
            val ended = last
            when {
                activity == null -> Unit
                // A stop, or a failure only the caller tells of: its notice takes the activity (claimActivity).
                stopped || ended == null -> loose = activity
                else -> withContext(NonCancellable) { messages.setActivity(ended, activity) }
            }
        }
    }

    /**
     * Keeps what the last turn did with the message [messageId], when the turn ended without one of its own: the
     * notice the caller wrote for a stop.
     */
    suspend fun claimActivity(messageId: Long) {
        val activity = loose ?: return
        loose = null
        messages.setActivity(messageId, activity)
    }

    /**
     * Hermes' identity file (SOUL.md) for a phone. The worker writes the desktop's only where there is none, so this
     * one, written first, stays; the user's own edits to it stay too.
     */
    private fun introduce(home: File) {
        val soul = File(home, "SOUL.md")
        if (soul.exists()) return
        home.mkdirs()
        soul.writeText("You assist the user through Private Agent, an app on their own phone.\n")
    }

    /** The user's decision on the command the turn waits on: once, session, always or deny. */
    fun decide(interactionId: String, decision: String) {
        if (decision !in HermesSession.DECISIONS) return
        resolve(interactionId, JSONObject().put("decision", decision))
    }

    /** The user's answers ({qid: answer}) to the questions the turn waits on. */
    fun answer(interactionId: String, answers: JSONObject) {
        resolve(interactionId, JSONObject().put("answers", answers).put("outcome", "submitted"))
    }

    private fun resolve(interactionId: String, answer: JSONObject) {
        val (id, pending) = waiting ?: return
        if (id == interactionId) pending.complete(answer)
    }

    private suspend fun askUser(
        kind: HermesSession.InteractionKind,
        payload: JSONObject,
        recorder: ActivityRecorder,
    ): JSONObject? {
        val id = UUID.randomUUID().toString()
        val pending = CompletableDeferred<JSONObject>()
        waiting = id to pending
        viewState.update { it?.copy(interaction = Interaction(id, kind, payload)) }
        navigator.show(Tab.CHAT)
        var answer: JSONObject? = null
        // A question nobody answered ran out of its time, unless the turn was stopped meanwhile.
        var unanswered = "timeout"
        try {
            answer = withTimeoutOrNull(INTERACTION_MS) { pending.await() }
            return answer
        } catch (stop: CancellationException) {
            unanswered = "stopped"
            throw stop
        } finally {
            waiting = null
            viewState.update { it?.copy(interaction = null) }
            when (kind) {
                HermesSession.InteractionKind.APPROVAL -> recorder.step(
                    StepKind.APPROVAL,
                    payload.text("command"),
                    payload.text("description"),
                    answer?.text("decision")?.takeIf { it in HermesSession.DECISIONS } ?: unanswered,
                )
                HermesSession.InteractionKind.CLARIFY -> recorder.step(
                    StepKind.QUESTION,
                    ActivityRecorder.questionText(payload.optJSONArray("questions")),
                    result = if (answer == null) unanswered else "submitted",
                )
            }
        }
    }

    /** One app action Hermes called: checked, shown as a card, run, and its result handed back with the card's id. */
    private suspend fun runAppTool(
        name: String,
        args: JSONObject,
        recorder: ActivityRecorder,
    ): Pair<Long, HermesSession.AppToolResult> {
        val action = when (val validation = ToolValidator(sites()).validate(name, args)) {
            is Validation.Valid -> validation.action
            is Validation.Invalid -> {
                val problem = texts.problem(validation.problem)
                val card = messages.addAction(cleanText(name, 40), "{}", ActionState.FAILED, problem)
                recorder.step(StepKind.ACTION, cleanText(name, 40), problem, "failed")
                return card to HermesSession.AppToolResult("failed", JSONObject().put("text", problem))
            }
        }
        val title = texts.toolTitle(action.tool)
        step("tool.started", title, "")
        val id = messages.addAction(action.tool, action.toJson().toString(), ActionState.RUNNING)
        val outcome = try {
            actions.run(action)
        } catch (stopped: CancellationException) {
            withContext(NonCancellable) {
                messages.moveAction(id, ActionState.RUNNING, ActionState.FAILED, context.getString(R.string.chat_stopped))
            }
            recorder.step(StepKind.ACTION, title, result = "stopped")
            throw stopped
        }
        messages.moveAction(id, ActionState.RUNNING, outcome.state, outcome.line, captureId = outcome.captureId)
        step("tool.completed", title, outcome.line)
        recorder.step(StepKind.ACTION, title, outcome.line, if (outcome.done) "done" else "failed")
        // A capture shows in the browser while it runs; the conversation goes on in the chat. A site opened for the
        // user to browse stays on the screen.
        if (action is AgentAction.WebCapture || action is AgentAction.CapturePage) navigator.show(Tab.CHAT)
        return id to HermesSession.AppToolResult(if (outcome.done) "done" else "failed", outcome.result)
    }

    /** Hermes' own tools by what they do, in the chat's words; others by their name. */
    private fun label(tool: String): String = when (tool) {
        "web_search" -> context.getString(R.string.hermes_tool_web_search)
        "web_extract" -> context.getString(R.string.hermes_tool_web_extract)
        "memory" -> context.getString(R.string.hermes_tool_memory)
        "skill_view", "skills_list", "skill_manage" -> context.getString(R.string.hermes_tool_skills)
        "todo_list" -> context.getString(R.string.hermes_tool_todo)
        "session_search" -> context.getString(R.string.hermes_tool_sessions)
        "delegate_task" -> context.getString(R.string.hermes_tool_delegate)
        "vision_analyze" -> context.getString(R.string.hermes_tool_vision)
        "clarify" -> context.getString(R.string.hermes_tool_clarify)
        else -> tool
    }

    private fun step(event: String, tool: String, preview: String) {
        if (tool.isEmpty()) return
        viewState.update { view ->
            view ?: return@update null
            val text = cleanText(if (preview.isEmpty()) tool else "$tool: $preview", MAX_STEP)
            val steps = if (event == "tool.completed") {
                val open = view.steps.indexOfLast { it.tool == tool && !it.done }
                if (open >= 0) {
                    view.steps.toMutableList().also { it[open] = Step(tool, text, done = true) }
                } else {
                    view.steps + Step(tool, text, done = true)
                }
            } else {
                view.steps + Step(tool, text, done = false)
            }
            view.copy(steps = steps.takeLast(MAX_STEPS))
        }
    }

    private fun problem(failure: HermesSession.TurnException): String = when (failure.problem) {
        HermesSession.Problem.UNAVAILABLE -> context.getString(R.string.hermes_unavailable)
        HermesSession.Problem.START -> context.getString(R.string.hermes_start_failed)
        HermesSession.Problem.ENDED -> context.getString(R.string.hermes_ended)
        HermesSession.Problem.WORKER -> context.getString(R.string.hermes_failed, failure.detail.ifEmpty { "unknown" })
        HermesSession.Problem.TIMEOUT -> context.getString(R.string.hermes_timeout)
        HermesSession.Problem.TOO_LARGE -> context.getString(R.string.hermes_too_large)
    }

    companion object {
        /** Hermes' toolsets on a phone: no terminal, code execution, files, browser automation or computer use. */
        val TOOLSETS = listOf(
            "private_agent", "memory", "skills", "web", "todo", "session_search", "delegation", "vision", "clarify",
        )

        /**
         * worker.py's runtime settings: no computer use, no MCP servers, and the web search that needs no key
         * (ddgs). No service reads pages: ddgs only searches, and a phone has no service key to name one.
         */
        val SETTINGS: JSONObject
            get() = JSONObject().put("computer_use", false).put("mcp_servers", JSONObject()).put("extract_backend", "ddgs")

        /** The whole turn's compute budget, as the desktop's: four times the connection's 60-second limit. */
        const val BUDGET_SECONDS = 240

        /** An approval or clarify waits this long for the user before the turn goes on without (deny, cancelled). */
        const val INTERACTION_MS = 300_000L

        private val APP_TOOLS = setOf(
            AgentTools.LIST_RECORDS, AgentTools.WEB_CAPTURE, AgentTools.OPEN_SITE, AgentTools.CAPTURE_PAGE,
            AgentTools.SCHEDULE_CAPTURE,
        )
        private const val HISTORY_CHARS = 6_000
        private const val MAX_DRAFT = 12_000
        private const val MAX_STEP = 200
        private const val MAX_STEPS = 20
        private val TODAY: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd (EEEE)", Locale.ENGLISH)

        /** The system prompt of the Hermes turn: the phone, the app's own tools and their rules. No secrets. */
        fun system(sites: Sites, now: ZonedDateTime): String {
            val siteList = sites.all.joinToString(", ") { "${it.key} (${it.name})" }
            return """
                You are the agent of Private Agent for Android, an app on the user's own phone that keeps their web records: the ads, products and results it saw on shopping and search sites. Reply in Korean, in short concrete sentences.

                You act directly with your tools — web search, memory, skills, a todo list, past conversations — and with the app's own tools below. Do the work; do not ask permission for ordinary steps. This phone has no terminal, code execution, file access or browser automation for you. Never claim a result you do not have, and do not invent records, prices or ads.

                The app's own tools:
                - list_records {site?, query?, kind?, limit?}: search the web records kept on this phone (kind: ad, product, result, clicked).
                - web_capture {site, query}: open a site's search results in the app's visible browser and record the ads, products and results. Sites: $siteList.
                - open_site {site?, url?}: open a site or an https address in the app's browser for the user to browse.
                - capture_page: record the ads and items of the page the app's browser shows now.
                - schedule_capture {site, query, time, days}: at that time the phone shows a notification; when the user taps it, the app records that search.

                The app only reads shopping pages; it never clicks ads or links. Ads the user clicks themselves are recorded with their landing page. If a site asks for a robot check (CAPTCHA), the user solves it in the browser; never try to bypass it. If a request is ambiguous (which site? what to search?), use the clarify tool to ask.

                Now: ${now.toLocalDate()} ${now.toLocalTime().withNano(0)}, time zone ${now.zone.id}.
            """.trimIndent()
        }

        /** The recent conversation as Hermes reads it, ending with the new message (agent_chat.context). */
        fun context(history: List<ChatMessage>, message: String, now: ZonedDateTime): String {
            val lines = ArrayList<String>()
            var used = 0
            for (entry in history.asReversed()) {
                val said = when (entry.role) {
                    MessageRole.USER -> "사용자: " + entry.text
                    MessageRole.ASSISTANT -> "에이전트: " + entry.text
                    MessageRole.ACTION -> "에이전트: [작업] ${entry.tool} → " + (entry.result ?: entry.state?.key.orEmpty())
                    MessageRole.NOTICE -> continue
                }
                if (used + said.length > HISTORY_CHARS) break
                lines += said
                used += said.length
            }
            lines.reverse()
            return "Today is ${TODAY.format(now)}; tomorrow is ${now.plusDays(1).toLocalDate()}.\n" +
                "Conversation so far:\n" + lines.joinToString("\n") + "\n\nNew message from the user:\n" + message
        }

        // What a raw plan starts with: JSON ({" [{ [" {} …) or the markup of a tool call (<tool_call>, <|…). An answer
        // that only opens with a bracket ("[요약] …", "[이름](https://…)", "<오늘의 광고>") is words.
        private val RAW_PLAN = Regex("""^(?:[\[{]\s*(?:["{\[\]}]|$)|<[A-Za-z_|/!?])""")

        /** Hermes' words as the chat shows them: no reasoning block, and nothing that reads as a raw plan. */
        fun spoken(value: String): String {
            val words = LlmProtocol.stripThinking(value)
            return if (RAW_PLAN.containsMatchIn(words)) "" else words.take(4_000)
        }

        /** A draft as the chat shows it: none while it reads as a control answer (JSON or a code block). */
        fun visibleDraft(draft: String): String {
            val words = LlmProtocol.stripThinking(draft)
            return if (words.startsWith("{") || words.startsWith("```")) "" else words
        }
    }
}
