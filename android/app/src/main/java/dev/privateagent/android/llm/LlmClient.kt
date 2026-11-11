package dev.privateagent.android.llm

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONException
import org.json.JSONObject
import java.io.IOException
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.net.UnknownHostException
import java.net.UnknownServiceException
import java.security.cert.CertificateException
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLException

/** Why an LLM request gave no reply; the screen turns it into a message (never the request, the key or a stack). */
class LlmException(val kind: Kind, val status: Int? = null) : Exception("LLM request failed: $kind") {
    enum class Kind { AUTH, REQUEST, RATE_LIMIT, SERVER, STATUS, REDIRECT, TIMEOUT, NETWORK, TLS, INSECURE, FORMAT, REFUSED }
}

/**
 * Sends one request to the user's LLM endpoint and returns the response body. HTTPS only, the platform's certificate
 * checks as they are (never relaxed), no redirects followed (one would carry the API Key to another address), and one
 * deadline for the whole exchange. Nothing about the request is logged.
 */
class LlmClient(
    private val totalTimeoutMs: Long = 60_000,
    private val connectTimeoutMs: Int = 15_000,
) {
    suspend fun post(endpoint: String, headers: Map<String, String>, body: JSONObject): JSONObject = coroutineScope {
        val url = try {
            URL(endpoint)
        } catch (_: IOException) {
            throw LlmException(LlmException.Kind.NETWORK)
        }
        if (url.protocol != "https") throw LlmException(LlmException.Kind.INSECURE)
        val connection = withContext(Dispatchers.IO) { url.openConnection() } as? HttpsURLConnection
            ?: throw LlmException(LlmException.Kind.INSECURE)
        val finished = AtomicBoolean(false)
        val timedOut = AtomicBoolean(false)
        // The deadline of the whole exchange; cancelling the caller (the user's stop button) ends it the same way.
        val watchdog = launch(Dispatchers.IO) {
            try {
                delay(totalTimeoutMs)
                timedOut.set(true)
            } finally {
                if (!finished.get()) connection.disconnect()
            }
        }
        try {
            withContext(Dispatchers.IO) { exchange(connection, headers, body) }
        } catch (failure: IOException) {
            throw if (timedOut.get()) LlmException(LlmException.Kind.TIMEOUT) else failure.toLlmException()
        } finally {
            finished.set(true)
            watchdog.cancel()
            connection.disconnect()
        }
    }

    private fun exchange(connection: HttpsURLConnection, headers: Map<String, String>, body: JSONObject): JSONObject {
        val bytes = body.toString().toByteArray(Charsets.UTF_8)
        connection.instanceFollowRedirects = false
        connection.useCaches = false
        connection.connectTimeout = connectTimeoutMs
        connection.readTimeout = totalTimeoutMs.toInt()
        connection.requestMethod = "POST"
        connection.doOutput = true
        connection.setFixedLengthStreamingMode(bytes.size)
        for ((name, value) in headers) connection.setRequestProperty(name, value)
        connection.outputStream.use { it.write(bytes) }
        val status = connection.responseCode
        if (status !in 200..299) {
            // The error body is read and dropped: it is not shown, and it may repeat what the request sent.
            runCatching { connection.errorStream?.use { drain(it) } }
            throw statusException(status)
        }
        val text = connection.inputStream.use { read(it) }
        return try {
            JSONObject(text)
        } catch (_: JSONException) {
            throw LlmException(LlmException.Kind.FORMAT)
        }
    }

    private fun read(stream: InputStream): String {
        val output = java.io.ByteArrayOutputStream()
        val buffer = ByteArray(16 * 1024)
        while (true) {
            val count = stream.read(buffer)
            if (count < 0) break
            output.write(buffer, 0, count)
            if (output.size() > MAX_RESPONSE_BYTES) throw LlmException(LlmException.Kind.FORMAT)
        }
        return output.toString(Charsets.UTF_8.name())
    }

    private fun drain(stream: InputStream) {
        val buffer = ByteArray(8 * 1024)
        var total = 0
        while (total < MAX_RESPONSE_BYTES) {
            val count = stream.read(buffer)
            if (count < 0) break
            total += count
        }
    }

    companion object {
        private const val MAX_RESPONSE_BYTES = 4 * 1024 * 1024

        fun statusException(status: Int): LlmException = LlmException(
            when (status) {
                401, 403 -> LlmException.Kind.AUTH
                400, 404, 405, 415, 422 -> LlmException.Kind.REQUEST
                429 -> LlmException.Kind.RATE_LIMIT
                in 300..399 -> LlmException.Kind.REDIRECT
                in 500..599, 408 -> LlmException.Kind.SERVER
                else -> LlmException.Kind.STATUS
            },
            status,
        )

        private fun IOException.toLlmException(): LlmException = when (this) {
            is SocketTimeoutException -> LlmException(LlmException.Kind.TIMEOUT)
            is UnknownServiceException -> LlmException(LlmException.Kind.INSECURE)
            is SSLException -> LlmException(LlmException.Kind.TLS)
            is UnknownHostException -> LlmException(LlmException.Kind.NETWORK)
            else -> if (cause is CertificateException) {
                LlmException(LlmException.Kind.TLS)
            } else {
                LlmException(LlmException.Kind.NETWORK)
            }
        }
    }
}

/** Sends the conversation to the configured LLM and reads the reply. */
class LlmService(private val client: LlmClient = LlmClient()) {
    suspend fun complete(
        connection: dev.privateagent.android.security.LlmConnection,
        system: String,
        turns: List<ChatTurn>,
        tools: List<ToolDefinition>,
        round: ToolRound? = null,
    ): LlmReply {
        val body = LlmProtocol.requestBody(connection.format, connection.model, system, turns, tools, round)
        val response = client.post(connection.endpoint, LlmProtocol.headers(connection.format, connection.apiKey), body)
        return try {
            LlmProtocol.parseReply(connection.format, response)
        } catch (_: LlmFormatException) {
            throw LlmException(LlmException.Kind.FORMAT)
        } catch (_: LlmRefusedException) {
            throw LlmException(LlmException.Kind.REFUSED)
        }
    }

    /** The connection check: a tiny request whose reply only has to be of the configured format. */
    suspend fun ping(connection: dev.privateagent.android.security.LlmConnection) {
        val response = client.post(
            connection.endpoint,
            LlmProtocol.headers(connection.format, connection.apiKey),
            LlmProtocol.pingBody(connection.format, connection.model),
        )
        try {
            LlmProtocol.parseReply(connection.format, response)
        } catch (_: LlmFormatException) {
            throw LlmException(LlmException.Kind.FORMAT)
        } catch (_: LlmRefusedException) {
            // The model answered; the connection works.
        }
    }
}
