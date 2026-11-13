package dev.privateagent.android

import android.os.ParcelFileDescriptor
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkManager
import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.schedule.ScheduleManager
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Instant
import java.time.LocalTime
import java.time.ZoneId
import kotlin.math.abs

/**
 * A change of the device's time zone re-times the scheduled reminders (ClockChangeReceiver): the next one comes at the
 * schedule's time on the new zone's clock, not at the old zone's. The zone is changed the way the system changes it
 * (the shell's alarm command, which sends the broadcast), and put back afterwards.
 */
@RunWith(AndroidJUnit4::class)
class ScheduleTimeZoneTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val graph = (context.applicationContext as PrivateAgentApp).graph

    private fun shell(command: String): String =
        ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).use {
            it.readBytes().toString(Charsets.UTF_8).trim()
        }

    private suspend fun <T : Any> waitFor(what: String, timeoutMs: Long, value: suspend () -> T?): T {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            value()?.let { return it }
            delay(200)
        }
        throw AssertionError("timed out waiting for $what")
    }

    /** The schedule's next run once it is at 09:00 in [zone]. */
    private suspend fun nextRunAtNineIn(zone: ZoneId, id: Long): Long = waitFor("09:00 in $zone", 20_000) {
        graph.schedules.get(id)?.nextRunAt?.takeIf {
            Instant.ofEpochMilli(it).atZone(zone).toLocalTime() == LocalTime.of(9, 0)
        }
    }

    @Test
    fun aChangeOfTimeZoneRetimesTheNextReminder() = runBlocking {
        val original = shell("getprop persist.sys.timezone").ifEmpty { ZoneId.systemDefault().id }
        // A zone far from the device's: 09:00 there is another moment than 09:00 here.
        val target = if (original == "Pacific/Kiritimati") "Pacific/Pago_Pago" else "Pacific/Kiritimati"
        val row = graph.scheduler.create("coupang", "시간대 확인", "09:00", ScheduleDays.DAILY)
        try {
            nextRunAtNineIn(ZoneId.of(original), row.id)
            shell("cmd alarm set-timezone $target")
            val next = nextRunAtNineIn(ZoneId.of(target), row.id)
            assertTrue(next > System.currentTimeMillis())
            // The reminder itself waits for that moment, not only the time the settings show.
            val job = WorkManager.getInstance(context).getWorkInfosForUniqueWork(ScheduleManager.name(row.id)).get()
                .single { !it.state.isFinished }
            assertTrue("${job.nextScheduleTimeMillis} != $next", abs(job.nextScheduleTimeMillis - next) < 60_000)
        } finally {
            shell("cmd alarm set-timezone $original")
            // Back in the device's zone before the schedule goes, so no re-timing comes after it.
            runCatching { nextRunAtNineIn(ZoneId.of(original), row.id) }
            graph.scheduler.delete(row.id)
        }
        assertEquals(original, shell("getprop persist.sys.timezone"))
    }
}
