package dev.privateagent.android

import android.os.ParcelFileDescriptor
import androidx.core.view.WindowCompat
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/**
 * The dark theme, turned on and off while the app is open: the screen stays (it is not made anew), and the system
 * bars' icons follow the theme, light on the dark one and dark on the light one. The device's own setting is put back.
 */
@RunWith(AndroidJUnit4::class)
class DarkThemeTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()

    private fun shell(command: String): String =
        ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).use {
            it.readBytes().toString(Charsets.UTF_8).trim()
        }

    /** Whether the status bar's icons are the dark ones, for a light background, once they are [expected]. */
    private suspend fun lightBars(scenario: ActivityScenario<MainActivity>, expected: Boolean) {
        val end = System.currentTimeMillis() + 20_000
        var light: Boolean? = null
        while (System.currentTimeMillis() < end) {
            scenario.onActivity {
                light = WindowCompat.getInsetsController(it.window, it.window.decorView).isAppearanceLightStatusBars
            }
            if (light == expected) return
            delay(200)
        }
        throw AssertionError("the status bar's icons are for a ${if (light == true) "light" else "dark"} background")
    }

    @Test
    fun theSystemBarsFollowTheThemeWhileTheAppIsOpen(): Unit = runBlocking {
        val before = shell("cmd uimode night").substringAfter(':').trim()
        shell("cmd uimode night no")
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            lightBars(scenario, expected = true)
            var screen: MainActivity? = null
            scenario.onActivity { screen = it }
            shell("cmd uimode night yes")
            lightBars(scenario, expected = false)
            shell("cmd uimode night no")
            lightBars(scenario, expected = true)
            // The same screen throughout: the theme changed in place.
            scenario.onActivity { assertSame(screen, it) }
            assertTrue(screen != null)
        } finally {
            shell("cmd uimode night ${before.ifEmpty { "no" }}")
            scenario.close()
        }
    }
}
