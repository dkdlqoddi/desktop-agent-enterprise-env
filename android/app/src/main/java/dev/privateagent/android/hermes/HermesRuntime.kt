package dev.privateagent.android.hermes

import android.content.Context
import android.content.res.AssetManager
import android.os.Build
import androidx.annotation.WorkerThread
import org.json.JSONObject
import java.io.BufferedInputStream
import java.io.File
import java.io.FileNotFoundException
import java.io.FileOutputStream
import java.io.IOException
import java.security.DigestInputStream
import java.security.MessageDigest
import java.util.UUID
import java.util.zip.ZipInputStream

/**
 * The Hermes Agent runtime that android/hermes/build_runtime.py packs into the APK: the pinned CPython 3.14 with the
 * Hermes source and its dependencies in assets/hermes/, and the Python executable in lib/<abi>/.
 *
 * Android lets an app execute files only from its native library directory, which the installer fills from the APK
 * (legacy packaging, see build.gradle.kts). The Python files are extracted once per runtime build into the code
 * cache, which Android also clears when the app is updated; Hermes' own state ([home]) stays in the files directory.
 */
class HermesRuntime(context: Context) {
    private val app = context.applicationContext
    private val runtimes = File(app.codeCacheDir, "hermes-runtime")

    /** HERMES_HOME: sessions, memories and configuration, kept across runtime updates. */
    val home = File(app.filesDir, "hermes")

    /** The directory of the executable and of libpython, where the dynamic linker finds them. */
    val libraryDir = File(app.applicationInfo.nativeLibraryDir)

    /** The packed runtime's description, or null when this build of the app has none. */
    val index: RuntimeIndex? by lazy {
        try {
            app.assets.open("$ASSETS/runtime.json").use { RuntimeIndex.parse(it.readBytes().decodeToString()) }
        } catch (_: FileNotFoundException) {
            null
        }
    }

    /** Whether this build of the app carries a runtime for this device. */
    val isBundled: Boolean get() = index?.let { abi(it) != null && executable(it).canExecute() } ?: false

    /** The extracted runtime, extracting it first (seconds) when this runtime build has not been extracted yet. */
    @WorkerThread
    @Throws(HermesUnavailableException::class)
    fun prepare(): Installed = synchronized(FILES) {
        val index = index ?: throw HermesUnavailableException("이 앱에는 내장 Hermes 실행 환경이 없습니다.")
        val abi = abi(index) ?: throw HermesUnavailableException(
            "이 기기의 CPU(${Build.SUPPORTED_ABIS.firstOrNull()})용 Hermes 실행 환경이 없습니다.",
        )
        val executable = executable(index)
        if (!executable.canExecute()) {
            throw HermesUnavailableException("내장 Hermes 실행 파일이 설치되지 않았습니다. 앱을 다시 설치해 주세요.")
        }
        val name = "${index.id.take(16)}-$abi"
        val root = File(runtimes, name)
        if (File(root, READY).takeIf { it.isFile }?.readText() != index.id) {
            try {
                extract(index, abi, root)
            } catch (error: IOException) {
                throw HermesUnavailableException("내장 Hermes 실행 환경을 준비하지 못했습니다. 저장 공간을 확인해 주세요.", error)
            }
        }
        runtimes.listFiles()?.filter { it.name != name }?.forEach { it.deleteRecursively() }
        for (folder in listOf(home) + HOME_FOLDERS.map { File(home, it) }) {
            if (!folder.isDirectory && !folder.mkdirs()) {
                throw HermesUnavailableException("Hermes 저장 폴더를 만들지 못했습니다.")
            }
        }
        Installed(root, abi, index, home, executable, libraryDir)
    }

    /**
     * Starts Python with -I -B on [script] (the runtime's worker.py by default) as a child process. Its stdin and
     * stdout carry JSON lines; [HermesProcess.stop] kills it.
     */
    @WorkerThread
    @Throws(HermesUnavailableException::class, IOException::class)
    fun start(script: File? = null, args: List<String> = emptyList()): HermesProcess {
        val installed = prepare()
        if (script == null && !installed.isWorkerIntact()) {
            // The runtime directory was damaged (a lost write, a storage error): extract it again on the next start.
            File(installed.root, READY).delete()
            throw HermesUnavailableException("내장 Hermes 실행 환경이 손상되었습니다. 다시 시도해 주세요.")
        }
        val command = listOf(installed.executable.path, "-I", "-B", (script ?: installed.worker).path) + args
        val builder = ProcessBuilder(command).directory(home)
        builder.environment().apply {
            val inherited = HashMap(this)
            clear()
            putAll(environment(installed, inherited))
        }
        return HermesProcess(builder.start())
    }

    /** Removes every extracted runtime (not [home]); the next [prepare] extracts again. */
    @WorkerThread
    fun removeExtracted() {
        synchronized(FILES) { runtimes.deleteRecursively() }
    }

    private fun executable(index: RuntimeIndex) = File(libraryDir, index.executable)

    /** The ABI of the native libraries the installer chose for this app, if the runtime has archives for it. */
    private fun abi(index: RuntimeIndex): String? {
        val installed = when (libraryDir.name) {
            "arm64" -> "arm64-v8a"
            "x86_64" -> "x86_64"
            "arm" -> "armeabi-v7a"
            "x86" -> "x86"
            else -> null
        }
        if (installed != null) return installed.takeIf { it in index.abis }
        return Build.SUPPORTED_ABIS.firstOrNull { it in index.abis }
    }

    private fun extract(index: RuntimeIndex, abi: String, root: File) {
        runtimes.mkdirs()
        runtimes.listFiles()?.filter { it.name.startsWith(".tmp-") }?.forEach { it.deleteRecursively() }
        val temp = File(runtimes, ".tmp-" + UUID.randomUUID())
        try {
            for (name in index.abis.getValue(abi)) {
                unzip(name, index.archives.getValue(name), temp)
            }
            File(temp, READY).writeText(index.id)
            root.deleteRecursively()
            if (!temp.renameTo(root)) throw IOException("Cannot move the extracted runtime into place")
        } finally {
            temp.deleteRecursively()
        }
    }

    /** Extracts one archive, checking its SHA-256 and file count against the index while it streams. */
    private fun unzip(name: String, archive: RuntimeIndex.Archive, target: File) {
        val digest = MessageDigest.getInstance("SHA-256")
        val created = HashSet<String>()
        val buffer = ByteArray(1 shl 16)
        var files = 0
        app.assets.open("$ASSETS/$name", AssetManager.ACCESS_STREAMING).use { asset ->
            val hashed = DigestInputStream(BufferedInputStream(asset, 1 shl 16), digest)
            val zip = ZipInputStream(hashed)
            while (true) {
                val entry = zip.nextEntry ?: break
                if (entry.isDirectory) continue
                if (!RuntimeIndex.isSafeEntry(entry.name)) throw IOException("Unsafe archive entry")
                val file = File(target, entry.name)
                val parent = file.parentFile!!
                if (created.add(parent.path) && !parent.isDirectory && !parent.mkdirs()) {
                    throw IOException("Cannot create a runtime directory")
                }
                FileOutputStream(file).use { output ->
                    while (true) {
                        val count = zip.read(buffer)
                        if (count < 0) break
                        output.write(buffer, 0, count)
                    }
                }
                files++
            }
            // The digest covers the whole archive: read what follows the last entry (the central directory) too.
            while (hashed.read(buffer) >= 0) Unit
        }
        if (digest.digest().toHex() != archive.sha256 || files != archive.files) {
            throw IOException("The runtime archive $name does not match runtime.json")
        }
    }

    /** An extracted runtime: the desktop bundle's layout (python/, source/, dependencies/, worker.py). */
    class Installed(
        val root: File,
        val abi: String,
        val index: RuntimeIndex,
        val home: File,
        val executable: File,
        val libraryDir: File,
    ) {
        val pythonHome get() = File(root, "python")
        val worker get() = File(root, "worker.py")
        val manifest: JSONObject by lazy { JSONObject(File(root, "manifest.json").readText()) }

        /** Whether worker.py is the file the build recorded (the desktop backend checks the same before each run). */
        fun isWorkerIntact(): Boolean = worker.isFile && sha256(worker) == index.workerSha256 &&
            manifest.optString("worker_sha256") == index.workerSha256
    }

    companion object {
        /** Every instance in the process extracts into the same directories. */
        private val FILES = Any()
        private const val ASSETS = "hermes"
        private const val READY = ".ready"
        private val HOME_FOLDERS = listOf("tmp", "cache", "config", "data")

        /** System locations bionic and the runtime look up (time zones, ICU); nothing of the user's or the app's. */
        private val PASSED = listOf("ANDROID_ROOT", "ANDROID_DATA", "ANDROID_ART_ROOT", "ANDROID_I18N_ROOT",
            "ANDROID_TZDATA_ROOT")

        /**
         * The child's whole environment, like the desktop backend's child_environment: no inherited Python, proxy or
         * Hermes settings. PYTHONHOME is applied by the executable itself, because -I makes CPython ignore it.
         */
        fun environment(installed: Installed, inherited: Map<String, String>): Map<String, String> {
            val home = installed.home
            val tmp = File(home, "tmp").path
            return inherited.filterKeys { it in PASSED } + mapOf(
                "PATH" to "/system/bin",
                "LD_LIBRARY_PATH" to installed.libraryDir.path,
                "PYTHONHOME" to installed.pythonHome.path,
                "HERMES_HOME" to home.path,
                "HERMES_DISABLE_LAZY_INSTALLS" to "1",
                "HERMES_ENABLE_PROJECT_PLUGINS" to "0",
                "PYTHONDONTWRITEBYTECODE" to "1",
                "PYTHONUTF8" to "1",
                "PYTHONIOENCODING" to "utf-8",
                "LANG" to "C.UTF-8",
                "HOME" to home.path,
                "XDG_CONFIG_HOME" to File(home, "config").path,
                "XDG_DATA_HOME" to File(home, "data").path,
                "XDG_CACHE_HOME" to File(home, "cache").path,
                "TMPDIR" to tmp,
                "TEMP" to tmp,
                "TMP" to tmp,
            )
        }

        internal fun sha256(file: File): String {
            val digest = MessageDigest.getInstance("SHA-256")
            file.inputStream().use { input ->
                val buffer = ByteArray(1 shl 16)
                while (true) {
                    val count = input.read(buffer)
                    if (count < 0) break
                    digest.update(buffer, 0, count)
                }
            }
            return digest.digest().toHex()
        }

        private fun ByteArray.toHex(): String = joinToString("") { "%02x".format(it) }
    }
}

/** The runtime is missing or cannot be prepared; [message] is the Korean sentence to show. */
class HermesUnavailableException(message: String, cause: Throwable? = null) : Exception(message, cause)
