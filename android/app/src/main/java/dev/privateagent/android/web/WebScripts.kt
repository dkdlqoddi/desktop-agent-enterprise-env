package dev.privateagent.android.web

import android.content.res.AssetManager
import org.json.JSONObject

/**
 * The shared page scripts as the build copied them into the assets (shared/web-capture). capture.js is one arrow
 * function expression taking options; click-watch.js is one statement (an immediately invoked function).
 */
class WebScripts(captureSource: String, val clickWatch: String) {
    // Prettier ends the expression statement with a semicolon, which may not stand inside the call's parentheses.
    private val capture = captureSource.trim().trimEnd(';', ' ', '\n', '\r', '\t')

    /**
     * The call of capture.js for WebView.evaluateJavascript: `(captureJs)({site, maxItems, token})`. The closing
     * parenthesis starts a line of its own, so a line comment at the end of the file cannot swallow it.
     */
    fun captureCall(site: String, token: String, maxItems: Int = MAX_ITEMS): String {
        val options = JSONObject().put("site", site).put("maxItems", maxItems).put("token", token)
        return "($capture\n)($options)"
    }

    companion object {
        const val MAX_ITEMS = 80
        const val ASSET_DIR = "web-capture"

        /** One step of the gradual scroll before a capture, so lazily loaded cards appear. */
        const val SCROLL_STEP = "window.scrollBy(0, innerHeight*0.9)"
        const val SCROLL_TOP = "window.scrollTo(0, 0)"

        fun load(assets: AssetManager): WebScripts = WebScripts(
            captureSource = read(assets, "capture.js"),
            clickWatch = read(assets, "click-watch.js"),
        )

        fun read(assets: AssetManager, name: String): String =
            assets.open("$ASSET_DIR/$name").bufferedReader(Charsets.UTF_8).use { it.readText() }
    }
}
