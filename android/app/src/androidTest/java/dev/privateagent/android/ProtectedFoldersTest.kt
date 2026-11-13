package dev.privateagent.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.agent.ProtectedZones
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * What is closed to the chat's agent, on the device's own layout of an app's data: the database, the settings, the
 * browser's profile and the caches are closed, by name where they are not there yet, and the agent's home is open.
 */
@RunWith(AndroidJUnit4::class)
class ProtectedFoldersTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val graph = (context.applicationContext as PrivateAgentApp).graph

    private fun closed(zones: List<String>, file: File): Boolean {
        val path = file.canonicalPath
        return zones.any { path == it || path.startsWith("$it/") }
    }

    @Test
    fun theAppsDataIsClosedAroundTheAgentsHome() {
        val home = graph.hermesRuntime.home.apply { mkdirs() }
        val roots = listOf(context.dataDir, context.createDeviceProtectedStorageContext().dataDir)
        // The folder that holds the agent's home is closed entry by entry, around the home: what is in it now.
        val beside = File(context.filesDir, "protected-folders-test").apply { writeText("x") }
        val zones = try {
            ProtectedZones.ofApp(roots, home)
        } finally {
            beside.delete()
        }
        val own = listOf(
            context.getDatabasePath("private-agent.db"),
            File(context.dataDir, "shared_prefs/settings.xml"),
            File(context.dataDir, "shared_prefs/agent.xml"),
            File(context.dataDir, "app_webview/Default/Cookies"),
            File(context.cacheDir, "exports/records.csv"),
            File(context.codeCacheDir, "hermes-runtime"),
            context.noBackupFilesDir,
            beside,
            context.createDeviceProtectedStorageContext().dataDir,
        )
        for (file in own) assertTrue(file.path, closed(zones, file))
        // The agent's own: its home with all that is in it, and so the folders that lead there are no zones themselves.
        for (file in listOf(home, File(home, "memories/MEMORY.md"), File(home, "state.db"), File(home, "skills"))) {
            assertFalse(file.path, closed(zones, file))
        }
        assertFalse(context.filesDir.canonicalPath in zones)
        assertFalse(context.dataDir.canonicalPath in zones)
    }
}
