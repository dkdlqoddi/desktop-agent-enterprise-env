package dev.privateagent.android

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.privateagent.android.llm.LlmClient
import dev.privateagent.android.llm.LlmException
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.BufferedInputStream
import java.io.IOException
import java.io.InputStream
import java.net.InetAddress
import java.net.Socket
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Principal
import java.security.PrivateKey
import java.security.cert.X509Certificate
import java.security.spec.ECGenParameterSpec
import java.util.Collections
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLEngine
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory
import javax.net.ssl.X509ExtendedKeyManager
import javax.security.auth.x500.X500Principal
import kotlin.concurrent.thread

/**
 * The LLM client on the device's own HTTPS stack: a redirect (which would carry the API Key to another address) ends
 * the request and is never followed. Android's stack follows a POST's 301, 302 and 303 as a GET unless told not to,
 * which the JVM's does not, so this is checked here. The server is the test's own, on the device, with a key and
 * certificate made for the test in the device's keystore; only the test's process trusts it, for the test's duration.
 */
@RunWith(AndroidJUnit4::class)
class LlmRedirectTest {
    private lateinit var server: SSLServerSocket
    private lateinit var trustedBefore: SSLSocketFactory
    private lateinit var verifierBefore: HostnameVerifier
    private val hits: MutableList<String> = Collections.synchronizedList(mutableListOf())

    private val base get() = "https://127.0.0.1:${server.localPort}"

    @Before
    fun start() {
        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, KEYSTORE)
        generator.initialize(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_NONE, KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_SHA384)
                .setCertificateSubject(X500Principal("CN=127.0.0.1"))
                .build(),
        )
        generator.generateKeyPair()
        val store = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        val key = store.getKey(ALIAS, null) as PrivateKey
        val certificate = store.getCertificate(ALIAS) as X509Certificate

        val serverContext = SSLContext.getInstance("TLS")
        serverContext.init(arrayOf(ServerKey(key, certificate)), null, null)
        val sockets = serverContext.serverSocketFactory
        server = sockets.createServerSocket(0, 16, InetAddress.getByName("127.0.0.1")) as SSLServerSocket
        thread(isDaemon = true, name = "llm-redirect-test-server") { serve() }

        val trusted = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null)
            setCertificateEntry("server", certificate)
        }
        val trust = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm()).apply { init(trusted) }
        trustedBefore = HttpsURLConnection.getDefaultSSLSocketFactory()
        verifierBefore = HttpsURLConnection.getDefaultHostnameVerifier()
        val clientContext = SSLContext.getInstance("TLS").apply { init(null, trust.trustManagers, null) }
        HttpsURLConnection.setDefaultSSLSocketFactory(clientContext.socketFactory)
        // The device's keystore cannot name an address in a certificate: this one is the test server's own.
        HttpsURLConnection.setDefaultHostnameVerifier { host, _ -> host == "127.0.0.1" }
    }

    @After
    fun stop() {
        HttpsURLConnection.setDefaultSSLSocketFactory(trustedBefore)
        HttpsURLConnection.setDefaultHostnameVerifier(verifierBefore)
        server.close()
        KeyStore.getInstance(KEYSTORE).apply { load(null) }.deleteEntry(ALIAS)
    }

    /** The test server's key, for its TLS handshakes. */
    private class ServerKey(
        private val key: PrivateKey,
        private val certificate: X509Certificate,
    ) : X509ExtendedKeyManager() {
        override fun chooseServerAlias(keyType: String?, issuers: Array<out Principal>?, socket: Socket?) =
            if (keyType == "EC") ALIAS else null

        override fun chooseEngineServerAlias(keyType: String?, issuers: Array<out Principal>?, engine: SSLEngine?) =
            if (keyType == "EC") ALIAS else null

        override fun getCertificateChain(alias: String?) = arrayOf(certificate)

        override fun getPrivateKey(alias: String?) = key

        override fun getServerAliases(keyType: String?, issuers: Array<out Principal>?) = arrayOf(ALIAS)

        override fun getClientAliases(keyType: String?, issuers: Array<out Principal>?) = null

        override fun chooseClientAlias(
            keyType: Array<out String>?,
            issuers: Array<out Principal>?,
            socket: Socket?,
        ): String? = null
    }

    /** One request per connection: /v1/moved-<status> answers with that redirect to /v1/elsewhere. */
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
            val request = line(input).split(' ')
            var length = 0
            while (true) {
                val header = line(input)
                if (header.isEmpty()) break
                if (header.substringBefore(':').trim().equals("Content-Length", ignoreCase = true)) {
                    length = header.substringAfter(':').trim().toInt()
                }
            }
            repeat(length) { input.read() }
            hits += request.getOrElse(0) { "" } + " " + request.getOrElse(1) { "" }
            val moved = Regex("/v1/moved-(\\d{3})").matchEntire(request.getOrElse(1) { "" })?.groupValues?.get(1)
            val body = """{"elsewhere":true}"""
            val response = if (moved != null) {
                "HTTP/1.1 $moved Moved\r\nLocation: $base/v1/elsewhere\r\nContent-Length: 0\r\n" +
                    "Connection: close\r\n\r\n"
            } else {
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: ${body.length}\r\n" +
                    "Connection: close\r\n\r\n$body"
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
        val text = StringBuilder()
        while (true) {
            val byte = input.read()
            if (byte < 0 || byte == '\n'.code) break
            if (byte != '\r'.code) text.append(byte.toChar())
        }
        return text.toString()
    }

    @Test
    fun aRedirectEndsTheRequestAndIsNeverFollowed() = runBlocking {
        val statuses = listOf(301, 302, 303, 307, 308)
        val headers = mapOf("Authorization" to "Bearer test-key", "Content-Type" to "application/json")
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
        assertEquals(statuses.map { "POST /v1/moved-$it" }, hits.toList())
        assertTrue(hits.none { it.endsWith("/v1/elsewhere") })
    }

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "llm-redirect-test-server"
    }
}
