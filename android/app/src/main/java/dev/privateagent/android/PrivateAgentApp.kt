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
        // The Hermes runtime is extracted once per app version, ahead of the first chat message (seconds).
        scope.launch(Dispatchers.IO) {
            runCatching { if (graph.hermesRuntime.isBundled) graph.hermesRuntime.prepare() }
        }
    }
}
