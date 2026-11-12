package dev.privateagent.android.web

import android.app.Activity
import android.graphics.Bitmap
import android.net.http.SslError
import android.os.SystemClock
import android.view.ViewGroup
import android.webkit.ConsoleMessage
import android.webkit.CookieManager
import android.webkit.GeolocationPermissions
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.annotation.MainThread
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.NewCapture
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.security.SettingsStore
import dev.privateagent.android.util.cleanText
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.UUID
import kotlin.coroutines.resume

/** Why the browser could not do what was asked. */
enum class BrowserFailure { NO_BROWSER, LOAD_FAILED, TIMEOUT, MOVED, SCRIPT }

sealed interface CaptureOutcome {
    data class Saved(val captureId: Long, val counts: CaptureCounts) : CaptureOutcome

    /** The site showed a robot check (CAPTCHA): the capture stopped, and the user solves it themselves. */
    data object Blocked : CaptureOutcome

    data class Failed(val failure: BrowserFailure) : CaptureOutcome
}

/**
 * The in-app browser: one WebView the user sees, which the agent loads pages in and reads with capture.js. The app
 * never clicks, taps or submits anything on a page, never solves or avoids a robot check and never changes the user
 * agent: it loads, scrolls, reads, and records the clicks the user makes. All state is touched on the main thread.
 */
class BrowserController(
    private val records: RecordStore,
    private val settings: SettingsStore,
    private val sitesProvider: () -> Sites,
    private val scriptsProvider: () -> WebScripts,
) {
    data class PageState(
        val url: String = "",
        val title: String = "",
        val loading: Boolean = false,
        val progress: Int = 0,
        val canGoBack: Boolean = false,
        val canGoForward: Boolean = false,
    )

    /** A capture in progress, which the browser screen shows above the page. */
    data class Work(val site: String?, val query: String?)

    sealed interface Notice {
        /** A link to another app (intent:, market:, ...) that was not opened. */
        data class External(val scheme: String) : Notice

        /** A plain http:// page that was not opened; the user may open it in another browser. */
        data class Insecure(val url: String) : Notice

        data object BadAddress : Notice
        data object Certificate : Notice
        data object Restarted : Notice
        data object Captcha : Notice
        /** The user's click on a marked item was recorded; [ad] tells an ad from a product or result. */
        data class ClickRecorded(val ad: Boolean) : Notice

        /** The result of a capture the user started with the browser's button. */
        data class Captured(val outcome: CaptureOutcome) : Notice
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val sites get() = sitesProvider()
    private val scripts get() = scriptsProvider()

    private val webViewState = MutableStateFlow<WebView?>(null)
    val webView: StateFlow<WebView?> = webViewState
    private val pageState = MutableStateFlow(PageState())
    val page: StateFlow<PageState> = pageState
    private val workState = MutableStateFlow<Work?>(null)
    val work: StateFlow<Work?> = workState
    private val noticeState = MutableStateFlow<Notice?>(null)
    val notice: StateFlow<Notice?> = noticeState
    private val markedAdsState = MutableStateFlow(0)

    /** How many ads of the page are marked, so the user's click on one is recorded. */
    val markedAds: StateFlow<Int> = markedAdsState

    private var host: Activity? = null
    private val mutex = Mutex()
    private val clickMutex = Mutex()

    /** Counts main-frame page loads that finished; a load waits for it to move past the value it started at. */
    private val finishCount = MutableStateFlow(0)
    private var mainFrameFailed = false
    private var busy = false

    /** The capture being saved: a click on its marks can arrive before its row exists. */
    private var savingToken: String? = null
    private var savingDone: CompletableDeferred<Long?>? = null

    /** Pages the user browsed whose ads were marked; saved only when the user clicks one of the ads. */
    private val browsedPages = object : LinkedHashMap<String, BrowsedPage>() {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, BrowsedPage>?) = size > 20
    }

    private class BrowsedPage(val site: String, val query: String, val result: CaptureResult)

    /**
     * A click whose landing page is awaited. The page may finish loading before the click's item is found in the
     * database, so the landing waits here for [itemId] when it comes first.
     */
    private class PendingLanding(val clickedAt: Long, val fromUrl: String) {
        var itemId: Long? = null
        var landedAt = 0L
        var landingUrl: String? = null
        var landingTitle = ""
    }

    private var pendingLanding: PendingLanding? = null

    @MainThread
    fun attach(activity: Activity) {
        if (host === activity && webViewState.value != null) return
        webViewState.value?.let(::destroy)
        host = activity
        webViewState.value = create(activity)
        pageState.value = PageState()
    }

    @MainThread
    fun detach(activity: Activity) {
        if (host !== activity) return
        webViewState.value?.let(::destroy)
        webViewState.value = null
        host = null
    }

    fun pause() {
        webViewState.value?.onPause()
        CookieManager.getInstance().flush()
    }

    fun resume() {
        webViewState.value?.onResume()
    }

    /** Opens [url] for the user; only https pages open in the app. */
    @MainThread
    fun load(url: String) {
        when (val check = BrowserUrls.normalize(url)) {
            is BrowserUrls.Check.Ok -> webViewState.value?.loadUrl(check.url)
            is BrowserUrls.Check.Insecure -> noticeState.value = Notice.Insecure(check.url)
            BrowserUrls.Check.Invalid -> noticeState.value = Notice.BadAddress
        }
    }

    /** The address bar: an address opens; other words search the web with the shared site list's search page. */
    @MainThread
    fun submitAddress(text: String) {
        val input = text.trim()
        if (input.isEmpty()) return
        if (BrowserUrls.looksLikeAddress(input)) {
            load(input)
        } else {
            val site = sites.byKey("google") ?: sites.all.firstOrNull() ?: return
            webViewState.value?.loadUrl(sites.searchUrl(site, input))
        }
    }

    fun back() {
        webViewState.value?.takeIf { it.canGoBack() }?.goBack()
    }

    fun forward() {
        webViewState.value?.takeIf { it.canGoForward() }?.goForward()
    }

    fun reload() {
        webViewState.value?.reload()
    }

    fun stopLoading() {
        webViewState.value?.stopLoading()
    }

    fun dismissNotice() {
        noticeState.value = null
    }

    fun showNotice(notice: Notice) {
        noticeState.value = notice
    }

    /** Forgets cookies, storage, cache and history of the in-app browser. */
    fun clearData() {
        CookieManager.getInstance().removeAllCookies(null)
        CookieManager.getInstance().flush()
        WebStorage.getInstance().deleteAllData()
        webViewState.value?.apply {
            clearCache(true)
            clearHistory()
            clearFormData()
        }
        browsedPages.clear()
    }

    /**
     * Loads [url] (when given) in the visible browser, scrolls down a few screens, reads the page with capture.js and
     * saves what it found. A robot check stops it ([CaptureOutcome.Blocked]); a page of another site than [site] is not
     * saved. Captures run one at a time.
     */
    suspend fun capture(site: Site?, query: String, source: CaptureSource, url: String?): CaptureOutcome =
        withContext(Dispatchers.Main.immediate) {
            mutex.withLock {
                busy = true
                workState.value = Work(site?.name, query.ifEmpty { null })
                try {
                    runCapture(site, query, source, url)
                } finally {
                    busy = false
                    workState.value = null
                }
            }
        }

    /** Records the page on the screen: for the browser's own button, or for the chat ([source]). */
    suspend fun captureCurrentPage(source: CaptureSource = CaptureSource.MANUAL): CaptureOutcome =
        withContext(Dispatchers.Main.immediate) {
            // WebView methods only on the main thread.
            val url = webViewState.value?.url.orEmpty()
            capture(sites.forUrl(url), "", source, null)
        }

    /** Opens [url] for the user to browse; null when it opened. */
    suspend fun open(url: String): BrowserFailure? = withContext(Dispatchers.Main.immediate) {
        mutex.withLock {
            when (loadAndSettle(url)) {
                Load.OK -> null
                Load.FAILED -> BrowserFailure.LOAD_FAILED
                Load.TIMEOUT -> BrowserFailure.TIMEOUT
                Load.NO_BROWSER -> BrowserFailure.NO_BROWSER
            }
        }
    }

    private suspend fun runCapture(site: Site?, query: String, source: CaptureSource, url: String?): CaptureOutcome {
        if (url != null) {
            when (loadAndSettle(url)) {
                Load.OK -> Unit
                Load.FAILED -> return CaptureOutcome.Failed(BrowserFailure.LOAD_FAILED)
                Load.TIMEOUT -> return CaptureOutcome.Failed(BrowserFailure.TIMEOUT)
                Load.NO_BROWSER -> return CaptureOutcome.Failed(BrowserFailure.NO_BROWSER)
            }
        } else if (withTimeoutOrNull(LOAD_TIMEOUT_MS) { settle() } == null) {
            return CaptureOutcome.Failed(BrowserFailure.TIMEOUT)
        }
        val view = webViewState.value ?: return CaptureOutcome.Failed(BrowserFailure.NO_BROWSER)
        // Lazily loaded cards appear as the page scrolls: a few screens down, with pauses, as a person would.
        repeat(SCROLL_STEPS) {
            evaluate(view, WebScripts.SCROLL_STEP)
            delay(SCROLL_PAUSE_MS)
        }
        delay(SCROLL_SETTLE_MS)
        evaluate(view, scripts.clickWatch)
        // capture.js marks the page's items anew under this token, clearing an earlier capture's marks.
        val token = UUID.randomUUID().toString()
        val siteKey = site?.key ?: sites.forUrl(view.url.orEmpty())?.key ?: GENERIC
        val raw = evaluate(view, scripts.captureCall(siteKey, token), CAPTURE_TIMEOUT_MS)
        val result = CaptureResultParser.parse(raw) ?: return CaptureOutcome.Failed(BrowserFailure.SCRIPT)
        if (result.blocked) {
            noticeState.value = Notice.Captcha
            return CaptureOutcome.Blocked
        }
        if (site != null && sites.forUrl(result.url)?.key != site.key) return CaptureOutcome.Failed(BrowserFailure.MOVED)
        val words = query.ifEmpty { site?.let { sites.queryFromUrl(it, result.url) }.orEmpty() }
        val done = CompletableDeferred<Long?>()
        savingToken = token
        savingDone = done
        val id = try {
            records.saveCapture(NewCapture(siteKey, words, source, token), result).also { done.complete(it) }
        } finally {
            done.complete(null)
            savingToken = null
            savingDone = null
        }
        markedAdsState.value = result.counts.ad
        evaluate(view, WebScripts.SCROLL_TOP)
        return CaptureOutcome.Saved(id, result.counts)
    }

    private enum class Load { OK, FAILED, TIMEOUT, NO_BROWSER }

    /** Loads [url] and waits until the page finished and no other page started for SETTLE_MS (JS redirects). */
    private suspend fun loadAndSettle(url: String): Load {
        val view = webViewState.value ?: return Load.NO_BROWSER
        val before = finishCount.value
        mainFrameFailed = false
        view.loadUrl(url)
        withTimeoutOrNull(LOAD_TIMEOUT_MS) {
            finishCount.first { it > before }
            settle()
        } ?: return Load.TIMEOUT
        return if (mainFrameFailed) Load.FAILED else Load.OK
    }

    private suspend fun settle() {
        while (true) {
            if (pageState.value.loading) pageState.first { !it.loading }
            val serial = finishCount.value
            delay(SETTLE_MS)
            if (!pageState.value.loading && finishCount.value == serial) return
        }
    }

    private suspend fun evaluate(view: WebView, script: String, timeoutMs: Long = SCRIPT_TIMEOUT_MS): String? =
        withTimeoutOrNull(timeoutMs) {
            suspendCancellableCoroutine { continuation ->
                view.evaluateJavascript(script) { value -> if (continuation.isActive) continuation.resume(value) }
            }
        }

    private fun create(activity: Activity): WebView {
        val view = WebView(activity)
        WebViewSetup.configure(view)
        view.addJavascriptInterface(PaBridge(::onBridgeClick), PaBridge.NAME)
        view.webViewClient = Client()
        view.webChromeClient = Chrome()
        // Cookies persist, so the user can sign in to a site once, themselves.
        CookieManager.getInstance().setAcceptCookie(true)
        return view
    }

    private fun destroy(view: WebView) {
        (view.parent as? ViewGroup)?.removeView(view)
        view.stopLoading()
        view.destroy()
    }

    private fun update(view: WebView, loading: Boolean = pageState.value.loading) {
        pageState.value = PageState(
            url = view.url.orEmpty(),
            title = view.title.orEmpty(),
            loading = loading,
            progress = view.progress,
            canGoBack = view.canGoBack(),
            canGoForward = view.canGoForward(),
        )
    }

    /** Called on the JavaBridge thread by click-watch.js; everything else happens on the main thread. */
    private fun onBridgeClick(json: String) {
        // Off with the setting: a click is recorded neither on a page the user browses nor on a recorded one.
        if (!settings.recordClicks.value) return
        val report = ClickReportParser.parse(json) ?: return
        scope.launch {
            // Set before the page the click opens can finish: that page is posted to the main thread after this.
            val pending = PendingLanding(SystemClock.elapsedRealtime(), webViewState.value?.url.orEmpty())
            pendingLanding = pending
            clickMutex.withLock { recordClick(report, pending) }
        }
    }

    private suspend fun recordClick(report: ClickReport, pending: PendingLanding) {
        val clickedAt = System.currentTimeMillis()
        val itemId = clickedItem(report, clickedAt)
        if (itemId == null) {
            if (pendingLanding === pending) pendingLanding = null
            return
        }
        pending.itemId = itemId
        pending.landingUrl?.let { records.setLanding(itemId, it, pending.landingTitle) }
        noticeState.value = Notice.ClickRecorded(ad = report.kind == ItemKind.AD)
    }

    /** Marks the clicked item of a capture the app knows by the report's token; its id, or null. */
    private suspend fun clickedItem(report: ClickReport, clickedAt: Long): Long? {
        var captureId = records.captureIdForToken(report.token)
        if (captureId == null && report.token == savingToken) captureId = savingDone?.await()
        // The capture may have been saved while the first look was under way.
        if (captureId == null) captureId = records.captureIdForToken(report.token)
        if (captureId == null) {
            // A page the user browsed: its ads were only marked. A click on one of them saves the page with it.
            val browsed = browsedPages[report.token] ?: return null
            val item = browsed.result.items.firstOrNull { it.ref == report.ref } ?: return null
            if (item.kind != ItemKind.AD) return null
            captureId = records.saveCapture(
                NewCapture(browsed.site, browsed.query, CaptureSource.CLICK, report.token, clickedAt),
                browsed.result,
            )
            browsedPages.remove(report.token)
        }
        return records.markClicked(captureId, report.ref, clickedAt, report.href)
    }

    /**
     * The landing page of a clicked item: the first page that finishes after the click (within LANDING_WINDOW_MS),
     * and the pages a redirect chain moves on to right after it.
     */
    private fun recordLanding(view: WebView, url: String) {
        val pending = pendingLanding ?: return
        val now = SystemClock.elapsedRealtime()
        val first = pending.landedAt == 0L
        val inWindow = if (first) now - pending.clickedAt <= LANDING_WINDOW_MS else now - pending.landedAt <= REDIRECT_WINDOW_MS
        if (!inWindow) {
            pendingLanding = null
            return
        }
        if ((first && url == pending.fromUrl) || !url.startsWith("https://")) return
        pending.landedAt = now
        pending.landingUrl = url
        pending.landingTitle = cleanText(view.title.orEmpty(), 200)
        // Without the item yet, recordClick saves the landing page once it has found it.
        val itemId = pending.itemId ?: return
        val title = pending.landingTitle
        scope.launch { records.setLanding(itemId, url, title) }
    }

    /**
     * Marks the ads of a page the user browses (capture.js, nothing saved), so a click on one is recorded with its
     * landing page. Off with the setting, and never while a capture runs.
     */
    private fun markBrowsedPage(serial: Int) {
        if (!settings.recordClicks.value) return
        scope.launch {
            delay(SETTLE_MS)
            if (busy || finishCount.value != serial || pageState.value.loading) return@launch
            val view = webViewState.value ?: return@launch
            val url = view.url.orEmpty()
            if (!url.startsWith("https://")) return@launch
            val site = sites.forUrl(url)
            val token = UUID.randomUUID().toString()
            val result = CaptureResultParser.parse(evaluate(view, scripts.captureCall(site?.key ?: GENERIC, token)))
                ?: return@launch
            if (result.blocked || result.items.isEmpty() || finishCount.value != serial) return@launch
            browsedPages[token] = BrowsedPage(
                site?.key ?: GENERIC,
                site?.let { sites.queryFromUrl(it, url) }.orEmpty(),
                result,
            )
            markedAdsState.update { maxOf(it, result.counts.ad) }
        }
    }

    private inner class Client : WebViewClient() {
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            val url = request.url.toString()
            if (!request.isForMainFrame) return !NavigationPolicy.allowsSubframe(url)
            return when (val decision = NavigationPolicy.decide(url)) {
                NavigationDecision.Allow -> false
                is NavigationDecision.OpenInstead -> {
                    view.loadUrl(decision.url)
                    true
                }
                is NavigationDecision.BlockInsecure -> {
                    noticeState.value = Notice.Insecure(decision.url)
                    true
                }
                is NavigationDecision.BlockExternal -> {
                    noticeState.value = Notice.External(decision.scheme)
                    true
                }
            }
        }

        override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
            mainFrameFailed = false
            markedAdsState.value = 0
            update(view, loading = true)
        }

        override fun onPageFinished(view: WebView, url: String?) {
            update(view, loading = false)
            // The click watcher, after every page load: it reports only the user's own clicks on marked items.
            view.evaluateJavascript(scripts.clickWatch, null)
            finishCount.update { it + 1 }
            recordLanding(view, url.orEmpty())
            markBrowsedPage(finishCount.value)
        }

        override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
            update(view)
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) mainFrameFailed = true
        }

        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            // Never proceed past a certificate error.
            handler.cancel()
            noticeState.value = Notice.Certificate
        }

        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            val activity = host ?: return true
            if (webViewState.value === view) {
                destroy(view)
                webViewState.value = create(activity)
                pageState.value = PageState()
                noticeState.value = Notice.Restarted
            }
            return true
        }
    }

    private inner class Chrome : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) {
            pageState.update { it.copy(progress = newProgress) }
        }

        override fun onReceivedTitle(view: WebView, title: String?) {
            pageState.update { it.copy(title = title.orEmpty()) }
        }

        override fun onPermissionRequest(request: PermissionRequest) {
            request.deny()
        }

        override fun onGeolocationPermissionsShowPrompt(origin: String?, callback: GeolocationPermissions.Callback?) {
            callback?.invoke(origin, false, false)
        }

        // What pages write to their console stays out of the system log, which the WebView would write it to: it is
        // page content (search words, account details a site prints), and bug reports collect that log.
        override fun onConsoleMessage(message: ConsoleMessage): Boolean = true
    }

    companion object {
        const val GENERIC = "generic"
        private const val LOAD_TIMEOUT_MS = 30_000L
        private const val SETTLE_MS = 1_500L
        private const val SCROLL_STEPS = 4
        private const val SCROLL_PAUSE_MS = 700L
        private const val SCROLL_SETTLE_MS = 800L
        private const val SCRIPT_TIMEOUT_MS = 10_000L
        private const val CAPTURE_TIMEOUT_MS = 20_000L
        private const val LANDING_WINDOW_MS = 30_000L
        private const val REDIRECT_WINDOW_MS = 3_000L
    }
}
