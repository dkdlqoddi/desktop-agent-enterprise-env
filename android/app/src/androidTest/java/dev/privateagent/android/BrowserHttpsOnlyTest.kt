package dev.privateagent.android

import android.graphics.Bitmap
import android.net.http.SslError
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
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
import dev.privateagent.android.ui.Tab
import dev.privateagent.android.web.BrowserController.Notice
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.BufferedInputStream
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.io.InputStream
import java.net.Inet4Address
import java.net.InetAddress
import java.net.NetworkInterface
import java.net.ServerSocket
import java.net.Socket
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Principal
import java.security.PrivateKey
import java.security.cert.X509Certificate
import java.security.spec.ECGenParameterSpec
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLEngine
import javax.net.ssl.SSLServerSocket
import javax.net.ssl.X509ExtendedKeyManager
import javax.security.auth.x500.X500Principal
import kotlin.concurrent.thread

/**
 * The in-app browser sends nothing over plain http, while the LLM client reaches an http:// endpoint the user typed
 * over a socket of its own (LlmPlainHttpTest). The app keeps cleartext off for the platform's HTTP stack and the
 * WebView (network_security_config.xml), and the browser blocks http navigations itself with a notice. A plain HTTP
 * server of the test's own on the device's network address (as a server on the same Wi-Fi would be), and one on its
 * loopback address, note every request they get and must get none: not from a link, a script or a form (GET or POST)
 * of an https page, not through a server redirect (a POST stays one through a 307 or 308, which WebView asks the app
 * nothing about), not from an https page's images, scripts, frames, requests and WebSockets, and not from the WebView
 * with a client that refuses nothing. The https page is served in place of the network; the redirects come from an
 * HTTPS server of the test's own, whose certificate only this test's WebView accepts, while the test runs.
 */
@RunWith(AndroidJUnit4::class)
class BrowserHttpsOnlyTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val graph = (instrumentation.targetContext.applicationContext as PrivateAgentApp).graph
    private lateinit var plain: ServerSocket
    private lateinit var local: ServerSocket
    private lateinit var secure: SSLServerSocket

    /** The request lines each server got, in order. */
    private val plainHits: MutableList<String> = Collections.synchronizedList(mutableListOf())
    private val localHits: MutableList<String> = Collections.synchronizedList(mutableListOf())
    private val secureHits: MutableList<String> = Collections.synchronizedList(mutableListOf())

    private val http get() = "http://${plain.inetAddress.hostAddress}:${plain.localPort}"
    private val loopback get() = "http://127.0.0.1:${local.localPort}"
    private val https get() = "https://127.0.0.1:${secure.localPort}"

    /** The errors of the main frame's loads, as the WebView described them: address to description. */
    private val errors: MutableList<Pair<String, String>> = Collections.synchronizedList(mutableListOf())

    /** The start page of the case under way: a new address each time, so its load is told from the last one's. */
    private var start = ""
    private var opened = 0

    @Before
    fun start() {
        plain = ServerSocket(0, 50, networkAddress())
        thread(isDaemon = true, name = "browser-https-only-plain") { serve(plain, plainHits) }
        local = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        thread(isDaemon = true, name = "browser-https-only-local") { serve(local, localHits) }

        val generator = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, KEYSTORE)
        generator.initialize(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_NONE, KeyProperties.DIGEST_SHA256, KeyProperties.DIGEST_SHA384)
                .setCertificateSubject(X500Principal("CN=127.0.0.1"))
                .build(),
        )
        generator.generateKeyPair()
        val store = KeyStore.getInstance(KEYSTORE).apply { load(null) }
        val key = store.getKey(ALIAS, null) as PrivateKey
        val certificate = store.getCertificate(ALIAS) as X509Certificate
        val context = SSLContext.getInstance("TLS").apply { init(arrayOf(ServerKey(key, certificate)), null, null) }
        secure = context.serverSocketFactory
            .createServerSocket(0, 50, InetAddress.getByName("127.0.0.1")) as SSLServerSocket
        thread(isDaemon = true, name = "browser-https-only-secure") { serve(secure, secureHits) }
    }

    @After
    fun stop() {
        plain.close()
        local.close()
        secure.close()
        KeyStore.getInstance(KEYSTORE).apply { load(null) }.deleteEntry(ALIAS)
    }

    /** The device's own address on its network (the emulator's or the phone's Wi-Fi), not the loopback one. */
    private fun networkAddress(): InetAddress = NetworkInterface.getNetworkInterfaces().toList()
        .filter { it.isUp && !it.isLoopback }
        .flatMap { it.inetAddresses.toList() }
        .firstOrNull { it is Inet4Address && !it.isLoopbackAddress && !it.isLinkLocalAddress }
        ?: throw AssertionError("The device has no network address")

    /** The test server's key, for its TLS handshakes. */
    private class ServerKey(
        private val key: PrivateKey,
        private val certificate: X509Certificate,
    ) : X509ExtendedKeyManager() {
        override fun chooseServerAlias(keyType: String?, issuers: Array<out Principal>?, socket: Socket?) =
            if (keyType == "EC") ALIAS else null

        override fun chooseEngineServerAlias(keyType: String?, issuers: Array<out Principal>?, engine: SSLEngine?) =
            if (keyType == "EC") ALIAS else null

        override fun getCertificateChain(alias: String?) = arrayOf(certificate)

        override fun getPrivateKey(alias: String?) = key

        override fun getServerAliases(keyType: String?, issuers: Array<out Principal>?) = arrayOf(ALIAS)

        override fun getClientAliases(keyType: String?, issuers: Array<out Principal>?) = null

        override fun chooseClientAlias(
            keyType: Array<out String>?,
            issuers: Array<out Principal>?,
            socket: Socket?,
        ): String? = null
    }

    /** One request per connection: /moved-<status> redirects to the plain server, anything else is a small page. */
    private fun serve(server: ServerSocket, hits: MutableList<String>) {
        while (true) {
            val socket = try {
                server.accept()
            } catch (_: IOException) {
                return
            }
            thread(isDaemon = true) { socket.use { answer(it, hits) } }
        }
    }

    private fun answer(socket: Socket, hits: MutableList<String>) {
        try {
            val input = BufferedInputStream(socket.getInputStream())
            val request = line(input)
            if (request.isEmpty()) return
            hits += request.substringBeforeLast(' ')
            var length = 0
            while (true) {
                val header = line(input)
                if (header.isEmpty()) break
                if (header.substringBefore(':').trim().equals("Content-Length", ignoreCase = true)) {
                    length = header.substringAfter(':').trim().toInt()
                }
            }
            repeat(length) { input.read() }
            val path = request.split(' ').getOrElse(1) { "" }.substringBefore('?')
            val moved = Regex("/moved-(\\d{3})").matchEntire(path)?.groupValues?.get(1)
            val response = if (moved != null) {
                "HTTP/1.1 $moved Moved\r\nLocation: $http/redirected-$moved\r\nContent-Length: 0\r\n" +
                    "Connection: close\r\n\r\n"
            } else {
                val page = "<!doctype html><title>reached</title><p>reached</p>"
                "HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: ${page.length}\r\n" +
                    "Connection: close\r\n\r\n$page"
            }
            socket.getOutputStream().apply {
                write(response.toByteArray(Charsets.UTF_8))
                flush()
            }
        } catch (_: IOException) {
            // The client went away (or the WebView gave up on the handshake).
        }
    }

    private fun line(input: InputStream): String {
        val bytes = ByteArrayOutputStream()
        while (true) {
            val byte = input.read()
            if (byte < 0 || byte == '\n'.code) break
            if (byte != '\r'.code) bytes.write(byte)
        }
        return bytes.toString(Charsets.UTF_8.name())
    }

    /** What the test saw so far, for a failure's message. */
    private fun seen() = "plain server $plainHits, loopback server $localHits, https server $secureHits, " +
        "notice ${graph.browser.notice.value}, page ${graph.browser.page.value}"

    private suspend fun <T : Any> waitFor(what: String, timeoutMs: Long, value: suspend () -> T?): T {
        val end = System.currentTimeMillis() + timeoutMs
        while (System.currentTimeMillis() < end) {
            value()?.let { return it }
            delay(200)
        }
        throw AssertionError("timed out waiting for $what; ${seen()}")
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
        assertTrue("no answer to $script; ${seen()}", done.await(20, TimeUnit.SECONDS))
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
     * Serves [pages] (address without its query to HTML) in place of the network and lets the WebView on past the
     * certificate of the test's own HTTPS server; the browser's own client handles everything else, as the app's does.
     */
    private fun serve(webView: WebView, pages: Map<String, String>) {
        instrumentation.runOnMainSync {
            val own = webView.webViewClient
            webView.webViewClient = object : WebViewClient() {
                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                    val address = request.url.buildUpon().clearQuery().build().toString()
                    val page = pages[address] ?: return own.shouldInterceptRequest(view, request)
                    return WebResourceResponse("text/html", "UTF-8", page.byteInputStream())
                }

                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest) =
                    own.shouldOverrideUrlLoading(view, request)

                override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) =
                    own.onPageStarted(view, url, favicon)

                override fun onPageFinished(view: WebView, url: String?) = own.onPageFinished(view, url)

                override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) =
                    own.doUpdateVisitedHistory(view, url, isReload)

                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                    if (request.isForMainFrame) errors += request.url.toString() to error.description.toString()
                    own.onReceivedError(view, request, error)
                }

                override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                    if (error.url.startsWith("$https/")) {
                        handler.proceed()
                    } else {
                        own.onReceivedSslError(view, handler, error)
                    }
                }

                override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail) =
                    own.onRenderProcessGone(view, detail)
            }
        }
    }

    /** The https page the cases start from: a link, forms, and links into the redirects. */
    private fun startPage() = """
        <!doctype html><html><head><meta charset="utf-8"><title>start</title></head><body>
        <a id="link" href="$http/link">link</a>
        <form id="get" method="get" action="$http/get-form"><input name="q" value="typed"></form>
        <form id="post" method="post" action="$http/post-form"><input name="q" value="typed"></form>
        ${REDIRECTS.joinToString("") { "<a id=\"moved-$it\" href=\"$https/moved-$it\">$it</a>" }}
        <form id="post-307" method="post" action="$https/moved-307"><input name="q" value="typed"></form>
        <form id="post-308" method="post" action="$https/moved-308"><input name="q" value="typed"></form>
        </body></html>
    """.trimIndent()

    /** An https page that asks for its parts over http, every way a page can, and says how each attempt ended. */
    private fun partsPage() = """
        <!doctype html><html><head><meta charset="utf-8"><title>parts</title>
        <link rel="stylesheet" href="$http/style.css">
        <script src="$http/script.js"></script>
        </head><body>
        <img src="$http/image.png">
        <iframe src="$http/frame"></iframe>
        <iframe srcdoc="<img src='$http/inner-image.png'>"></iframe>
        <audio src="$http/audio.mp3" autoplay></audio>
        <script>
        const results = {};
        const ended = (name, attempt) => Promise.race([
          attempt.then(() => 'loaded', () => 'blocked'),
          new Promise((resolve) => setTimeout(() => resolve('timeout'), 8000)),
        ]).then((outcome) => { results[name] = outcome; });
        const attempt = (start) => new Promise((resolve, reject) => {
          try { start(resolve, reject); } catch (e) { reject(e); }
        });
        const socket = (address) => attempt((resolve, reject) => {
          const socket = new WebSocket(address); socket.onopen = resolve; socket.onerror = reject;
        });
        Promise.all([
          ended('fetch', fetch('$http/fetch')),
          ended('post', fetch('$http/fetch-post', { method: 'POST', body: 'typed' })),
          ended('xhr', attempt((resolve, reject) => {
            const xhr = new XMLHttpRequest(); xhr.onload = resolve; xhr.onerror = reject;
            xhr.open('GET', '$http/xhr'); xhr.send();
          })),
          ended('image', attempt((resolve, reject) => {
            const image = new Image(); image.onload = resolve; image.onerror = reject; image.src = '$http/image-2.png';
          })),
          ended('socket', socket('${http.replace("http:", "ws:")}/socket')),
          ended('loopback-socket', socket('${loopback.replace("http:", "ws:")}/socket')),
          ended('loopback-fetch', fetch('$loopback/fetch')),
          ended('events', attempt((resolve, reject) => {
            const events = new EventSource('$http/events'); events.onopen = resolve; events.onerror = reject;
          })),
        ]).then(() => {
          try { navigator.sendBeacon('$http/beacon', 'typed'); } catch (e) {}
          window.results = results;
          document.title = 'done';
        });
        </script>
        </body></html>
    """.trimIndent()

    /** Opens a new address of the start page (served in place of the network) and waits until it has loaded. */
    private suspend fun openStart() {
        start = "$START?n=${++opened}"
        instrumentation.runOnMainSync {
            graph.browser.dismissNotice()
            graph.browser.load(start)
        }
        waitFor("the start page", 30_000) {
            graph.browser.page.value.takeIf { it.url == start && !it.loading && it.title == "start" }
        }
    }

    /**
     * [url] was refused by the platform, which keeps cleartext off: the page that asked for it is replaced by the
     * WebView's error page (the browser hears of no POST it could block itself), and the server got nothing.
     */
    private suspend fun refusedByThePlatform(url: String) {
        val error = waitFor("the error about $url", 15_000) { errors.lastOrNull { it.first == url } }
        assertEquals(seen(), CLEARTEXT_REFUSED, error.second)
        delay(1_500)
        assertEquals(seen(), emptyList<String>(), plainHits.toList())
    }

    /** [url] was not opened: the notice offers it to another browser, the start page stays, the server got nothing. */
    private suspend fun refused(url: String) {
        val notice = waitFor("the notice about $url", 15_000) { graph.browser.notice.value as? Notice.Insecure }
        assertEquals(seen(), url, notice.url)
        // Anything that went on would reach the server in a moment.
        delay(1_500)
        assertEquals(seen(), emptyList<String>(), plainHits.toList())
        assertEquals(seen(), start, graph.browser.page.value.url)
    }

    @Test
    fun aLinkAScriptOrAFormOfAnHttpsPageNeverOpensAnHttpPage() = runBlocking {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            val webView = shownWebView()
            serve(webView, mapOf(START to startPage()))
            val cases = listOf(
                "document.getElementById('link').click()" to "$http/link",
                "location.href = '$http/script'" to "$http/script",
                "document.getElementById('get').submit()" to "$http/get-form?q=typed",
            )
            for ((script, url) in cases) {
                openStart()
                evaluate(webView, script)
                refused(url)
            }
            // A POST: WebView never asks shouldOverrideUrlLoading about it, so it is the platform's rule that holds.
            openStart()
            evaluate(webView, "document.getElementById('post').submit()")
            refusedByThePlatform("$http/post-form")
        } finally {
            scenario.close()
        }
    }

    @Test
    fun aServerRedirectToAnHttpPageIsNeverFollowed() = runBlocking {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            val webView = shownWebView()
            serve(webView, mapOf(START to startPage()))
            // A link of the page, each redirect status.
            for (status in REDIRECTS) {
                openStart()
                evaluate(webView, "document.getElementById('moved-$status').click()")
                refused("$http/redirected-$status")
                assertTrue(seen(), "GET /moved-$status" in secureHits)
            }
            // A page the app opens itself (the address bar, the chat's open_site) that redirects.
            openStart()
            instrumentation.runOnMainSync { graph.browser.load("$https/moved-302?app") }
            refused("$http/redirected-302")
            // A form posted to an https page that sends the POST on to http: WebView asks the app nothing about it.
            for (status in listOf(307, 308)) {
                openStart()
                evaluate(webView, "document.getElementById('post-$status').submit()")
                refusedByThePlatform("$http/redirected-$status")
                assertTrue(seen(), "POST /moved-$status" in secureHits)
            }
        } finally {
            instrumentation.runOnMainSync { graph.browser.webView.value?.clearSslPreferences() }
            scenario.close()
        }
    }

    @Test
    fun anHttpsPageLoadsNoPartOverHttp() = runBlocking {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            val webView = shownWebView()
            serve(webView, mapOf(PARTS to partsPage()))
            instrumentation.runOnMainSync { graph.browser.load(PARTS) }
            waitFor("the page's attempts", 30_000) {
                evaluate(webView, "document.title")?.takeIf { it == "\"done\"" }
            }
            val results = JSONObject(evaluate(webView, "window.results")!!)
            delay(2_000)
            assertEquals("$results; ${seen()}", emptyList<String>(), plainHits.toList())
            assertEquals("$results; ${seen()}", emptyList<String>(), localHits.toList())
            val attempts = listOf(
                "fetch", "post", "xhr", "image", "socket", "loopback-socket", "loopback-fetch", "events",
            )
            for (name in attempts) {
                assertEquals("$name: $results", "blocked", results.getString(name))
            }
        } finally {
            scenario.close()
        }
    }

    /** Under the app's own checks lies the platform's rule: a WebView whose client refuses nothing sends nothing. */
    @Test
    fun theWebViewSendsNothingOverHttpEvenWithAClientThatRefusesNothing() = runBlocking {
        val scenario = ActivityScenario.launch(MainActivity::class.java)
        try {
            val webView = shownWebView()
            val failed = CountDownLatch(1)
            var description = ""
            instrumentation.runOnMainSync {
                webView.webViewClient = object : WebViewClient() {
                    override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                        description = error.description.toString()
                        failed.countDown()
                    }
                }
                webView.loadUrl("$http/control")
            }
            assertTrue(seen(), failed.await(30, TimeUnit.SECONDS))
            assertEquals(CLEARTEXT_REFUSED, description)
            delay(1_500)
            assertEquals(seen(), emptyList<String>(), plainHits.toList())
        } finally {
            scenario.close()
        }
    }

    private companion object {
        const val KEYSTORE = "AndroidKeyStore"
        const val ALIAS = "browser-https-only-test-server"
        const val START = "https://page.test/start"
        const val PARTS = "https://page.test/parts"

        /** How the WebView describes a load the network security configuration refused for being cleartext. */
        const val CLEARTEXT_REFUSED = "net::ERR_CLEARTEXT_NOT_PERMITTED"
        val REDIRECTS = listOf(301, 302, 303, 307, 308)
    }
}
