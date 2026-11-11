package dev.privateagent.android.web

import dev.privateagent.android.TestSites
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class SitesTest {
    private val sites = TestSites.sites

    @Test
    fun theSharedListHasTheFourSites() {
        assertEquals(listOf("쿠팡", "아마존", "네이버", "구글"), listOf("coupang", "amazon", "naver", "google").map { sites.byKey(it)!!.name })
        assertTrue(sites.all.all { it.search.startsWith("https://") && it.home.startsWith("https://") })
    }

    @Test
    fun searchPagesCarryTheEncodedQuery() {
        assertEquals(
            "https://www.coupang.com/np/search?q=%EB%85%B8%ED%8A%B8%EB%B6%81",
            sites.searchUrl(sites.byKey("coupang")!!, "노트북"),
        )
        assertEquals(
            "https://search.naver.com/search.naver?query=%EC%BA%A0%ED%95%91%20%EC%9D%98%EC%9E%90",
            sites.searchUrl(sites.byKey("naver")!!, "캠핑 의자"),
        )
        assertEquals("https://www.amazon.com/s?k=a%26b%3Dc%23d", sites.searchUrl(sites.byKey("amazon")!!, "a&b=c#d"))
    }

    @Test
    fun pagesAreMatchedToSitesByHost() {
        assertEquals("coupang", sites.forUrl("https://m.coupang.com/nm/search?q=x")?.key)
        assertEquals("amazon", sites.forUrl("https://www.amazon.co.jp/s?k=x")?.key)
        assertEquals("naver", sites.forUrl("https://m.search.naver.com/search.naver?query=x")?.key)
        assertEquals("google", sites.forUrl("https://www.google.co.kr/search?q=x")?.key)
        assertNull(sites.forUrl("https://notcoupang.com/"))
        assertNull(sites.forUrl("https://coupang.com.example.test/"))
        assertNull(sites.forUrl("not a url"))
    }

    @Test
    fun theQueryIsReadBackFromASearchPage() {
        assertEquals(
            "캠핑 의자",
            sites.queryFromUrl(
                sites.byKey("naver")!!,
                "https://search.naver.com/search.naver?where=nexearch&query=%EC%BA%A0%ED%95%91+%EC%9D%98%EC%9E%90",
            ),
        )
        assertEquals("headphones", sites.queryFromUrl(sites.byKey("amazon")!!, "https://www.amazon.com/s?k=headphones&ref=x"))
        assertNull(sites.queryFromUrl(sites.byKey("google")!!, "https://www.google.com/"))
    }

    @Test
    fun namesResolveToSites() {
        assertEquals("coupang", sites.resolve("쿠팡")?.key)
        assertEquals("google", sites.resolve("GOOGLE")?.key)
        assertNull(sites.resolve("ebay"))
        assertNull(sites.resolve(""))
    }

    @Test
    fun entriesWithoutAnHttpsSearchTemplateAreLeftOut() {
        val parsed = Sites.parse(
            """{"good": {"name": "Good", "home": "https://good.example.com/", "search": "https://good.example.com/s?q={query}", "hosts": ["good.example.com"]},
               "plain": {"name": "Plain", "home": "http://plain.example.com/", "search": "http://plain.example.com/s?q={query}"},
               "noquery": {"name": "No", "home": "https://no.example.com/", "search": "https://no.example.com/s"},
               "Bad Key": {"name": "Bad", "home": "https://bad.example.com/", "search": "https://bad.example.com/s?q={query}"}}""",
        )
        assertEquals(listOf("good"), parsed.keys)
        assertEquals(0, Sites.parse("not json").all.size)
    }

    @Test
    fun theTestsReadTheSharedFileItself() {
        assertTrue(TestSites.file.path.replace(File.separatorChar, '/').endsWith("shared/web-capture/sites.json"))
    }
}
