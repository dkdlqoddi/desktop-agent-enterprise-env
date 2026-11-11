package dev.privateagent.android

import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.ComponentActivity
import androidx.test.core.app.ActivityScenario
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.uiautomator.UiDevice
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureResult
import dev.privateagent.android.web.CaptureResultParser
import dev.privateagent.android.web.ClickReportParser
import dev.privateagent.android.web.ItemKind
import dev.privateagent.android.web.PaBridge
import dev.privateagent.android.web.WebScripts
import dev.privateagent.android.web.WebViewSetup
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import kotlin.math.roundToInt

/**
 * Runs the shared capture.js and click-watch.js, as the build copied them into the app's assets, on a local fixture
 * page in a WebView set up as the app sets up its browser. The page is loaded from the test's assets under a reserved
 * example address; its links never load (the test's client cancels every navigation), so no site is contacted.
 */
@RunWith(AndroidJUnit4::class)
class CaptureScriptTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val scripts = WebScripts.load(instrumentation.targetContext.assets)
    private val reports = LinkedBlockingQueue<String>()
    private lateinit var scenario: ActivityScenario<ComponentActivity>
    private lateinit var webView: WebView

    @Before
    fun loadFixture() {
        val html = instrumentation.context.assets.open("capture-fixture.html").bufferedReader().use { it.readText() }
        val loaded = CountDownLatch(1)
        scenario = ActivityScenario.launch(ComponentActivity::class.java)
        scenario.onActivity { activity ->
            webView = WebView(activity)
            WebViewSetup.configure(webView)
            webView.addJavascriptInterface(PaBridge { reports.add(it) }, PaBridge.NAME)
            webView.webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) = true

                override fun onPageFinished(view: WebView, url: String?) {
                    loaded.countDown()
                }
            }
            activity.setContentView(webView)
            webView.loadDataWithBaseURL(BASE_URL, html, "text/html", "UTF-8", null)
        }
        assertTrue("the fixture did not load", loaded.await(30, TimeUnit.SECONDS))
        instrumentation.waitForIdleSync()
    }

    @After
    fun close() {
        scenario.onActivity { webView.destroy() }
        scenario.close()
    }

    private fun evaluate(script: String): String? {
        val done = CountDownLatch(1)
        var value: String? = null
        instrumentation.runOnMainSync {
            webView.evaluateJavascript(script) {
                value = it
                done.countDown()
            }
        }
        assertTrue("the script did not finish", done.await(30, TimeUnit.SECONDS))
        return value
    }

    private fun capture(site: String, token: String): CaptureResult {
        val result = CaptureResultParser.parse(evaluate(scripts.captureCall(site, token)))
        assertNotNull("capture.js returned nothing readable", result)
        return result!!
    }

    @Test
    fun adsAndProductsOfTheFixtureAreCounted() {
        val token = UUID.randomUUID().toString()
        val result = capture("coupang", token)
        assertFalse(result.blocked)
        assertEquals(BASE_URL, result.url)
        assertEquals("테스트 상점 - 노트북", result.title)
        assertEquals(CaptureCounts(ad = 3, product = 4, result = 0), result.counts)
        val ads = result.items.filter { it.kind == ItemKind.AD }
        assertEquals(listOf("테스트 노트북 A", "Test Laptop B", "테스트 노트북 C"), ads.map { it.title })
        assertEquals(listOf("1,299,000원", "$999.00", "1,499,000원"), ads.map { it.price })
        assertTrue(result.items.all { it.link.startsWith("https://shop.example.com/vp/products/") })
        assertEquals((1..7).toList(), result.items.map { it.position })
        // Every reported card carries the capture's token, for the click watcher.
        assertEquals("7", evaluate("document.querySelectorAll('[data-pa-capture-id=\"$token\"]').length"))
    }

    @Test
    fun theGenericPassFindsAdsByTheirLabel() {
        val result = capture("generic", UUID.randomUUID().toString())
        assertEquals(CaptureCounts(ad = 3, product = 0, result = 0), result.counts)
        assertFalse(result.items.any { it.title.contains("숨은") })
    }

    @Test
    fun aSecondCaptureOfThePageMarksItsCardsAgain() {
        assertEquals(7, capture("coupang", UUID.randomUUID().toString()).items.size)
        // capture.js clears the marks of the capture before: the cards carry the later token only.
        val token = UUID.randomUUID().toString()
        assertEquals(7, capture("coupang", token).items.size)
        assertEquals("7", evaluate("document.querySelectorAll('[data-pa-capture-id=\"$token\"]').length"))
        assertEquals("7", evaluate("document.querySelectorAll('[data-pa-capture]').length"))
    }

    @Test
    fun onlyATrustedTapOnAMarkedAdReachesPaBridge() {
        evaluate(scripts.clickWatch)
        val token = UUID.randomUUID().toString()
        val result = capture("coupang", token)
        val box = JSONObject(
            evaluate(
                "(() => { const card = document.querySelector('[data-pa-kind=\"ad\"]'); " +
                    "const box = card.getBoundingClientRect(); return { x: box.left + box.width / 2, " +
                    "y: box.top + box.height / 2, ratio: devicePixelRatio, ref: Number(card.getAttribute('data-pa-capture')) }; })()",
            )!!,
        )
        val ref = box.getInt("ref")

        // A script's click is not the user's: the watcher ignores it (event.isTrusted is false).
        evaluate("document.querySelector('[data-pa-kind=\"ad\"] .name').dispatchEvent(new MouseEvent('click', { bubbles: true }))")
        assertNull("a script's click was reported", reports.poll(2, TimeUnit.SECONDS))

        // A tap through the input system, at the card's place on the screen, is a trusted event.
        val location = IntArray(2)
        instrumentation.runOnMainSync { webView.getLocationOnScreen(location) }
        val ratio = box.getDouble("ratio")
        val x = (location[0] + box.getDouble("x") * ratio).roundToInt()
        val y = (location[1] + box.getDouble("y") * ratio).roundToInt()
        assertTrue(UiDevice.getInstance(instrumentation).click(x, y))
        val report = ClickReportParser.parse(reports.poll(15, TimeUnit.SECONDS))
        assertNotNull("the tap was not reported", report)
        assertEquals(token, report!!.token)
        assertEquals(ref, report.ref)
        assertEquals(ItemKind.AD, report.kind)
        assertEquals(result.items.first { it.ref == ref }.link, report.href)
    }

    private companion object {
        const val BASE_URL = "https://shop.example.com/search?q=%EB%85%B8%ED%8A%B8%EB%B6%81"
    }
}
