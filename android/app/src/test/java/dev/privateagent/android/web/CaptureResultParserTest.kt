package dev.privateagent.android.web

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class CaptureResultParserTest {
    private fun item(ref: Int, kind: String, title: String = "상품 $ref", link: String = "https://shop.example.com/p/$ref") =
        JSONObject().put("ref", ref).put("kind", kind).put("position", ref + 1).put("title", title).put("price", "1,000원")
            .put("displayUrl", "shop.example.com").put("link", link).put("image", "https://img.example.com/$ref.png")
            .put("text", "광고 $title 1,000원").put("top", 100 * ref)

    /** What capture.js returns, as WebView.evaluateJavascript hands it over: JSON text. */
    private fun result(vararg items: JSONObject, blocked: Boolean = false) = JSONObject()
        .put("url", "https://shop.example.com/search?q=%EB%85%B8%ED%8A%B8%EB%B6%81")
        .put("title", "노트북 - 검색")
        .put("blocked", blocked)
        .put("counts", JSONObject().put("ad", 99).put("product", 99).put("result", 99))
        .put("items", JSONArray(items.toList()))
        .toString()

    @Test
    fun aCaptureIsRead() {
        val parsed = CaptureResultParser.parse(result(item(0, "ad"), item(1, "product"), item(2, "product"), item(3, "result")))!!
        assertEquals("https://shop.example.com/search?q=%EB%85%B8%ED%8A%B8%EB%B6%81", parsed.url)
        assertEquals("노트북 - 검색", parsed.title)
        assertFalse(parsed.blocked)
        // The counts are those of the items kept, not what the page claimed.
        assertEquals(CaptureCounts(ad = 1, product = 2, result = 1), parsed.counts)
        val first = parsed.items.first()
        assertEquals(0, first.ref)
        assertEquals(ItemKind.AD, first.kind)
        assertEquals(1, first.position)
        assertEquals("상품 0", first.title)
        assertEquals("1,000원", first.price)
        assertEquals("shop.example.com", first.displayUrl)
        assertEquals("https://shop.example.com/p/0", first.link)
        assertEquals("https://img.example.com/0.png", first.image)
        assertEquals(0, first.top)
    }

    @Test
    fun aRobotCheckIsReported() {
        assertTrue(CaptureResultParser.parse(result(blocked = true))!!.blocked)
    }

    @Test
    fun itemsThatAreNotItemsAreLeftOut() {
        val noRef = item(5, "ad").apply { remove("ref") }
        val parsed = CaptureResultParser.parse(
            result(item(0, "ad"), item(0, "ad", "같은 ref"), item(1, "banner"), noRef, item(2, "product").put("ref", -1)),
        )!!
        assertEquals(listOf(0), parsed.items.map { it.ref })
        assertEquals("상품 0", parsed.items.single().title)
    }

    @Test
    fun linksThatAreNotWebAddressesAreDropped() {
        val parsed = CaptureResultParser.parse(
            result(item(0, "ad", link = "javascript:alert(1)"), item(1, "ad", link = "data:text/html,x"), item(2, "ad", link = "https://a b")),
        )!!
        assertTrue(parsed.items.all { it.link.isEmpty() })
    }

    @Test
    fun longTextIsCut() {
        val parsed = CaptureResultParser.parse(result(item(0, "product", title = "가".repeat(500))))!!
        assertEquals(200, parsed.items.single().title.length)
    }

    @Test
    fun nothingReadableIsNull() {
        assertNull(CaptureResultParser.parse(null))
        assertNull(CaptureResultParser.parse("null"))
        assertNull(CaptureResultParser.parse("\"a string\""))
        assertNull(CaptureResultParser.parse("{broken"))
    }

    @Test
    fun aClickReportIsRead() {
        val report = ClickReportParser.parse(
            JSONObject().put("token", "0f8fad5b-d9cb-469f-a165-70867728950e").put("ref", 3).put("kind", "ad")
                .put("href", "https://shop.example.com/p/3").put("at", "2026-11-11T00:00:00.000Z").toString(),
        )!!
        assertEquals("0f8fad5b-d9cb-469f-a165-70867728950e", report.token)
        assertEquals(3, report.ref)
        assertEquals(ItemKind.AD, report.kind)
        assertEquals("https://shop.example.com/p/3", report.href)
    }

    @Test
    fun clickReportsAreCheckedStrictly() {
        val good = { JSONObject().put("token", "0f8fad5b-d9cb-469f-a165-70867728950e").put("ref", 3) }
        assertNull(ClickReportParser.parse(good().put("token", "x").toString()))
        assertNull(ClickReportParser.parse(good().put("token", "abc\"; drop table").toString()))
        assertNull(ClickReportParser.parse(good().put("ref", -1).toString()))
        assertNull(ClickReportParser.parse(good().put("ref", "3").toString()))
        assertNull(ClickReportParser.parse(good().put("ref", 1.5).toString()))
        assertNull(ClickReportParser.parse("not json"))
        assertNull(ClickReportParser.parse(good().put("pad", "x".repeat(9000)).toString()))
        assertEquals("", ClickReportParser.parse(good().put("href", "javascript:void(0)").toString())!!.href)
        // An SVG link's href is an object in the page; JSON.stringify makes it {}.
        assertEquals("", ClickReportParser.parse(good().put("href", JSONObject()).toString())!!.href)
        assertNull(ClickReportParser.parse(good().put("kind", "banner").toString())!!.kind)
        // A follow-up of a click (the address the site's script gave the link) is no click of its own.
        assertNull(ClickReportParser.parse(good().put("follow", true).toString()))
    }
}
