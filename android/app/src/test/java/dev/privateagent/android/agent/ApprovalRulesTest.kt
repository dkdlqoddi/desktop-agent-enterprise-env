package dev.privateagent.android.agent

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ApprovalRulesTest {
    private fun asked(command: String, permanent: Boolean = true, session: Boolean = true) =
        JSONObject().put("command", command).put("allow_permanent", permanent).put("allow_session", session)

    @Test
    fun aDecisionAppliesAsItWasGivenWhereTheQuestionOfferedIt() {
        val question = asked("  git push origin main ")
        for (decision in listOf("once", "session", "always", "deny")) {
            assertEquals(ApprovalRules.Applied(decision, "git push origin main"), ApprovalRules.settle(decision, question))
        }
    }

    @Test
    fun aScopeTheQuestionDidNotOfferCountsAsOnce() {
        assertEquals("once", ApprovalRules.settle("always", asked("git push", permanent = false)).decision)
        assertEquals("once", ApprovalRules.settle("session", asked("git push", session = false)).decision)
        // The flags must be true, not merely there.
        val words = JSONObject().put("command", "git push").put("allow_permanent", "true").put("allow_session", 1)
        assertEquals("once", ApprovalRules.settle("always", words).decision)
        assertEquals("once", ApprovalRules.settle("session", words).decision)
        assertEquals("once", ApprovalRules.settle("always", JSONObject().put("allow_permanent", true)).decision)
    }

    @Test
    fun aCommandThatHoldsASecretOrIsNoPlainLineIsNeverKept() {
        val secret = asked("curl -H 'Authorization: Bearer abcdef123456' https://example.test")
        assertEquals("once", ApprovalRules.settle("always", secret).decision)
        assertEquals("once", ApprovalRules.settle("always", asked("export API_KEY=abc123; make")).decision)
        assertEquals("once", ApprovalRules.settle("always", asked("echo a\necho b")).decision)
        assertEquals("once", ApprovalRules.settle("always", asked("x".repeat(ApprovalRules.COMMAND_CHARS + 1))).decision)
        // The other scopes do not keep the command, so they stay as they were asked.
        assertEquals("session", ApprovalRules.settle("session", secret).decision)
    }

    @Test
    fun anAnswerThatIsNoDecisionRefusesTheCommand() {
        for (decision in listOf("", "yes", "ALWAYS", "allow")) {
            assertEquals("deny", ApprovalRules.settle(decision, asked("git push")).decision)
        }
    }

    @Test
    fun aKeptCommandIsOneShortLineWithoutControlCharacters() {
        assertEquals("ls -la", ApprovalRules.keepable("  ls -la \n"))
        assertNull(ApprovalRules.keepable(""))
        assertNull(ApprovalRules.keepable("   "))
        assertNull(ApprovalRules.keepable("a\nb"))
        assertNull(ApprovalRules.keepable("a\tb"))
        assertNull(ApprovalRules.keepable("a\u007Fb"))
        assertEquals(ApprovalRules.COMMAND_CHARS, ApprovalRules.keepable("x".repeat(ApprovalRules.COMMAND_CHARS))?.length)
        assertNull(ApprovalRules.keepable("x".repeat(ApprovalRules.COMMAND_CHARS + 1)))
        assertEquals("k".repeat(ApprovalRules.KEY_CHARS), ApprovalRules.sessionKey("k".repeat(500)))
        assertNull(ApprovalRules.sessionKey(""))
    }
}
