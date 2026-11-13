package dev.privateagent.android.hermes

import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
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
 * The session's side of the worker protocol, against a worker of the test's own: what a turn runs unasked and what
 * is closed to it go out with the turn, an approval is answered with the decision it was given, and the kinds of
 * command a "this conversation" answer covers reach the handler.
 */
class HermesSessionTest {
    /** The worker's end of the pipes: frames the session wrote, and frames to send it. */
    private class Pipes(private val reader: BufferedReader, private val output: OutputStream) {
        fun read(): JSONObject = JSONObject(reader.readLine() ?: error("The session closed the pipe"))

        fun write(frame: JSONObject) {
            output.write((frame.toString() + "\n").toByteArray(Charsets.UTF_8))
            output.flush()
        }
    }

    /** A process that is a thread of this test running [script] on its end of the pipes. */
    private class ScriptedProcess(script: (Pipes) -> Unit) : Process() {
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

    private var session: HermesSession? = null

    @After
    fun close() {
        session?.shutdown()
    }

    private fun session(script: (Pipes) -> Unit): HermesSession =
        HermesSession(File("."), { HermesProcess(ScriptedProcess(script)) }, { System.nanoTime() / 1_000_000 })
            .also { session = it }

    private fun turn(limits: HermesSession.Limits = HermesSession.Limits()) = HermesSession.Turn(
        system = "system",
        context = "context",
        model = "model",
        toolsets = listOf("memory"),
        appTools = JSONArray(),
        settings = JSONObject(),
        budgetSeconds = 30,
        limits = limits,
    )

    private open class Answers(private val answer: JSONObject?) : HermesSession.Handler {
        val asked: MutableList<JSONObject> = Collections.synchronizedList(mutableListOf())
        val grants: MutableList<List<String>> = Collections.synchronizedList(mutableListOf())

        override suspend fun complete(request: JSONObject): JSONObject = JSONObject()

        override suspend fun appTool(name: String, args: JSONObject) = HermesSession.AppToolResult("done", JSONObject())

        override suspend fun ask(kind: HermesSession.InteractionKind, payload: JSONObject): JSONObject? {
            asked += payload
            return answer
        }

        override fun granted(keys: List<String>) {
            grants += keys
        }

        override fun progress(event: String, tool: String, preview: String) = Unit

        override fun draft(text: String) = Unit
    }

    private fun strings(array: JSONArray) = (0 until array.length()).map { array.getString(it) }

    @Test
    fun theTurnFrameSaysWhatRunsUnaskedAndWhatIsClosed() {
        val limits = HermesSession.Limits(
            allowed = listOf("git push origin main"),
            sessionAllowed = listOf("recursive delete"),
            protectedPaths = listOf("/data/user/0/app/databases", "/data/user/0/app/shared_prefs"),
        )
        val frame = HermesSession.turnFrame(turn(limits))
        assertEquals("turn", frame.getString("type"))
        assertEquals(limits.allowed, strings(frame.getJSONArray("allowed")))
        assertEquals(limits.sessionAllowed, strings(frame.getJSONArray("session_allowed")))
        assertEquals(limits.protectedPaths, strings(frame.getJSONArray("protected")))
        // A phone's agent has no terminal: there are no command patterns to refuse.
        assertEquals(0, frame.getJSONArray("blocked").length())
        // A turn that names none hands over empty lists, so nothing a turn before left counts.
        val plain = HermesSession.turnFrame(turn())
        for (name in listOf("allowed", "session_allowed", "protected", "blocked")) {
            assertEquals(name, 0, plain.getJSONArray(name).length())
        }
    }

    @Test
    fun anApprovalIsAnsweredWithTheDecisionAndAGrantReachesTheHandler() = runBlocking {
        val seen = Collections.synchronizedList(mutableListOf<JSONObject>())
        val handler = Answers(JSONObject().put("decision", "session"))
        val running = session { pipes ->
            seen += pipes.read()
            pipes.write(
                JSONObject().put("type", "approval").put("id", "a1").put("command", "rm -rf build")
                    .put("description", "recursive delete").put("smart_denied", false).put("allow_session", true)
                    .put("allow_permanent", true),
            )
            seen += pipes.read()
            pipes.write(JSONObject().put("type", "grant").put("keys", JSONArray(listOf("recursive delete", 7))))
            pipes.write(JSONObject().put("type", "result").put("message", "지웠습니다."))
        }
        val answer = withTimeout(20_000) { running.run(turn(HermesSession.Limits(allowed = listOf("ls"))), handler) }
        assertEquals("지웠습니다.", answer)
        assertEquals(listOf("ls"), strings(seen[0].getJSONArray("allowed")))
        // The question reaches the screen without its routing fields, with the scopes it offers.
        val asked = handler.asked.single()
        assertFalse(asked.has("type") || asked.has("id"))
        assertEquals("rm -rf build", asked.getString("command"))
        assertTrue(asked.getBoolean("allow_session") && asked.getBoolean("allow_permanent"))
        assertEquals("approval_reply", seen[1].getString("type"))
        assertEquals("a1", seen[1].getString("id"))
        assertEquals("session", seen[1].getString("decision"))
        // Only the names of kinds: anything else in the frame is left out.
        assertEquals(listOf(listOf("recursive delete")), handler.grants.toList())
    }

    @Test
    fun anApprovalNobodyAnsweredAndAnAnswerThatIsNoDecisionRefuseTheCommand() = runBlocking {
        for (answer in listOf(null, JSONObject().put("decision", "yes"), JSONObject())) {
            val seen = Collections.synchronizedList(mutableListOf<JSONObject>())
            val running = session { pipes ->
                pipes.read()
                pipes.write(JSONObject().put("type", "approval").put("id", "a2").put("command", "rm -rf /"))
                seen += pipes.read()
                pipes.write(JSONObject().put("type", "result").put("message", "하지 않았습니다."))
            }
            assertEquals("하지 않았습니다.", withTimeout(20_000) { running.run(turn(), Answers(answer)) })
            assertEquals("deny", seen.single().getString("decision"))
            running.shutdown()
        }
    }
}
