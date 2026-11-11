package dev.privateagent.android.web

import dev.privateagent.android.util.arrayOrNull
import dev.privateagent.android.util.cleanText
import dev.privateagent.android.util.flag
import dev.privateagent.android.util.objects
import dev.privateagent.android.util.text
import dev.privateagent.android.util.wholeOrNull
import org.json.JSONException
import org.json.JSONObject

/** What capture.js calls an element it reported. */
enum class ItemKind(val key: String) {
    AD("ad"),
    PRODUCT("product"),
    RESULT("result"),
    ;

    companion object {
        fun of(key: String?): ItemKind? = entries.firstOrNull { it.key == key }
    }
}

data class CaptureCounts(val ad: Int = 0, val product: Int = 0, val result: Int = 0) {
    val total: Int get() = ad + product + result

    companion object {
        fun of(items: List<CapturedItem>) = CaptureCounts(
            ad = items.count { it.kind == ItemKind.AD },
            product = items.count { it.kind == ItemKind.PRODUCT },
            result = items.count { it.kind == ItemKind.RESULT },
        )
    }
}

/** One element capture.js reported; `ref` is the name the page's mark (data-pa-capture) and the click watcher use. */
data class CapturedItem(
    val ref: Int,
    val kind: ItemKind,
    val position: Int,
    val title: String,
    val price: String,
    val displayUrl: String,
    val link: String,
    val image: String,
    val text: String,
    val top: Int,
)

data class CaptureResult(
    val url: String,
    val title: String,
    val blocked: Boolean,
    val counts: CaptureCounts,
    val items: List<CapturedItem>,
)

/**
 * Reads what capture.js returned through WebView.evaluateJavascript (the result as JSON text). The page is not trusted:
 * every field is checked and cut to size, links that are not http(s) are dropped, and the counts are those of the items
 * kept.
 */
object CaptureResultParser {
    const val MAX_ITEMS = 200
    private const val MAX_URL = 2048

    fun parse(json: String?): CaptureResult? {
        if (json.isNullOrBlank() || json == "null") return null
        val root = try {
            JSONObject(json)
        } catch (_: JSONException) {
            return null
        }
        val seen = HashSet<Int>()
        val items = root.arrayOrNull("items")?.objects().orEmpty().mapNotNull { item ->
            val kind = ItemKind.of(item.text("kind")) ?: return@mapNotNull null
            val ref = item.wholeOrNull("ref")?.takeIf { it in 0..9_999 }?.toInt() ?: return@mapNotNull null
            if (!seen.add(ref)) return@mapNotNull null
            CapturedItem(
                ref = ref,
                kind = kind,
                position = item.wholeOrNull("position")?.takeIf { it in 1..9_999 }?.toInt() ?: (ref + 1),
                title = cleanText(item.text("title"), 200),
                price = cleanText(item.text("price"), 40),
                displayUrl = cleanText(item.text("displayUrl"), 200),
                link = webUrl(item.text("link")),
                image = webUrl(item.text("image")),
                text = cleanText(item.text("text"), 400),
                top = item.wholeOrNull("top")?.coerceIn(-1_000_000L, 10_000_000L)?.toInt() ?: 0,
            )
        }.take(MAX_ITEMS)
        return CaptureResult(
            url = webUrl(root.text("url")),
            title = cleanText(root.text("title"), 200),
            blocked = root.flag("blocked"),
            counts = CaptureCounts.of(items),
            items = items,
        )
    }

    /** An absolute http(s) URL without spaces, or "". */
    fun webUrl(value: String): String {
        val url = value.trim()
        if (url.length > MAX_URL || url.any { it.isWhitespace() || Character.isISOControl(it) }) return ""
        val lower = url.lowercase()
        return if (lower.startsWith("https://") || lower.startsWith("http://")) url else ""
    }
}

/** A click the page's click watcher (click-watch.js) reported through PaBridge.reportClick. */
data class ClickReport(val token: String, val ref: Int, val kind: ItemKind?, val href: String)

object ClickReportParser {
    private val TOKEN = Regex("^[A-Za-z0-9-]{8,64}$")
    private const val MAX_LENGTH = 8192

    /** The report, or null for anything that is not one; any frame of any page can call the bridge. */
    fun parse(json: String?): ClickReport? {
        if (json == null || json.length > MAX_LENGTH) return null
        val root = try {
            JSONObject(json)
        } catch (_: JSONException) {
            return null
        }
        val token = root.text("token")
        if (!TOKEN.matches(token)) return null
        val ref = root.wholeOrNull("ref")?.takeIf { it in 0..9_999 }?.toInt() ?: return null
        return ClickReport(token, ref, ItemKind.of(root.text("kind")), CaptureResultParser.webUrl(root.text("href")))
    }
}
