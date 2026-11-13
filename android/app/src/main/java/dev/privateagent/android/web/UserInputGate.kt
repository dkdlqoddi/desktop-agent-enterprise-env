package dev.privateagent.android.web

/**
 * The user's own input on the page (a touch lifted, a click key, a mouse button, an accessibility click), as the
 * browser's WebView receives it, and the click reports it lets through. click-watch.js reports only trusted clicks, but
 * a page's script can call PaBridge itself: a report counts only when an input came shortly before it ([windowMs]),
 * and each input counts for one report. A page cannot record clicks the user never made; what it can still do is
 * send a report of its own right after a real tap, in place of the tap's.
 *
 * Times are the device's uptime in milliseconds (MotionEvent.getEventTime). Inputs come on the main thread and
 * reports on the JavaScript bridge's thread.
 */
class UserInputGate(private val windowMs: Long = WINDOW_MS) {
    private val inputs = ArrayDeque<Long>()

    /** An input of the user's at [at]. */
    @Synchronized
    fun record(at: Long) {
        inputs.addLast(at)
        while (inputs.size > MAX_INPUTS) inputs.removeFirst()
    }

    /**
     * Whether a report that came at [at] follows an input of the user's; the input it follows is used up. An input
     * older than the window no longer counts.
     */
    @Synchronized
    fun take(at: Long): Boolean {
        while (inputs.isNotEmpty() && at - inputs.first() > windowMs) inputs.removeFirst()
        val index = inputs.indexOfFirst { it <= at }
        if (index < 0) return false
        inputs.removeAt(index)
        return true
    }

    companion object {
        /**
         * A tap's click reaches the bridge within a few hundred milliseconds (300 more on a page that waits for a
         * double tap); a busy page takes longer.
         */
        const val WINDOW_MS = 2_000L
        private const val MAX_INPUTS = 8
    }
}
