package dev.privateagent.android.hermes

import android.os.SystemClock
import dev.privateagent.android.util.text
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.util.UUID

/**
 * The bundled Hermes worker (runtime/hermes/worker.py) and its JSON-lines protocol, as the desktop's hermes.py drives
 * it. One turn at a time: the turn frame goes out, then every frame the worker sends is answered until its result.
 * Model requests are answered with the app's LLM connection ([Handler.complete]), so no key reaches the worker; the
 * app's own actions run in the app ([Handler.appTool]); an approval or a clarify question waits for the user, bounded.
 * The worker is kept for the next turn and shut down after a while without one. A stop sends cancel and kills it.
 */
class HermesSession(private val runtime: HermesRuntime) {
    /** HERMES_HOME. */
    val home get() = runtime.home

    private val turns = Mutex()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var idle: Job? = null

    @Volatile
    private var process: HermesProcess? = null

    /** What one turn sends: the frame fields of worker.py's "turn" request. */
    class Turn(
        val system: String,
        val context: String,
        val model: String,
        val toolsets: List<String>,
        val appTools: JSONArray,
        val settings: JSONObject,
        val budgetSeconds: Int,
    )

    enum class InteractionKind(val key: String) { APPROVAL("approval"), CLARIFY("clarify") }

    /** What the app does with the worker's requests during a turn. */
    interface Handler {
        /** An inference frame's request body; the answer is a whole chat.completion. */
        suspend fun complete(request: JSONObject): JSONObject

        /** An app tool Hermes called: the card's status ("done" or "failed") and result ({"text", ...}). */
        suspend fun appTool(name: String, args: JSONObject): AppToolResult

        /** An approval or clarify the user answers; null when they did not in time, or the turn stopped. */
        suspend fun ask(kind: InteractionKind, payload: JSONObject): JSONObject?

        fun progress(event: String, tool: String, preview: String)

        fun draft(text: String)
    }

    class AppToolResult(val status: String, val result: JSONObject)

    /** A turn that did not end with Hermes' answer; [problem] says why, [detail] is the worker's error kind. */
    class TurnException(val problem: Problem, val detail: String = "") : Exception("Hermes turn failed: $problem")

    enum class Problem { UNAVAILABLE, START, ENDED, WORKER, TIMEOUT, TOO_LARGE }

    /** Runs one turn and returns Hermes' answer (the result frame's message). */
    suspend fun run(turn: Turn, handler: Handler): String = turns.withLock {
        idle?.cancel()
        try {
            withContext(Dispatchers.IO) { loop(turn, handler) }
        } finally {
            scheduleIdleShutdown()
        }
    }

    /** Stops the worker (the app is closing, or the settings it was started with changed). */
    fun shutdown() {
        idle?.cancel()
        scope.launch { turns.withLock { close(graceful = true) } }
    }

    private suspend fun loop(turn: Turn, handler: Handler): String {
        val worker = ensureProcess()
        var finished = false
        try {
            send(worker, turnFrame(turn))
            var deadline = SystemClock.elapsedRealtime() + (turn.budgetSeconds + GRACE_SECONDS) * 1000L
            while (true) {
                currentCoroutineContextEnsureActive()
                if (SystemClock.elapsedRealtime() > deadline) throw TurnException(Problem.TIMEOUT)
                val frame = try {
                    worker.receive(POLL_MS)
                } catch (_: HermesProcessEndedException) {
                    throw TurnException(Problem.ENDED)
                } ?: continue
                val id = frame.text("id")
                when (frame.text("type")) {
                    "inference" -> {
                        val completion = handler.complete(frame.optJSONObject("body") ?: JSONObject())
                        send(worker, JSONObject().put("type", "completion").put("id", id).put("body", completion))
                    }
                    "app_tool" -> {
                        val card = handler.appTool(frame.text("tool"), frame.optJSONObject("args") ?: JSONObject())
                        send(
                            worker,
                            JSONObject().put("type", "app_tool_reply").put("id", id).put("status", card.status)
                                .put("result", card.result),
                        )
                    }
                    "approval", "clarify" -> {
                        val kind = if (frame.text("type") == "approval") InteractionKind.APPROVAL else InteractionKind.CLARIFY
                        val waited = SystemClock.elapsedRealtime()
                        val answer = handler.ask(kind, payload(frame))
                        // The user's thinking time is not the turn's compute time.
                        deadline += SystemClock.elapsedRealtime() - waited
                        send(worker, reply(kind, id, answer))
                    }
                    "progress" -> handler.progress(frame.text("event"), frame.text("tool"), frame.text("preview"))
                    "draft" -> handler.draft(frame.text("text"))
                    "result" -> {
                        finished = true
                        if (frame.optBoolean("cancelled")) throw CancellationException("Hermes cancelled the turn")
                        return frame.text("message")
                    }
                    "error" -> {
                        finished = true
                        throw TurnException(Problem.WORKER, frame.text("kind").take(60))
                    }
                }
            }
        } catch (stopped: CancellationException) {
            withContext(NonCancellable) { stop(worker) }
            throw stopped
        } catch (failure: Throwable) {
            // A turn that broke off leaves the worker waiting for a reply that will not come: start fresh next time.
            if (!finished) withContext(NonCancellable) { stop(worker) }
            throw failure
        }
    }

    private suspend fun currentCoroutineContextEnsureActive() {
        kotlin.coroutines.coroutineContext.ensureActive()
    }

    private fun ensureProcess(): HermesProcess {
        process?.takeIf { it.isAlive }?.let { return it }
        process?.stop()
        val started = try {
            runtime.start()
        } catch (_: HermesUnavailableException) {
            throw TurnException(Problem.UNAVAILABLE)
        } catch (_: IOException) {
            throw TurnException(Problem.START)
        }
        process = started
        return started
    }

    private fun send(worker: HermesProcess, frame: JSONObject) {
        try {
            worker.send(frame)
        } catch (_: IOException) {
            throw if (frame.toString().length > HermesProcess.MAX_FRAME) {
                TurnException(Problem.TOO_LARGE)
            } else {
                TurnException(Problem.ENDED)
            }
        }
    }

    /** The desktop's stop: cancel the turn, then end the process (a model call it waits on never answers). */
    private fun stop(worker: HermesProcess) {
        runCatching { worker.send(JSONObject().put("type", "cancel")) }
        worker.stop()
        if (process === worker) process = null
    }

    private fun close(graceful: Boolean) {
        val worker = process ?: return
        process = null
        if (graceful && worker.isAlive) {
            runCatching { worker.send(JSONObject().put("type", "shutdown")) }
            if (worker.waitFor(SHUTDOWN_MS) != null) return
        }
        worker.stop()
    }

    /** An idle worker holds a few hundred MB: it ends after a while without a turn, and the next turn starts one. */
    private fun scheduleIdleShutdown() {
        idle?.cancel()
        idle = scope.launch {
            delay(IDLE_MS)
            turns.withLock { close(graceful = true) }
        }
    }

    companion object {
        private const val POLL_MS = 200L
        private const val GRACE_SECONDS = 30
        private const val SHUTDOWN_MS = 3_000L
        private const val IDLE_MS = 5 * 60_000L

        /** worker.py's turn request. No browser executable: the phone has no Chromium for Hermes. */
        fun turnFrame(turn: Turn): JSONObject = JSONObject()
            .put("type", "turn")
            .put("system", turn.system)
            .put("context", turn.context)
            .put("model", turn.model)
            .put("session_id", "pa_" + UUID.randomUUID().toString().replace("-", ""))
            .put("toolsets", JSONArray(turn.toolsets))
            .put("app_tools", turn.appTools)
            .put("settings", turn.settings)
            .put("chromium", "")
            .put("budget", turn.budgetSeconds)

        /** An approval or clarify frame without its routing fields, as the screen shows it. */
        fun payload(frame: JSONObject): JSONObject {
            val payload = JSONObject(frame.toString())
            payload.remove("type")
            payload.remove("id")
            return payload
        }

        /** The worker's reply to an approval or clarify: deny or cancelled when there is no usable answer. */
        fun reply(kind: InteractionKind, id: String, answer: JSONObject?): JSONObject = when (kind) {
            InteractionKind.APPROVAL -> {
                val decision = answer?.text("decision").orEmpty()
                JSONObject().put("type", "approval_reply").put("id", id)
                    .put("decision", if (decision in DECISIONS) decision else "deny")
            }
            InteractionKind.CLARIFY -> {
                val answers = answer?.optJSONObject("answers")
                val outcome = if (answers != null && answer.text("outcome") != "cancelled") "submitted" else "cancelled"
                JSONObject().put("type", "clarify_reply").put("id", id).put("answers", answers ?: JSONObject())
                    .put("outcome", outcome)
            }
        }

        val DECISIONS = setOf("once", "session", "always", "deny")
    }
}
