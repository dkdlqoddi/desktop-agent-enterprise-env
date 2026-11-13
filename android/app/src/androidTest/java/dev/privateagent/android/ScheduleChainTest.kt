package dev.privateagent.android

import android.app.NotificationManager
import android.os.ParcelFileDescriptor
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.work.WorkInfo
import androidx.work.WorkManager
import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.schedule.Notifications
import dev.privateagent.android.schedule.ScheduleManager
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.time.Duration
import java.time.ZonedDateTime
import java.time.temporal.ChronoUnit
import kotlin.math.abs

/**
 * The chain of a schedule's reminders on the device's own WorkManager: the reminder comes at its time with its
 * notification and, before it ends, queues the next one, the same time the next day. A schedule for the coming
 * minute is made and waited for (up to two minutes); it and its notification go afterwards.
 */
@RunWith(AndroidJUnit4::class)
class ScheduleChainTest {
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
            delay(500)
        }
        throw AssertionError("timed out waiting for $what")
    }

    @Test
    fun aReminderComesAtItsTimeAndQueuesTheNextDays(): Unit = runBlocking {
        // Android 13 and later post a reminder only with the user's permission.
        shell("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS")
        val now = ZonedDateTime.now()
        // The coming minute, unless it starts too soon to be queued in time.
        var at = now.plusMinutes(1).truncatedTo(ChronoUnit.MINUTES)
        if (Duration.between(now, at).seconds < 10) at = at.plusMinutes(1)
        val time = "%02d:%02d".format(at.hour, at.minute)
        val row = graph.scheduler.create("google", "예약 연쇄 확인", time, ScheduleDays.DAILY)
        val notifications = context.getSystemService(NotificationManager::class.java)
        try {
            assertEquals(at.toInstant().toEpochMilli(), row.nextRunAt)
            waitFor("the reminder's notification", 150_000) {
                notifications.activeNotifications.firstOrNull { it.id == Notifications.id(row.id) }
            }
            // The reminder came at its time, not before it.
            assertTrue(System.currentTimeMillis() >= at.toInstant().toEpochMilli())
            val tomorrow = at.plusDays(1).toInstant().toEpochMilli()
            waitFor("the next day's run", 30_000) { graph.schedules.get(row.id)?.nextRunAt?.takeIf { it == tomorrow } }
            // The reminder that ran ended well, and the next waits in the same chain for that time.
            val chain = waitFor("the queued reminder", 30_000) {
                WorkManager.getInstance(context).getWorkInfosForUniqueWork(ScheduleManager.name(row.id)).get()
                    .takeIf { infos -> infos.any { it.state == WorkInfo.State.ENQUEUED } }
            }
            assertTrue(chain.any { it.state == WorkInfo.State.SUCCEEDED })
            val queued = chain.single { it.state == WorkInfo.State.ENQUEUED }
            val off = abs(queued.nextScheduleTimeMillis - tomorrow)
            assertTrue("${queued.nextScheduleTimeMillis} != $tomorrow", off < 60_000)
        } finally {
            graph.forgetSchedule(row.id)
            notifications.cancel(Notifications.id(row.id))
        }
    }
}
