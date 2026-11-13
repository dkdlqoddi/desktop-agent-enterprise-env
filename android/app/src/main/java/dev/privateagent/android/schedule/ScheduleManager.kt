package dev.privateagent.android.schedule

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import dev.privateagent.android.PrivateAgentApp
import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.data.ScheduleRow
import dev.privateagent.android.data.ScheduleStore
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.time.Duration
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.concurrent.TimeUnit

/**
 * The scheduled captures: each enabled schedule has one pending WorkManager job (unique per schedule) that posts a
 * notification at its time and queues the next one. The capture itself runs only when the user taps the notification,
 * in the visible browser; no hidden browser runs in the background. WorkManager keeps the jobs across restarts; the
 * system may deliver them a few minutes late (Doze).
 */
class ScheduleManager(private val context: Context, private val store: ScheduleStore) {
    private val work get() = WorkManager.getInstance(context)

    suspend fun create(site: String, query: String, time: String, days: ScheduleDays): ScheduleRow {
        val id = store.add(site, query, time, days)
        val row = store.get(id)!!
        enqueue(row, ExistingWorkPolicy.REPLACE)
        return store.get(id)!!
    }

    suspend fun setEnabled(id: Long, enabled: Boolean) {
        store.setEnabled(id, enabled)
        if (enabled) {
            store.get(id)?.let { enqueue(it, ExistingWorkPolicy.REPLACE) }
        } else {
            work.cancelUniqueWork(name(id))
            store.setNextRun(id, null)
        }
    }

    suspend fun delete(id: Long) {
        work.cancelUniqueWork(name(id))
        store.delete(id)
    }

    /** At app start: queues each enabled schedule that has no pending job (one that is pending keeps its time). */
    suspend fun restoreAll() {
        for (row in store.all()) {
            if (!row.enabled) continue
            val pending = withContext(Dispatchers.IO) {
                runCatching { work.getWorkInfosForUniqueWork(name(row.id)).get() }.getOrNull().orEmpty()
                    .any { !it.state.isFinished }
            }
            if (!pending) enqueue(row, ExistingWorkPolicy.REPLACE)
        }
    }

    /** Queues the run after this one, from within the running job: appended, so the running job is not stopped. */
    suspend fun enqueueNext(row: ScheduleRow) {
        enqueue(row, ExistingWorkPolicy.APPEND_OR_REPLACE, ZonedDateTime.now().plusMinutes(1))
    }

    /**
     * After the device's time zone or clock changed ([ClockChangeReceiver]): every enabled schedule is queued again
     * for its time in [zone], replacing the reminder queued before, which would come at the old zone's time.
     */
    suspend fun retimeAll(zone: ZoneId) {
        for ((id, next) in NextRun.retimed(store.all(), Instant.now(), zone)) {
            queue(id, ExistingWorkPolicy.REPLACE, next)
        }
    }

    private suspend fun enqueue(row: ScheduleRow, policy: ExistingWorkPolicy, after: ZonedDateTime = ZonedDateTime.now()) {
        val time = NextRun.parse(row.time) ?: return
        queue(row.id, policy, NextRun.after(after, time, row.days))
    }

    private suspend fun queue(id: Long, policy: ExistingWorkPolicy, next: ZonedDateTime) {
        val delay = Duration.between(Instant.now(), next.toInstant()).toMillis().coerceAtLeast(0)
        val request = OneTimeWorkRequestBuilder<CaptureReminderWorker>()
            .setInitialDelay(delay, TimeUnit.MILLISECONDS)
            .setInputData(workDataOf(KEY_SCHEDULE_ID to id))
            .addTag(TAG)
            .build()
        work.enqueueUniqueWork(name(id), policy, request)
        store.setNextRun(id, next.toInstant().toEpochMilli())
    }

    companion object {
        const val KEY_SCHEDULE_ID = "schedule_id"
        private const val TAG = "scheduled-capture"

        fun name(id: Long) = "scheduled-capture-$id"
    }
}

/** Posts the notification of a scheduled capture and queues the next one. */
class CaptureReminderWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val graph = (applicationContext as PrivateAgentApp).graph
        val id = inputData.getLong(ScheduleManager.KEY_SCHEDULE_ID, -1)
        val row = graph.schedules.get(id) ?: return Result.success()
        if (!row.enabled) return Result.success()
        graph.notifications.showScheduled(row)
        graph.scheduler.enqueueNext(row)
        return Result.success()
    }
}
