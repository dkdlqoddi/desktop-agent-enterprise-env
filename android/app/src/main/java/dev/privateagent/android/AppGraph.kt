package dev.privateagent.android

import android.app.Application
import dev.privateagent.android.agent.AgentGrants
import dev.privateagent.android.agent.AgentMemory
import dev.privateagent.android.agent.AppActions
import dev.privateagent.android.agent.ChatAgent
import dev.privateagent.android.agent.HermesChat
import dev.privateagent.android.agent.ProtectedZones
import dev.privateagent.android.agent.Texts
import dev.privateagent.android.data.AppDatabase
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.data.Restored
import dev.privateagent.android.data.ScheduleStore
import dev.privateagent.android.data.TrashKind
import dev.privateagent.android.data.TrashStore
import dev.privateagent.android.hermes.HermesRuntime
import dev.privateagent.android.hermes.HermesSession
import dev.privateagent.android.llm.LlmService
import dev.privateagent.android.schedule.Notifications
import dev.privateagent.android.schedule.ScheduleManager
import dev.privateagent.android.schedule.Upkeep
import dev.privateagent.android.security.SettingsStore
import dev.privateagent.android.ui.Navigator
import dev.privateagent.android.web.BrowserController
import dev.privateagent.android.web.Sites
import dev.privateagent.android.web.WebScripts

/** The app's parts, made once per process. */
class AppGraph(private val app: Application) {
    val database = AppDatabase(app)
    val messages = MessageStore(database)
    val records = RecordStore(database)
    val schedules = ScheduleStore(database)
    val trash = TrashStore(database)
    val settings = SettingsStore(app)
    val sites: Sites by lazy { Sites.parse(WebScripts.read(app.assets, "sites.json")) }
    val scripts: WebScripts by lazy { WebScripts.load(app.assets) }
    val texts = Texts(app) { sites }
    val navigator = Navigator()
    val llm = LlmService()
    val notifications = Notifications(app, settings) { sites }
    val scheduler = ScheduleManager(app, schedules)
    val upkeep = Upkeep(app, records, trash) { settings.recordsKept.value }
    val browser = BrowserController(records, settings, { sites }, { scripts })
    val actions = AppActions(app, records, browser, scheduler, notifications, navigator, { sites }, texts)

    /** The bundled Hermes agent (android/hermes/build_runtime.py); a build without it chats with the LLM directly. */
    val hermesRuntime = HermesRuntime(app)
    val hermesSession = HermesSession(hermesRuntime)

    /** What the agent runs without asking: the commands allowed for good, and the kinds allowed for this conversation. */
    val grants = AgentGrants(AgentGrants.PrefsStore(app))

    /**
     * What the agent may not touch: the app's own data, in the user's storage and in the device's, around the agent's
     * home. Worked out for each turn, as the folders are made as the app is used.
     */
    private val appFolders = listOf(app.dataDir, app.createDeviceProtectedStorageContext().dataDir)
    val hermes = HermesChat(app, messages, actions, navigator, { sites }, texts, hermesSession, grants) {
        ProtectedZones.ofApp(appFolders, hermesRuntime.home)
    }

    val agent = ChatAgent(
        context = app,
        messages = messages,
        schedules = schedules,
        settings = settings,
        llm = llm,
        actions = actions,
        navigator = navigator,
        sites = { sites },
        texts = texts,
        runtime = hermesRuntime,
        hermes = hermes,
    )

    /** What the agent remembers, for the user to read and change; nothing of it changes while a turn runs. */
    val memory = AgentMemory(hermesRuntime.home) { agent.busy.value }

    /**
     * Brings the trash's entries [ids] back (TrashStore.restore). A schedule among them is queued again, for its next
     * time from now on.
     */
    suspend fun restore(ids: Collection<Long>): Restored {
        val restored = trash.restore(ids)
        if (TrashKind.SCHEDULE in restored.kinds) scheduler.restoreAll()
        return restored
    }

    /**
     * Says [text] of what went to the trash as [entries], with 되돌리기, which brings them back; it says so when they
     * are no longer there to bring back.
     */
    fun sayMoved(text: String, entries: List<Long>) {
        if (entries.isEmpty()) return navigator.say(text)
        navigator.say(text) {
            if (restore(entries).count == 0) navigator.say(app.getString(R.string.trash_gone))
        }
    }
}
