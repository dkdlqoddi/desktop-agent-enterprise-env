package dev.privateagent.android.data

import dev.privateagent.android.agent.RecordKindFilter
import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.web.ItemKind

enum class MessageRole(val key: String) {
    USER("user"),
    ASSISTANT("assistant"),

    /** A tool call the app shows as a card: it waits for approval, runs and ends with a result line. */
    ACTION("action"),

    /** A message of the app itself: an error, or the LLM setup that is missing. */
    NOTICE("notice"),
    ;

    companion object {
        fun of(key: String?) = entries.firstOrNull { it.key == key } ?: NOTICE
    }
}

enum class ActionState(val key: String) {
    PENDING("pending"),
    RUNNING("running"),
    DONE("done"),
    FAILED("failed"),
    CANCELLED("cancelled"),
    ;

    companion object {
        fun of(key: String?) = entries.firstOrNull { it.key == key }
    }
}

enum class NoticeKind(val key: String) {
    SETUP("setup"),
    ERROR("error"),
    INFO("info"),
    ;

    companion object {
        fun of(key: String?) = entries.firstOrNull { it.key == key }
    }
}

/** What started a capture. */
enum class CaptureSource(val key: String) {
    CHAT("chat"),
    SCHEDULE("schedule"),

    /** The browser's "record this page" button. */
    MANUAL("manual"),

    /** The user clicked an ad on a page they were browsing: the page's marked items were saved with the click. */
    CLICK("click"),
    ;

    companion object {
        fun of(key: String?) = entries.firstOrNull { it.key == key } ?: MANUAL
    }
}

data class ChatMessage(
    val id: Long,
    val role: MessageRole,
    val text: String,
    val createdAt: Long,
    val tool: String? = null,
    val args: String? = null,
    val state: ActionState? = null,
    val result: String? = null,
    val captureId: Long? = null,
    val notice: NoticeKind? = null,
)

data class CaptureRow(
    val id: Long,
    val site: String,
    val query: String,
    val url: String,
    val title: String,
    val capturedAt: Long,
    val adCount: Int,
    val productCount: Int,
    val resultCount: Int,
    val source: CaptureSource,
)

data class ItemRow(
    val id: Long,
    val captureId: Long,
    val ref: Int,
    val kind: ItemKind,
    val position: Int,
    val title: String,
    val price: String,
    val displayUrl: String,
    val linkUrl: String,
    val imageUrl: String,
    val text: String,
    val clickedAt: Long?,
    val clickedUrl: String?,
    val landingUrl: String?,
    val landingTitle: String?,
    val site: String,
    val query: String,
    val capturedAt: Long,
    val pageUrl: String,
)

/** The records screen's and list_records' filter; null fields do not filter. */
data class ItemFilter(
    val site: String? = null,
    val kind: RecordKindFilter? = null,
    val search: String = "",
    val captureId: Long? = null,
)

data class ScheduleRow(
    val id: Long,
    val site: String,
    val query: String,
    val time: String,
    val days: ScheduleDays,
    val enabled: Boolean,
    val createdAt: Long,
    val nextRunAt: Long?,
)

/** A capture about to be saved. */
data class NewCapture(
    val site: String,
    val query: String,
    val source: CaptureSource,
    val token: String,
    val capturedAt: Long = System.currentTimeMillis(),
)
