package dev.privateagent.android

import android.content.Intent
import android.content.res.Configuration
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import dev.privateagent.android.schedule.Notifications
import dev.privateagent.android.ui.AppRoot
import dev.privateagent.android.ui.Tab
import dev.privateagent.android.ui.theme.PrivateAgentTheme

/**
 * The app's one screen. It handles configuration changes in place (the manifest's configChanges), so the browser's
 * page stays as it is; what it saves is for a screen made anew after the system ended the app in the background: the
 * tab in front and the browser's page and history.
 */
class MainActivity : ComponentActivity() {
    private val graph get() = (application as PrivateAgentApp).graph

    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        graph.browser.attach(this, savedInstanceState?.getBundle(KEY_BROWSER))
        savedInstanceState?.getString(KEY_TAB)?.let { name -> Tab.entries.firstOrNull { it.name == name } }
            ?.let(graph.navigator::show)
        setContent {
            PrivateAgentTheme {
                AppRoot(graph)
            }
        }
        if (savedInstanceState == null) handle(intent)
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString(KEY_TAB, graph.navigator.tab.value.name)
        outState.putBundle(KEY_BROWSER, Bundle().also(graph.browser::saveState))
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        // The screen stays when the dark theme is turned on or off: the system bars' icons are made to follow it here,
        // or they would stay dark on the dark theme (or light on the light one) until the app is opened again.
        enableEdgeToEdge()
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

    private companion object {
        const val KEY_TAB = "tab"
        const val KEY_BROWSER = "browser"
    }
}
