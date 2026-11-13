package dev.privateagent.android.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AgentGrantsTest {
    @Test
    fun aCommandAllowedForGoodIsKeptToTheLetterAndOutlastsTheApp() {
        val store = AgentGrants.MemoryStore()
        val grants = AgentGrants(store)
        assertEquals(emptyList<String>(), grants.allowed.value)
        assertTrue(grants.allow("  git push origin main "))
        assertTrue(grants.allow("npm run build"))
        // Once each, in the order they were allowed.
        assertTrue(grants.allow("git push origin main"))
        assertEquals(listOf("git push origin main", "npm run build"), grants.allowed.value)
        // The app started again reads the same list.
        assertEquals(listOf("git push origin main", "npm run build"), AgentGrants(store).allowed.value)
        grants.revoke("git push origin main")
        grants.revoke("never allowed")
        assertEquals(listOf("npm run build"), grants.allowed.value)
        assertEquals(listOf("npm run build"), AgentGrants(store).allowed.value)
    }

    @Test
    fun whatIsNoCommandToKeepNeverGetsOnTheList() {
        val store = AgentGrants.MemoryStore()
        val grants = AgentGrants(store)
        for (command in listOf("", "   ", "echo a\necho b", "x".repeat(ApprovalRules.COMMAND_CHARS + 1))) {
            assertFalse(grants.allow(command))
        }
        assertEquals(emptyList<String>(), store.load())
        // No more than the limit.
        repeat(ApprovalRules.MAX_ALLOWED) { assertTrue(grants.allow("command $it")) }
        assertFalse(grants.allow("one more"))
        assertEquals(ApprovalRules.MAX_ALLOWED, grants.allowed.value.size)
        // A list that was written by other means is read without what does not belong on it.
        val edited = AgentGrants.MemoryStore(listOf("ls", "a\nb", "ls", "", " pwd "))
        assertEquals(listOf("ls", "pwd"), AgentGrants(edited).allowed.value)
    }

    @Test
    fun theKindsAllowedForAConversationEndWithItAndWithTheApp() {
        val store = AgentGrants.MemoryStore()
        val grants = AgentGrants(store)
        grants.grant(listOf("recursive delete", "", "force push"))
        grants.grant(listOf("recursive delete", "k".repeat(500)))
        assertEquals(listOf("recursive delete", "force push", "k".repeat(ApprovalRules.KEY_CHARS)), grants.session.value)
        // They are not among what is kept for good, and the app started again knows none.
        assertEquals(emptyList<String>(), store.load())
        assertEquals(emptyList<String>(), AgentGrants(store).session.value)
        grants.forgetSession()
        assertEquals(emptyList<String>(), grants.session.value)
        repeat(ApprovalRules.MAX_SESSION + 10) { grants.grant(listOf("kind $it")) }
        assertEquals(ApprovalRules.MAX_SESSION, grants.session.value.size)
    }
}
