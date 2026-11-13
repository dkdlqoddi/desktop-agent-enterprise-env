package dev.privateagent.android

import android.content.ContentValues
import android.database.sqlite.SQLiteFullException
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.agent.ChatAgent
import dev.privateagent.android.agent.ScheduleDays
import dev.privateagent.android.data.AppDatabase
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.NewCapture
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.data.ScheduleStore
import dev.privateagent.android.data.StoreException
import dev.privateagent.android.data.StoreProblem
import dev.privateagent.android.llm.LlmService
import dev.privateagent.android.ui.Navigator
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureResult
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import org.junit.runner.RunWith

/**
 * A database that cannot take another page, as on a phone whose storage is full (SQLite's max_page_count, in a
 * database that lives in memory only): the stores give their own StoreException, never the platform's error, what is
 * there is still read, and the chat says why the request failed instead of ending the app.
 */
@RunWith(AndroidJUnit4::class)
class StoreErrorsTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val graph = (context.applicationContext as PrivateAgentApp).graph
    private val database = AppDatabase(context, name = null)
    private val messages = MessageStore(database)
    private val records = RecordStore(database)
    private val schedules = ScheduleStore(database)

    @After
    fun close() = database.close()

    /** Allows no page beyond those the database has, then fills the messages' last page: the next message fails. */
    private fun fill() {
        val db = database.writableDatabase
        val pages = db.rawQuery("PRAGMA page_count", null).use {
            it.moveToFirst()
            it.getLong(0)
        }
        db.rawQuery("PRAGMA max_page_count = $pages", null).use { it.moveToFirst() }
        try {
            repeat(10_000) {
                db.insertOrThrow(
                    "messages",
                    null,
                    ContentValues().apply {
                        put("role", "notice")
                        put("text", "")
                        put("created_at", 0L)
                    },
                )
            }
            fail("The database never filled up")
        } catch (_: SQLiteFullException) {
            // Full: no write that needs a page more goes through.
        }
    }

    private suspend fun assertFull(what: String, write: suspend () -> Unit) {
        try {
            write()
            fail("$what went through")
        } catch (failure: StoreException) {
            assertEquals(what, StoreProblem.FULL, failure.problem)
        }
    }

    @Test
    fun aFullDatabaseGivesTheStoresOwnErrorAndWhatIsThereIsStillRead() = runBlocking {
        messages.addUser("가득 차기 전")
        fill()
        val long = "가".repeat(50_000)
        val result = CaptureResult(
            url = "https://shop.example.com/",
            title = "검색",
            blocked = false,
            counts = CaptureCounts(),
            items = emptyList(),
        )
        assertFull("a message") { messages.addUser(long) }
        val token = "d8f1b0c2-3a4e-4b5c-9d6e-7f8091a2b3c4"
        assertFull("a capture") { records.saveCapture(NewCapture("coupang", long, CaptureSource.CHAT, token), result) }
        assertFull("a schedule") { schedules.add("coupang", long, "09:00", ScheduleDays.DAILY) }
        assertEquals("가득 차기 전", messages.all(limit = 1_000_000).first().text)
        assertTrue(records.captures().isEmpty())
    }

    @Test
    fun theChatSaysWhyItCouldNotTakeAMessageAndTheAppGoesOn() = runBlocking {
        fill()
        val navigator = Navigator()
        val agent = ChatAgent(
            context = context,
            messages = messages,
            schedules = schedules,
            settings = graph.settings,
            llm = LlmService(),
            actions = graph.actions,
            navigator = navigator,
            sites = { graph.sites },
            texts = graph.texts,
        )
        // Listening before the message goes: a message nobody listens for is not kept.
        val said = async(Dispatchers.Default, CoroutineStart.UNDISPATCHED) {
            withTimeout(20_000) { navigator.snackbar.first() }
        }
        // Neither the message nor the notice of its failure fits: the screen says why.
        agent.send("노트북 광고 기록해 줘")
        agent.join()
        assertEquals(graph.texts.storage(StoreProblem.FULL), said.await())
        assertEquals(false, agent.busy.value)
    }
}
