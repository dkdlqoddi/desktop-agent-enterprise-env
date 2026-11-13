package dev.privateagent.android

import android.graphics.Bitmap
import android.net.http.SslError
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.data.ItemRow
import dev.privateagent.android.ui.Tab
import dev.privateagent.android.web.CaptureOutcome
import dev.privateagent.android.web.ItemKind
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

/**
 * The app's own browser from end to end, on local fixture pages: MainActivity's visible WebView, the browser's "record
 * this page" capture (settle, scroll, click watcher, capture.js, database), and real taps on recorded ads (PaBridge,
 * the clicked item, its landing page), while a report the page's own script sends without a tap is not recorded. The
 * fixtures' links point to an example host that does not resolve; the test serves the few pages that should load in
 * place of the network, so no site is contacted.
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

    private suspend fun shownWebView(): WebView {
        instrumentation.runOnMainSync { graph.navigator.show(Tab.BROWSER) }
        return waitFor("the browser's WebView", 20_000) {
            var shown: WebView? = null
            instrumentation.runOnMainSync {
                shown = graph.browser.webView.value?.takeIf { it.isAttachedToWindow && it.isShown }
            }
            shown
        }
    }

    /**
     * Serves [pages] (address to HTML) in place of the network: the browser's own client handles everything else, as
     * the app's does. The WebView is made anew with the next screen, without this.
     */
    private fun serve(webView: WebView, pages: Map<String, String>) {
        instrumentation.runOnMainSync {
            val own = webView.webViewClient
            webView.webViewClient = object : WebViewClient() {
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                    val page = pages[request.url.toString()] ?: return own.shouldInterceptRequest(view, request)
                    return WebResourceResponse("text/html", "UTF-8", page.byteInputStream())
                }

                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) =
                    own.shouldOverrideUrlLoading(view, request)

                override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) =
                    own.onPageStarted(view, url, favicon)

                override fun onPageFinished(view: WebView, url: String?) = own.onPageFinished(view, url)

                override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) =
                    own.doUpdateVisitedHistory(view, url, isReload)

                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) =
                    own.onReceivedError(view, request, error)

                override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) =
                    own.onReceivedSslError(view, handler, error)

                override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail) =
                    own.onRenderProcessGone(view, detail)
            }
        }
    }

    /** The fixture [asset] opened under [BASE_URL] and recorded as the browser's own button records it: its capture. */
    private suspend fun record(webView: WebView, asset: String, ads: Int): Long {
        val html = instrumentation.context.assets.open(asset).bufferedReader().use { it.readText() }
        instrumentation.runOnMainSync { webView.loadDataWithBaseURL(BASE_URL, html, "text/html", "UTF-8", BASE_URL) }
        waitFor("the fixture", 30_000) {
            graph.browser.page.value.takeIf { it.url == BASE_URL && !it.loading && it.title.isNotEmpty() }
        }
        val outcome = graph.browser.captureCurrentPage()
        assertTrue("capture failed: $outcome", outcome is CaptureOutcome.Saved)
        assertEquals(ads, (outcome as CaptureOutcome.Saved).counts.ad)
        return outcome.captureId
    }

    /**
     * A real tap, through the input system, on the middle of the element [selector] names, once the screen has settled
     * (the banners above the page come and go as a capture ends).
     */
    private suspend fun tap(webView: WebView, selector: String) {
        instrumentation.waitForIdleSync()
        delay(1_000)
        instrumentation.waitForIdleSync()
        val box = JSONObject(
            evaluate(
                webView,
                "(() => { const element = document.querySelector('$selector'); " +
                    "element.scrollIntoView({ block: 'center' }); " +
                    "const box = element.getBoundingClientRect(); return { x: box.left + box.width / 2, " +
                    "y: box.top + box.height / 2, ratio: devicePixelRatio }; })()",
            )!!,
        )
        instrumentation.waitForIdleSync()
        val location = IntArray(2)
        instrumentation.runOnMainSync { webView.getLocationOnScreen(location) }
        val ratio = box.getDouble("ratio")
        assertTrue(
            UiDevice.getInstance(instrumentation).click(
                (location[0] + box.getDouble("x") * ratio).roundToInt(),
                (location[1] + box.getDouble("y") * ratio).roundToInt(),
            ),
        )
    }

    private suspend fun item(captureId: Long, title: String): ItemRow? =
        graph.records.items(ItemFilter(captureId = captureId)).firstOrNull { it.title.contains(title) }

    private suspend fun shows(url: String) =
        waitFor(url, 30_000) { graph.browser.page.value.takeIf { it.url == url && !it.loading } }

    @Test
    fun theVisibleBrowserRecordsThePageAndTheAdTheUserTaps() = runBlocking {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        var captureId: Long? = null
        try {
            val webView = shownWebView()
            serve(webView, mapOf(AD_PAGE to LANDING))
            // The browser's own button runs this: the page is none of the shared sites, so the generic pass reads it.
            captureId = record(webView, "capture-fixture.html", ads = 3)
            val capture = graph.records.capture(captureId)!!
            assertEquals(CaptureSource.MANUAL, capture.source)
            assertEquals(BASE_URL, capture.url)
            val items = graph.records.items(ItemFilter(captureId = captureId))
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
            assertTrue(graph.records.items(ItemFilter(captureId = captureId)).all { it.clickedAt == null })

            // A real tap on the first recorded ad: its click, and the page its link opened as its landing page.
            delay(1_000)
            tap(webView, "[data-pa-kind=\"ad\"]")
            val clicked = waitFor("the recorded click", 15_000) {
                graph.records.items(ItemFilter(captureId = captureId)).firstOrNull { it.clickedAt != null }
            }
            assertTrue(clicked.title, clicked.title.contains("테스트 노트북 A"))
            assertEquals(AD_PAGE, clicked.clickedUrl)
            val landed = waitFor("the landing page", 30_000) {
                graph.records.item(clicked.id)?.takeIf { it.landingUrl != null }
            }
            assertEquals(AD_PAGE, landed.landingUrl)
            assertEquals("도착 페이지", landed.landingTitle)
        } finally {
            captureId?.let { graph.records.deleteCapture(it) }
            scenario.close()
        }
    }

    @Test
    fun onlyThePageTheAdsLinkOpenedIsItsLandingPage() = runBlocking {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        val captures = mutableListOf<Long>()
        try {
            val webView = shownWebView()
            serve(webView, mapOf(LOADS to LANDING, MOVES to MOVING, AFTER to AFTER_PAGE, NEXT to NEXT_PAGE))

            // A tap beside the ad's link (its wish-list button) is no click on the ad, nor is the page the app opens
            // next its landing page.
            var capture = record(webView, "landing-fixture.html", ads = 4).also(captures::add)
            tap(webView, "#loads .wish")
            delay(3_000)
            assertEquals("\"wish\"", evaluate(webView, "window.tapped"))
            instrumentation.runOnMainSync { graph.browser.load(NEXT) }
            shows(NEXT)
            delay(1_000)
            assertTrue(graph.records.items(ItemFilter(captureId = capture)).all { it.clickedAt == null })

            // A link whose page does not load: the click is kept, without a landing page, and the page the app opens
            // next is none either.
            capture = record(webView, "landing-fixture.html", ads = 4).also(captures::add)
            tap(webView, "#fails a")
            val failed = waitFor("the click on the failing link", 15_000) {
                item(capture, "열리지 않는 광고 상품")?.takeIf { it.clickedAt != null }
            }
            shows(FAILS)
            delay(1_000)
            instrumentation.runOnMainSync { graph.browser.load(NEXT) }
            shows(NEXT)
            delay(1_000)
            assertNull(graph.records.item(failed.id)!!.landingUrl)

            // A link to another app, which the browser does not open: the click is kept, and the page going back
            // shows is no landing page.
            capture = record(webView, "landing-fixture.html", ads = 4).also(captures::add)
            tap(webView, "#app a")
            assertEquals("\"A\"", evaluate(webView, "window.tapped"))
            val app = waitFor("the click on the app's link", 15_000) {
                item(capture, "앱으로 여는 광고 상품")?.takeIf { it.clickedAt != null }
            }
            instrumentation.runOnMainSync { graph.browser.back() }
            shows(NEXT)
            delay(1_000)
            assertNull(graph.records.item(app.id)!!.landingUrl)

            // The page a link opened moves on by itself: the page it moved to is the landing page.
            capture = record(webView, "landing-fixture.html", ads = 4).also(captures::add)
            tap(webView, "#moves a")
            val moved = waitFor("the landing page it moved to", 30_000) {
                item(capture, "스스로 넘어가는 광고 상품")?.takeIf { it.landingUrl == AFTER }
            }
            assertEquals("넘어간 도착 페이지", moved.landingTitle)

            // And the plain case: the page the ad's link opened.
            capture = record(webView, "landing-fixture.html", ads = 4).also(captures::add)
            tap(webView, "#loads a")
            val loaded = waitFor("the landing page", 30_000) {
                item(capture, "열리는 광고 상품")?.takeIf { it.landingUrl != null }
            }
            assertEquals(LOADS, loaded.landingUrl)
            assertEquals("도착 페이지", loaded.landingTitle)
        } finally {
            captures.forEach { graph.records.deleteCapture(it) }
            scenario.close()
        }
    }

    private companion object {
        const val BASE_URL = "https://shop.example.com/search?q=%EB%85%B8%ED%8A%B8%EB%B6%81"
        const val AD_PAGE = "https://shop.example.com/vp/products/101?src=ad"
        const val LOADS = "https://shop.example.com/landing/loads"
        const val FAILS = "https://shop.example.com/vp/products/301"
        const val MOVES = "https://shop.example.com/landing/moves"
        const val AFTER = "https://shop.example.com/landing/after"
        const val NEXT = "https://shop.example.com/landing/next"
        const val LANDING = "<!doctype html><title>도착 페이지</title><p>광고주 페이지</p>"
        const val MOVING = "<!doctype html><title>잠시만</title><script>location.replace('$AFTER')</script>"
        const val AFTER_PAGE = "<!doctype html><title>넘어간 도착 페이지</title><p>광고주 페이지</p>"
        const val NEXT_PAGE = "<!doctype html><title>다음 페이지</title><p>다음 페이지</p>"
    }
}
