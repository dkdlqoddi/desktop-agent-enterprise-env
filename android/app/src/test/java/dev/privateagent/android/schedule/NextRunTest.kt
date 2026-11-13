package dev.privateagent.android.schedule

import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.data.ScheduleRow
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

    private fun row(id: Long, time: String, days: ScheduleDays = ScheduleDays.DAILY, enabled: Boolean = true) =
        ScheduleRow(id, "coupang", "노트북", time, days, enabled, createdAt = 0, nextRunAt = null)

    @Test
    fun aChangeOfTimeZoneRetimesTheNextRunToTheNewZonesClock() {
        val paris = ZoneId.of("Europe/Paris")
        // Queued at 08:00 in Seoul, the 09:00 reminder waits until 09:00 Seoul time (01:00 in Paris).
        val queued = NextRun.after(at("2026-11-11T08:00"), nine, ScheduleDays.DAILY)
        assertEquals(at("2026-11-11T01:00", paris).toInstant(), queued.toInstant())
        // The phone arrives in Paris at 08:30 Seoul time (00:30 in Paris): the next run is 09:00 in Paris.
        val now = at("2026-11-11T08:30").toInstant()
        val retimed = NextRun.retimed(listOf(row(1, "09:00")), now, paris)
        assertEquals(mapOf(1L to at("2026-11-11T09:00", paris)), retimed)
    }

    @Test
    fun retimingKeepsTheDaysInTheNewZone() {
        val losAngeles = ZoneId.of("America/Los_Angeles")
        // Saturday 01:00 in Seoul is still Friday 08:00 in Los Angeles: a weekday reminder at 09:00 comes that Friday.
        val weekdays = listOf(row(3, "09:00", ScheduleDays.WEEKDAYS))
        val retimed = NextRun.retimed(weekdays, at("2026-11-14T01:00").toInstant(), losAngeles)
        assertEquals(at("2026-11-13T09:00", losAngeles), retimed[3L])
        // Saturday 10:00 in Seoul is Friday 17:00 there: the next weekday is Monday.
        val later = NextRun.retimed(weekdays, at("2026-11-14T10:00").toInstant(), losAngeles)
        assertEquals(at("2026-11-16T09:00", losAngeles), later[3L])
    }

    @Test
    fun retimingLeavesOutTurnedOffSchedulesAndUnreadableTimes() {
        val rows = listOf(row(1, "09:00"), row(2, "10:00", enabled = false), row(3, "9시"), row(4, "23:30"))
        val retimed = NextRun.retimed(rows, at("2026-11-11T12:00").toInstant(), seoul)
        assertEquals(setOf(1L, 4L), retimed.keys)
        assertEquals(at("2026-11-12T09:00"), retimed[1L])
        assertEquals(at("2026-11-11T23:30"), retimed[4L])
    }

    @Test
    fun theNewZoneIsTheOneTheChangeNamesOrTheDevicesOwn() {
        assertEquals(ZoneId.of("Europe/Paris"), NextRun.zone("Europe/Paris"))
        for (unreadable in listOf(null, "", " ", "Not/AZone", "+99:00")) {
            assertEquals(unreadable.toString(), ZoneId.systemDefault(), NextRun.zone(unreadable))
        }
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
