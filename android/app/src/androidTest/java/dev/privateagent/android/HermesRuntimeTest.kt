package dev.privateagent.android

import android.os.Bundle
import android.os.SystemClock
import android.util.Log
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.hermes.HermesProcess
import dev.privateagent.android.hermes.HermesProcessEndedException
import dev.privateagent.android.hermes.HermesRuntime
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * The Hermes runtime of the APK on a device: extraction, a separate Python process with JSON lines on its pipes, one
 * Hermes turn against canned chat completions (android/hermes/smoke/hermes_smoke.py), and stopping a busy process.
 * Skipped when the APK was built without android/hermes/build_runtime.py's output.
 */
@RunWith(AndroidJUnit4::class)
class HermesRuntimeTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val runtime = HermesRuntime(context)

    @Before
    fun bundled() {
        assumeTrue("This APK has no Hermes runtime (android/hermes/build_runtime.py)", runtime.isBundled)
    }

    @Test
    fun runsOneHermesTurnWithCannedCompletions() {
        // The app extracts the runtime at start; wait for that before measuring an extraction of our own.
        runtime.prepare()
        runtime.removeExtracted()
        var started = SystemClock.elapsedRealtime()
        val installed = runtime.prepare()
        val extractMs = SystemClock.elapsedRealtime() - started
        started = SystemClock.elapsedRealtime()
        runtime.prepare()
        val reuseMs = SystemClock.elapsedRealtime() - started
        assertTrue(installed.isWorkerIntact())
        assertEquals(installed.abi, installed.manifest.getString("abi"))
        assertEquals(installed.index.hermesCommit, installed.manifest.getString("commit"))

        started = SystemClock.elapsedRealtime()
        start(installed.root).use { process ->
            val ready = expect(process, "ready", 60_000)
            val firstFrameMs = SystemClock.elapsedRealtime() - started
            assertEquals("android", ready.getString("platform"))
            assertEquals(installed.index.python, ready.getString("python"))
            assertEquals(installed.executable.path, ready.getString("executable"))
            assertEquals(1, ready.getJSONObject("flags").getInt("isolated"))
            assertEquals(1, ready.getJSONObject("flags").getInt("dont_write_bytecode"))

            started = SystemClock.elapsedRealtime()
            process.send(JSONObject().put("type", "run"))
            val result = expect(process, "result", 300_000)
            val runMs = SystemClock.elapsedRealtime() - started
            assertTrue(result.toString(), result.getBoolean("completed"))
            assertEquals("smoke ok: 안녕하세요, Hermes", result.getString("final"))
            assertEquals("[\"smoke_echo\"]", result.getJSONArray("tools").toString())
            assertEquals("[\"안녕하세요, Hermes\"]", result.getJSONArray("echoed").toString())
            val calls = result.getJSONArray("calls")
            assertEquals(2, calls.length())
            assertEquals("[\"smoke_echo\"]", calls.getJSONObject(0).getJSONArray("tools").toString())
            val natives = result.getJSONObject("natives")
            for (name in natives.keys()) {
                val module = natives.getJSONObject(name)
                assertTrue("$name: ${module.getString("value")}", module.getBoolean("ok"))
            }
            assertTrue(natives.getJSONObject("pillow").getString("value").contains("True, True"))

            process.send(JSONObject().put("type", "shutdown"))
            assertEquals(0, process.waitFor(10_000))

            report(
                JSONObject()
                    .put("abi", installed.abi)
                    .put("extract_ms", extractMs)
                    .put("prepare_again_ms", reuseMs)
                    .put("first_frame_ms", firstFrameMs)
                    .put("python_startup_ms", ready.getLong("startup_ms"))
                    .put("run_ms", runMs)
                    .put("timings", result.getJSONObject("timings"))
                    .put("memory_kb_ready", ready.getJSONObject("memory_kb"))
                    .put("memory_kb_after_turn", result.getJSONObject("memory_kb"))
                    .put("imported", result.getJSONObject("imported"))
                    .put("runtime_mb", installed.root.walkTopDown().filter { it.isFile }.sumOf { it.length() } / 1e6),
            )
        }
    }

    @Test
    fun carriesLargeUnicodeFramesBothWays() {
        start(runtime.prepare().root).use { process ->
            expect(process, "ready", 60_000)
            val text = "가나다라마바사 ✓ ".repeat(50_000)
            process.send(JSONObject().put("type", "echo").put("text", text))
            assertEquals(text, expect(process, "echo", 30_000).getString("text"))
            process.send(JSONObject().put("type", "shutdown"))
            assertEquals(0, process.waitFor(10_000))
        }
    }

    @Test
    fun stopKillsABusyProcessAtOnce() {
        val process = start(runtime.prepare().root)
        expect(process, "ready", 60_000)
        process.send(JSONObject().put("type", "hang"))
        val started = SystemClock.elapsedRealtime()
        process.stop()
        assertNotNull(process.waitFor(2_000))
        assertFalse(process.isAlive)
        try {
            process.receive(2_000)
            fail("A stopped process has no more frames")
        } catch (_: HermesProcessEndedException) {
        }
        report(JSONObject().put("stop_ms", SystemClock.elapsedRealtime() - started))
    }

    private fun start(root: File): HermesProcess {
        val script = File(context.cacheDir, "hermes-smoke/hermes_smoke.py")
        script.parentFile!!.mkdirs()
        instrumentation.context.assets.open("hermes_smoke.py").use { input ->
            script.outputStream().use { input.copyTo(it) }
        }
        return runtime.start(script, listOf(root.path))
    }

    private fun expect(process: HermesProcess, type: String, timeoutMillis: Long): JSONObject {
        val frame = try {
            process.receive(timeoutMillis)
        } catch (error: HermesProcessEndedException) {
            fail("Python ended (${error.exitCode}) before \"$type\": ${process.stderrTail().takeLast(3000)}")
            null
        }
        if (frame == null) fail("No \"$type\" frame in $timeoutMillis ms: ${process.stderrTail().takeLast(3000)}")
        assertEquals(frame.toString().take(500), type, frame!!.getString("type"))
        return frame
    }

    /** Measurements go to the instrumentation status and to logcat (tag HermesRuntimeTest). */
    private fun report(metrics: JSONObject) {
        Log.i("HermesRuntimeTest", metrics.toString())
        instrumentation.sendStatus(0, Bundle().apply { putString("hermes", metrics.toString()) })
    }
}
