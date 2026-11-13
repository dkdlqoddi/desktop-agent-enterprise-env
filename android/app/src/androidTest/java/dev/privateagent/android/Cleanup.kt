package dev.privateagent.android

import dev.privateagent.android.data.TrashKind
import dev.privateagent.android.data.TrashStore

// What a test made in the app's own database on the test device goes for good when it ends: a delete only moves it to
// the trash, so its entries there are deleted too.

/** Deletes for good the recorded page [id] a test made, from the records or from the trash. */
suspend fun AppGraph.forgetCapture(id: Long) {
    records.deleteCapture(id)
    val entries = trash.page(TrashKind.CAPTURE, limit = TrashStore.MAX_PAGE).entries.filter { it.capture?.id == id }
    trash.purge(entries.map { it.id })
}

/** Deletes for good the schedule [id] a test made, its reminder cancelled, from the settings or from the trash. */
suspend fun AppGraph.forgetSchedule(id: Long) {
    scheduler.delete(id)
    val entries = trash.page(TrashKind.SCHEDULE, limit = TrashStore.MAX_PAGE).entries.filter { it.schedule?.id == id }
    trash.purge(entries.map { it.id })
}
