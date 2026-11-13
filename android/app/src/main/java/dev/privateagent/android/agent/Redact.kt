package dev.privateagent.android.agent

/**
 * Text with the secrets it may hold written as [REDACTED], as the desktop's diagnostics.redact, with the same rules in
 * the same order: a header whose whole value is secret, anything named as a password, key, token or secret, bearer
 * tokens, and the tokens whose form gives them away wherever they stand (the app's own API tokens, OpenAI- and
 * Anthropic-style keys, and those of GitHub, Slack, Telegram, Discord, Google, Hugging Face, Firecrawl, Tavily, fal and
 * AWS, and JSON Web Tokens). The named and header rules come first, so a value they take goes whole. What the chat keeps
 * of a turn (a command it asked about, a tool's preview) goes through it, and a command it changes is never kept as one
 * to run unasked.
 */
object Redact {
    private val SECRETS: List<Pair<Regex, String>> = listOf(
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
        Regex("""\bpa_[A-Za-z0-9_-]{6,}""") to "pa_[REDACTED]",
        Regex("""\bsk-[A-Za-z0-9_-]{6,}""") to "sk-[REDACTED]",
        Regex("""\bsk_[A-Za-z0-9_]{20,}""") to "sk_[REDACTED]",
        Regex("""\b(gh[pousr])_[A-Za-z0-9]{20,}""") to "\$1_[REDACTED]",
        Regex("""\bgithub_pat_[A-Za-z0-9_]{20,}""") to "github_pat_[REDACTED]",
        Regex("""\b(xox[abeoprs]|xapp)-[A-Za-z0-9-]{10,}""") to "\$1-[REDACTED]",
        // A Telegram bot token, also inside the Bot API's addresses (https://api.telegram.org/bot<token>/getMe).
        Regex("""\b(bot)?(\d{6,12}):[A-Za-z0-9_-]{30,}""") to "\$1\$2:[REDACTED]",
        Regex("""\b[MNO][A-Za-z0-9_-]{23,27}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}""") to "[REDACTED]",
        Regex("""\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}""") to "[REDACTED]",
        Regex("""\bAIza[A-Za-z0-9_-]{30,}""") to "AIza[REDACTED]",
        Regex("""\bhf_[A-Za-z0-9]{30,}""") to "hf_[REDACTED]",
        Regex("""\b(fc|tvly)-[A-Za-z0-9-]{16,}""") to "\$1-[REDACTED]",
        Regex("""\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}:[0-9a-f]{32}\b""") to "[REDACTED]",
        Regex("""\b(AKIA|ASIA)[0-9A-Z]{16}\b""") to "\$1[REDACTED]",
    )

    fun text(value: String): String = SECRETS.fold(value) { text, (pattern, replacement) -> pattern.replace(text, replacement) }
}
