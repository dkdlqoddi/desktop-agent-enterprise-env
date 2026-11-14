package dev.privateagent.android.llm

import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.IDN
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URL

/** One request's exchange with the LLM server: over the platform's HTTPS stack, or over [PlainHttpPost]. */
internal interface LlmExchange {
    /** Sends [body] with [headers]: the response's status and its body (null when the server sent none). */
    fun send(headers: Map<String, String>, body: ByteArray): LlmAnswer

    /** Ends the exchange at once, from any thread, any number of times. */
    fun abort()
}

internal class LlmAnswer(val status: Int, val body: InputStream?)

/**
 * One POST over HTTP/1.1 on a plain socket of its own: how the LLM client reaches an endpoint the user typed with
 * http:// (a local LLM on the same Wi-Fi, say). The app keeps cleartext traffic off (network_security_config.xml), so
 * the platform's HTTP stack and the in-app browser's WebView send nothing over plain http, wherever a page, a form or
 * a redirect points them: Android cannot permit cleartext for just the one host a user types at run time, and its rule
 * does not govern a socket. One request per connection (Connection: close), no proxy, no compression, no redirect
 * followed (the caller gets the 3xx), and a cap on the response's head; the caller caps the body.
 */
internal class PlainHttpPost(
    private val url: URL,
    private val connectTimeoutMs: Int,
    private val readTimeoutMs: Int,
) : LlmExchange {
    private val socket = Socket()

    override fun send(headers: Map<String, String>, body: ByteArray): LlmAnswer {
        require(url.protocol == "http") { "Not an http address" }
        // Written out before connecting, so a header that cannot be sent leaves nothing sent.
        val head = head(headers, body.size)
        socket.soTimeout = readTimeoutMs
        socket.connect(InetSocketAddress(hostName(), port()), connectTimeoutMs)
        BufferedOutputStream(socket.getOutputStream()).apply {
            write(head)
            write(body)
            flush()
        }
        val input = BufferedInputStream(socket.getInputStream())
        repeat(MAX_INTERIM + 1) {
            val (status, fields) = readHead(input)
            // An interim answer (100 Continue, 103 Early Hints) comes before the final one.
            if (status !in 100..199) return LlmAnswer(status, content(input, status, fields))
        }
        throw IOException("No final answer")
    }

    override fun abort() {
        try {
            socket.close()
        } catch (_: IOException) {
            // Closed already.
        }
    }

    /** The host to connect to: an IPv6 literal without its brackets, a name in its ASCII (punycode) form. */
    private fun hostName(): String = IDN.toASCII(url.host.removeSurrounding("[", "]"))

    private fun port(): Int = if (url.port != -1) url.port else url.defaultPort

    private fun head(headers: Map<String, String>, length: Int): ByteArray {
        val path = url.path.ifEmpty { "/" }
        val target = if (url.query != null) "$path?${url.query}" else path
        val host = if (url.host.startsWith("[")) "[${hostName()}]" else hostName()
        val text = StringBuilder("POST $target HTTP/1.1\r\n")
        text.append(field("Host", if (url.port == -1 || url.port == url.defaultPort) host else "$host:${url.port}"))
        for ((name, value) in headers) {
            if (name.lowercase() !in FRAMING) text.append(field(name, value))
        }
        text.append(field("Content-Length", length.toString()))
        text.append(field("Connection", "close"))
        text.append("\r\n")
        return text.toString().toByteArray(Charsets.US_ASCII)
    }

    /** A header line; one that would break the request (a line break, a character outside ASCII) is refused. */
    private fun field(name: String, value: String): String {
        require(name.isNotEmpty() && name.all { it in '!'..'~' && it != ':' }) { "A header name that cannot be sent" }
        // The message never repeats the value: it may be the API Key.
        require(value.all { it == '\t' || it in ' '..'~' }) { "A header value that cannot be sent" }
        return "$name: $value\r\n"
    }

    /** The status and the header fields (names in lower case) of the next response; IOException when malformed. */
    private fun readHead(input: InputStream): Pair<Int, Map<String, String>> {
        var left = MAX_HEAD
        fun next(): String = line(input, left).also { left -= it.length + 2 }
        val status = STATUS.matchEntire(next())?.groupValues?.get(1)?.toInt()
            ?: throw IOException("Not an HTTP response")
        val fields = mutableMapOf<String, String>()
        while (true) {
            val field = next()
            if (field.isEmpty()) break
            val name = field.substringBefore(':', "").trim().lowercase()
            if (name.isEmpty()) throw IOException("A malformed header")
            val value = field.substringAfter(':').trim()
            fields[name] = fields[name]?.let { "$it, $value" } ?: value
        }
        return status to fields
    }

    /** The response's body as its head frames it: chunked, so many bytes, or up to the end of the connection. */
    private fun content(input: InputStream, status: Int, fields: Map<String, String>): InputStream? {
        if (status == 204 || status == 304) return null
        val coding = fields["transfer-encoding"]
        if (coding != null) {
            if (!coding.lowercase().endsWith("chunked")) throw IOException("An unknown transfer coding")
            return ChunkedInput(input)
        }
        val length = fields["content-length"] ?: return input
        return LimitedInput(input, length.toLongOrNull()?.takeIf { it >= 0 } ?: throw IOException("A bad length"))
    }

    /** A body of [left] bytes. */
    private class LimitedInput(private val input: InputStream, private var left: Long) : InputStream() {
        override fun read(): Int {
            val one = ByteArray(1)
            return if (read(one, 0, 1) < 0) -1 else one[0].toInt() and 0xff
        }

        override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
            if (left == 0L) return -1
            if (length == 0) return 0
            val count = input.read(buffer, offset, minOf(length.toLong(), left).toInt())
            if (count < 0) throw IOException("The body was cut short")
            left -= count
            return count
        }
    }

    /** A chunked body (RFC 9112, 7.1), read to its last chunk; the trailer that may follow is skipped. */
    private class ChunkedInput(private val input: InputStream) : InputStream() {
        private var left = 0L
        private var started = false
        private var ended = false

        override fun read(): Int {
            val one = ByteArray(1)
            return if (read(one, 0, 1) < 0) -1 else one[0].toInt() and 0xff
        }

        override fun read(buffer: ByteArray, offset: Int, length: Int): Int {
            if (ended) return -1
            if (length == 0) return 0
            if (left == 0L) {
                // The line break after the last chunk's data, then the size of the next chunk.
                if (started && line(input, MAX_LINE).isNotEmpty()) throw IOException("A chunk was not ended")
                started = true
                val size = line(input, MAX_LINE).substringBefore(';').trim()
                left = size.toLongOrNull(16)?.takeIf { it >= 0 } ?: throw IOException("A bad chunk size")
                if (left == 0L) {
                    var lines = 0
                    while (line(input, MAX_LINE).isNotEmpty()) {
                        if (++lines > MAX_TRAILER) throw IOException("The trailer is too long")
                    }
                    ended = true
                    return -1
                }
            }
            val count = input.read(buffer, offset, minOf(length.toLong(), left).toInt())
            if (count < 0) throw IOException("A chunk was cut short")
            left -= count
            return count
        }
    }

    private companion object {
        val STATUS = Regex("HTTP/1\\.[01] (\\d{3})(?: .*)?")

        /** Headers the exchange writes itself, from what it sends: never the caller's. */
        val FRAMING = setOf("host", "content-length", "transfer-encoding", "connection")

        /** The most the status line and the headers of a response may take, in bytes. */
        const val MAX_HEAD = 64 * 1024
        const val MAX_LINE = 8 * 1024
        const val MAX_TRAILER = 64
        const val MAX_INTERIM = 8

        /** One line of the response's head, without its line break; IOException past [max] bytes or at the end. */
        fun line(input: InputStream, max: Int): String {
            val bytes = ByteArrayOutputStream()
            while (true) {
                val byte = input.read()
                if (byte < 0) throw IOException("The connection closed")
                if (byte == '\n'.code) break
                if (bytes.size() >= max) throw IOException("A line is too long")
                bytes.write(byte)
            }
            return bytes.toString(Charsets.ISO_8859_1.name()).removeSuffix("\r")
        }
    }
}
