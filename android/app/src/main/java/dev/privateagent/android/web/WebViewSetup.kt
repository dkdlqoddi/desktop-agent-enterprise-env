package dev.privateagent.android.web

import android.annotation.SuppressLint
import android.webkit.JavascriptInterface
import android.webkit.WebSettings
import android.webkit.WebView

object WebViewSetup {
    /**
     * The in-app browser's settings. JavaScript is on because capture.js and click-watch.js run in the page; files
     * and content providers are out of the page's reach; mixed content never loads; Safe Browsing is on. The user
     * agent stays the WebView's own: the app never disguises itself.
     */
    @SuppressLint("SetJavaScriptEnabled")
    fun configure(view: WebView) {
        view.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
            safeBrowsingEnabled = true
            javaScriptCanOpenWindowsAutomatically = false
            // A link that opens a new window loads in this one, so the landing page of a clicked ad is seen here.
            setSupportMultipleWindows(false)
            setGeolocationEnabled(false)
            useWideViewPort = true
            loadWithOverviewMode = true
            builtInZoomControls = true
            displayZoomControls = false
        }
    }
}

/**
 * The page's only way to reach the app: click-watch.js reports the user's own clicks on the elements capture.js
 * marked. It has one method. Any frame of any page can call it, so the app checks every report (ClickReportParser) and
 * only marks the item of a capture it knows by the report's token.
 */
class PaBridge(private val onClick: (String) -> Unit) {
    @JavascriptInterface
    fun reportClick(json: String?) {
        if (json != null) onClick(json)
    }

    companion object {
        const val NAME = "PaBridge"
    }
}
