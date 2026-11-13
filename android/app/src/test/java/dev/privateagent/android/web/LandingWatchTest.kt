package dev.privateagent.android.web

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Test

/**
 * The landing page of a click is the page the click's own link opened, after its redirects and a move it made by
 * itself: not a page that failed, one the app or the user opened next, the page going back shows, or what a link to
 * another app left behind. The browser's calls come in the order a WebView makes them.
 */
class LandingWatchTest {
    private var now = 100_000L
    private val watch = LandingWatch { now }

    private fun click(href: String = AD) = watch.clicked(TOKEN, 3, href, SEARCH)

    /** The WebView loads [url] for the page (not the app): its navigation, the page shown, the page finished. */
    private fun opens(url: String, gesture: Boolean = true): LandingWatch.Click? {
        watch.navigation(url, redirect = false, gesture = gesture, allowed = true)
        now += 300
        watch.committed(url)
        now += 300
        return watch.finished(url)
    }

    @Test
    fun theLinksOwnPageIsTheLandingPage() {
        val clicked = click()!!
        // The search page finishing what it loaded is not it.
        assertNull(watch.finished(SEARCH))
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        // Not before the page has started to show.
        assertNull(watch.finished(AD))
        watch.committed(AD)
        assertSame(clicked, watch.finished(AD))
    }

    @Test
    fun theLinksNavigationMayBeHeardOfBeforeTheReport() {
        // The report reached the bridge first, but the app hears of the navigation before it handles the report.
        val reported = now
        now += 20
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        now += 10
        val clicked = watch.clicked(TOKEN, 3, AD, SEARCH, at = reported)!!
        watch.committed(AD)
        assertSame(clicked, watch.finished(AD))
        // A navigation from before the click is not its own.
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        now += 10
        val later = watch.clicked(TOKEN, 3, AD, SEARCH, at = now)!!
        assertNull(watch.finished(AD))
        assertSame(later, watch.click)
    }

    @Test
    fun theServersRedirectsLeadToIt() {
        val clicked = click()!!
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        watch.navigation(TRACKER, redirect = true, gesture = true, allowed = true)
        watch.navigation(ADVERTISER, redirect = true, gesture = true, allowed = true)
        watch.committed(ADVERTISER)
        assertSame(clicked, watch.finished(ADVERTISER))
    }

    @Test
    fun aLinkToNoWebPageIsFollowedByNone() {
        // An intent: or market: link reaches the app as no address at all.
        assertNull(click(href = ""))
        assertNull(watch.click)
        watch.leave() // The user goes back.
        assertNull(opens(SEARCH))
        // A web link whose server sends the browser to another app (blocked) leaves no landing page either, nor does
        // the page the user goes back to.
        click()
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        watch.navigation("intent://open#Intent;scheme=shop;end", redirect = true, gesture = true, allowed = false)
        assertNull(watch.click)
        assertNull(opens(SEARCH, gesture = false))
        // Nor a plain http:// link, which the browser does not open.
        click(href = "http://shop.example.com/p")
        watch.navigation("http://shop.example.com/p", redirect = false, gesture = true, allowed = false)
        assertNull(watch.click)
    }

    @Test
    fun aPageThatFailedIsNoLandingPage() {
        click()
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        watch.committed(AD)
        watch.failed(AD)
        assertNull(watch.finished(AD))
        // The page the app opens next is none either.
        watch.leave()
        assertNull(opens(ADVERTISER))
    }

    @Test
    fun aPageOpenedNextIsNoLandingPage() {
        click()
        // The click's own page did not come (the site kept the click); the user taps another link.
        assertNull(opens(ADVERTISER))
        // The app opens a page (the address bar, a capture), or the user goes back: what the click opened ends.
        click()
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        watch.leave()
        watch.committed(AD)
        assertNull(watch.finished(AD))
    }

    @Test
    fun goingBackBeforeTheLinksPageShowedIsNoLandingPage() {
        click()
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        // The page's own script goes back (no navigation the browser is asked about): the search page shows again.
        watch.committed(SEARCH)
        assertNull(watch.click)
        assertNull(watch.finished(SEARCH))
    }

    @Test
    fun theLinksPageMayMoveOnByItselfButNotWithATap() {
        val clicked = click()!!
        assertSame(clicked, opens(AD))
        // A script of the landing page moves on right after it loaded: the page it moves to is the landing page.
        now += 1_000
        assertSame(clicked, opens(ADVERTISER, gesture = false))
        // A tap of the user's on it is not: they went on from the landing page.
        now += 500
        assertNull(opens("https://advertiser.example.com/other"))
        assertNull(watch.click)
    }

    @Test
    fun aPageThatMovesOnAsItLoadsLeadsToTheNextOne() {
        val clicked = click()!!
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        // Its script asks for the next page before the WebView tells that the first one began to show.
        now += 50
        watch.navigation(ADVERTISER, redirect = false, gesture = false, allowed = true)
        watch.committed(AD)
        watch.committed(ADVERTISER)
        assertSame(clicked, watch.finished(ADVERTISER))
        // A tap of the user's before the click's page came is another page in its place.
        click()
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        watch.navigation(TRACKER, redirect = false, gesture = true, allowed = true)
        assertNull(watch.click)
    }

    @Test
    fun aMoveLongAfterItLoadedIsNotFollowed() {
        val clicked = click()!!
        assertSame(clicked, opens(AD))
        now += LandingWatch.REDIRECT_MS + 1
        assertNull(opens(ADVERTISER, gesture = false))
    }

    @Test
    fun aMoveBackToTheSearchPageIsNotFollowed() {
        val clicked = click()!!
        assertSame(clicked, opens(AD))
        assertNull(opens(SEARCH, gesture = false))
    }

    @Test
    fun theLinksNavigationMustComeInTime() {
        click()
        now += LandingWatch.START_MS + 1
        assertNull(opens(AD))
        // Its page must finish loading in time too.
        val clicked = click()!!
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        watch.committed(AD)
        now += LandingWatch.LANDING_MS + 1
        assertNull(watch.finished(AD))
        assertNull(watch.click)
        assertNull(clicked.landingUrl)
    }

    @Test
    fun anAddressTheSitesScriptGaveTheLinkCountsEvenWhenItCameFirst() {
        val clicked = click()!!
        // The click counter's navigation reached the browser before the follow-up report that names it.
        watch.navigation(TRACKER, redirect = false, gesture = true, allowed = true)
        watch.follow(TOKEN, 3, TRACKER)
        watch.navigation(ADVERTISER, redirect = true, gesture = true, allowed = true)
        watch.committed(ADVERTISER)
        assertSame(clicked, watch.finished(ADVERTISER))
    }

    @Test
    fun aFollowUpCountsOnlyForItsOwnClickAndOnlyBeforeItsPageCame() {
        click()
        watch.follow(TOKEN, 4, TRACKER) // Another item's.
        assertNull(opens(TRACKER))
        val clicked = click()!!
        watch.navigation(AD, redirect = false, gesture = true, allowed = true)
        watch.follow(TOKEN, 3, TRACKER) // Its navigation is under way: too late to change it.
        watch.committed(AD)
        assertSame(clicked, watch.finished(AD))
    }

    @Test
    fun aClickThatWasNotTheUsersIsDropped() {
        val clicked = click()!!
        watch.drop(clicked)
        assertNull(opens(AD))
        // Dropping an older click leaves the newer one.
        val older = click()!!
        val newer = click()!!
        watch.drop(older)
        assertSame(newer, opens(AD))
    }

    @Test
    fun addressesAreComparedWithoutTheirPlace() {
        assertEquals(true, LandingWatch.same("https://a.example.com/p#top", "https://a.example.com/p"))
        assertEquals(false, LandingWatch.same("https://a.example.com/p", "https://a.example.com/q"))
        val clicked = click(href = "$AD#reviews")!!
        assertSame(clicked, opens(AD))
    }

    private companion object {
        const val TOKEN = "0f8fad5b-d9cb-469f-a165-70867728950e"
        const val SEARCH = "https://shop.example.com/search?q=a"
        const val AD = "https://shop.example.com/vp/products/101?src=ad"
        const val TRACKER = "https://ads.example.com/click?id=1"
        const val ADVERTISER = "https://advertiser.example.com/landing"
    }
}
