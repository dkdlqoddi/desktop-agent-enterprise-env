package dev.privateagent.android.web

import dev.privateagent.android.TestSites
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class WebScriptsTest {
    private fun options(call: String): JSONObject = JSONObject(call.substringAfterLast("\n)(").removeSuffix(")"))

    @Test
    fun theCallWrapsTheFunctionWithoutItsTrailingSemicolon() {
        val call = WebScripts("// comment\n(options) => {\n  return 1;\n};\n", "").captureCall("coupang", "token-1234")
        assertTrue(call.startsWith("(// comment\n(options) => {"))
        assertTrue(call.contains("return 1;\n}\n)("))
        val options = options(call)
        assertEquals("coupang", options.getString("site"))
        assertEquals(80, options.getInt("maxItems"))
        assertEquals("token-1234", options.getString("token"))
    }

    @Test
    fun theSharedCaptureScriptWrapsIntoOneCall() {
        val source = File(TestSites.file.parentFile, "capture.js").readText(Charsets.UTF_8)
        val call = WebScripts(source, "").captureCall("naver", "token-5678", maxItems = 10)
        val function = call.substringBeforeLast("\n)(")
        assertTrue(function.startsWith("("))
        assertTrue(function.trimEnd().endsWith("}"))
        assertFalse(function.trimEnd().endsWith(";"))
        assertEquals(10, options(call).getInt("maxItems"))
    }

    @Test
    fun theSharedClickWatcherReportsThroughPaBridge() {
        val source = File(TestSites.file.parentFile, "click-watch.js").readText(Charsets.UTF_8)
        assertTrue(source.contains("PaBridge.reportClick"))
        assertTrue(source.contains("isTrusted"))
        assertEquals("PaBridge", PaBridge.NAME)
    }
}
