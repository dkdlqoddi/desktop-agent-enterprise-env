package dev.privateagent.android.llm

import java.net.URI
import java.net.URISyntaxException

/**
 * The rules for the LLM endpoint the user enters. An address without a scheme is saved as https://. A plain http://
 * address is refused rather than allowed: cleartext traffic is off for the whole app (network_security_config.xml),
 * because Android cannot allow it for only the one host a user typed at run time, and an http request would carry the
 * API Key and the conversation unencrypted.
 */
object Endpoint {
    enum class Problem { EMPTY, INSECURE_HTTP, UNSUPPORTED_SCHEME, INVALID, CREDENTIALS, FRAGMENT }

    sealed interface Check {
        data class Ok(val url: String) : Check

        data class Rejected(val problem: Problem) : Check
    }

    private val SCHEME = Regex("^[A-Za-z][A-Za-z0-9+.-]*://")
    private const val MAX_URL = 2048

    fun normalize(input: String): Check {
        val text = input.trim()
        if (text.isEmpty()) return Check.Rejected(Problem.EMPTY)
        if (text.length > MAX_URL || text.any { it.isWhitespace() || Character.isISOControl(it) }) {
            return Check.Rejected(Problem.INVALID)
        }
        val withScheme = if (SCHEME.containsMatchIn(text)) text else "https://$text"
        val scheme = withScheme.substringBefore("://").lowercase()
        when (scheme) {
            "https" -> Unit
            "http" -> return Check.Rejected(Problem.INSECURE_HTTP)
            else -> return Check.Rejected(Problem.UNSUPPORTED_SCHEME)
        }
        val uri = try {
            URI(withScheme)
        } catch (_: URISyntaxException) {
            return Check.Rejected(Problem.INVALID)
        }
        if (uri.host.isNullOrEmpty()) return Check.Rejected(Problem.INVALID)
        if (uri.rawUserInfo != null) return Check.Rejected(Problem.CREDENTIALS)
        if (uri.rawFragment != null) return Check.Rejected(Problem.FRAGMENT)
        return Check.Ok("https" + withScheme.substring(scheme.length))
    }

    /**
     * The server an endpoint is on (scheme, host and port), which a saved API Key belongs to: a key is never sent to
     * another server than the one it was entered for.
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
