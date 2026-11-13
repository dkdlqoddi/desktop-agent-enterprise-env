package dev.privateagent.android

import android.os.ParcelFileDescriptor
import android.webkit.WebView
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.ui.Tab
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotSame
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.abs

/**
 * The browser's page outlives its screen: a change of font size is handled in place (the same WebView, the page not
 * loaded again), and a screen made anew, as after the system ended the app in the background, opens the page and its
 * history again from the saved state. The pages are the app's own text under an example address; nothing is fetched.
 */
@RunWith(AndroidJUnit4::class)
class BrowserStateTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val graph = (instrumentation.targetContext.applicationContext as PrivateAgentApp).graph

    private suspend fun <T : Any> waitFor(what: String, timeoutMs: Long = 20_000, value: () -> T?): T {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            var found: T? = null
            instrumentation.runOnMainSync { found = value() }
            found?.let { return it }
            delay(200)
        }
        throw AssertionError("timed out waiting for $what")
    }

    private fun shell(command: String): String =
        ParcelFileDescriptor.AutoCloseInputStream(instrumentation.uiAutomation.executeShellCommand(command)).use {
            it.readBytes().toString(Charsets.UTF_8).trim()
        }

    private fun evaluate(webView: WebView, script: String): String? {
        val done = CountDownLatch(1)
        var result: String? = null
        instrumentation.runOnMainSync {
            webView.evaluateJavascript(script) {
                result = it
                done.countDown()
            }
        }
        assertTrue(done.await(20, TimeUnit.SECONDS))
        return result
    }

    private suspend fun shownWebView(): WebView = waitFor("the browser's WebView") {
        graph.browser.webView.value?.takeIf { it.isAttachedToWindow && it.isShown }
    }

    private suspend fun open(webView: WebView, url: String, title: String) {
        instrumentation.runOnMainSync {
            webView.loadDataWithBaseURL(url, "<title>$title</title><p>$title</p>", "text/html", "UTF-8", url)
        }
        waitFor(url) { graph.browser.page.value.takeIf { it.url == url && !it.loading } }
    }

    @Test
    fun aScreenMadeAnewOpensThePageAndItsHistoryAgain(): Unit = runBlocking {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            instrumentation.runOnMainSync { graph.navigator.show(Tab.BROWSER) }
            val first = shownWebView()
            open(first, PAGE_A, "첫 페이지")
            open(first, PAGE_B, "둘째 페이지")

            // As after the system ended the app: the screen and its WebView go, and the saved state is all that is left.
            scenario.recreate()
            val second = shownWebView()
            assertNotSame(first, second)
            waitFor("the page and the one before it") {
                graph.browser.page.value.takeIf { it.url == PAGE_B && it.canGoBack && !it.loading }
            }
            assertEquals(Tab.BROWSER, graph.navigator.tab.value)
            instrumentation.runOnMainSync { graph.browser.back() }
            waitFor("the page before") { graph.browser.page.value.takeIf { it.url == PAGE_A && !it.loading } }
        } finally {
            scenario.close()
        }
    }

    @Test
    fun aChangeOfFontSizeKeepsThePageAsItIs(): Unit = runBlocking {
        val scale = shell("settings get system font_scale")
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            instrumentation.runOnMainSync { graph.navigator.show(Tab.BROWSER) }
            val webView = shownWebView()
            open(webView, PAGE_A, "첫 페이지")
            // Something only this load of the page holds: a page loaded again would not have it.
            assertEquals("7", evaluate(webView, "window.kept = 7; String(window.kept)")?.trim('"'))
            var before: MainActivity? = null
            scenario.onActivity { before = it }

            shell("settings put system font_scale 1.3")
            val end = System.currentTimeMillis() + 20_000
            var scaled = false
            while (!scaled && System.currentTimeMillis() < end) {
                delay(200)
                scenario.onActivity { scaled = abs(it.resources.configuration.fontScale - 1.3f) < 0.01f }
            }
            assertTrue("the larger font was not applied", scaled)
            scenario.onActivity { assertSame(before, it) }
            assertSame(webView, graph.browser.webView.value)
            assertEquals(PAGE_A, graph.browser.page.value.url)
            assertEquals("7", evaluate(webView, "String(window.kept)")?.trim('"'))
        } finally {
            if (scale.isEmpty() || scale == "null") {
                shell("settings delete system font_scale")
            } else {
                shell("settings put system font_scale $scale")
            }
            scenario.close()
        }
    }

    private companion object {
        const val PAGE_A = "https://shop.example.com/state-a"
        const val PAGE_B = "https://shop.example.com/state-b"
    }
}
