package dev.privateagent.android.agent

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.nio.file.Files

class ProtectedZonesTest {
    @get:Rule
    val folder = TemporaryFolder()

    private fun file(root: File, path: String): File = File(root, path).also {
        it.parentFile!!.mkdirs()
        it.writeText("x")
    }

    /** An app's data folder as Android lays it out, with the agent's home in files/hermes. */
    private fun appData(): Pair<File, File> {
        val data = folder.newFolder("data").canonicalFile
        file(data, "databases/private-agent.db")
        file(data, "shared_prefs/settings.xml")
        file(data, "app_webview/Default/Cookies")
        file(data, "cache/exports/records.csv")
        file(data, "files/other.txt")
        file(data, "files/hermes/memories/MEMORY.md")
        file(data, "files/hermes/state.db")
        return data to File(data, "files/hermes")
    }

    @Test
    fun theAppsOwnDataIsClosedAroundTheAgentsHome() {
        val (data, home) = appData()
        val zones = ProtectedZones.ofApp(listOf(data), home)
        // What is there, and by name what Android makes later (the folders this app has not used yet).
        val closed = listOf("app_webview", "cache", "code_cache", "databases", "files/other.txt", "no_backup", "shared_prefs")
        assertEquals(closed.map { File(data, it).path }, zones)
        // Neither the home, nor the folders that lead to it: closing those would close the home with them.
        for (open in listOf(home, home.parentFile!!, data)) assertTrue(open.path, open.path !in zones)
        assertTrue(zones.none { it.startsWith(home.path) })
    }

    @Test
    fun aFolderThatHoldsNothingOfTheAgentsIsClosedAsItIs() {
        val (data, home) = appData()
        val other = folder.newFolder("external").canonicalFile
        file(other, "a/b.txt")
        assertEquals(listOf(other.path), ProtectedZones.zones(listOf(other), listOf(home)))
        // A folder inside the home stays closed too, as it was named.
        val inside = File(home, "memories")
        assertEquals(listOf(inside.path), ProtectedZones.zones(listOf(inside), listOf(home)))
        // One that is not there yet is closed by its name: it may be made later.
        val later = File(data, "no_backup")
        assertEquals(listOf(later.path), ProtectedZones.zones(listOf(later), listOf(home)))
    }

    @Test
    fun aRootIsTakenByItsRealPathAndTheFileSystemsRootIsNeverAZone() {
        val (data, home) = appData()
        val link = File(folder.root, "link")
        Files.createSymbolicLink(link.toPath(), data.toPath())
        assertEquals(ProtectedZones.ofApp(listOf(data), home), ProtectedZones.ofApp(listOf(link), File(link, "files/hermes")))
        assertEquals(emptyList<String>(), ProtectedZones.zones(listOf(File("/")), listOf(home)))
        assertEquals(emptyList<String>(), ProtectedZones.zones(emptyList(), listOf(home)))
    }

    @Test
    fun noMoreZonesThanTheLimitAreKept() {
        val data = folder.newFolder("many").canonicalFile
        val home = File(data, "home").also { it.mkdirs() }
        repeat(ProtectedZones.MAX_ZONES + 20) { file(data, "f%04d".format(it)) }
        val zones = ProtectedZones.ofApp(listOf(data), home)
        assertEquals(ProtectedZones.MAX_ZONES, zones.size)
        // The folders known by name are taken first, so they are among those kept.
        for (name in listOf("databases", "shared_prefs", "app_webview")) assertTrue(name, File(data, name).path in zones)
    }
}
