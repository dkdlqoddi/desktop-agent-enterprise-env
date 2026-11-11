package dev.privateagent.android.hermes

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.io.StringReader

class RuntimeIndexTest {
    private val hash = "a".repeat(64)

    private fun index(
        executable: String = "libhermes_python.so",
        abis: String = """{"x86_64": ["runtime-common.zip", "runtime-x86_64.zip"]}""",
        format: Int = 1,
    ) = """
        {"format": $format, "id": "$hash", "hermes": {"version": "0.23.0", "commit": "c0ffee"}, "python": "3.14.0",
         "api_level": 24, "executable": "$executable", "worker_sha256": "$hash", "abis": $abis,
         "archives": {"runtime-common.zip": {"sha256": "$hash", "size": 10, "files": 2, "bytes": 20},
                      "runtime-x86_64.zip": {"sha256": "$hash", "size": 5, "files": 1, "bytes": 9}}}
    """.trimIndent()

    @Test
    fun readsTheArchivesOfEachAbi() {
        val parsed = RuntimeIndex.parse(index())
        assertNotNull(parsed)
        assertEquals(listOf("runtime-common.zip", "runtime-x86_64.zip"), parsed!!.abis["x86_64"])
        assertEquals(2, parsed.archives.getValue("runtime-common.zip").files)
        assertEquals("3.14.0", parsed.python)
        assertEquals("0.23.0", parsed.hermesVersion)
    }

    @Test
    fun rejectsIndexesItCannotTrust() {
        assertNull(RuntimeIndex.parse("not json"))
        assertNull(RuntimeIndex.parse(index(format = 2)))
        assertNull(RuntimeIndex.parse(index(executable = "../bin/sh")))
        assertNull(RuntimeIndex.parse(index(abis = """{"x86_64": ["runtime-missing.zip"]}""")))
        assertNull(RuntimeIndex.parse(index(abis = "{}")))
        assertNull(RuntimeIndex.parse(index().replace("\"files\": 1", "\"files\": \"one\"")))
    }

    @Test
    fun acceptsOnlyPlainRelativeEntryNames() {
        for (name in listOf("worker.py", "python/lib/python3.14/os.py", "source/agent/__pycache__/a.cpython-314.pyc")) {
            assertTrue(name, RuntimeIndex.isSafeEntry(name))
        }
        for (name in listOf("", "/etc/passwd", "../outside", "a/../../b", "a//b", "./a", "a\\b", "a/", "a\u0000b")) {
            assertFalse(name, RuntimeIndex.isSafeEntry(name))
        }
    }

    @Test
    fun theChildEnvironmentKeepsOnlySystemLocations() {
        val root = File("/data/code_cache/hermes-runtime/abc-x86_64")
        val installed = HermesRuntime.Installed(
            root = root,
            abi = "x86_64",
            index = RuntimeIndex.parse(index())!!,
            home = File("/data/files/hermes"),
            executable = File("/data/app/lib/x86_64/libhermes_python.so"),
            libraryDir = File("/data/app/lib/x86_64"),
        )
        val inherited = mapOf(
            "ANDROID_ROOT" to "/system", "ANDROID_DATA" to "/data", "PYTHONPATH" to "/sdcard/evil",
            "HTTPS_PROXY" to "http://proxy.example.com", "OPENAI_API_KEY" to "sk-test", "CLASSPATH" to "/x.jar",
        )
        val env = HermesRuntime.environment(installed, inherited)
        assertEquals("/system", env["ANDROID_ROOT"])
        assertEquals("/data", env["ANDROID_DATA"])
        for (name in listOf("PYTHONPATH", "HTTPS_PROXY", "OPENAI_API_KEY", "CLASSPATH")) {
            assertFalse(name, name in env)
        }
        assertEquals(File(root, "python").path, env["PYTHONHOME"])
        assertEquals("/data/app/lib/x86_64", env["LD_LIBRARY_PATH"])
        assertEquals("/data/files/hermes", env["HERMES_HOME"])
        assertEquals("/data/files/hermes", env["HOME"])
        assertEquals("/data/files/hermes/tmp", env["TMPDIR"])
        assertEquals("1", env["HERMES_DISABLE_LAZY_INSTALLS"])
    }

    @Test
    fun framesAreLinesUpToTheLimit() {
        val reader = FrameReader(StringReader("{\"a\":1}\n{\"b\":\"한글\"}\npartial"), 16)
        assertEquals("{\"a\":1}", reader.next())
        assertEquals("{\"b\":\"한글\"}", reader.next())
        assertNull(reader.next())
        try {
            FrameReader(StringReader("x".repeat(17) + "\n"), 16).next()
            throw AssertionError("A line over the limit must fail")
        } catch (_: FrameReader.TooLargeException) {
        }
        assertEquals("x".repeat(16), FrameReader(StringReader("x".repeat(16) + "\n"), 16).next())
    }
}
