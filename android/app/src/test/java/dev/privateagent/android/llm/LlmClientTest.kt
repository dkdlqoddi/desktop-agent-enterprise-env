package dev.privateagent.android.llm

import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.BufferedInputStream
import java.io.File
import java.io.IOException
import java.io.InputStream
import java.net.InetAddress
import java.net.Socket
import java.nio.file.Files
import java.security.KeyStore
import java.util.Collections
import java.util.concurrent.TimeUnit
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.KeyManagerFactory
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import kotlin.concurrent.thread

/**
 * The client against an HTTPS server of the test's own, on this machine: it posts and reads an answer, a redirect
 * (which would carry the API Key to another address) ends the request with its status, and an address of a scheme
 * other than https and http is never opened (plain http: PlainHttpTest). The server's certificate is made for the
 * test and trusted only by this JVM for its duration; the client's own checks stay on. The JVM's stack never follows a
 * POST it streams, so whether the client turns following off is checked on the device's stack (LlmRedirectTest).
 */
class LlmClientTest {
    private lateinit var folder: File
    private lateinit var server: SSLServerSocket
    private lateinit var trustedBefore: SSLSocketFactory

    /** The paths the server was asked for, and the Authorization header of each request, in order. */
    private val hits: MutableList<String> = Collections.synchronizedList(mutableListOf())
    private val keys: MutableList<String?> = Collections.synchronizedList(mutableListOf())

    private val base get() = "https://127.0.0.1:${server.localPort}"
    private val headers = mapOf("Authorization" to "Bearer test-key", "Content-Type" to "application/json")

    @Before
    fun start() {
        folder = Files.createTempDirectory("llm-client-test").toFile()
        val store = File(folder, "server.p12")
        val keytool = File(System.getProperty("java.home"), "bin/keytool").path
        val made = ProcessBuilder(
            keytool, "-genkeypair", "-alias", "server", "-keyalg", "EC", "-groupname", "secp256r1",
            "-dname", "CN=localhost", "-ext", "SAN=ip:127.0.0.1,dns:localhost", "-validity", "2",
            "-keystore", store.path, "-storetype", "PKCS12", "-storepass", PASSWORD, "-keypass", PASSWORD,
        ).redirectErrorStream(true).start()
        made.inputStream.readBytes()
        assertTrue(made.waitFor(60, TimeUnit.SECONDS) && made.exitValue() == 0)
        val keyStore = KeyStore.getInstance("PKCS12")
        store.inputStream().use { keyStore.load(it, PASSWORD.toCharArray()) }

        val keyManagers = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm())
        keyManagers.init(keyStore, PASSWORD.toCharArray())
        val serverContext = SSLContext.getInstance("TLS").apply { init(keyManagers.keyManagers, null, null) }
        val sockets = serverContext.serverSocketFactory
        server = sockets.createServerSocket(0, 16, InetAddress.getLoopbackAddress()) as SSLServerSocket
        thread(isDaemon = true, name = "llm-client-test-server") { serve() }

        // Only this JVM, only for the test: the client trusts the test's certificate as it would a public one.
        val trust = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(keyStore) }
        val clientContext = SSLContext.getInstance("TLS").apply { init(null, trust.trustManagers, null) }
        trustedBefore = HttpsURLConnection.getDefaultSSLSocketFactory()
        HttpsURLConnection.setDefaultSSLSocketFactory(clientContext.socketFactory)
    }

    @After
    fun stop() {
        HttpsURLConnection.setDefaultSSLSocketFactory(trustedBefore)
        server.close()
        folder.deleteRecursively()
    }

    /** One request per connection: /v1/ok answers, /v1/moved-<status> redirects to /v1/elsewhere, which answers. */
    private fun serve() {
        while (true) {
            val socket = try {
                server.accept()
            } catch (_: IOException) {
                return
            }
            thread(isDaemon = true) {
                socket.use { answer(it) }
            }
        }
    }

    private fun answer(socket: Socket) {
        try {
            val input = BufferedInputStream(socket.getInputStream())
            val path = line(input).split(' ').getOrElse(1) { "" }
            var length = 0
            var key: String? = null
            while (true) {
                val header = line(input)
                if (header.isEmpty()) break
                val name = header.substringBefore(':').trim()
                val value = header.substringAfter(':', "").trim()
                if (name.equals("Content-Length", ignoreCase = true)) length = value.toInt()
                if (name.equals("Authorization", ignoreCase = true)) key = value
            }
            repeat(length) { if (input.read() < 0) return@repeat }
            hits += path
            keys += key
            val moved = Regex("/v1/moved-(\\d{3})").matchEntire(path)?.groupValues?.get(1)
            val response = when {
                moved != null -> "HTTP/1.1 $moved Moved\r\nLocation: $base/v1/elsewhere\r\nContent-Length: 0\r\n\r\n"
                else -> {
                    val body = if (path == "/v1/ok") """{"ok":true}""" else """{"elsewhere":true}"""
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\n\r\n$body"
                }
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
        val bytes = StringBuilder()
        while (true) {
            val byte = input.read()
            if (byte < 0 || byte == '\n'.code) break
            if (byte != '\r'.code) bytes.append(byte.toChar())
        }
        return bytes.toString()
    }

    @Test
    fun aRequestIsPostedAndItsAnswerRead() = runBlocking {
        val answer = LlmClient().post("$base/v1/ok", headers, JSONObject().put("model", "m"))
        assertTrue(answer.getBoolean("ok"))
        assertEquals(listOf("/v1/ok"), hits.toList())
        assertEquals(listOf("Bearer test-key"), keys.toList())
    }

    @Test
    fun aRedirectEndsTheRequestAndIsNeverFollowed() = runBlocking {
        val statuses = listOf(301, 302, 303, 307, 308)
        for (status in statuses) {
            try {
                LlmClient().post("$base/v1/moved-$status", headers, JSONObject().put("model", "m"))
                fail("A $status was followed")
            } catch (refused: LlmException) {
                assertEquals(LlmException.Kind.REDIRECT, refused.kind)
                assertEquals(status, refused.status)
            }
        }
        // Each request reached the address it was sent to, and nothing went on to where the redirect pointed.
        assertEquals(statuses.map { "/v1/moved-$it" }, hits.toList())
    }

    @Test
    fun anAddressOfAnotherSchemeIsNeverOpened() = runBlocking {
        val file = File(folder, "answer.json").apply { writeText("""{"ok":true}""") }
        val addresses = listOf(
            file.toURI().toString(),
            "jar:${file.toURI()}!/a",
            "ftp://127.0.0.1:${server.localPort}/v1",
        )
        for (address in addresses) {
            try {
                LlmClient().post(address, headers, JSONObject())
                fail("$address was opened")
            } catch (refused: LlmException) {
                assertEquals(address, LlmException.Kind.INSECURE, refused.kind)
            }
        }
        assertTrue(hits.isEmpty())
    }

    private companion object {
        const val PASSWORD = "test-only"
    }
}
