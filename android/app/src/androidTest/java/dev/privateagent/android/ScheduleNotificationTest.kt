package dev.privateagent.android

import android.app.NotificationManager
import android.os.ParcelFileDescriptor
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.By
import androidx.test.uiautomator.UiDevice
import androidx.test.uiautomator.Until
import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.MessageRole
import dev.privateagent.android.schedule.Notifications
import dev.privateagent.android.ui.Tab
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID

/**
 * A scheduled capture's notification, tapped in the notification shade: the app comes to the front on its browser and
 * runs that schedule's capture there, as a card in the chat. The device is in airplane mode meanwhile, so the capture
 * reaches no site and its card ends with the reason; the capture itself is BrowserControllerTest's. The schedule goes
 * afterwards; its card stays in the chat.
 */
@RunWith(AndroidJUnit4::class)
class ScheduleNotificationTest {
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
            delay(300)
        }
        throw AssertionError("timed out waiting for $what")
    }

    @Test
    fun aTappedReminderRunsItsCaptureInTheBrowser(): Unit = runBlocking {
        shell("pm grant ${context.packageName} android.permission.POST_NOTIFICATIONS")
        val query = "알림 기록 확인 " + UUID.randomUUID().toString().take(8)
        val id = graph.schedules.add("google", query, "09:00", ScheduleDays.DAILY)
        val device = UiDevice.getInstance(instrumentation)
        try {
            shell("cmd connectivity airplane-mode enable")
            graph.notifications.showScheduled(graph.schedules.get(id)!!)
            device.openNotification()
            val reminder = device.wait(Until.findObject(By.textContains(query)), 15_000)
            assertNotNull("the reminder in the notification shade", reminder)
            reminder.click()

            val card = waitFor("the schedule's capture card", 60_000) {
                graph.messages.all().lastOrNull { message ->
                    message.role == MessageRole.ACTION &&
                        JSONObject(message.args ?: "{}").optLong("schedule_id") == id &&
                        message.state in setOf(ActionState.DONE, ActionState.FAILED)
                }
            }
            // The capture ran in the browser, in front, and told why it could not open the page.
            assertEquals(Tab.BROWSER, graph.navigator.tab.value)
            assertEquals(JSONObject(card.args!!).getString("query"), query)
            assertEquals(ActionState.FAILED, card.state)
            val reasons = listOf(R.string.result_load_failed, R.string.result_timeout).map(context::getString)
            assertTrue(card.result, card.result in reasons)
            // The tapped notification is gone.
            val notifications = context.getSystemService(NotificationManager::class.java)
            assertTrue(notifications.activeNotifications.none { it.id == Notifications.id(id) })
        } finally {
            shell("cmd connectivity airplane-mode disable")
            graph.forgetSchedule(id)
            context.getSystemService(NotificationManager::class.java).cancel(Notifications.id(id))
            device.pressHome()
        }
    }
}
