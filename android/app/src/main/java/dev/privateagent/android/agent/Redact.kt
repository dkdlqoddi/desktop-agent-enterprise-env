package dev.privateagent.android.agent

/**
 * Text with the secrets it may hold written as [REDACTED], as the desktop's diagnostics.redact: API keys of the usual
 * shapes, bearer tokens, a header whose whole value is secret, and anything named as a password, key, token or
 * secret. What the chat keeps of a turn (a command it asked about, a tool's preview) goes through it, and a command
 * it changes is never kept as one to run unasked.
 */
object Redact {
    private val SECRETS: List<Pair<Regex, String>> = listOf(
        Regex("""\bpa_[A-Za-z0-9_-]{6,}""") to "pa_[REDACTED]",
        Regex("""\bsk-[A-Za-z0-9_-]{6,}""") to "sk-[REDACTED]",
        // A header whose whole value is secret, to the end of its line.
        Regex(
            """\b(authorization|proxy-authorization|cookie|set-cookie)\b(["']?\s*[=:]\s*)("[^"]*"|'[^']*'|[^\r\n]+)""",
            RegexOption.IGNORE_CASE,
        ) to "\$1\$2[REDACTED]",
        // A value named as a secret, whatever comes before or after the word in its name. A count of tokens is none.
        Regex(
            """\b([A-Za-z0-9_-]*(?:password|passwd|pwd|api[_-]?key|token(?!s)|secret)[A-Za-z0-9_-]*)""" +
                """(["']?\s*[=:]\s*)("[^"]*"|'[^']*'|[^\s,;&}\]]+)""",
            RegexOption.IGNORE_CASE,
        ) to "\$1\$2[REDACTED]",
        Regex("""\b(bearer)\s+[A-Za-z0-9._~+/=-]{6,}""", RegexOption.IGNORE_CASE) to "\$1 [REDACTED]",
    )

    fun text(value: String): String = SECRETS.fold(value) { text, (pattern, replacement) -> pattern.replace(text, replacement) }
}
