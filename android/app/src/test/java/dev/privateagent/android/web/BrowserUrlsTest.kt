package dev.privateagent.android.web

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BrowserUrlsTest {
    @Test
    fun addressesOpenAsHttps() {
        assertEquals(BrowserUrls.Check.Ok("https://shop.example.com"), BrowserUrls.normalize("shop.example.com"))
        assertEquals(BrowserUrls.Check.Ok("https://shop.example.com/a?b=1"), BrowserUrls.normalize("HTTPS://shop.example.com/a?b=1"))
        assertEquals(BrowserUrls.Check.Insecure("http://shop.example.com"), BrowserUrls.normalize("http://shop.example.com"))
    }

    @Test
    fun otherSchemesAndCredentialsAreInvalid() {
        assertEquals(BrowserUrls.Check.Invalid, BrowserUrls.normalize("javascript:alert(1)"))
        assertEquals(BrowserUrls.Check.Invalid, BrowserUrls.normalize("intent://scan/#Intent;end"))
        assertEquals(BrowserUrls.Check.Invalid, BrowserUrls.normalize("file:///sdcard/a.html"))
        assertEquals(BrowserUrls.Check.Invalid, BrowserUrls.normalize("https://user@shop.example.com"))
        assertEquals(BrowserUrls.Check.Invalid, BrowserUrls.normalize("two words"))
        assertEquals(BrowserUrls.Check.Invalid, BrowserUrls.normalize(""))
    }

    @Test
    fun addressBarTellsAddressesFromSearchWords() {
        assertTrue(BrowserUrls.looksLikeAddress("shop.example.com"))
        assertTrue(BrowserUrls.looksLikeAddress("https://shop.example.com"))
        assertFalse(BrowserUrls.looksLikeAddress("캠핑 의자"))
        assertFalse(BrowserUrls.looksLikeAddress("노트북"))
        assertFalse(BrowserUrls.looksLikeAddress("끝에 점."))
    }

    @Test
    fun pageNavigationsStayOnHttps() {
        assertEquals(NavigationDecision.Allow, NavigationPolicy.decide("https://shop.example.com/p/1"))
        assertEquals(NavigationDecision.BlockInsecure("http://shop.example.com/p/1"), NavigationPolicy.decide("http://shop.example.com/p/1"))
        assertEquals(NavigationDecision.BlockExternal("market"), NavigationPolicy.decide("market://details?id=com.example.app"))
        assertEquals(NavigationDecision.BlockExternal("tel"), NavigationPolicy.decide("tel:0123"))
        assertEquals(NavigationDecision.BlockExternal("javascript"), NavigationPolicy.decide("javascript:alert(1)"))
        assertEquals(NavigationDecision.BlockExternal("file"), NavigationPolicy.decide("file:///data/a"))
    }

    @Test
    fun anIntentLinkOpensItsHttpsFallbackInsteadOfAnApp() {
        val link = "intent://item/1#Intent;scheme=shopapp;package=com.example.app;" +
            "S.browser_fallback_url=https%3A%2F%2Fshop.example.com%2Fitem%2F1%3Fa%3Db;end"
        assertEquals(NavigationDecision.OpenInstead("https://shop.example.com/item/1?a=b"), NavigationPolicy.decide(link))
        val insecure = "intent://item/1#Intent;scheme=shopapp;S.browser_fallback_url=http%3A%2F%2Fshop.example.com;end"
        assertEquals(NavigationDecision.BlockExternal("intent"), NavigationPolicy.decide(insecure))
        assertEquals(NavigationDecision.BlockExternal("intent"), NavigationPolicy.decide("intent://item/1#Intent;scheme=shopapp;end"))
        assertNull(NavigationPolicy.intentFallback("intent://item/1"))
    }

    @Test
    fun framesMayLoadWebPagesButNeverOpenApps() {
        assertTrue(NavigationPolicy.allowsSubframe("https://ads.example.com/frame"))
        assertTrue(NavigationPolicy.allowsSubframe("about:blank"))
        assertTrue(NavigationPolicy.allowsSubframe("data:text/html,x"))
        assertFalse(NavigationPolicy.allowsSubframe("intent://x#Intent;end"))
        assertFalse(NavigationPolicy.allowsSubframe("market://details?id=x"))
    }
}
