package dev.privateagent.android.agent

import java.io.File
import java.io.IOException

/**
 * What the chat's agent is kept away from on the phone (the desktop's 보호 구역, agent_guard.py). The worker runs as
 * the app itself, so the app's own data is within its reach: the database of the conversation and the web records,
 * the settings with the sealed API Key and the commands allowed to run unasked, and the in-app browser's profile with
 * its cookies. All of that is closed to Hermes' tools that take a path (worker.py guard_files), and only the agent's
 * own home stays open. This is a second line, not a sandbox, and it cannot be turned off.
 *
 * A phone's agent has no terminal and no file tools, so there are no folders of the user's to add and no command
 * patterns to refuse, as the desktop has.
 */
object ProtectedZones {
    const val MAX_ZONES = 400

    /** How deep a protected folder is opened up to leave the agent's own folder reachable inside it. */
    const val DEPTH = 12

    /**
     * The folders Android makes in an app's data folder as the app comes to use them. They are closed by name, and
     * first: one may not be there yet when a turn starts (the browser's profile, before a page was ever opened), and
     * they are the ones to keep when there are more paths than [MAX_ZONES]. The folder that holds the agent's home
     * (files) cannot be closed whole: it is closed entry by entry around the home, so an entry made in it while a turn
     * runs is closed from the next turn on.
     */
    private val APP_FOLDERS = listOf("databases", "shared_prefs", "app_webview", "no_backup", "cache", "code_cache", "files")

    /** The app's own data folders ([roots]) around the agent's [home]: those known by name, then whatever else they hold. */
    fun ofApp(roots: List<File>, home: File): List<String> =
        zones(roots.flatMap { root -> APP_FOLDERS.map { File(root, it) } } + roots, listOf(home))

    /**
     * The protected paths: [roots], each as its real path, around what must stay reachable ([keep]). A root that
     * holds a kept folder is replaced by what lies in it beside the way to the kept folder; a root that holds none is
     * protected as it is. Sorted, so that the same folders give the same list.
     */
    fun zones(roots: List<File>, keep: List<File>): List<String> {
        val kept = keep.map(::real)
        val found = sortedSetOf<String>()

        fun add(path: String, depth: Int) {
            if (found.size >= MAX_ZONES) return
            if (kept.none { under(it, path) } || depth >= DEPTH) {
                found += path
                return
            }
            // A kept folder met on the way down is the agent's own: it stays open, with all that is in it.
            if (depth > 0 && path in kept) return
            val names = File(path).list()?.sorted() ?: return
            for (name in names) add(File(path, name).path, depth + 1)
        }

        for (root in roots) {
            val path = real(root)
            // The root of the file system is never a zone: everything would be.
            if (File(path).parent != null) add(path, 0)
        }
        return found.toList()
    }

    private fun real(file: File): String = try {
        file.canonicalPath
    } catch (_: IOException) {
        file.absolutePath
    }

    /** Whether [path] is [root] or lies inside it (both real paths). */
    private fun under(path: String, root: String): Boolean =
        path == root || path.startsWith(root.trimEnd(File.separatorChar) + File.separator)
}
