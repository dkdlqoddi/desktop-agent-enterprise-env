package dev.privateagent.android

import android.app.Application
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

class PrivateAgentApp : Application() {
    lateinit var graph: AppGraph
        private set

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)

    override fun onCreate() {
        super.onCreate()
        graph = AppGraph(this)
        graph.notifications.ensureChannel()
        graph.agent.start()
        scope.launch { runCatching { graph.scheduler.restoreAll() } }
        // The records older than the days the user keeps them go to the trash, and the trash forgets what has waited
        // its days: now, and once a day while the app is installed.
        scope.launch { runCatching { graph.upkeep.run() } }
        runCatching { graph.upkeep.schedule() }
        // The Hermes runtime is extracted once per app version, ahead of the first chat message (seconds).
        scope.launch(Dispatchers.IO) {
            runCatching { if (graph.hermesRuntime.isBundled) graph.hermesRuntime.prepare() }
        }
    }
}
