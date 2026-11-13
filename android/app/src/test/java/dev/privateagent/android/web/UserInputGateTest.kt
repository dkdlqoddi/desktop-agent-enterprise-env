package dev.privateagent.android.web

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** A click report counts only right after an input of the user's, and each input counts once. */
class UserInputGateTest {
    @Test
    fun aReportWithoutAnInputDoesNotCount() {
        val gate = UserInputGate()
        assertFalse(gate.take(10_000))
    }

    @Test
    fun aReportRightAfterAnInputCountsOnce() {
        val gate = UserInputGate()
        gate.record(10_000)
        assertTrue(gate.take(10_150))
        // The same tap does not let a second report through, a page's own script included.
        assertFalse(gate.take(10_200))
    }

    @Test
    fun anInputOlderThanTheWindowNoLongerCounts() {
        val gate = UserInputGate(windowMs = 2_000)
        gate.record(10_000)
        assertFalse(gate.take(12_001))
        // Gone for good, not only for that report.
        assertFalse(gate.take(10_500))
        gate.record(20_000)
        assertTrue(gate.take(22_000))
    }

    @Test
    fun aReportBeforeTheInputDoesNotCount() {
        val gate = UserInputGate()
        gate.record(10_000)
        assertFalse(gate.take(9_999))
        assertTrue(gate.take(10_000))
    }

    @Test
    fun twoQuickTapsLetTwoReportsThrough() {
        val gate = UserInputGate()
        gate.record(10_000)
        gate.record(10_300)
        assertTrue(gate.take(10_350))
        assertTrue(gate.take(10_400))
        assertFalse(gate.take(10_450))
    }

    @Test
    fun onlyTheLatestInputsAreKept() {
        val gate = UserInputGate()
        repeat(20) { gate.record(10_000L + it) }
        var taken = 0
        while (gate.take(10_100)) taken++
        assertTrue("$taken", taken in 1..8)
    }
}
