package dev.privateagent.android.hermes

import org.json.JSONException
import org.json.JSONObject
import java.io.BufferedWriter
import java.io.Closeable
import java.io.IOException
import java.io.InputStream
import java.io.OutputStreamWriter
import java.io.Reader
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread

/**
 * A running Hermes Python process ([HermesRuntime.start]). Frames are JSON objects, one per line, on its stdin and
 * stdout (the worker protocol of runtime/hermes/worker.py). Its stderr is kept only as a short tail for diagnostics:
 * it is never logged, because it may contain conversation text.
 *
 * A chat turn takes the frames from its turn frame to its result ([startTurn], [endTurn]); a frame that arrives while
 * no turn takes them is dropped.
 */
class HermesProcess internal constructor(private val process: Process) : Closeable {
    private val writer = BufferedWriter(OutputStreamWriter(process.outputStream, Charsets.UTF_8), 1 shl 16)
    private val frames = LinkedBlockingQueue<Any>()
    private val stderr = StringBuilder()

    @Volatile
    private var stopped = false

    /** Whether frames are queued: until the first [endTurn], and from each [startTurn] to its [endTurn]. */
    private var taking = true
    private val gate = Any()
    private val discarded = AtomicInteger()

    init {
        thread(name = "hermes-stdout", isDaemon = true) { readFrames() }
        thread(name = "hermes-stderr", isDaemon = true) { drain(process.errorStream) }
    }

    val isAlive: Boolean get() = process.isAlive

    /** Writes one frame. Throws [IOException] once the process has ended. */
    @Throws(IOException::class)
    fun send(frame: JSONObject) {
        val line = frame.toString()
        if (line.length > MAX_FRAME) throw IOException("Frame too large")
        synchronized(writer) {
            writer.write(line)
            writer.write("\n")
            writer.flush()
        }
    }

    /**
     * The next frame, or null when [timeoutMillis] passes first. Throws [HermesProcessEndedException] when the
     * process has ended (or sent something that is not a frame) and every earlier frame has been received.
     */
    @Throws(HermesProcessEndedException::class, InterruptedException::class)
    fun receive(timeoutMillis: Long): JSONObject? {
        val item = frames.poll(timeoutMillis, TimeUnit.MILLISECONDS) ?: return null
        if (item === END) {
            frames.put(END)
            throw HermesProcessEndedException(exitCode())
        }
        return item as JSONObject
    }

    /**
     * Starts taking the frames of a new turn, before its turn frame goes out. Anything still queued came after the
     * earlier turn's last frame was taken: it is dropped.
     */
    fun startTurn() = synchronized(gate) {
        dropQueued()
        taking = true
    }

    /**
     * Stops taking frames until the next [startTurn], once a turn has its result (or has failed). The worker sends
     * nothing of its own accord: a frame that comes between turns was sent by a thread of an earlier turn that ran on
     * past its result. It is dropped, the queued ones now and later ones as they arrive, so it is never read as a
     * request or the answer of the next turn: an app action or a model call it asks for is not made, and a result it
     * carries does not end the next turn. A request dropped this way leaves only the thread that sent it waiting,
     * until a stop cancels it or the process ends.
     */
    fun endTurn() = synchronized(gate) {
        taking = false
        dropQueued()
    }

    /** How many frames came between turns and were dropped. */
    internal val discardedFrames: Int get() = discarded.get()

    /** Kills the process at once (a stopped turn does not wait for Python to finish anything). */
    fun stop() {
        stopped = true
        process.destroyForcibly()
        runCatching { process.outputStream.close() }
    }

    /** The exit code once the process has ended within [timeoutMillis], or null. */
    fun waitFor(timeoutMillis: Long): Int? =
        if (process.waitFor(timeoutMillis, TimeUnit.MILLISECONDS)) process.exitValue() else null

    /** The last few kilobytes the process wrote to stderr: for tests and diagnostics, never for logs or the screen. */
    fun stderrTail(): String = synchronized(stderr) { stderr.toString() }

    override fun close() = stop()

    private fun exitCode(): Int? = if (process.isAlive) null else process.exitValue()

    /** Drops the queued frames; the end of the stream stays queued, so the next receive still learns of it. */
    private fun dropQueued() {
        var ended = false
        while (true) {
            val item = frames.poll() ?: break
            if (item === END) ended = true else discarded.incrementAndGet()
        }
        if (ended) frames.put(END)
    }

    private fun readFrames() {
        try {
            val reader = FrameReader(process.inputStream.reader(Charsets.UTF_8), MAX_FRAME)
            while (true) {
                val line = reader.next() ?: break
                val frame = JSONObject(line)
                synchronized(gate) {
                    if (taking) frames.put(frame) else discarded.incrementAndGet()
                }
            }
        } catch (_: IOException) {
            // The pipe closed (the process ended or was stopped).
        } catch (_: JSONException) {
            // Not the protocol: end it, as the desktop backend does.
            if (!stopped) process.destroyForcibly()
        } catch (_: FrameReader.TooLargeException) {
            if (!stopped) process.destroyForcibly()
        } finally {
            frames.put(END)
        }
    }

    private fun drain(stream: InputStream) {
        try {
            val reader = stream.reader(Charsets.UTF_8)
            val buffer = CharArray(4096)
            while (true) {
                val count = reader.read(buffer)
                if (count < 0) break
                synchronized(stderr) {
                    stderr.appendRange(buffer, 0, count)
                    if (stderr.length > STDERR_TAIL) stderr.delete(0, stderr.length - STDERR_TAIL)
                }
            }
        } catch (_: IOException) {
        }
    }

    companion object {
        /** The worker's own frame limit (runtime/hermes/worker.py MAX_FRAME), in characters. */
        const val MAX_FRAME = 2_000_000
        private const val STDERR_TAIL = 16_384
        private val END = Any()
    }
}

/** The process ended; [exitCode] is null when it was still exiting. */
class HermesProcessEndedException(val exitCode: Int?) : Exception("Hermes process ended ($exitCode)")

/** Lines of at most [limit] characters from [reader], without their line break. */
class FrameReader(private val reader: Reader, private val limit: Int) {
    class TooLargeException : Exception("Frame too large")

    private val buffer = CharArray(1 shl 16)
    private var start = 0
    private var end = 0
    private val line = StringBuilder()

    /** The next line, or null at the end of the stream (an unterminated last line is dropped: it is incomplete). */
    @Throws(IOException::class, TooLargeException::class)
    fun next(): String? {
        line.setLength(0)
        while (true) {
            if (start == end) {
                val count = reader.read(buffer)
                if (count < 0) return null
                start = 0
                end = count
            }
            var stop = start
            while (stop < end && buffer[stop] != '\n') stop++
            if (line.length + (stop - start) > limit) throw TooLargeException()
            line.appendRange(buffer, start, stop)
            if (stop < end) {
                start = stop + 1
                return line.toString()
            }
            start = end
        }
    }
}
