package dev.privateagent.android.agent

import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.nio.file.StandardCopyOption

/**
 * What the chat's agent remembers, for the user to read and change (the desktop's agent_memory.py).
 *
 * Hermes keeps two short files in its home's memories folder: MEMORY.md, its own notes, and USER.md, what it learned
 * about the user. Each holds entries separated by a line with § alone, is read into every turn, and has a limit in
 * characters that Hermes itself keeps to. Beside them it keeps a record of every turn it ran (state.db and sessions/),
 * which its search of earlier conversations reads.
 *
 * The agent writes all of that by itself as it works, so the user must be able to see it, correct it and delete it:
 * the entries are listed, saved as the list the user left, and the records of the turns are deleted whole. Nothing is
 * changed while a turn runs ([busy]); the caller stops the worker before the records go, as it holds them open.
 */
class AgentMemory(private val home: File, private val busy: () -> Boolean = { false }) {
    enum class Target(val file: String, val limit: Int) {
        /** The agent's own notes. */
        MEMORY("MEMORY.md", 2200),

        /** What it keeps about the user. */
        USER("USER.md", 1375),
    }

    /** One of the two lists: its entries, their size as Hermes counts it, and Hermes' own limit. */
    data class Entries(val entries: List<String>, val chars: Int, val limit: Int)

    /** How much Hermes keeps of the turns it ran. */
    data class Records(val files: Int, val bytes: Long)

    data class View(val memory: Entries, val user: Entries, val records: Records) {
        fun of(target: Target): Entries = if (target == Target.MEMORY) memory else user
    }

    enum class Problem { BUSY, INVALID, DELIMITER, TOO_LONG, WRITE, PARTIAL }

    /** Why nothing (or not everything) was changed; [limit] and [size] say how long a list that is TOO_LONG may be. */
    class MemoryException(val problem: Problem, val limit: Int = 0, val size: Int = 0) : Exception("Agent memory: $problem")

    private val folder get() = File(home, "memories")

    fun read(): View = View(entries(Target.MEMORY), entries(Target.USER), records())

    /**
     * Replaces one of the two lists with [given]: the list as the user left it, an entry edited, removed or added. It
     * must fit Hermes' limit, unless it only got shorter (a file that grew past the limit can always be trimmed).
     */
    @Synchronized
    fun save(target: Target, given: List<String>): View {
        if (given.size > MAX_ENTRIES) throw MemoryException(Problem.INVALID)
        val entries = LinkedHashSet<String>()
        for (raw in given) {
            if ('\u0000' in raw) throw MemoryException(Problem.INVALID)
            val entry = raw.trim()
            // A line of § alone would cut the entry in two the next time the file is read.
            if (entry.lines().any { it.trim() == "§" }) throw MemoryException(Problem.DELIMITER)
            if (entry.isNotEmpty()) entries += entry
        }
        if (busy()) throw MemoryException(Problem.BUSY)
        val text = entries.joinToString(DELIMITER)
        val size = chars(text)
        if (size > target.limit && size >= entries(target).chars) throw MemoryException(Problem.TOO_LONG, target.limit, size)
        try {
            folder.mkdirs()
            val file = File(folder, target.file)
            val draft = File(folder, "${target.file}.${System.nanoTime()}.tmp")
            try {
                draft.writeText(text, Charsets.UTF_8)
                Files.move(draft.toPath(), file.toPath(), StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
            } finally {
                draft.delete()
            }
        } catch (_: IOException) {
            throw MemoryException(Problem.WRITE)
        }
        return read()
    }

    /**
     * Deletes Hermes' records of the turns it ran (its session database and transcripts). The chat's own conversation
     * is not among them, nor are the two memory files.
     */
    @Synchronized
    fun forget(): View {
        if (busy()) throw MemoryException(Problem.BUSY)
        var failed = false
        for (name in RECORDS) {
            val file = File(home, name)
            if (file.exists() && !file.delete()) failed = true
        }
        val sessions = File(home, SESSIONS)
        if (sessions.isDirectory && !Files.isSymbolicLink(sessions.toPath())) {
            for (entry in sessions.listFiles().orEmpty()) {
                val removed = if (entry.isDirectory && !Files.isSymbolicLink(entry.toPath())) {
                    entry.deleteRecursively()
                } else {
                    entry.delete()
                }
                if (!removed) failed = true
            }
        }
        if (failed) throw MemoryException(Problem.PARTIAL)
        return read()
    }

    private fun entries(target: Target): Entries {
        val entries = try {
            parse(File(folder, target.file).readText(Charsets.UTF_8))
        } catch (_: IOException) {
            emptyList()
        }
        return Entries(entries, chars(entries.joinToString(DELIMITER)), target.limit)
    }

    private fun records(): Records {
        val files = RECORDS.map { File(home, it) }.filter { it.isFile } +
            File(home, SESSIONS).walkTopDown().filter { it.isFile }
        return Records(files.size, files.sumOf { it.length() })
    }

    companion object {
        const val DELIMITER = "\n§\n"
        const val MAX_ENTRIES = 200

        // Its records of the turns it ran: the session database with SQLite's side files, and the folder of transcripts.
        private val RECORDS = listOf("state.db", "state.db-wal", "state.db-shm", "state.db-journal")
        private const val SESSIONS = "sessions"

        /** The entries of a memory file, each once, as Hermes reads it. */
        fun parse(raw: String): List<String> =
            raw.split(DELIMITER).map { it.trim() }.filter { it.isNotEmpty() }.distinct()

        /** A text's length as Hermes counts it for its limits: in characters, not in UTF-16 units. */
        fun chars(text: String): Int = text.codePointCount(0, text.length)
    }
}
