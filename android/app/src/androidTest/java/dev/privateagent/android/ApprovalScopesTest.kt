package dev.privateagent.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.agent.AgentGrants
import dev.privateagent.android.agent.AppActions
import dev.privateagent.android.agent.ChatAgent
import dev.privateagent.android.agent.HermesChat
import dev.privateagent.android.agent.StepKind
import dev.privateagent.android.data.AppDatabase
import dev.privateagent.android.data.MessageRole
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.data.ScheduleStore
import dev.privateagent.android.data.TrashStore
import dev.privateagent.android.hermes.HermesModel
import dev.privateagent.android.hermes.HermesProcess
import dev.privateagent.android.hermes.HermesSession
import dev.privateagent.android.llm.LlmService
import dev.privateagent.android.ui.Navigator
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.BufferedReader
import java.io.ByteArrayInputStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.io.PipedInputStream
import java.io.PipedOutputStream
import java.util.Collections
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/**
 * The scopes of an answer to a command, through the chat as the app runs it, against a worker of the test's own (a
 * phone's Hermes has no terminal, so the real worker never asks): "always" keeps the very command and hands it to the
 * later turns, "session" keeps the kinds the worker tells, a scope the question did not offer or a command that holds
 * a secret is one run only, and each question is kept with how it was decided among what the turn did. A conversation
 * cleared and restored from the trash comes back without what was allowed for it.
 */
@RunWith(AndroidJUnit4::class)
class ApprovalScopesTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val graph = (context.applicationContext as PrivateAgentApp).graph
    private val database = AppDatabase(context, name = null)
    private val messages = MessageStore(database)
    private val actions = AppActions(
        context, RecordStore(database), graph.browser, graph.scheduler, graph.notifications, Navigator(), { graph.sites },
        graph.texts,
    )
    private val home = File(context.cacheDir, "approval-scopes-home")
    private val grants = AgentGrants(AgentGrants.MemoryStore())

    /** What the worker asks about, one question a turn, in order. */
    private val questions = Collections.synchronizedList(mutableListOf<JSONObject>())
    private val turns = Collections.synchronizedList(mutableListOf<JSONObject>())
    private val replies = Collections.synchronizedList(mutableListOf<JSONObject>())

    private val session = HermesSession(home, { HermesProcess(Worker(::serve)) })
    private val chat = HermesChat(context, messages, actions, Navigator(), { graph.sites }, graph.texts, session, grants)
    private val model = HermesModel { JSONObject() }

    @After
    fun close() {
        session.shutdown()
        database.close()
        home.deleteRecursively()
    }

    /** The worker: each turn it asks about the next command, tells what a "session" answer covers, and answers. */
    private fun serve(pipes: Pipes) {
        while (true) {
            val frame = pipes.read()
            if (frame.optString("type") != "turn") return
            turns += frame
            val question = questions.removeAt(0)
            pipes.write(JSONObject(question.toString()).put("type", "approval").put("id", "a${turns.size}"))
            val reply = pipes.read()
            replies += reply
            if (reply.optString("decision") == "session") {
                pipes.write(JSONObject().put("type", "grant").put("keys", JSONArray(listOf("recursive delete"))))
            }
            pipes.write(JSONObject().put("type", "result").put("message", "끝났습니다."))
        }
    }

    private fun question(command: String, session: Boolean = true, permanent: Boolean = true, refused: Boolean = false) =
        JSONObject().put("command", command).put("description", "확인이 필요한 명령").put("allow_session", session)
            .put("allow_permanent", permanent).put("smart_denied", refused)

    /** Runs one turn in which the worker asks [asked], answers it with [decision] (twice), and returns the reply. */
    private fun turn(asked: JSONObject, decision: String): JSONObject = runBlocking {
        questions += asked
        val running = async(Dispatchers.Default) { chat.converse(model, "stand-in", "실행해 줘", emptyList()) }
        val interaction = withTimeout(30_000) {
            var found: HermesChat.Interaction? = null
            while (found == null) {
                found = chat.view.value?.interaction
                if (found == null) delay(50)
            }
            found
        }
        assertEquals(HermesSession.InteractionKind.APPROVAL, interaction.kind)
        assertEquals(asked.getString("command"), interaction.payload.getString("command"))
        chat.decide(interaction.id, decision)
        // A second answer to the same question finds it answered.
        chat.decide(interaction.id, "deny")
        withTimeout(30_000) { running.await() }
        replies.last()
    }

    private fun strings(array: JSONArray) = (0 until array.length()).map { array.getString(it) }

    private fun lastApproval() = messages.let {
        runBlocking { it.all() }.last { message -> message.role == MessageRole.ASSISTANT }.activity!!.steps
            .single { step -> step.kind == StepKind.APPROVAL }
    }

    @Test
    fun anAnswerKeepsWhatItsScopeCoversAndTheLaterTurnsAreHandedIt() {
        // "always": the very command, kept and handed to every later turn.
        assertEquals("always", turn(question("git push origin main"), "always").getString("decision"))
        assertEquals(listOf("git push origin main"), grants.allowed.value)
        assertEquals(emptyList<String>(), strings(turns[0].getJSONArray("allowed")))
        assertEquals("git push origin main" to "always", lastApproval().let { it.text to it.result })
        assertEquals("확인이 필요한 명령", lastApproval().note)

        // "session": the kinds of command the worker tells, for this conversation.
        assertEquals("session", turn(question("rm -rf build"), "session").getString("decision"))
        assertEquals(listOf("recursive delete"), grants.session.value)
        assertEquals(listOf("git push origin main"), strings(turns[1].getJSONArray("allowed")))
        assertEquals(emptyList<String>(), strings(turns[1].getJSONArray("session_allowed")))
        assertEquals("session", lastApproval().result)

        // A command that holds what reads as a secret is run once and never kept; what is kept of it is redacted.
        val secret = "curl -H 'Authorization: Bearer abcdef123456' https://example.test"
        assertEquals("once", turn(question(secret), "always").getString("decision"))
        assertEquals(listOf("git push origin main"), grants.allowed.value)
        assertEquals(listOf("recursive delete"), strings(turns[2].getJSONArray("session_allowed")))
        assertEquals("curl -H 'Authorization: [REDACTED]" to "once", lastApproval().let { it.text to it.result })

        // A scope the question did not offer (the safety check's own refusal) is one run only.
        val refused = question("rm -rf /tmp/x", session = false, permanent = false, refused = true)
        assertEquals("once", turn(refused, "session").getString("decision"))
        assertEquals("once", turn(JSONObject(refused.toString()), "always").getString("decision"))
        assertEquals("deny", turn(question("rm -rf /tmp/y"), "deny").getString("decision"))
        assertEquals("deny", lastApproval().result)

        // One reply a question, whatever was tapped after the first answer; nothing more was kept.
        assertEquals(turns.size, replies.size)
        assertEquals(listOf("git push origin main"), grants.allowed.value)
        assertEquals(listOf("recursive delete"), grants.session.value)
        assertTrue(turns.all { it.getJSONArray("blocked").length() == 0 })
        assertFalse(turns.any { it.getJSONArray("protected").length() > 0 })

        // The conversation cleared: its grants end with it, and the command allowed for good stays.
        grants.forgetSession()
        turn(question("ls"), "once")
        assertEquals(emptyList<String>(), strings(turns.last().getJSONArray("session_allowed")))
        assertEquals(listOf("git push origin main"), strings(turns.last().getJSONArray("allowed")))
    }

    @Test
    fun aClearedConversationComesBackFromTheTrashWithoutWhatWasAllowedForIt() {
        assertEquals("session", turn(question("rm -rf build"), "session").getString("decision"))
        assertEquals(listOf("recursive delete"), grants.session.value)
        val before = runBlocking { messages.all() }

        // 대화 지우기, as the settings ask the chat for it: the conversation goes to the trash, and what was allowed
        // for it is asked about again.
        val agent = ChatAgent(
            context = context,
            messages = messages,
            schedules = ScheduleStore(database),
            settings = graph.settings,
            llm = LlmService(),
            actions = actions,
            navigator = Navigator(),
            sites = { graph.sites },
            texts = graph.texts,
            hermes = chat,
        )
        val entry = runBlocking { agent.clear() }!!
        assertTrue(runBlocking { messages.all() }.isEmpty())
        assertTrue(grants.session.value.isEmpty())

        // Restored: the messages come back with what each turn did, the answer "session" among it, and what it
        // allowed does not: the next turn is handed none of it.
        assertEquals(1, runBlocking { TrashStore(database).restore(listOf(entry)) }.count)
        assertEquals(before, runBlocking { messages.all() })
        assertEquals("session", lastApproval().result)
        assertTrue(grants.session.value.isEmpty())
        turn(question("ls -la"), "once")
        assertEquals(emptyList<String>(), strings(turns.last().getJSONArray("session_allowed")))
    }

    /** The worker's end of the pipes: frames the session wrote, and frames to send it. */
    private class Pipes(private val reader: BufferedReader, private val output: OutputStream) {
        fun read(): JSONObject = JSONObject(reader.readLine() ?: error("The session closed the pipe"))

        fun write(frame: JSONObject) {
            output.write((frame.toString() + "\n").toByteArray(Charsets.UTF_8))
            output.flush()
        }
    }

    /** A process that is a thread of this test running [script] on its end of the pipes. */
    private class Worker(script: (Pipes) -> Unit) : Process() {
        private val toWorker = PipedOutputStream()
        private val workerInput = PipedInputStream(toWorker, 1 shl 16)
        private val fromWorker = PipedOutputStream()
        private val sessionInput = PipedInputStream(fromWorker, 1 shl 16)

        @Volatile
        private var alive = true
        private val worker = thread(isDaemon = true) {
            try {
                script(Pipes(workerInput.bufferedReader(Charsets.UTF_8), fromWorker))
            } catch (_: Exception) {
                // The session ended the process: the pipes are closed under the script.
            } finally {
                runCatching { fromWorker.close() }
                alive = false
            }
        }

        override fun getOutputStream(): OutputStream = toWorker

        override fun getInputStream(): InputStream = sessionInput

        override fun getErrorStream(): InputStream = ByteArrayInputStream(ByteArray(0))

        override fun waitFor(): Int {
            worker.join()
            return 0
        }

        override fun waitFor(timeout: Long, unit: TimeUnit): Boolean {
            worker.join(unit.toMillis(timeout))
            return !worker.isAlive
        }

        override fun exitValue(): Int = if (alive) throw IllegalThreadStateException() else 0

        override fun isAlive(): Boolean = alive

        override fun destroy() {
            alive = false
            runCatching { workerInput.close() }
            runCatching { fromWorker.close() }
        }

        override fun destroyForcibly(): Process {
            destroy()
            return this
        }
    }
}
