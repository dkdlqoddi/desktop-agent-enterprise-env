package dev.privateagent.android.schedule

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import dev.privateagent.android.PrivateAgentApp
import dev.privateagent.android.data.StoreException
import dev.privateagent.android.data.TrashStore
import java.util.concurrent.TimeUnit

/**
 * The app's upkeep: the trash deletes for good what has waited TrashStore.DAYS days. It runs at each start of the app
 * and once a day while the app is installed ([UpkeepWorker]), so what the user deleted is gone in time even when the
 * app is not opened.
 */
class Upkeep(private val context: Context, private val trash: TrashStore) {
    /** What one run did: the entries of the trash deleted for good. */
    data class Done(val expired: Int)

    suspend fun run(now: Long = System.currentTimeMillis()): Done = Done(expired = trash.expire(now))

    /** Queues the daily run, once: a run already queued keeps its time. */
    fun schedule() {
        val request = PeriodicWorkRequestBuilder<UpkeepWorker>(1, TimeUnit.DAYS).build()
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
