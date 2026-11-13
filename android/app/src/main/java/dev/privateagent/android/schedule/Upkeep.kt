package dev.privateagent.android.schedule

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import dev.privateagent.android.PrivateAgentApp
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.data.StoreException
import dev.privateagent.android.data.TrashStore
import java.util.concurrent.TimeUnit

/**
 * The app's upkeep, as the desktop's retention policy (Service.retain): the recorded pages older than the days the
 * user keeps them ([keptDays], 0 while they are kept until deleted) go to the trash, marked as moved by it, and the
 * trash deletes for good what has waited TrashStore.DAYS days. It runs at each start of the app and once a day while
 * the app is installed ([UpkeepWorker]), so that both happen in time even when the app is not opened.
 */
class Upkeep(
    private val context: Context,
    private val records: RecordStore,
    private val trash: TrashStore,
    private val keptDays: () -> Int,
) {
    /** What one run did: the pages moved to the trash, and the entries of the trash deleted for good. */
    data class Done(val retired: Int, val expired: Int)

    suspend fun run(now: Long = System.currentTimeMillis()): Done {
        val days = keptDays()
        val retired = if (days > 0) records.retire(now - days * TrashStore.DAY_MS, now) else 0
        return Done(retired = retired, expired = trash.expire(now))
    }

    /** Queues the daily run, once: a run already queued keeps its time. The app's start has just run it. */
    fun schedule() {
        val request = PeriodicWorkRequestBuilder<UpkeepWorker>(1, TimeUnit.DAYS).setInitialDelay(1, TimeUnit.DAYS).build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.KEEP, request)
    }

    private companion object {
        const val NAME = "upkeep"
    }
}

/** The daily run of the [Upkeep]. */
class UpkeepWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result = try {
        (applicationContext as PrivateAgentApp).graph.upkeep.run()
        Result.success()
    } catch (_: StoreException) {
        // The database cannot be written now (no space left): WorkManager tries again later.
        Result.retry()
    }
}
