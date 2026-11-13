package dev.privateagent.android.ui

import dev.privateagent.android.data.ItemFilter
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map

enum class Tab { CHAT, BROWSER, RECORDS, SETTINGS }

enum class RecordsView { ITEMS, CAPTURES }

/** A short message at the bottom of the screen, and the button that takes back what it tells of ([undo]), if any. */
class Notice(val text: String, val undo: (suspend () -> Unit)? = null)

/** The screen state that other parts of the app change: the tab, the records filter, and short messages. */
class Navigator {
    private val tabState = MutableStateFlow(Tab.CHAT)
    val tab: StateFlow<Tab> = tabState

    val recordsFilter = MutableStateFlow(ItemFilter())
    val recordsView = MutableStateFlow(RecordsView.ITEMS)

    private val messages = MutableSharedFlow<Notice>(extraBufferCapacity = 4)
    val notices: SharedFlow<Notice> = messages

    /** What the messages say. */
    val snackbar: Flow<String> = messages.map { it.text }

    fun show(tab: Tab) {
        tabState.value = tab
    }

    /** The records tab with the items of one capture. */
    fun showCapture(captureId: Long) {
        recordsFilter.value = ItemFilter(captureId = captureId)
        recordsView.value = RecordsView.ITEMS
        tabState.value = Tab.RECORDS
    }

    fun showRecords(filter: ItemFilter) {
        recordsFilter.value = filter
        recordsView.value = RecordsView.ITEMS
        tabState.value = Tab.RECORDS
    }

    fun say(text: String) {
        messages.tryEmit(Notice(text))
    }

    /** A message with 되돌리기, which runs [undo]. */
    fun say(text: String, undo: suspend () -> Unit) {
        messages.tryEmit(Notice(text, undo))
    }
}
