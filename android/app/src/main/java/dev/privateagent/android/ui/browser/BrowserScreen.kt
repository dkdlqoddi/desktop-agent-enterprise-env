package dev.privateagent.android.ui.browser

import android.content.ActivityNotFoundException
import android.content.Intent
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AssistChip
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.net.toUri
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.privateagent.android.AppGraph
import dev.privateagent.android.R
import dev.privateagent.android.web.BrowserController.Notice
import dev.privateagent.android.web.CaptureOutcome
import kotlinx.coroutines.launch

/**
 * The visible in-app browser: an address bar, back, forward, reload and "이 페이지 기록", and the page. It stays composed
 * while another tab is in front (invisible then), so the page keeps loading and a capture can finish.
 */
@Composable
fun BrowserScreen(graph: AppGraph, visible: Boolean) {
    val browser = graph.browser
    val webView by browser.webView.collectAsStateWithLifecycle()
    val page by browser.page.collectAsStateWithLifecycle()
    val work by browser.work.collectAsStateWithLifecycle()
    val notice by browser.notice.collectAsStateWithLifecycle()
    val marked by browser.markedAds.collectAsStateWithLifecycle()
    val scope = rememberCoroutineScope()
    val focus = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    // Saved with the screen: a screen made anew with the address bar in focus shows what was typed there.
    var address by rememberSaveable { mutableStateOf("") }
    var editing by remember { mutableStateOf(false) }
    LaunchedEffect(page.url, editing) { if (!editing) address = page.url }
    BackHandler(enabled = visible && page.canGoBack) { browser.back() }

    Column(
        Modifier
            .fillMaxSize()
            .imePadding()
            .then(if (visible) Modifier else Modifier.clearAndSetSemantics { }),
    ) {
        OutlinedTextField(
            value = address,
            onValueChange = { address = it },
            modifier = Modifier
                .fillMaxWidth()
                .padding(start = 12.dp, end = 12.dp, top = 8.dp)
                .onFocusChanged { editing = it.isFocused },
            singleLine = true,
            placeholder = { Text(stringResource(R.string.browser_address_hint)) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go, autoCorrectEnabled = false),
            keyboardActions = KeyboardActions(onGo = {
                browser.submitAddress(address)
                keyboard?.hide()
                focus.clearFocus()
            }),
            textStyle = MaterialTheme.typography.bodyMedium,
        )
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = browser::back, enabled = page.canGoBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.browser_back))
            }
            IconButton(onClick = browser::forward, enabled = page.canGoForward) {
                Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = stringResource(R.string.browser_forward))
            }
            if (page.loading) {
                IconButton(onClick = browser::stopLoading) {
                    Icon(Icons.Filled.Close, contentDescription = stringResource(R.string.browser_stop))
                }
            } else {
                IconButton(onClick = browser::reload, enabled = page.url.isNotEmpty()) {
                    Icon(Icons.Filled.Refresh, contentDescription = stringResource(R.string.browser_reload))
                }
            }
            Spacer(Modifier.weight(1f))
            FilledTonalButton(
                onClick = {
                    scope.launch {
                        val outcome = browser.captureCurrentPage()
                        browser.showNotice(Notice.Captured(outcome))
                    }
                },
                enabled = page.url.startsWith("https://") && work == null && !page.loading,
                modifier = Modifier.padding(end = 8.dp),
            ) {
                Text(stringResource(R.string.browser_capture_page))
            }
        }
        if (page.loading) {
            LinearProgressIndicator(progress = { page.progress / 100f }, modifier = Modifier.fillMaxWidth().height(2.dp))
        }
        work?.let { current ->
            Banner(
                text = if (current.site != null && current.query != null) {
                    stringResource(R.string.browser_capturing, current.site, current.query)
                } else {
                    stringResource(R.string.browser_capturing_page)
                },
                busy = true,
            )
        }
        notice?.let { shown ->
            NoticeBanner(
                notice = shown,
                graph = graph,
                onDismiss = browser::dismissNotice,
            )
        }
        if (marked > 0 && work == null && notice == null) {
            Text(
                stringResource(R.string.browser_marked, marked),
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Box(
            Modifier
                .weight(1f)
                .fillMaxWidth(),
        ) {
            AndroidView(
                factory = { context -> FrameLayout(context) },
                update = { container ->
                    val view = webView
                    if (view == null) {
                        container.removeAllViews()
                    } else if (view.parent !== container) {
                        (view.parent as? ViewGroup)?.removeView(view)
                        container.removeAllViews()
                        container.addView(
                            view,
                            FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT),
                        )
                    }
                    container.visibility = if (visible) View.VISIBLE else View.INVISIBLE
                },
                modifier = Modifier.fillMaxSize(),
            )
            if (page.url.isEmpty() && !page.loading && work == null) {
                StartPage(graph)
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun StartPage(graph: AppGraph) {
    Surface(Modifier.fillMaxSize()) {
        Column(
            Modifier
                .fillMaxSize()
                .verticalScroll(rememberScrollState())
                .padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Spacer(Modifier.height(24.dp))
            Text(stringResource(R.string.browser_empty_title), style = MaterialTheme.typography.titleLarge)
            Text(
                stringResource(R.string.browser_empty_body),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                textAlign = TextAlign.Center,
            )
            FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterHorizontally)) {
                for (site in graph.sites.all) {
                    AssistChip(onClick = { graph.browser.load(site.home) }, label = { Text(site.name) })
                }
            }
        }
    }
}

@Composable
private fun Banner(text: String, busy: Boolean = false, content: @Composable () -> Unit = {}) {
    Surface(
        color = MaterialTheme.colorScheme.secondaryContainer,
        contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        modifier = Modifier
            .fillMaxWidth()
            .semantics { liveRegion = LiveRegionMode.Polite },
    ) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            if (busy) {
                CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                Spacer(Modifier.size(8.dp))
            }
            Text(text, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            content()
        }
    }
}

@Composable
private fun NoticeBanner(notice: Notice, graph: AppGraph, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val text = when (notice) {
        is Notice.External -> stringResource(R.string.browser_blocked_external, notice.scheme)
        is Notice.Insecure -> stringResource(R.string.browser_blocked_insecure)
        Notice.BadAddress -> stringResource(R.string.browser_bad_address)
        Notice.Certificate -> stringResource(R.string.browser_ssl_error)
        Notice.Restarted -> stringResource(R.string.browser_renderer_gone)
        Notice.Captcha -> stringResource(R.string.result_blocked)
        is Notice.ClickRecorded -> stringResource(
            if (notice.ad) R.string.browser_click_recorded else R.string.browser_click_recorded_item,
        )
        is Notice.Captured -> graph.texts.outcome(notice.outcome)
    }
    Banner(text) {
        when (notice) {
            is Notice.Insecure -> TextButton(onClick = {
                // Leaving the app happens only here, on the user's tap.
                try {
                    context.startActivity(
                        Intent(Intent.ACTION_VIEW, notice.url.toUri()).addCategory(Intent.CATEGORY_BROWSABLE),
                    )
                } catch (_: ActivityNotFoundException) {
                    // No other browser: the notice stays.
                }
                onDismiss()
            }) { Text(stringResource(R.string.browser_open_external)) }
            is Notice.Captured -> {
                val outcome = notice.outcome
                if (outcome is CaptureOutcome.Saved) {
                    TextButton(onClick = {
                        onDismiss()
                        graph.navigator.showCapture(outcome.captureId)
                    }) { Text(stringResource(R.string.action_view_records)) }
                }
            }
            else -> Unit
        }
        IconButton(onClick = onDismiss) {
            Icon(Icons.Filled.Close, contentDescription = stringResource(R.string.browser_dismiss))
        }
    }
}
