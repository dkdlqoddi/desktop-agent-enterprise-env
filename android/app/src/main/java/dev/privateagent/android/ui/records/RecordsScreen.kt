package dev.privateagent.android.ui.records

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.InputChip
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.privateagent.android.AppGraph
import dev.privateagent.android.R
import dev.privateagent.android.agent.RecordKindFilter
import dev.privateagent.android.agent.Texts
import dev.privateagent.android.data.CaptureRow
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.data.ItemRow
import dev.privateagent.android.data.StoreException
import dev.privateagent.android.export.CsvExporter
import dev.privateagent.android.ui.RecordsView
import dev.privateagent.android.ui.orSay
import dev.privateagent.android.ui.settings.Confirm
import dev.privateagent.android.web.BrowserController
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.ItemKind
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RecordsScreen(graph: AppGraph) {
    val navigator = graph.navigator
    val filter by navigator.recordsFilter.collectAsStateWithLifecycle()
    val view by navigator.recordsView.collectAsStateWithLifecycle()
    val changes by graph.database.changes.collectAsStateWithLifecycle()
    var items by remember { mutableStateOf<List<ItemRow>>(emptyList()) }
    var counts by remember { mutableStateOf(CaptureCounts()) }
    var captures by remember { mutableStateOf<List<CaptureRow>>(emptyList()) }
    var loaded by remember { mutableStateOf(false) }
    LaunchedEffect(filter, changes) {
        graph.orSay(Unit) {
            items = graph.records.items(filter)
            counts = graph.records.counts(filter)
            loaded = true
        }
    }
    LaunchedEffect(changes) { captures = graph.orSay(captures) { graph.records.captures() } }
    var detail by remember { mutableStateOf<ItemRow?>(null) }
    var deleting by remember { mutableStateOf<CaptureRow?>(null) }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val exportEmpty = stringResource(R.string.records_export_empty)
    val exportFailed = stringResource(R.string.records_export_failed)
    val deleted = stringResource(R.string.records_deleted)

    Column(Modifier.fillMaxSize()) {
        TopAppBar(
            title = { Text(stringResource(R.string.records_title)) },
            actions = {
                IconButton(onClick = {
                    scope.launch {
                        try {
                            // Every item the filter finds, not only those the list shows.
                            val file = CsvExporter.exportAll(context, graph.texts) { each ->
                                graph.records.eachItem(filter, each)
                            }
                            if (file == null) navigator.say(exportEmpty) else CsvExporter.share(context, file)
                        } catch (cancelled: CancellationException) {
                            throw cancelled
                        } catch (failure: StoreException) {
                            navigator.say(graph.texts.storage(failure.problem))
                        } catch (_: Exception) {
                            navigator.say(exportFailed)
                        }
                    }
                }) {
                    Icon(Icons.Filled.Share, contentDescription = stringResource(R.string.records_export))
                }
            },
        )
        SingleChoiceSegmentedButtonRow(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
        ) {
            val views = listOf(RecordsView.ITEMS to R.string.records_items, RecordsView.CAPTURES to R.string.records_captures)
            views.forEachIndexed { index, (option, label) ->
                SegmentedButton(
                    selected = view == option,
                    onClick = { navigator.recordsView.value = option },
                    shape = SegmentedButtonDefaults.itemShape(index, views.size),
                ) { Text(stringResource(label)) }
            }
        }
        when (view) {
            RecordsView.ITEMS -> {
                Filters(filter, graph, onChange = { navigator.recordsFilter.value = it })
                Text(
                    stringResource(R.string.records_counts, counts.ad, counts.product, counts.result),
                    modifier = Modifier.padding(horizontal = 16.dp, vertical = 4.dp),
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (loaded && items.isEmpty()) {
                    Empty(
                        if (captures.isEmpty()) {
                            stringResource(R.string.records_empty)
                        } else {
                            stringResource(R.string.records_no_match)
                        },
                    )
                } else {
                    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 16.dp)) {
                        items(items, key = { it.id }) { item ->
                            ItemListRow(item, graph.texts, onClick = { detail = item })
                            HorizontalDivider()
                        }
                    }
                }
            }
            RecordsView.CAPTURES -> if (captures.isEmpty()) {
                Empty(stringResource(R.string.records_empty))
            } else {
                LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(bottom = 16.dp)) {
                    items(captures, key = { it.id }) { capture ->
                        CaptureListRow(
                            capture,
                            graph.texts,
                            onClick = { navigator.showCapture(capture.id) },
                            onDelete = { deleting = capture },
                        )
                        HorizontalDivider()
                    }
                }
            }
        }
    }
    detail?.let { item -> ItemDetail(item, graph.texts, onDismiss = { detail = null }) }
    deleting?.let { capture ->
        Confirm(
            title = stringResource(R.string.records_delete_title),
            body = stringResource(
                R.string.records_delete_body,
                captureLabel(capture, graph.texts),
                graph.texts.time(capture.capturedAt),
                capture.adCount + capture.productCount + capture.resultCount,
            ),
            confirm = stringResource(R.string.confirm_clear),
            onConfirm = {
                deleting = null
                scope.launch {
                    graph.orSay(Unit) {
                        graph.records.deleteCapture(capture.id)
                        // The items of a page that is gone are no longer a filter.
                        val filter = navigator.recordsFilter.value
                        if (filter.captureId == capture.id) navigator.recordsFilter.value = filter.copy(captureId = null)
                        navigator.say(deleted)
                    }
                }
            },
            onDismiss = { deleting = null },
        )
    }
}

/** A recorded page as its row names it: the site and, when there is one, the search words. */
private fun captureLabel(capture: CaptureRow, texts: Texts): String =
    listOf(texts.siteName(capture.site), capture.query.takeIf { it.isNotEmpty() }?.let { "'$it'" })
        .filterNotNull().joinToString(" ")

@Composable
private fun Filters(filter: ItemFilter, graph: AppGraph, onChange: (ItemFilter) -> Unit) {
    Column(Modifier.padding(top = 8.dp)) {
        OutlinedTextField(
            value = filter.search,
            onValueChange = { onChange(filter.copy(search = it)) },
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp),
            singleLine = true,
            leadingIcon = { Icon(Icons.Filled.Search, contentDescription = null) },
            trailingIcon = {
                if (filter.search.isNotEmpty()) {
                    IconButton(onClick = { onChange(filter.copy(search = "")) }) {
                        Icon(Icons.Filled.Clear, contentDescription = stringResource(R.string.records_clear_search))
                    }
                }
            },
            placeholder = { Text(stringResource(R.string.records_search_hint)) },
            textStyle = MaterialTheme.typography.bodyMedium,
        )
        Row(
            Modifier
                .fillMaxWidth()
                .horizontalScroll(rememberScrollState())
                .padding(horizontal = 16.dp, vertical = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            SiteFilter(filter, graph, onChange)
            FilterChip(
                selected = filter.kind == null,
                onClick = { onChange(filter.copy(kind = null)) },
                label = { Text(stringResource(R.string.kind_all)) },
            )
            for (kind in RecordKindFilter.entries) {
                FilterChip(
                    selected = filter.kind == kind,
                    onClick = { onChange(filter.copy(kind = if (filter.kind == kind) null else kind)) },
                    label = { Text(graph.texts.filter(kind)) },
                )
            }
        }
        if (filter.captureId != null) {
            InputChip(
                selected = true,
                onClick = { onChange(filter.copy(captureId = null)) },
                label = { Text(stringResource(R.string.records_only_capture)) },
                trailingIcon = { Icon(Icons.Filled.Clear, contentDescription = stringResource(R.string.records_show_all)) },
                modifier = Modifier.padding(horizontal = 16.dp),
            )
        }
    }
}

@Composable
private fun SiteFilter(filter: ItemFilter, graph: AppGraph, onChange: (ItemFilter) -> Unit) {
    var open by remember { mutableStateOf(false) }
    Box {
        FilterChip(
            selected = filter.site != null,
            onClick = { open = true },
            label = {
                Text(filter.site?.let { graph.texts.siteName(it) } ?: stringResource(R.string.filter_all_sites))
            },
            trailingIcon = { Icon(Icons.Filled.ArrowDropDown, contentDescription = null) },
        )
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            DropdownMenuItem(
                text = { Text(stringResource(R.string.filter_all_sites)) },
                onClick = {
                    open = false
                    onChange(filter.copy(site = null))
                },
            )
            for (site in graph.sites.all) {
                DropdownMenuItem(
                    text = { Text(site.name) },
                    onClick = {
                        open = false
                        onChange(filter.copy(site = site.key))
                    },
                )
            }
            DropdownMenuItem(
                text = { Text(stringResource(R.string.site_generic)) },
                onClick = {
                    open = false
                    onChange(filter.copy(site = BrowserController.GENERIC))
                },
            )
        }
    }
}

@Composable
private fun ItemListRow(item: ItemRow, texts: Texts, onClick: () -> Unit) {
    ListItem(
        modifier = Modifier.clickable(onClick = onClick),
        overlineContent = {
            Text(
                listOf(texts.kind(item.kind), texts.siteName(item.site), item.query).filter { it.isNotEmpty() }
                    .joinToString(" · "),
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
        },
        headlineContent = {
            Text(item.title.ifEmpty { item.linkUrl }, maxLines = 2, overflow = TextOverflow.Ellipsis)
        },
        supportingContent = {
            val parts = listOfNotNull(
                item.price.takeIf { it.isNotEmpty() },
                texts.time(item.capturedAt),
                item.clickedAt?.let { stringResource(R.string.records_clicked_badge) },
            )
            Text(parts.joinToString(" · "), maxLines = 1, overflow = TextOverflow.Ellipsis)
        },
        leadingContent = { KindBadge(item.kind, texts) },
    )
}

@Composable
private fun KindBadge(kind: ItemKind, texts: Texts) {
    val colors = MaterialTheme.colorScheme
    val (container, content) = when (kind) {
        ItemKind.AD -> colors.tertiaryContainer to colors.onTertiaryContainer
        ItemKind.PRODUCT -> colors.primaryContainer to colors.onPrimaryContainer
        ItemKind.RESULT -> colors.secondaryContainer to colors.onSecondaryContainer
    }
    Surface(color = container, contentColor = content, shape = RoundedCornerShape(8.dp)) {
        Text(
            texts.kind(kind),
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
            style = MaterialTheme.typography.labelSmall,
        )
    }
}

@Composable
private fun CaptureListRow(capture: CaptureRow, texts: Texts, onClick: () -> Unit, onDelete: () -> Unit) {
    val label = captureLabel(capture, texts)
    ListItem(
        modifier = Modifier.clickable(onClick = onClick),
        overlineContent = { Text(texts.time(capture.capturedAt) + " · " + texts.source(capture.source)) },
        headlineContent = { Text(label, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        supportingContent = {
            Text(stringResource(R.string.records_counts, capture.adCount, capture.productCount, capture.resultCount))
        },
        trailingContent = {
            IconButton(onClick = onDelete) {
                Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.records_delete_one, label))
            }
        },
    )
}

@Composable
private fun Empty(text: String) {
    Box(
        Modifier
            .fillMaxSize()
            .padding(32.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
    }
}

@Composable
private fun ItemDetail(item: ItemRow, texts: Texts, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val copied = stringResource(R.string.detail_copied)
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(item.title.ifEmpty { texts.kind(item.kind) }, maxLines = 3, overflow = TextOverflow.Ellipsis) },
        text = {
            SelectionContainer {
                Column(
                    Modifier.verticalScroll(rememberScrollState()),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Field(R.string.detail_kind, texts.kind(item.kind) + " · " + stringResource(R.string.detail_position_value, item.position))
                    Field(R.string.detail_site, texts.siteName(item.site))
                    Field(R.string.detail_query, item.query)
                    Field(R.string.detail_price, item.price)
                    Field(R.string.detail_display_url, item.displayUrl)
                    Field(R.string.detail_link, item.linkUrl)
                    Field(R.string.detail_captured_at, texts.time(item.capturedAt))
                    item.clickedAt?.let { Field(R.string.detail_clicked_at, texts.time(it)) }
                    Field(R.string.detail_clicked_url, item.clickedUrl.orEmpty())
                    Field(R.string.detail_landing_url, item.landingUrl.orEmpty())
                    Field(R.string.detail_landing_title, item.landingTitle.orEmpty())
                    Field(R.string.detail_page, item.pageUrl)
                    Field(R.string.detail_text, item.text)
                }
            }
        },
        confirmButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.detail_close)) } },
        dismissButton = {
            val link = item.landingUrl ?: item.linkUrl
            if (link.isNotEmpty()) {
                TextButton(onClick = {
                    copy(context, link)
                    android.widget.Toast.makeText(context, copied, android.widget.Toast.LENGTH_SHORT).show()
                }) { Text(stringResource(R.string.detail_copy)) }
            }
        },
    )
}

@Composable
private fun Field(label: Int, value: String) {
    if (value.isEmpty()) return
    Column {
        Text(stringResource(label), style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.size(2.dp))
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

private fun copy(context: Context, text: String) {
    val clipboard = context.getSystemService(ClipboardManager::class.java) ?: return
    clipboard.setPrimaryClip(ClipData.newPlainText("link", text))
}
