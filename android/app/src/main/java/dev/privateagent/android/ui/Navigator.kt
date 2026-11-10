package dev.privateagent.android.ui

import dev.privateagent.android.data.ItemFilter
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

enum class Tab { CHAT, BROWSER, RECORDS, SETTINGS }

enum class RecordsView { ITEMS, CAPTURES }

/** The screen state that other parts of the app change: the tab, the records filter, and short messages. */
class Navigator {
    private val tabState = MutableStateFlow(Tab.CHAT)
    val tab: StateFlow<Tab> = tabState

    val recordsFilter = MutableStateFlow(ItemFilter())
    val recordsView = MutableStateFlow(RecordsView.ITEMS)

    private val messages = MutableSharedFlow<String>(extraBufferCapacity = 4)
    val snackbar: SharedFlow<String> = messages

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
        messages.tryEmit(text)
    }
}
