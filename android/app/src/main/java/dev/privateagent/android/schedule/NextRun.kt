package dev.privateagent.android.schedule

import dev.privateagent.android.agent.ScheduleDays
import java.time.DayOfWeek
import java.time.LocalTime
import java.time.ZonedDateTime

object NextRun {
    /**
     * The first time after [after] (strictly later) at [time] on a day [days] allows, in [after]'s time zone. A time
     * that a daylight saving change skips that day moves forward by the gap, as ZonedDateTime resolves it.
     */
    fun after(after: ZonedDateTime, time: LocalTime, days: ScheduleDays): ZonedDateTime {
        var date = after.toLocalDate()
        repeat(9) {
            val candidate = ZonedDateTime.of(date, time, after.zone)
            if (candidate.isAfter(after) && allows(days, date.dayOfWeek)) return candidate
            date = date.plusDays(1)
        }
        error("No run within nine days")
    }

    fun allows(days: ScheduleDays, day: DayOfWeek): Boolean = when (days) {
        ScheduleDays.DAILY -> true
        ScheduleDays.WEEKDAYS -> day != DayOfWeek.SATURDAY && day != DayOfWeek.SUNDAY
    }

    /** HH:MM as a LocalTime, or null. */
    fun parse(time: String): LocalTime? {
        val parts = time.split(':')
        if (parts.size != 2) return null
        val hour = parts[0].toIntOrNull() ?: return null
        val minute = parts[1].toIntOrNull() ?: return null
        if (hour !in 0..23 || minute !in 0..59) return null
        return LocalTime.of(hour, minute)
    }
}
