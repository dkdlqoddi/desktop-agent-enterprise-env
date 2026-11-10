package dev.privateagent.android

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import dev.privateagent.android.schedule.Notifications
import dev.privateagent.android.ui.AppRoot
import dev.privateagent.android.ui.theme.PrivateAgentTheme

class MainActivity : ComponentActivity() {
    private val graph get() = (application as PrivateAgentApp).graph

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        graph.browser.attach(this)
        setContent {
            PrivateAgentTheme {
                AppRoot(graph)
            }
        }
        if (savedInstanceState == null) handle(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handle(intent)
    }

    override fun onResume() {
        super.onResume()
        graph.browser.resume()
    }

    override fun onPause() {
        graph.browser.pause()
        super.onPause()
    }

    override fun onDestroy() {
        graph.browser.detach(this)
        super.onDestroy()
    }

    /** A tapped notification of a scheduled capture; only the app's own (it carries the install's launch key). */
    private fun handle(intent: Intent?) {
        if (intent?.action != Notifications.ACTION_RUN_SCHEDULE) return
        val id = intent.getLongExtra(Notifications.EXTRA_SCHEDULE_ID, -1)
        val key = intent.getStringExtra(Notifications.EXTRA_LAUNCH_KEY)
        intent.action = null
        if (id <= 0 || key == null || key != graph.settings.launchSecret) return
        graph.agent.runSchedule(id)
    }
}
