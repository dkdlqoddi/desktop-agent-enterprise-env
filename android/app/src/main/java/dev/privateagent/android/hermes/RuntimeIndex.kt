package dev.privateagent.android.hermes

import org.json.JSONException
import org.json.JSONObject

/**
 * assets/hermes/runtime.json, written by android/hermes/build_runtime.py: which archives make up the runtime of each
 * ABI and the SHA-256 of each archive. [id] changes whenever any archive does, so a new APK extracts again.
 */
class RuntimeIndex(
    val id: String,
    val hermesVersion: String,
    val hermesCommit: String,
    val python: String,
    val executable: String,
    val workerSha256: String,
    val abis: Map<String, List<String>>,
    val archives: Map<String, Archive>,
) {
    class Archive(val sha256: String, val size: Long, val files: Int)

    companion object {
        private val HEX = Regex("[0-9a-f]{64}")
        private val ARCHIVE = Regex("runtime-[A-Za-z0-9_-]+\\.zip")

        /** The index, or null when it is not one this app version understands. */
        fun parse(text: String): RuntimeIndex? = try {
            val json = JSONObject(text)
            val hermes = json.getJSONObject("hermes")
            val archives = json.getJSONObject("archives").let { all ->
                all.keys().asSequence().associateWith { name ->
                    val archive = all.getJSONObject(name)
                    Archive(archive.getString("sha256"), archive.getLong("size"), archive.getInt("files"))
                }
            }
            val abis = json.getJSONObject("abis").let { all ->
                all.keys().asSequence().associateWith { abi ->
                    val names = all.getJSONArray(abi)
                    (0 until names.length()).map { names.getString(it) }
                }
            }
            val index = RuntimeIndex(
                id = json.getString("id"),
                hermesVersion = hermes.getString("version"),
                hermesCommit = hermes.getString("commit"),
                python = json.getString("python"),
                executable = json.getString("executable"),
                workerSha256 = json.getString("worker_sha256"),
                abis = abis,
                archives = archives,
            )
            index.takeIf { json.getInt("format") == 1 && it.isConsistent() }
        } catch (_: JSONException) {
            null
        }

        private fun RuntimeIndex.isConsistent(): Boolean =
            HEX.matches(id) && HEX.matches(workerSha256) && executable.matches(Regex("lib[A-Za-z0-9_]+\\.so")) &&
                archives.all { (name, archive) -> ARCHIVE.matches(name) && HEX.matches(archive.sha256) } &&
                abis.isNotEmpty() && abis.values.all { names -> names.isNotEmpty() && names.all { it in archives } }

        /**
         * Whether an archive entry is a plain relative path inside the runtime: no absolute path, no "..", no empty or
         * "." segment and no backslash, so extracting it cannot write outside the runtime directory.
         */
        fun isSafeEntry(name: String): Boolean {
            if (name.isEmpty() || name.length > 1024 || name.startsWith("/") || '\\' in name || '\u0000' in name) {
                return false
            }
            return name.split('/').all { it.isNotEmpty() && it != "." && it != ".." }
        }
    }
}
