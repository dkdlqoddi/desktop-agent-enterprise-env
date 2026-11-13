package dev.privateagent.android

import android.app.Application
import dev.privateagent.android.agent.AgentGrants
import dev.privateagent.android.agent.AppActions
import dev.privateagent.android.agent.ChatAgent
import dev.privateagent.android.agent.HermesChat
import dev.privateagent.android.agent.Texts
import dev.privateagent.android.data.AppDatabase
import dev.privateagent.android.data.MessageStore
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.data.ScheduleStore
import dev.privateagent.android.hermes.HermesRuntime
import dev.privateagent.android.hermes.HermesSession
import dev.privateagent.android.llm.LlmService
import dev.privateagent.android.schedule.Notifications
import dev.privateagent.android.schedule.ScheduleManager
import dev.privateagent.android.security.SettingsStore
import dev.privateagent.android.ui.Navigator
import dev.privateagent.android.web.BrowserController
import dev.privateagent.android.web.Sites
import dev.privateagent.android.web.WebScripts

/** The app's parts, made once per process. */
class AppGraph(app: Application) {
    val database = AppDatabase(app)
    val messages = MessageStore(database)
    val records = RecordStore(database)
    val schedules = ScheduleStore(database)
    val settings = SettingsStore(app)
    val sites: Sites by lazy { Sites.parse(WebScripts.read(app.assets, "sites.json")) }
    val scripts: WebScripts by lazy { WebScripts.load(app.assets) }
    val texts = Texts(app) { sites }
    val navigator = Navigator()
    val llm = LlmService()
    val notifications = Notifications(app, settings) { sites }
    val scheduler = ScheduleManager(app, schedules)
    val browser = BrowserController(records, settings, { sites }, { scripts })
    val actions = AppActions(app, records, browser, scheduler, notifications, navigator, { sites }, texts)

    /** The bundled Hermes agent (android/hermes/build_runtime.py); a build without it chats with the LLM directly. */
    val hermesRuntime = HermesRuntime(app)
    val hermesSession = HermesSession(hermesRuntime)

    /** What the agent runs without asking: the commands allowed for good, and the kinds allowed for this conversation. */
    val grants = AgentGrants(AgentGrants.PrefsStore(app))
    val hermes = HermesChat(app, messages, actions, navigator, { sites }, texts, hermesSession, grants)

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
}
