package dev.privateagent.android.ui

import dev.privateagent.android.AppGraph
import dev.privateagent.android.data.StoreException

/**
 * A screen's database work. When the database cannot be read or written (the phone's storage is full), the screen
 * says so for a moment and goes on with [fallback]: what it showed before, or nothing done.
 */
suspend fun <T> AppGraph.orSay(fallback: T, work: suspend () -> T): T = try {
    work()
} catch (failure: StoreException) {
    navigator.say(texts.storage(failure.problem))
    fallback
}
