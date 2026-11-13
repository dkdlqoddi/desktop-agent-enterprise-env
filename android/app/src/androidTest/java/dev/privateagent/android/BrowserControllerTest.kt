package dev.privateagent.android

import android.webkit.WebView
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.ui.Tab
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureOutcome
import dev.privateagent.android.web.ItemKind
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

/**
 * The app's own browser from end to end, on the local fixture page: MainActivity's visible WebView, the browser's
 * "record this page" capture (settle, scroll, click watcher, capture.js, database), and a real tap on a recorded ad
 * (PaBridge, the clicked item, its landing page), while a report the page's own script sends without a tap is not
 * recorded. The fixture's links point to an example host that does not resolve, so the tap's navigation reaches no
 * server.
 */
@RunWith(AndroidJUnit4::class)
class BrowserControllerTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val graph = (instrumentation.targetContext.applicationContext as PrivateAgentApp).graph

    private suspend fun <T : Any> waitFor(what: String, timeoutMs: Long, value: suspend () -> T?): T {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            value()?.let { return it }
            delay(200)
        }
        throw AssertionError("timed out waiting for $what")
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

    @Test
    fun theVisibleBrowserRecordsThePageAndTheAdTheUserTaps() = runBlocking {
        val html = instrumentation.context.assets.open("capture-fixture.html").bufferedReader().use { it.readText() }
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        var captureId: Long? = null
        try {
            instrumentation.runOnMainSync { graph.navigator.show(Tab.BROWSER) }
            val webView = waitFor("the browser's WebView", 20_000) {
                var shown: WebView? = null
                instrumentation.runOnMainSync {
                    shown = graph.browser.webView.value?.takeIf { it.isAttachedToWindow && it.isShown }
                }
                shown
            }
            // The history URL is what WebView.getUrl() reports, as it would for the real page.
            instrumentation.runOnMainSync { webView.loadDataWithBaseURL(BASE_URL, html, "text/html", "UTF-8", BASE_URL) }
            waitFor("the fixture", 30_000) { graph.browser.page.value.takeIf { it.url == BASE_URL && !it.loading } }

            // The browser's own button runs this: the page is not one of the shared sites, so the generic pass reads it.
            val outcome = graph.browser.captureCurrentPage()
            assertTrue("capture failed: $outcome", outcome is CaptureOutcome.Saved)
            val saved = outcome as CaptureOutcome.Saved
            captureId = saved.captureId
            assertEquals(CaptureCounts(ad = 3), saved.counts)
            val capture = graph.records.capture(saved.captureId)!!
            assertEquals(CaptureSource.MANUAL, capture.source)
            assertEquals(BASE_URL, capture.url)
            val items = graph.records.items(ItemFilter(captureId = saved.captureId))
            // The generic pass titles a card by its link's text, which here holds the ad label and the price too.
            val titles = items.sortedBy { it.position }.map { it.title }
            assertEquals(3, titles.size)
            listOf("테스트 노트북 A", "Test Laptop B", "테스트 노트북 C").forEachIndexed { index, name ->
                assertTrue(titles[index], titles[index].contains(name))
            }
            assertTrue(items.all { it.kind == ItemKind.AD && it.clickedAt == null })

            // The page's own script reports a click on a recorded ad, with the right token, and nobody touched the
            // screen: it is not recorded.
            evaluate(
                webView,
                "(() => { const card = document.querySelector('[data-pa-kind=\"ad\"]'); " +
                    "PaBridge.reportClick(JSON.stringify({ token: card.getAttribute('data-pa-capture-id'), " +
                    "ref: Number(card.getAttribute('data-pa-capture')), kind: 'ad', " +
                    "href: 'https://shop.example.com/script', at: new Date().toISOString() })); })()",
            )
            delay(3_000)
            assertTrue(graph.records.items(ItemFilter(captureId = saved.captureId)).all { it.clickedAt == null })

            // A real tap, through the input system, on the first recorded ad.
            instrumentation.waitForIdleSync()
            delay(1_000)
            instrumentation.waitForIdleSync()
            val box = JSONObject(
                evaluate(
                    webView,
                    "(() => { const card = document.querySelector('[data-pa-kind=\"ad\"]'); " +
                        "const box = card.getBoundingClientRect(); return { x: box.left + box.width / 2, " +
                        "y: box.top + box.height / 2, ratio: devicePixelRatio, ref: Number(card.getAttribute('data-pa-capture')) }; })()",
                )!!,
            )
            val location = IntArray(2)
            instrumentation.runOnMainSync { webView.getLocationOnScreen(location) }
            val ratio = box.getDouble("ratio")
            assertTrue(
                UiDevice.getInstance(instrumentation).click(
                    (location[0] + box.getDouble("x") * ratio).roundToInt(),
                    (location[1] + box.getDouble("y") * ratio).roundToInt(),
                ),
            )
            val clicked = waitFor("the recorded click", 15_000) {
                graph.records.items(ItemFilter(captureId = saved.captureId)).firstOrNull { it.clickedAt != null }
            }
            assertEquals(box.getInt("ref"), clicked.ref)
            assertTrue(clicked.title, clicked.title.contains("테스트 노트북 A"))
            assertEquals("https://shop.example.com/vp/products/101?src=ad", clicked.clickedUrl)
            // The page the tap opened is its landing page (here an error page: the example host does not resolve).
            val landed = waitFor("the landing page", 30_000) {
                graph.records.item(clicked.id)?.takeIf { it.landingUrl != null }
            }
            assertEquals("https://shop.example.com/vp/products/101?src=ad", landed.landingUrl)
            assertNotNull(landed.landingTitle)
        } finally {
            captureId?.let { graph.records.deleteCapture(it) }
            scenario.close()
        }
    }

    private companion object {
        const val BASE_URL = "https://shop.example.com/search?q=%EB%85%B8%ED%8A%B8%EB%B6%81"
    }
}
