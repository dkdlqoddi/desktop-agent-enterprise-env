package dev.privateagent.android.web

/**
 * Which page is the landing page of the user's click on the link of a marked item: the page that click opens. The
 * browser tells of the click (click-watch.js reports only a click on an item's link) and of what the WebView does;
 * this decides. The landing page is the main-frame navigation to the link's address (or to the address the site's
 * script gave the link as the click went, which a follow-up report tells), followed through its server redirects and
 * through a move the page makes by itself as it loads (a script or a meta refresh, without a tap), once it has
 * finished loading. Nothing else is: a link to another app (intent:, market:) or a plain http:// page the browser does
 * not open, the page a blocked link leaves the user on, going back or forward, a page the app opens (the address bar,
 * a capture, an intent's fallback page), a page that failed to load, and any page that merely came next.
 *
 * Times are the device's elapsed time in milliseconds ([clock]); every call comes on the main thread.
 */
class LandingWatch(private val clock: () -> Long) {
    /** A click whose landing page is awaited. The browser keeps its item and the landing page found for it here. */
    class Click internal constructor(
        val token: String,
        val ref: Int,
        href: String,
        internal val at: Long,
        internal val from: String,
    ) {
        internal val starts = mutableListOf(href)
        internal val chain = mutableListOf<String>()
        internal var committed = false
        internal var finishedAt = 0L

        /** The clicked item, once the click is saved; its landing page may be found before. */
        var itemId: Long? = null
        var landingUrl: String? = null
        var landingTitle = ""
    }

    private class Navigation(
        val url: String,
        val redirect: Boolean,
        val gesture: Boolean,
        val allowed: Boolean,
        val at: Long,
    )

    /** The click being followed, until its landing page settled or its time is over. */
    var click: Click? = null
        private set

    /**
     * The main-frame navigations of the last moments. A click's report reaches the bridge before its navigation starts
     * (the page waits for the bridge), but the app may hear of the navigation first, and a follow-up a handler held up
     * comes after the navigation it names.
     */
    private val recent = ArrayDeque<Navigation>()

    /**
     * The user's click on a marked link to [href] (an https or http address, "" for any other) on the page at [from],
     * whose report reached the bridge at [at]: the click to follow, or null when its link opens no web page. A click
     * before it is followed no more.
     */
    fun clicked(token: String, ref: Int, href: String, from: String, at: Long = clock()): Click? {
        if (href.isEmpty()) {
            click = null
            return null
        }
        return Click(token, ref, href, at, from).also {
            click = it
            catchUp(it)
        }
    }

    /** The address the site's script gave the clicked link as the click went: its navigation may go there instead. */
    fun follow(token: String, ref: Int, href: String) {
        val armed = live() ?: return
        if (href.isEmpty() || armed.token != token || armed.ref != ref || armed.chain.isNotEmpty()) return
        if (clock() - armed.at > START_MS) return
        if (armed.starts.none { same(it, href) }) armed.starts += href
        catchUp(armed)
    }

    /** No longer follow [given], which turned out to be no click of the user's. */
    fun drop(given: Click) {
        if (click === given) click = null
    }

    /** The app opens a page itself, or goes back or forward: what the click opened ends there. */
    fun leave() {
        click = null
    }

    /**
     * A main-frame navigation the page started (WebViewClient.shouldOverrideUrlLoading): a server redirect when
     * [redirect], with a tap or click behind it when [gesture], and one the browser opens when [allowed].
     */
    fun navigation(url: String, redirect: Boolean, gesture: Boolean, allowed: Boolean) {
        val now = clock()
        val navigation = Navigation(url, redirect, gesture, allowed, now)
        recent.addLast(navigation)
        while (recent.size > MAX_RECENT || now - recent.first().at > RECENT_MS) recent.removeFirst()
        step(navigation)
    }

    /** A page began to show in the main frame (WebViewClient.onPageStarted). */
    fun committed(url: String) {
        val armed = live() ?: return
        if (armed.chain.isEmpty()) return
        if (armed.chain.any { same(it, url) }) {
            armed.committed = true
        } else {
            // Another page in its place: going back by the page's own script, a page the click did not lead to.
            click = null
        }
    }

    /** The main frame's page at [url] failed to load (WebViewClient.onReceivedError): it is no landing page. */
    fun failed(url: String) {
        val armed = click ?: return
        if (armed.chain.any { same(it, url) }) click = null
    }

    /** The main frame finished loading [url]: the click whose landing page it is, or null. */
    fun finished(url: String): Click? {
        val armed = live() ?: return null
        if (!armed.committed || armed.chain.none { same(it, url) }) return null
        armed.finishedAt = clock()
        return armed
    }

    private fun step(navigation: Navigation) {
        val armed = live() ?: return
        if (armed.chain.isEmpty()) {
            val inTime = navigation.at - armed.at in 0..START_MS
            if (!navigation.redirect && inTime && armed.starts.any { same(it, navigation.url) }) {
                // The link's own navigation: one the browser does not open (another app, http://) opens no page.
                if (navigation.allowed) armed.chain += navigation.url else click = null
            }
            return
        }
        // The WebView tells that a page began to show (onPageStarted) after the fact: a page that moves on as it loads
        // asks for its next page before that. A move without a tap is the page's own, but not one back to where the
        // click was.
        val movedOn = !navigation.gesture && !same(navigation.url, armed.from) &&
            (armed.finishedAt == 0L || navigation.at - armed.finishedAt <= REDIRECT_MS)
        when {
            // A redirect belongs to the navigation under way, which is the click's.
            navigation.redirect -> if (navigation.allowed) armed.chain += navigation.url else click = null
            // The landing page moved on by itself: the page it moves to is the one.
            movedOn -> if (navigation.allowed) {
                armed.chain += navigation.url
                armed.committed = false
                armed.finishedAt = 0L
            } else {
                click = null
            }
            // Another page took its place before it opened, or the user went on from it.
            else -> click = null
        }
    }

    private fun catchUp(armed: Click) {
        for (navigation in recent.toList()) {
            if (click !== armed) return
            if (navigation.at >= armed.at) step(navigation)
        }
    }

    /** The click being followed, or null once its time is over: its page never came, or settled. */
    private fun live(): Click? {
        val armed = click ?: return null
        val now = clock()
        val over = if (armed.finishedAt == 0L) now - armed.at > LANDING_MS else now - armed.finishedAt > REDIRECT_MS
        if (over) click = null
        return click
    }

    companion object {
        /** The click's own navigation begins within this long of the click. */
        const val START_MS = 15_000L

        /** Its page finishes loading within this long of the click. */
        const val LANDING_MS = 30_000L

        /** A move the landing page makes by itself this long after it loaded is still followed. */
        const val REDIRECT_MS = 3_000L

        private const val RECENT_MS = 5_000L
        private const val MAX_RECENT = 8

        /** Whether two addresses name one page: a place in it (#...) aside. */
        fun same(address: String, other: String): Boolean =
            address.substringBefore('#') == other.substringBefore('#')
    }
}
