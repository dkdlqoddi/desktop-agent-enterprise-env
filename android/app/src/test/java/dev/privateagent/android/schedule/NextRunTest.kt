package dev.privateagent.android.schedule

import dev.privateagent.android.agent.ScheduleDays
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.time.LocalTime
import java.time.ZoneId
import java.time.ZonedDateTime

class NextRunTest {
    private val seoul = ZoneId.of("Asia/Seoul")
    private val nine = LocalTime.of(9, 0)

    private fun at(text: String, zone: ZoneId = seoul) = ZonedDateTime.of(java.time.LocalDateTime.parse(text), zone)

    @Test
    fun dailyBeforeTheTimeIsToday() {
        // 2026-11-11 is a Wednesday.
        assertEquals(at("2026-11-11T09:00"), NextRun.after(at("2026-11-11T08:00"), nine, ScheduleDays.DAILY))
    }

    @Test
    fun dailyAtOrAfterTheTimeIsTomorrow() {
        assertEquals(at("2026-11-12T09:00"), NextRun.after(at("2026-11-11T09:00"), nine, ScheduleDays.DAILY))
        assertEquals(at("2026-11-12T09:00"), NextRun.after(at("2026-11-11T21:30"), nine, ScheduleDays.DAILY))
    }

    @Test
    fun dailyRunsOnWeekendsToo() {
        assertEquals(at("2026-11-14T09:00"), NextRun.after(at("2026-11-13T10:00"), nine, ScheduleDays.DAILY))
    }

    @Test
    fun weekdaysSkipTheWeekend() {
        // Friday after the time, Saturday and Sunday all move to Monday 2026-11-16.
        assertEquals(at("2026-11-16T09:00"), NextRun.after(at("2026-11-13T09:30"), nine, ScheduleDays.WEEKDAYS))
        assertEquals(at("2026-11-16T09:00"), NextRun.after(at("2026-11-14T08:00"), nine, ScheduleDays.WEEKDAYS))
        assertEquals(at("2026-11-16T09:00"), NextRun.after(at("2026-11-15T23:59"), nine, ScheduleDays.WEEKDAYS))
        assertEquals(at("2026-11-13T09:00"), NextRun.after(at("2026-11-13T08:59"), nine, ScheduleDays.WEEKDAYS))
    }

    @Test
    fun aTimeSkippedByDaylightSavingMovesForward() {
        val newYork = ZoneId.of("America/New_York")
        // Clocks go from 02:00 to 03:00 on 2026-03-08: 02:30 does not exist that day.
        val next = NextRun.after(at("2026-03-08T01:00", newYork), LocalTime.of(2, 30), ScheduleDays.DAILY)
        assertEquals(LocalTime.of(3, 30), next.toLocalTime())
        assertEquals(java.time.LocalDate.of(2026, 3, 8), next.toLocalDate())
    }

    @Test
    fun timesAreRead() {
        assertEquals(LocalTime.of(9, 0), NextRun.parse("09:00"))
        assertEquals(LocalTime.of(23, 59), NextRun.parse("23:59"))
        assertNull(NextRun.parse("24:00"))
        assertNull(NextRun.parse("9시"))
        assertNull(NextRun.parse(""))
    }
}
