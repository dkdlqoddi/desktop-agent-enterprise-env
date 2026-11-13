package dev.privateagent.android.data

import dev.privateagent.android.agent.ScheduleDays
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** What the trash's list makes of the rows it keeps: their titles, and the page or schedule each was. */
class TrashEntryTest {
    @Test
    fun aPageGoesByItsSearchWordsItsTitleOrItsAddressAndAScheduleByItsWords() {
        val page = JSONObject().put("query", "  노트북\n광고 ").put("title", "검색").put("url", "https://shop.example.com/")
        assertEquals("노트북 광고", trashTitle(TrashKind.CAPTURE, page))
        assertEquals("검색", trashTitle(TrashKind.CAPTURE, page.put("query", " ")))
        assertEquals("https://shop.example.com/", trashTitle(TrashKind.CAPTURE, page.put("title", JSONObject.NULL)))
        assertEquals(200, trashTitle(TrashKind.CAPTURE, JSONObject().put("query", "가".repeat(300))).length)
        assertEquals("의자", trashTitle(TrashKind.SCHEDULE, JSONObject().put("query", "의자")))
        assertEquals("", trashTitle(TrashKind.CHAT, page))
    }

    @Test
    fun anEntryCarriesThePageTheScheduleOrTheNumberOfMessagesItWas() {
        val page = JSONObject().put("id", 5).put("site", "coupang").put("query", "노트북").put("url", "https://shop.example.com/")
            .put("title", "검색").put("captured_at", 1_000L).put("ad_count", 2).put("product_count", 3)
            .put("result_count", 0).put("source", "schedule").put("token", "t")
        val capture = TrashEntry.of(1, TrashKind.CAPTURE, "노트북", page, retired = true, deletedAt = 9L).capture!!
        assertEquals(
            CaptureRow(5, "coupang", "노트북", "https://shop.example.com/", "검색", 1_000L, 2, 3, 0, CaptureSource.SCHEDULE),
            capture,
        )

        val schedule = JSONObject().put("id", 7).put("site", "naver").put("query", "의자").put("time", "09:00")
            .put("days", "weekdays").put("enabled", 0).put("created_at", 4L).put("next_run_at", JSONObject.NULL)
        val entry = TrashEntry.of(2, TrashKind.SCHEDULE, "의자", schedule, retired = false, deletedAt = 9L)
        assertEquals(ScheduleRow(7, "naver", "의자", "09:00", ScheduleDays.WEEKDAYS, false, 4L, null), entry.schedule)
        assertNull(entry.capture)
        assertFalse(entry.retired)

        val chat = TrashEntry.of(3, TrashKind.CHAT, "안녕", JSONObject().put("messages", 12), retired = false, deletedAt = 9L)
        assertEquals(12, chat.messages)
        assertTrue(chat.capture == null && chat.schedule == null)
        // A row this version cannot read is listed all the same.
        assertEquals(0, TrashEntry.of(4, TrashKind.CHAT, "", JSONObject(), retired = false, deletedAt = 9L).messages)
        assertEquals(ScheduleDays.DAILY, TrashEntry.of(5, TrashKind.SCHEDULE, "", JSONObject(), false, 9L).schedule!!.days)
    }
}
