package dev.privateagent.android.agent

import dev.privateagent.android.agent.AgentMemory.Problem
import dev.privateagent.android.agent.AgentMemory.Target
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class AgentMemoryTest {
    @get:Rule
    val folder = TemporaryFolder()

    private val home by lazy { folder.newFolder("hermes") }
    private var busy = false
    private val memory by lazy { AgentMemory(home) { busy } }

    private fun file(target: Target) = File(home, "memories/${target.file}")

    private fun refused(problem: Problem, block: () -> Unit): AgentMemory.MemoryException {
        try {
            block()
        } catch (refusal: AgentMemory.MemoryException) {
            assertEquals(problem, refusal.problem)
            return refusal
        }
        fail("Expected $problem")
        throw IllegalStateException()
    }

    @Test
    fun anAgentThatHasRememberedNothingHasTwoEmptyLists() {
        val view = memory.read()
        assertEquals(AgentMemory.Entries(emptyList(), 0, 2200), view.memory)
        assertEquals(AgentMemory.Entries(emptyList(), 0, 1375), view.user)
        assertEquals(AgentMemory.Records(0, 0), view.records)
    }

    @Test
    fun theEntriesAreReadAsHermesWritesThem() {
        file(Target.MEMORY).apply { parentFile!!.mkdirs() }.writeText("쿠팡은 로그인이 필요하다\n§\n  검색은 네이버부터  \n§\n쿠팡은 로그인이 필요하다\n§\n\n")
        val view = memory.read()
        assertEquals(listOf("쿠팡은 로그인이 필요하다", "검색은 네이버부터"), view.memory.entries)
        assertEquals("쿠팡은 로그인이 필요하다\n§\n검색은 네이버부터".length, view.memory.chars)
        assertEquals(emptyList<String>(), view.user.entries)
        assertEquals(listOf("a", "b"), AgentMemory.parse("a\n§\nb\n§\na"))
        // Hermes counts characters, not UTF-16 units.
        assertEquals(3, AgentMemory.chars("a😀b"))
    }

    @Test
    fun aListIsSavedAsTheUserLeftIt() {
        val view = memory.save(Target.USER, listOf("  한국어로 답하기를 원한다 ", "", "짧은 답을 좋아한다", "한국어로 답하기를 원한다"))
        assertEquals(listOf("한국어로 답하기를 원한다", "짧은 답을 좋아한다"), view.user.entries)
        assertEquals("한국어로 답하기를 원한다\n§\n짧은 답을 좋아한다", file(Target.USER).readText())
        assertEquals(view, memory.read())
        // Written whole, through a file of its own that does not stay behind.
        assertEquals(listOf("USER.md"), file(Target.USER).parentFile!!.list()!!.toList())
        // An entry removed, and then all of them.
        assertEquals(listOf("짧은 답을 좋아한다"), memory.save(Target.USER, listOf("짧은 답을 좋아한다")).user.entries)
        assertEquals(emptyList<String>(), memory.save(Target.USER, emptyList()).user.entries)
        assertEquals("", file(Target.USER).readText())
    }

    @Test
    fun anEntryThatWouldBeCutInTwoOrIsNoTextIsRefused() {
        refused(Problem.DELIMITER) { memory.save(Target.MEMORY, listOf("첫 줄\n§\n둘째 줄")) }
        refused(Problem.DELIMITER) { memory.save(Target.MEMORY, listOf("첫 줄\n  §  \n둘째 줄")) }
        refused(Problem.INVALID) { memory.save(Target.MEMORY, listOf("a\u0000b")) }
        refused(Problem.INVALID) { memory.save(Target.MEMORY, List(AgentMemory.MAX_ENTRIES + 1) { "entry $it" }) }
        assertFalse(file(Target.MEMORY).exists())
        // § inside a line is only a character.
        assertEquals(listOf("§ 기호는 구분선"), memory.save(Target.MEMORY, listOf("§ 기호는 구분선")).memory.entries)
    }

    @Test
    fun aListMustFitHermesLimitUnlessItOnlyGotShorter() {
        val long = "가".repeat(700)
        val refusal = refused(Problem.TOO_LONG) { memory.save(Target.USER, listOf(long, long + "나")) }
        assertEquals(1375, refusal.limit)
        assertEquals(700 + 3 + 701, refusal.size)
        assertFalse(file(Target.USER).exists())
        // A file that grew past the limit by other means can always be trimmed, though not yet under the limit.
        file(Target.USER).apply { parentFile!!.mkdirs() }.writeText(listOf(long, long + "나", long + "다").joinToString("\n§\n"))
        assertEquals(2, memory.save(Target.USER, listOf(long, long + "나")).user.entries.size)
        refused(Problem.TOO_LONG) { memory.save(Target.USER, listOf(long, long + "나라")) }
    }

    @Test
    fun nothingIsChangedWhileATurnRuns() {
        memory.save(Target.MEMORY, listOf("남는 기억"))
        File(home, "state.db").writeText("sessions")
        busy = true
        refused(Problem.BUSY) { memory.save(Target.MEMORY, emptyList()) }
        refused(Problem.BUSY) { memory.forget() }
        assertEquals(listOf("남는 기억"), memory.read().memory.entries)
        assertTrue(File(home, "state.db").exists())
    }

    @Test
    fun theRecordsOfTheTurnsAreDeletedWholeAndTheMemoriesStay() {
        memory.save(Target.MEMORY, listOf("남는 기억"))
        for (name in listOf("state.db", "state.db-wal", "state.db-shm")) File(home, name).writeText("12345")
        File(home, "sessions/2026/a.json").apply { parentFile!!.mkdirs() }.writeText("1234567")
        File(home, "sessions/b.json").writeText("123")
        File(home, "SOUL.md").writeText("identity")
        assertEquals(AgentMemory.Records(5, 25), memory.read().records)
        val view = memory.forget()
        assertEquals(AgentMemory.Records(0, 0), view.records)
        assertEquals(listOf("남는 기억"), view.memory.entries)
        assertEquals(listOf("SOUL.md", "memories", "sessions"), home.list()!!.sorted())
        assertEquals(emptyList<String>(), File(home, "sessions").list()!!.toList())
        // Nothing to delete is no failure.
        assertEquals(AgentMemory.Records(0, 0), memory.forget().records)
    }
}
