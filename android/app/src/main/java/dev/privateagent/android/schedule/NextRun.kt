package dev.privateagent.android.schedule

import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.data.ScheduleRow
import java.time.DateTimeException
import java.time.DayOfWeek
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId
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

    /**
     * When each enabled schedule of [rows] runs next, worked out again at [now] in [zone], by schedule id. A queued
     * reminder waits for a span worked out when it was queued: after the device's time zone changes, it would come
     * at the schedule's time in the old zone. A schedule whose time cannot be read is left out, as it was never queued.
     */
    fun retimed(rows: List<ScheduleRow>, now: Instant, zone: ZoneId): Map<Long, ZonedDateTime> {
        val at = now.atZone(zone)
        return rows.filter { it.enabled }
            .mapNotNull { row -> parse(row.time)?.let { time -> row.id to after(at, time, row.days) } }
            .toMap()
    }

    /** The time zone a change of zone names (the broadcast's "time-zone" extra), or the device's when it names none. */
    fun zone(id: String?): ZoneId {
        if (!id.isNullOrBlank()) {
            try {
                return ZoneId.of(id)
            } catch (_: DateTimeException) {
                // Not a zone this device knows: its own zone is the one that changed.
            }
        }
        return ZoneId.systemDefault()
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
