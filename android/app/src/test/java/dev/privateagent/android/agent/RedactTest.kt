package dev.privateagent.android.agent

import org.junit.Assert.assertEquals
import org.junit.Test

class RedactTest {
    @Test
    fun aKeyAfterItsNameGoesWholeWithNothingOfTheTokenRulesLeftBehind() {
        assertEquals("api_key=[REDACTED] next", Redact.text("api_key=sk-abcdef123456 next"))
        assertEquals("Authorization: [REDACTED]\nnext", Redact.text("Authorization: Bearer abcdef.ghijkl\nnext"))
        assertEquals("max_tokens=4096, prompt_tokens: 12", Redact.text("max_tokens=4096, prompt_tokens: 12"))
    }

    // The same table as the desktop's tests (backend/tests/test_diagnostics.py, tests/diagnostics.test.cjs).
    @Test
    fun tokensWhoseFormGivesThemAwayGoWhereNoNameSaysWhatTheyAre() {
        val tokens = listOf(
            "failed with ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8" to "failed with ghp_[REDACTED]",
            "pat github_pat_" + "11ABCDEFG0123456789_abcdefghijklmnop" to "pat github_pat_[REDACTED]",
            "slack xoxb-" + "1234567890-0987654321-AbCdEfGhIjKl" to "slack xoxb-[REDACTED]",
            "socket xapp-" + "1-A0123456789-0123456789-abcdef" to "socket xapp-[REDACTED]",
            "GET https://api.telegram.org/bot123456789:" + "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" + "/getMe failed" to
                "GET https://api.telegram.org/bot123456789:[REDACTED]/getMe failed",
            "token 123456789:" + "AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw" to "token 123456789:[REDACTED]",
            "discord " + "MTA0NjAxNDQyNTk3MzQyNjE4Mg" + ".GhIjKl." + "abcdefghijklmnopqrstuvwxyz01234" to "discord [REDACTED]",
            "jwt " + "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0In0" + ".abcdefghijklmnop" to "jwt [REDACTED]",
            "google AIza" + "SyA1234567890abcdefghijklmnopqrstu" to "google AIza[REDACTED]",
            "hf hf_" + "abcdefghijklmnopqrstuvwxyz012345" to "hf hf_[REDACTED]",
            "firecrawl fc-" + "0123456789abcdef0123456789abcdef" to "firecrawl fc-[REDACTED]",
            "tavily tvly-" + "dev-0123456789abcdefABCDEF" to "tavily tvly-[REDACTED]",
            "elevenlabs sk_" + "0123456789abcdef0123456789abcdef" to "elevenlabs sk_[REDACTED]",
            "fal 01234567-89ab-cdef-0123-456789abcdef:" + "0123456789abcdef0123456789abcdef" to "fal [REDACTED]",
            "aws AKIA" + "IOSFODNN7EXAMPLE" to "aws AKIA[REDACTED]",
            // Ordinary text with those letters stays: a time, a version, a module path, a word.
            "at 12:30:45 on v1.2.3 in agent.llm.request" to "at 12:30:45 on v1.2.3 in agent.llm.request",
            "task_queue skipped, bot 12 replied" to "task_queue skipped, bot 12 replied",
        )
        for ((written, kept) in tokens) assertEquals(written, kept, Redact.text(written))
    }
}
