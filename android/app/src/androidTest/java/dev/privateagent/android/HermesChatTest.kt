package dev.privateagent.android

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.agent.AppActions
import dev.privateagent.android.agent.ChatAgent
import dev.privateagent.android.agent.HermesChat
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.AppDatabase
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.MessageRole
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.NewCapture
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.hermes.HermesMessages
import dev.privateagent.android.hermes.HermesModel
import dev.privateagent.android.hermes.HermesRuntime
import dev.privateagent.android.hermes.HermesSession
import dev.privateagent.android.ui.Navigator
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureResult
import dev.privateagent.android.web.CapturedItem
import dev.privateagent.android.web.ItemKind
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.util.Collections

/**
 * One real chat turn on the bundled Hermes worker, with a stand-in model answering its inference frames (as the
 * desktop's test_hermes.py live test): Hermes calls the app's list_records itself, the app checks and runs it and
 * shows its card, and Hermes answers with what it found. Also a clarify question answered in the chat, and a stop.
 */
@RunWith(AndroidJUnit4::class)
class HermesChatTest {
    private val instrumentation = InstrumentationRegistry.getInstrumentation()
    private val context = instrumentation.targetContext
    private val graph = (context.applicationContext as PrivateAgentApp).graph
    private val database = AppDatabase(context, name = null)
    private val messages = MessageStore(database)
    private val records = RecordStore(database)
    private val runtime = HermesRuntime(context)
    private val session = HermesSession(runtime)
    private val actions = AppActions(
        context, records, graph.browser, graph.scheduler, graph.notifications, Navigator(), { graph.sites }, graph.texts,
    )
    private val chat = HermesChat(context, messages, actions, Navigator(), { graph.sites }, graph.texts, session)

    @Before
    fun bundled() {
        assumeTrue("This APK has no Hermes runtime (android/hermes/build_runtime.py)", runtime.isBundled)
    }

    @After
    fun close() {
        session.shutdown()
        database.close()
    }

    /** A stand-in model: [answer] gets each request Hermes makes (the main turn's and any auxiliary one). */
    private class Model(private val answer: suspend (JSONObject) -> JSONObject) : HermesModel {
        val requests: MutableList<JSONObject> = Collections.synchronizedList(mutableListOf())

        override suspend fun complete(request: JSONObject): JSONObject {
            requests += request
            return answer(request)
        }
    }

    private fun words(text: String) = HermesMessages.completion(JSONObject().put("content", text), "stand-in", null, null)

    private fun call(name: String, args: JSONObject) = HermesMessages.completion(
        JSONObject().put("content", JSONObject.NULL).put(
            "tool_calls",
            JSONArray().put(
                JSONObject().put("id", "call_1").put("type", "function")
                    .put("function", JSONObject().put("name", name).put("arguments", args.toString())),
            ),
        ),
        "stand-in",
        null,
        null,
    )

    private fun names(request: JSONObject): List<String> {
        val tools = request.optJSONArray("tools") ?: JSONArray()
        return (0 until tools.length()).map { tools.getJSONObject(it).getJSONObject("function").getString("name") }
    }

    private fun toolResult(request: JSONObject): String? {
        val sent = request.getJSONArray("messages")
        return (0 until sent.length()).map { sent.getJSONObject(it) }.lastOrNull { it.optString("role") == "tool" }
            ?.optString("content")
    }

    private fun saveRecord() = runBlocking {
        val item = CapturedItem(
            ref = 0, kind = ItemKind.AD, position = 1, title = "노트북 특가", price = "999,000원",
            displayUrl = "shop.example.com", link = "https://shop.example.com/p/1", image = "", text = "노트북 특가", top = 0,
        )
        val result = CaptureResult(
            url = "https://shop.example.com/search?q=노트북", title = "검색", blocked = false,
            counts = CaptureCounts(ad = 1), items = listOf(item),
        )
        records.saveCapture(NewCapture("coupang", "노트북", CaptureSource.CHAT, "token-1"), result)
    }

    @Test
    fun hermesCallsAnAppToolAndAnswersWithWhatItFound() = runBlocking {
        saveRecord()
        val model = Model { request ->
            when {
                // The main turn offers the app's tools: call list_records once, then answer with its result.
                "list_records" in names(request) && toolResult(request) == null ->
                    call("list_records", JSONObject().put("query", "노트북"))
                toolResult(request) != null -> words("노트북 광고 기록 1건을 찾았습니다.")
                else -> words("ok")
            }
        }
        withTimeout(300_000) { chat.converse(model, "stand-in", "노트북 광고 기록 찾아 줘", emptyList()) }

        val all = messages.all()
        val card = all.single { it.role == MessageRole.ACTION }
        assertEquals("list_records", card.tool)
        assertEquals(ActionState.DONE, card.state)
        assertTrue(card.result.orEmpty(), card.result.orEmpty().contains("1"))
        assertEquals("노트북 광고 기록 1건을 찾았습니다.", all.last { it.role == MessageRole.ASSISTANT }.text)
        assertNull(all.firstOrNull { it.role == MessageRole.NOTICE }?.text)

        // Hermes saw the app's tools and its own phone toolsets, and none that a phone cannot run.
        val main = model.requests.first { "list_records" in names(it) }
        val offered = names(main)
        // web_search is there because the runtime carries ddgs (android/hermes/requirements-android.txt).
        val expected = listOf(
            "list_records", "web_capture", "open_site", "capture_page", "schedule_capture", "clarify", "web_search",
            "memory",
        )
        for (name in expected) {
            assertTrue("$name in $offered", name in offered)
        }
        for (name in listOf("terminal", "execute_code", "browser_navigate", "computer_use", "read_file")) {
            assertFalse("$name in $offered", name in offered)
        }
        // The app's result went back to Hermes: the record it found.
        val result = toolResult(model.requests.first { toolResult(it) != null }).orEmpty()
        assertTrue(result, result.contains("노트북 특가"))
        assertNull(chat.view.value)
    }

    @Test
    fun aClarifyQuestionWaitsForTheUsersAnswer() = runBlocking {
        val asked = JSONObject().put(
            "questions",
            JSONArray().put(JSONObject().put("question", "어느 사이트에서 찾을까요?").put("choices", JSONArray(listOf("쿠팡", "네이버")))),
        )
        val model = Model { request ->
            when {
                "clarify" in names(request) && toolResult(request) == null -> call("clarify", asked)
                toolResult(request) != null -> words("쿠팡에서 찾겠습니다.")
                else -> words("ok")
            }
        }
        val turn = async(Dispatchers.Default) { chat.converse(model, "stand-in", "광고 찾아 줘", emptyList()) }
        val interaction = withTimeout(240_000) {
            var found: HermesChat.Interaction? = null
            while (found == null) {
                found = chat.view.value?.interaction
                if (found == null) delay(100)
            }
            found
        }
        assertEquals(HermesSession.InteractionKind.CLARIFY, interaction.kind)
        val question = interaction.payload.getJSONArray("questions").getJSONObject(0)
        assertEquals("어느 사이트에서 찾을까요?", question.getString("question"))
        chat.answer(interaction.id, JSONObject().put(question.getString("qid"), "쿠팡"))
        withTimeout(120_000) { turn.await() }

        assertEquals("쿠팡에서 찾겠습니다.", messages.all().last { it.role == MessageRole.ASSISTANT }.text)
        val result = toolResult(model.requests.first { toolResult(it) != null }).orEmpty()
        assertTrue(result, result.contains("쿠팡") && result.contains("submitted"))
    }

    @Test
    fun aStopEndsTheTurnWhileTheModelIsStillAnswering() = runBlocking {
        val called = CompletableDeferred<Unit>()
        val model = Model {
            called.complete(Unit)
            awaitCancellation()
        }
        val turn = async(Dispatchers.Default) { chat.converse(model, "stand-in", "노트북 찾아 줘", emptyList()) }
        withTimeout(240_000) { called.await() }
        val started = System.currentTimeMillis()
        turn.cancel()
        withTimeout(10_000) { turn.join() }
        assertTrue(System.currentTimeMillis() - started < 10_000)
        assertNull(chat.view.value)

        // The next turn starts a fresh worker and works.
        val again = Model { words("다시 왔습니다.") }
        withTimeout(240_000) { chat.converse(again, "stand-in", "안녕", emptyList()) }
        assertEquals("다시 왔습니다.", messages.all().last { it.role == MessageRole.ASSISTANT }.text)
    }

    /**
     * Hermes' keyless web search from the phone: a real DuckDuckGo search through ddgs and primp. It needs the
     * network, so it runs only when asked: am instrument -e hermesNetwork true.
     */
    @Test
    fun webSearchFindsResultsOnThePhone() = runBlocking {
        assumeTrue(InstrumentationRegistry.getArguments().getString("hermesNetwork") == "true")
        val model = Model { request ->
            when {
                "web_search" in names(request) && toolResult(request) == null ->
                    call("web_search", JSONObject().put("query", "Python programming language"))
                toolResult(request) != null -> words("검색했습니다.")
                else -> words("ok")
            }
        }
        withTimeout(300_000) { chat.converse(model, "stand-in", "파이썬을 웹에서 찾아 줘", emptyList()) }
        val result = toolResult(model.requests.first { toolResult(it) != null }).orEmpty()
        assertTrue(result.take(2000), result.contains("http"))
        assertEquals("검색했습니다.", messages.all().last { it.role == MessageRole.ASSISTANT }.text)
    }

    @Test
    fun theChatUsesHermesWhenTheRuntimeIsBundled() {
        assertEquals(ChatAgent.Engine.HERMES, graph.agent.engine)
        assertNotNull(graph.agent.hermes)
    }
}
