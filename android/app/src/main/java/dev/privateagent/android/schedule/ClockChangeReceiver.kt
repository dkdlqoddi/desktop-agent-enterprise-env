package dev.privateagent.android.schedule

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import dev.privateagent.android.PrivateAgentApp
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Re-times the scheduled reminders when the device's time zone or clock is changed. A reminder waits for a span
 * worked out when it was queued, so after a change of zone the next one would come at the schedule's time in the old
 * zone. Declared in the manifest, not exported: only the system sends these broadcasts, and it starts the app for them.
 */
class ClockChangeReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_TIMEZONE_CHANGED && intent.action != Intent.ACTION_TIME_CHANGED) return
        // The new zone as the broadcast names it: the process's cached default may not have caught up yet.
        val zone = NextRun.zone(intent.getStringExtra(EXTRA_TIME_ZONE))
        val graph = (context.applicationContext as PrivateAgentApp).graph
        val pending: PendingResult? = goAsync()
        scope.launch {
            try {
                graph.scheduler.retimeAll(zone)
            } catch (_: Exception) {
                // The database could not be read or written (no space left): the reminders keep the times they were
                // queued with, as before, and each one that comes queues the run after it in the new zone.
            } finally {
                pending?.finish()
            }
        }
    }

    private companion object {
        /** Intent.EXTRA_TIMEZONE, named in the SDK only from Android 11; the system sent it before that too. */
        const val EXTRA_TIME_ZONE = "time-zone"

        val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    }
}
