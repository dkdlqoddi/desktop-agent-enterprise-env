package dev.privateagent.android.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.painter.Painter
import androidx.compose.ui.graphics.vector.rememberVectorPainter
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.res.stringResource
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.privateagent.android.AppGraph
import dev.privateagent.android.R
import dev.privateagent.android.ui.browser.BrowserScreen
import dev.privateagent.android.ui.chat.ChatScreen
import dev.privateagent.android.ui.records.RecordsScreen
import dev.privateagent.android.ui.settings.SettingsScreen

/**
 * The bottom navigation and its four tabs. The browser stays composed under the other tabs, so its WebView is never
 * taken off the window while a capture runs; the tab in front is an opaque surface that takes the touches.
 */
@Composable
fun AppRoot(graph: AppGraph) {
    val tab by graph.navigator.tab.collectAsStateWithLifecycle()
    val work by graph.browser.work.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    ShowNotices(graph, snackbar)
    val saved = rememberSaveableStateHolder()

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            NavigationBar {
                TabItem(tab, Tab.CHAT, painterResource(R.drawable.ic_tab_chat), stringResource(R.string.tab_chat), graph)
                TabItem(
                    tab,
                    Tab.BROWSER,
                    painterResource(R.drawable.ic_tab_browser),
                    stringResource(R.string.tab_browser),
                    graph,
                    busy = work != null,
                )
                TabItem(
                    tab,
                    Tab.RECORDS,
                    rememberVectorPainter(Icons.AutoMirrored.Filled.List),
                    stringResource(R.string.tab_records),
                    graph,
                )
                TabItem(
                    tab,
                    Tab.SETTINGS,
                    rememberVectorPainter(Icons.Filled.Settings),
                    stringResource(R.string.tab_settings),
                    graph,
                )
            }
        },
    ) { padding ->
        Box(
            Modifier
                .fillMaxSize()
                .padding(padding)
                .consumeWindowInsets(padding),
        ) {
            BrowserScreen(graph, visible = tab == Tab.BROWSER)
            if (tab != Tab.BROWSER) {
                Surface(Modifier.fillMaxSize()) {
                    saved.SaveableStateProvider(tab.name) {
                        when (tab) {
                            Tab.CHAT -> ChatScreen(graph)
                            Tab.RECORDS -> RecordsScreen(graph)
                            Tab.SETTINGS -> SettingsScreen(graph)
                            Tab.BROWSER -> Unit
                        }
                    }
                }
            }
        }
    }
}

/**
 * Shows the navigator's short messages one after another in [snackbar]. One that can take back what it tells of (a
 * delete that went to the trash) has 되돌리기, and stays longer.
 */
@Composable
fun ShowNotices(graph: AppGraph, snackbar: SnackbarHostState) {
    val undo = stringResource(R.string.undo)
    LaunchedEffect(graph) {
        graph.navigator.notices.collect { notice ->
            val undoing = notice.undo
            if (undoing == null) {
                snackbar.showSnackbar(notice.text)
            } else if (
                snackbar.showSnackbar(notice.text, actionLabel = undo, duration = SnackbarDuration.Long) ==
                SnackbarResult.ActionPerformed
            ) {
                graph.orSay(Unit) { undoing() }
            }
        }
    }
}

@Composable
private fun androidx.compose.foundation.layout.RowScope.TabItem(
    current: Tab,
    tab: Tab,
    icon: Painter,
    label: String,
    graph: AppGraph,
    busy: Boolean = false,
) {
    NavigationBarItem(
        selected = current == tab,
        onClick = { graph.navigator.show(tab) },
        icon = {
            if (busy) {
                BadgedBox(badge = { Badge() }) { Icon(icon, contentDescription = null) }
            } else {
                Icon(icon, contentDescription = null)
            }
        },
        label = { Text(label) },
    )
}
