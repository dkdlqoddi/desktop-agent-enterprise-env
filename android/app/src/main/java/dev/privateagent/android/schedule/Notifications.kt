package dev.privateagent.android.schedule

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import dev.privateagent.android.MainActivity
import dev.privateagent.android.R
import dev.privateagent.android.data.ScheduleRow
import dev.privateagent.android.security.SettingsStore
import dev.privateagent.android.web.Sites

class Notifications(
    private val context: Context,
    private val settings: SettingsStore,
    private val sites: () -> Sites,
) {
    fun ensureChannel() {
        val manager = context.getSystemService(NotificationManager::class.java) ?: return
        val channel = NotificationChannel(
            CHANNEL,
            context.getString(R.string.notif_channel_name),
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply { description = context.getString(R.string.notif_channel_desc) }
        manager.createNotificationChannel(channel)
    }

    /** Whether the app may post notifications (Android 13+ asks the user for POST_NOTIFICATIONS). */
    fun canPost(): Boolean {
        val granted = Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        return granted && NotificationManagerCompat.from(context).areNotificationsEnabled()
    }

    @SuppressLint("MissingPermission") // canPost() checks it.
    fun showScheduled(row: ScheduleRow) {
        if (!canPost()) return
        val siteName = sites().byKey(row.site)?.name ?: row.site
        val intent = Intent(context, MainActivity::class.java).apply {
            action = ACTION_RUN_SCHEDULE
            putExtra(EXTRA_SCHEDULE_ID, row.id)
            putExtra(EXTRA_LAUNCH_KEY, settings.launchSecret)
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
        }
        val pending = PendingIntent.getActivity(
            context,
            row.id.toInt(),
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(context.getString(R.string.notif_title, siteName, row.query))
            .setContentText(context.getString(R.string.notif_body))
            .setContentIntent(pending)
            .setAutoCancel(true)
            .setCategory(NotificationCompat.CATEGORY_REMINDER)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .build()
        NotificationManagerCompat.from(context).notify(id(row.id), notification)
    }

    companion object {
        const val CHANNEL = "scheduled_capture"
        const val ACTION_RUN_SCHEDULE = "dev.privateagent.android.action.RUN_SCHEDULE"
        const val EXTRA_SCHEDULE_ID = "schedule_id"
        const val EXTRA_LAUNCH_KEY = "launch_key"
        private const val NOTIFICATION_BASE = 1000

        /** The notification of the schedule [scheduleId]: one per schedule, the latest reminder in place of the last. */
        fun id(scheduleId: Long): Int = NOTIFICATION_BASE + scheduleId.toInt()
    }
}
