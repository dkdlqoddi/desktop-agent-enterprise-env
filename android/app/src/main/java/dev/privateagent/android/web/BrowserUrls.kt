package dev.privateagent.android.web

import java.net.URI
import java.net.URISyntaxException
import java.net.URLDecoder

/** How an address the user or the LLM gave is opened in the in-app browser. */
object BrowserUrls {
    sealed interface Check {
        data class Ok(val url: String) : Check

        /** A plain http:// address: the app keeps cleartext traffic off (network_security_config.xml). */
        data class Insecure(val url: String) : Check

        data object Invalid : Check
    }

    private val SCHEME = Regex("^[A-Za-z][A-Za-z0-9+.-]*://")
    private const val MAX_URL = 2048

    /** An address with https:// added when it has no scheme; only https is opened. */
    fun normalize(input: String): Check {
        val text = input.trim()
        if (text.isEmpty() || text.length > MAX_URL || text.any { it.isWhitespace() || Character.isISOControl(it) }) {
            return Check.Invalid
        }
        val withScheme = if (SCHEME.containsMatchIn(text)) text else "https://$text"
        val uri = try {
            URI(withScheme)
        } catch (_: URISyntaxException) {
            return Check.Invalid
        }
        val scheme = uri.scheme?.lowercase() ?: return Check.Invalid
        if (uri.host.isNullOrEmpty() || uri.rawUserInfo != null) return Check.Invalid
        val url = scheme + withScheme.substring(scheme.length)
        return when (scheme) {
            "https" -> Check.Ok(url)
            "http" -> Check.Insecure(url)
            else -> Check.Invalid
        }
    }

    /** Whether address bar text reads as an address rather than search words. */
    fun looksLikeAddress(input: String): Boolean {
        val text = input.trim()
        if (text.isEmpty() || text.any { it.isWhitespace() }) return false
        return SCHEME.containsMatchIn(text) || (text.contains('.') && !text.startsWith('.') && !text.endsWith('.'))
    }
}

/** What the in-app browser does with a navigation a page (not the app) started. */
sealed interface NavigationDecision {
    data object Allow : NavigationDecision

    /** An intent: link with an https fallback page (browser_fallback_url): that page opens in the app instead. */
    data class OpenInstead(val url: String) : NavigationDecision

    /** A plain http:// page: blocked, as cleartext traffic is off; the user may open it in another browser. */
    data class BlockInsecure(val url: String) : NavigationDecision

    /** A link to another app (intent:, market:, tel:, ...): blocked, the app never leaves on its own. */
    data class BlockExternal(val scheme: String) : NavigationDecision
}

object NavigationPolicy {
    /** The decision for a main-frame navigation. */
    fun decide(url: String): NavigationDecision {
        val scheme = url.substringBefore(':', "").lowercase()
        return when (scheme) {
            "https" -> NavigationDecision.Allow
            "http" -> NavigationDecision.BlockInsecure(url)
            "intent" -> intentFallback(url)?.let { fallback ->
                if (fallback.lowercase().startsWith("https://")) NavigationDecision.OpenInstead(fallback) else null
            } ?: NavigationDecision.BlockExternal(scheme)
            else -> NavigationDecision.BlockExternal(scheme.take(20).ifEmpty { "?" })
        }
    }

    /** Whether a frame inside the page may navigate to [url]: web pages and inline documents, never other apps. */
    fun allowsSubframe(url: String): Boolean =
        when (url.substringBefore(':', "").lowercase()) {
            "https", "http", "about", "data", "blob" -> true
            else -> false
        }

    /** The browser_fallback_url of an intent: URI (intent://...#Intent;...;S.browser_fallback_url=...;end), or null. */
    fun intentFallback(url: String): String? {
        val fragment = url.substringAfter("#Intent;", "")
        if (fragment.isEmpty()) return null
        for (part in fragment.split(';')) {
            if (part.startsWith("S.browser_fallback_url=")) {
                return try {
                    URLDecoder.decode(part.substringAfter('='), "UTF-8").trim().ifEmpty { null }
                } catch (_: IllegalArgumentException) {
                    null
                }
            }
        }
        return null
    }
}
