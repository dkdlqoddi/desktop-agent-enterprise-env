package dev.privateagent.android.llm

import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.Collections
import kotlin.concurrent.thread

/**
 * The client's own HTTP/1.1 exchange for an endpoint the user typed with http:// (PlainHttpPost), against a plain
 * server of the test's own on this machine: what it sends, the answers it reads (a length, chunks, or up to the end of
 * the connection; an interim answer first), the redirects it does not follow, the answers it refuses, and how the
 * deadline and the caller's stop end it.
 */
class PlainHttpTest {
    private lateinit var server: ServerSocket

    /** A request the server got: its request line, its header fields in order, and its body. */
    private class Request(val line: String, val fields: List<Pair<String, String>>, val body: String) {
        fun field(name: String) = fields.filter { it.first.equals(name, ignoreCase = true) }.map { it.second }
    }

    private val requests: MutableList<Request> = Collections.synchronizedList(mutableListOf())

    /** What the server answers a request with, as bytes; null keeps silent until the client goes. */
    @Volatile
    private var answer: (Request) -> ByteArray? = { ok("""{"ok":true}""") }

    private val base get() = "http://127.0.0.1:${server.localPort}"
    private val headers = mapOf(
        "Authorization" to "Bearer test-key",
        "Content-Type" to "application/json; charset=utf-8",
    )

    @Before
    fun start() {
        server = ServerSocket(0, 16, InetAddress.getLoopbackAddress())
        thread(isDaemon = true, name = "plain-http-test-server") { serve(server) }
    }

    @After
    fun stop() {
        server.close()
    }

    private fun serve(listening: ServerSocket) {
        while (true) {
            val socket = try {
                listening.accept()
            } catch (_: IOException) {
                return
            }
            thread(isDaemon = true) { socket.use { handle(it) } }
        }
    }

    private fun handle(socket: Socket) {
        try {
            val input = BufferedInputStream(socket.getInputStream())
            val line = line(input) ?: return
            val fields = mutableListOf<Pair<String, String>>()
            while (true) {
                val field = line(input) ?: return
                if (field.isEmpty()) break
                fields += field.substringBefore(':').trim() to field.substringAfter(':', "").trim()
            }
            val length = fields.firstOrNull { it.first.equals("Content-Length", ignoreCase = true) }
                ?.second?.toInt() ?: 0
            val body = ByteArray(length)
            var read = 0
            while (read < length) {
                val count = input.read(body, read, length - read)
                if (count < 0) break
                read += count
            }
            val request = Request(line, fields, String(body, 0, read, Charsets.UTF_8))
            requests += request
            val bytes = answer(request)
            if (bytes == null) {
                // Silent: hold the connection until the client closes it.
                while (input.read() >= 0) Unit
                return
            }
            socket.getOutputStream().apply {
                write(bytes)
                flush()
            }
        } catch (_: IOException) {
            // The client went away.
        }
    }

    private fun line(input: InputStream): String? {
        val bytes = ByteArrayOutputStream()
        while (true) {
            val byte = input.read()
            if (byte < 0) return if (bytes.size() == 0) null else bytes.toString(Charsets.UTF_8.name())
            if (byte == '\n'.code) break
            if (byte != '\r'.code) bytes.write(byte)
        }
        return bytes.toString(Charsets.UTF_8.name())
    }

    private fun ascii(text: String) = text.toByteArray(Charsets.US_ASCII)

    private fun ok(json: String): ByteArray {
        val body = json.toByteArray(Charsets.UTF_8)
        return ascii("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.size}\r\n\r\n") + body
    }

    private suspend fun post(path: String = "/v1/chat/completions", client: LlmClient = LlmClient()): JSONObject {
        val endpoint = (Endpoint.normalize("$base$path") as Endpoint.Check.Ok).url
        return client.post(endpoint, headers, JSONObject().put("model", "m"))
    }

    private suspend fun refused(path: String = "/v1/chat/completions"): LlmException {
        try {
            post(path)
        } catch (refused: LlmException) {
            return refused
        }
        throw AssertionError("The request went through")
    }

    @Test
    fun aPostGoesOutAsHttp11AndItsAnswerIsRead() = runBlocking {
        val answered = post("/v1/chat/completions?api-version=1")

        assertTrue(answered.getBoolean("ok"))
        val request = requests.single()
        assertEquals("POST /v1/chat/completions?api-version=1 HTTP/1.1", request.line)
        assertEquals(listOf("127.0.0.1:${server.localPort}"), request.field("Host"))
        assertEquals(listOf("Bearer test-key"), request.field("Authorization"))
        assertEquals(listOf("application/json; charset=utf-8"), request.field("Content-Type"))
        assertEquals(listOf("close"), request.field("Connection"))
        assertEquals(listOf(request.body.toByteArray(Charsets.UTF_8).size.toString()), request.field("Content-Length"))
        assertEquals("m", JSONObject(request.body).getString("model"))
        // Nothing asks for a compressed answer, which the exchange would not undo.
        assertEquals(emptyList<String>(), request.field("Accept-Encoding"))
    }

    @Test
    fun aChunkedAnswerIsRead() = runBlocking {
        answer = {
            ascii(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n" +
                    "7;note=first\r\n{\"ok\":\r\n" + "5\r\ntrue}\r\n" + "0\r\nX-Trailer: done\r\n\r\n",
            )
        }
        assertTrue(post().getBoolean("ok"))
    }

    @Test
    fun anAnswerWithoutALengthIsReadToTheEndOfTheConnection() = runBlocking {
        answer = { ascii("HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{\"ok\":true}") }
        assertTrue(post().getBoolean("ok"))
    }

    @Test
    fun anInterimAnswerIsPassedOver() = runBlocking {
        answer = { ascii("HTTP/1.1 100 Continue\r\n\r\n") + ok("""{"ok":true}""") }
        assertTrue(post().getBoolean("ok"))
    }

    @Test
    fun aRedirectIsNeverFollowed() = runBlocking {
        val statuses = listOf(301, 302, 303, 307, 308)
        for (status in statuses) {
            answer = { request ->
                if (request.line.startsWith("POST /v1/moved")) {
                    ascii("HTTP/1.1 $status Moved\r\nLocation: $base/v1/elsewhere\r\nContent-Length: 0\r\n\r\n")
                } else {
                    ok("""{"elsewhere":true}""")
                }
            }
            val refused = refused("/v1/moved")
            assertEquals(LlmException.Kind.REDIRECT, refused.kind)
            assertEquals(status, refused.status)
        }
        assertEquals(statuses.map { "POST /v1/moved HTTP/1.1" }, requests.map { it.line })
    }

    @Test
    fun anErrorStatusEndsTheRequest() = runBlocking {
        answer = { ascii("HTTP/1.1 401 Unauthorized\r\nContent-Length: 12\r\n\r\n{\"error\":1}\n") }
        assertEquals(LlmException.Kind.AUTH, refused().kind)
        answer = { ascii("HTTP/1.1 503 Busy\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nno\r\n0\r\n\r\n") }
        assertEquals(LlmException.Kind.SERVER, refused().kind)
    }

    @Test
    fun aBrokenAnswerIsANetworkError() = runBlocking {
        val broken = listOf(
            "not http\r\n\r\n",
            "HTTP/1.1 200 OK\r\nX-Long: ${"a".repeat(70_000)}\r\n\r\n{}",
            "HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n{}\r\n0\r\n\r\n",
            "HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{\"ok\":true}",
            "HTTP/1.1 200 OK\r\nContent-Length: -1\r\n\r\n{}",
            "HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip\r\n\r\n{}",
            // Interim answers without end before the final one.
            "HTTP/1.1 100 Continue\r\n\r\n".repeat(20) + "HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}",
            "",
        )
        for (text in broken) {
            answer = { ascii(text) }
            assertEquals(text.take(40), LlmException.Kind.NETWORK, refused().kind)
        }
    }

    @Test
    fun anAnswerTooLargeIsRefused() = runBlocking {
        val size = 5 * 1024 * 1024
        answer = { ascii("HTTP/1.1 200 OK\r\nContent-Length: $size\r\n\r\n") + ByteArray(size) { ' '.code.toByte() } }
        assertEquals(LlmException.Kind.FORMAT, refused().kind)
    }

    @Test
    fun theDeadlineEndsTheExchangeWithASilentServer() = runBlocking {
        answer = { null }
        val started = System.currentTimeMillis()
        try {
            post(client = LlmClient(totalTimeoutMs = 1_000))
            fail("A silent server answered")
        } catch (refused: LlmException) {
            assertEquals(LlmException.Kind.TIMEOUT, refused.kind)
        }
        assertTrue(System.currentTimeMillis() - started < 10_000)
    }

    @Test
    fun theCallersStopEndsTheExchangeAtOnce() = runBlocking {
        answer = { null }
        val request = launch {
            try {
                post()
            } catch (_: LlmException) {
                // The stop closed the connection under the request.
            }
        }
        while (requests.isEmpty()) delay(20)
        val stopped = System.currentTimeMillis()
        withTimeout(5_000) { request.cancelAndJoin() }
        assertTrue(System.currentTimeMillis() - stopped < 5_000)
    }

    @Test
    fun aRefusedConnectionIsANetworkError() = runBlocking {
        val closed = ServerSocket(0, 1, InetAddress.getLoopbackAddress()).use { it.localPort }
        try {
            LlmClient().post("http://127.0.0.1:$closed/v1", headers, JSONObject())
            fail("A closed port answered")
        } catch (refused: LlmException) {
            assertEquals(LlmException.Kind.NETWORK, refused.kind)
        }
    }

    @Test
    fun aHeaderThatWouldBreakTheRequestIsNeverSent() = runBlocking {
        val endpoint = "$base/v1/chat/completions"
        for (key in listOf("test-key\r\nX-Injected: 1", "test-key\n", "키")) {
            try {
                LlmClient().post(endpoint, mapOf("Authorization" to "Bearer $key"), JSONObject())
                fail("A broken header was sent")
            } catch (refused: IllegalArgumentException) {
                // The message never repeats the value, which may be the key.
                assertTrue(refused.message.orEmpty(), !refused.message.orEmpty().contains("test-key"))
            }
        }
        delay(300)
        assertTrue(requests.isEmpty())
    }

    @Test
    fun anIpv6LoopbackAddressIsReachedWithItsBrackets() = runBlocking {
        val v6 = try {
            ServerSocket(0, 16, InetAddress.getByName("::1"))
        } catch (_: IOException) {
            null
        }
        assumeTrue("This machine has no IPv6 loopback", v6 != null)
        v6!!.use {
            thread(isDaemon = true) { serve(it) }
            val answered = LlmClient().post("http://[::1]:${it.localPort}/v1", headers, JSONObject())
            assertTrue(answered.getBoolean("ok"))
            assertEquals(listOf("[::1]:${it.localPort}"), requests.single().field("Host"))
        }
    }
}
