package dev.privateagent.android.llm

import java.net.URI
import java.net.URISyntaxException

/**
 * The rules for the LLM endpoint the user enters, as on the desktop: HTTPS is the default and is never turned into
 * HTTP. An address without a scheme is saved as https://; a plain http:// address is used only when the user typed
 * http:// (a local LLM on the same Wi-Fi, say), and the settings warn that it travels unencrypted. Such an endpoint is
 * reached over the LLM client's own socket (PlainHttp.kt): cleartext stays off for the rest of the app
 * (network_security_config.xml), as Android cannot permit it for just the one host a user typed at run time.
 */
object Endpoint {
    enum class Problem { EMPTY, UNSUPPORTED_SCHEME, INVALID, CREDENTIALS, FRAGMENT }

    sealed interface Check {
        data class Ok(val url: String) : Check

        data class Rejected(val problem: Problem) : Check
    }

    private val SCHEME = Regex("^[A-Za-z][A-Za-z0-9+.-]*://")
    private val LOOPBACK_V4 = Regex("^127(?:\\.\\d{1,3}){3}$")
    private const val MAX_URL = 2048

    fun normalize(input: String): Check {
        val text = input.trim()
        if (text.isEmpty()) return Check.Rejected(Problem.EMPTY)
        if (text.length > MAX_URL || text.any { it.isWhitespace() || Character.isISOControl(it) }) {
            return Check.Rejected(Problem.INVALID)
        }
        val withScheme = if (SCHEME.containsMatchIn(text)) text else "https://$text"
        val scheme = withScheme.substringBefore("://").lowercase()
        if (scheme != "https" && scheme != "http") return Check.Rejected(Problem.UNSUPPORTED_SCHEME)
        val uri = try {
            URI(withScheme)
        } catch (_: URISyntaxException) {
            return Check.Rejected(Problem.INVALID)
        }
        if (uri.host.isNullOrEmpty()) return Check.Rejected(Problem.INVALID)
        if (uri.rawUserInfo != null) return Check.Rejected(Problem.CREDENTIALS)
        if (uri.rawFragment != null) return Check.Rejected(Problem.FRAGMENT)
        // The scheme the address has, in lower case: http only ever because the user wrote it.
        return Check.Ok(scheme + withScheme.substring(scheme.length))
    }

    /**
     * Whether requests to [url] cross the network unencrypted: an http:// endpoint anywhere but on this phone itself.
     * One on the phone (localhost, 127.0.0.0/8, ::1) never leaves it, as the desktop's warning has it for its own PC.
     */
    fun unencrypted(url: String): Boolean {
        val uri = try {
            URI(url)
        } catch (_: URISyntaxException) {
            return false
        }
        if (!uri.scheme.equals("http", ignoreCase = true)) return false
        val host = uri.host?.lowercase() ?: return false
        return host != "localhost" && !host.endsWith(".localhost") && host != "[::1]" && !LOOPBACK_V4.matches(host)
    }

    /**
     * The server an endpoint is on (scheme, host and port), which a saved API Key belongs to: a key is never sent to
     * another server than the one it was entered for, nor over http:// when it was entered for https://.
     */
    fun origin(url: String): String? = try {
        val uri = URI(url)
        val host = uri.host?.lowercase() ?: return null
        val scheme = uri.scheme?.lowercase() ?: return null
        val port = if (uri.port != -1) uri.port else if (scheme == "https") 443 else 80
        "$scheme://$host:$port"
    } catch (_: URISyntaxException) {
        null
    }

    /** The host an endpoint is on, as the screen may show it: never its path or query, which may carry a key. */
    fun host(url: String): String = try {
        URI(url).host ?: "?"
    } catch (_: URISyntaxException) {
        "?"
    }
}
