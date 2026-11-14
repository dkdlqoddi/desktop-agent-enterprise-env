package dev.privateagent.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.agent.AppActions
import dev.privateagent.android.agent.HermesChat
import dev.privateagent.android.data.AppDatabase
import dev.privateagent.android.data.MessageRole
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.hermes.HermesRuntime
import dev.privateagent.android.hermes.HermesSession
import dev.privateagent.android.hermes.LlmHermesModel
import dev.privateagent.android.llm.ApiFormat
import dev.privateagent.android.llm.ChatTurn
import dev.privateagent.android.llm.Endpoint
import dev.privateagent.android.llm.LlmClient
import dev.privateagent.android.llm.LlmException
import dev.privateagent.android.llm.LlmService
import dev.privateagent.android.security.LlmConnection
import dev.privateagent.android.security.SecretBox
import dev.privateagent.android.security.SettingsStore
import dev.privateagent.android.ui.Navigator
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URL
import java.security.KeyStore
import java.util.Collections
import java.util.UUID
import kotlin.concurrent.thread

/**
 * The LLM connection to a plain HTTP server, as to a local LLM on the same Wi-Fi: an address the user typed with
 * http:// serves the connection check, a chat reply and a Hermes turn, over the client's own socket on the device,
 * while the platform's HTTP stack still refuses cleartext to the same server (the app's network security
 * configuration, which keeps the browser and everything else HTTPS-only). A key entered for an https server never
 * goes to the http one on the same host, and a redirect of the http server is not followed. The server is the test's
 * own, on the device's loopback address.
 */
@RunWith(AndroidJUnit4::class)
class LlmPlainHttpTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private lateinit var server: ServerSocket

    /** What the server got, in order: one entry per request. */
    private class Request(val line: String, val authorization: String?, val body: String)

    private val requests: MutableList<Request> = Collections.synchronizedList(mutableListOf())

    private val base get() = "http://127.0.0.1:${server.localPort}"

    /** The address as the settings save it when the user types it with http://. */
    private fun typed(path: String = "/v1/chat/completions"): String {
        val url = (Endpoint.normalize("$base$path") as Endpoint.Check.Ok).url
        assertTrue(url, url.startsWith("http://"))
        return url
    }

    @Before
    fun start() {
        server = ServerSocket(0, 16, InetAddress.getByName("127.0.0.1"))
        thread(isDaemon = true, name = "llm-plain-http-test-server") { serve() }
    }

    @After
    fun stop() {
        server.close()
    }

    /** One request per connection: /v1/chat/completions answers, /v1/moved-<status> redirects to /v1/elsewhere. */
    private fun serve() {
        while (true) {
            val socket = try {
                server.accept()
            } catch (_: IOException) {
                return
            }
            thread(isDaemon = true) { socket.use { answer(it) } }
        }
    }

    private fun answer(socket: Socket) {
        try {
            val input = BufferedInputStream(socket.getInputStream())
            val line = line(input)
            var length = 0
            var authorization: String? = null
            while (true) {
                val header = line(input)
                if (header.isEmpty()) break
                val name = header.substringBefore(':').trim()
                val value = header.substringAfter(':', "").trim()
                if (name.equals("Content-Length", ignoreCase = true)) length = value.toInt()
                if (name.equals("Authorization", ignoreCase = true)) authorization = value
            }
            val body = ByteArray(length)
            var read = 0
            while (read < length) {
                val count = input.read(body, read, length - read)
                if (count < 0) break
                read += count
            }
            requests += Request(line.substringBeforeLast(' '), authorization, String(body, 0, read, Charsets.UTF_8))
            val path = line.split(' ').getOrElse(1) { "" }
            val moved = Regex("/v1/moved-(\\d{3})").matchEntire(path)?.groupValues?.get(1)
            val response = when {
                moved != null -> "HTTP/1.1 $moved Moved\r\nLocation: $base/v1/elsewhere\r\nContent-Length: 0\r\n" +
                    "Connection: close\r\n\r\n"
                path == "/v1/chat/completions" -> {
                    val completion = JSONObject()
                        .put("id", "chatcmpl-plain").put("object", "chat.completion").put("created", 0)
                        .put("model", "test-model")
                        .put(
                            "choices",
                            JSONArray().put(
                                JSONObject().put("index", 0).put("finish_reason", "stop")
                                    .put("message", JSONObject().put("role", "assistant").put("content", ANSWER)),
                            ),
                        )
                        .put(
                            "usage",
                            JSONObject().put("prompt_tokens", 1).put("completion_tokens", 1).put("total_tokens", 2),
                        )
                        .toString().toByteArray(Charsets.UTF_8)
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json; charset=utf-8\r\n" +
                        "Content-Length: ${completion.size}\r\nConnection: close\r\n\r\n" +
                        String(completion, Charsets.UTF_8)
                }
                else -> "HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
            }
            socket.getOutputStream().apply {
                write(response.toByteArray(Charsets.UTF_8))
                flush()
            }
        } catch (_: IOException) {
            // The client went away.
        }
    }

    private fun line(input: InputStream): String {
        val bytes = ByteArrayOutputStream()
        while (true) {
            val byte = input.read()
            if (byte < 0 || byte == '\n'.code) break
            if (byte != '\r'.code) bytes.write(byte)
        }
        return bytes.toString(Charsets.UTF_8.name())
    }

    @Test
    fun theConnectionCheckAndAReplyComeOverPlainHttp() = runBlocking {
        val connection = LlmConnection(ApiFormat.OPENAI, typed(), "test-model", "sk-test-plain")
        val llm = LlmService(LlmClient())
        llm.ping(connection)
        val reply = llm.complete(connection, "You are a test.", listOf(ChatTurn(ChatTurn.Role.USER, "안녕")), emptyList())

        assertEquals(ANSWER, reply.text)
        assertEquals(listOf("POST /v1/chat/completions", "POST /v1/chat/completions"), requests.map { it.line })
        assertTrue(requests.all { it.authorization == "Bearer sk-test-plain" })
        assertTrue(requests.all { JSONObject(it.body).getString("model") == "test-model" })
    }

    @Test
    fun aKeyEnteredForHttpsNeverGoesToTheHttpServer() = runBlocking {
        val file = "settings_test_" + UUID.randomUUID().toString().replace("-", "")
        val alias = "settings_test_" + UUID.randomUUID()
        val store = SettingsStore(context, SecretBox(alias), file)
        val https = "https://127.0.0.1:${server.localPort}/v1/chat/completions"
        try {
            // The same host and port over http is another server: the key entered for https goes, and none is sent.
            store.saveLlm(ApiFormat.OPENAI, https, "test-model", "sk-https-only")
            assertTrue(store.saveLlm(ApiFormat.OPENAI, typed(), "test-model", null).keyCleared)
            LlmService(LlmClient()).ping(store.connection()!!)
            assertNull(requests.single().authorization)

            // A key entered for the http server goes there, and does not follow the address back to https.
            store.saveLlm(ApiFormat.OPENAI, typed(), "test-model", "sk-plain")
            LlmService(LlmClient()).ping(store.connection()!!)
            assertEquals("Bearer sk-plain", requests.last().authorization)
            assertTrue(store.saveLlm(ApiFormat.OPENAI, https, "test-model", null).keyCleared)
            assertNull(store.connection()!!.apiKey)
        } finally {
            context.deleteSharedPreferences(file)
            KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(alias)
        }
    }

    @Test
    fun aRedirectOfTheHttpServerIsNotFollowed() = runBlocking {
        val statuses = listOf(301, 302, 303, 307, 308)
        val headers = mapOf("Authorization" to "Bearer sk-test-plain", "Content-Type" to "application/json")
        for (status in statuses) {
            try {
                LlmClient().post(typed("/v1/moved-$status"), headers, JSONObject().put("model", "test-model"))
                fail("A $status was followed")
            } catch (refused: LlmException) {
                assertEquals(LlmException.Kind.REDIRECT, refused.kind)
                assertEquals(status, refused.status)
            }
        }
        // Each request reached the address it was sent to, and nothing went on to where the redirect pointed.
        assertEquals(statuses.map { "POST /v1/moved-$it" }, requests.map { it.line })
    }

    @Test
    fun thePlatformsHttpStackStillRefusesCleartext() {
        val connection = URL("$base/v1/chat/completions").openConnection() as HttpURLConnection
        try {
            connection.responseCode
            fail("The platform's stack sent a request in cleartext")
        } catch (refused: IOException) {
            // "Cleartext HTTP traffic to <host> not permitted" (UnknownServiceException on older versions of Android).
            assertTrue(refused.toString(), refused.message.orEmpty().contains("cleartext", ignoreCase = true))
        } finally {
            connection.disconnect()
        }
        Thread.sleep(500)
        assertTrue(requests.isEmpty())
    }

    /** The Hermes chat's model calls come back to the app and go out through the LLM connection: here, over http. */
    @Test
    fun aHermesTurnAsksTheModelOverPlainHttp() = runBlocking {
        val runtime = HermesRuntime(context)
        assumeTrue("This APK has no Hermes runtime (android/hermes/build_runtime.py)", runtime.isBundled)
        val graph = (context.applicationContext as PrivateAgentApp).graph
        val database = AppDatabase(context, name = null)
        val session = HermesSession(runtime)
        try {
            val messages = MessageStore(database)
            val actions = AppActions(
                context, RecordStore(database), graph.browser, graph.scheduler, graph.notifications, Navigator(),
                { graph.sites }, graph.texts,
            )
            val chat = HermesChat(context, messages, actions, Navigator(), { graph.sites }, graph.texts, session)
            val connection = LlmConnection(ApiFormat.OPENAI, typed(), "test-model", "sk-test-plain")
            val model = LlmHermesModel(LlmClient(), connection)
            withTimeout(300_000) { chat.converse(model, "test-model", "안녕", emptyList()) }

            assertEquals(ANSWER, messages.all().last { it.role == MessageRole.ASSISTANT }.text)
            assertTrue(requests.isNotEmpty())
            assertTrue(requests.all { it.line == "POST /v1/chat/completions" })
            assertTrue(requests.all { it.authorization == "Bearer sk-test-plain" })
            // The turn's own request, with Hermes' tools, went to the address the user typed.
            assertTrue(requests.any { JSONObject(it.body).optJSONArray("tools") != null })
        } finally {
            session.shutdown()
            database.close()
        }
    }

    private companion object {
        const val ANSWER = "평문 서버의 답입니다."
    }
}
