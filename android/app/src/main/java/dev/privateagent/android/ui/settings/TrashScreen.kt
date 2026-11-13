package dev.privateagent.android.ui.settings

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.privateagent.android.AppGraph
import dev.privateagent.android.R
import dev.privateagent.android.agent.Texts
import dev.privateagent.android.data.ChatPlace
import dev.privateagent.android.data.TrashEntry
import dev.privateagent.android.data.TrashKind
import dev.privateagent.android.data.TrashStore
import dev.privateagent.android.ui.orSay
import kotlinx.coroutines.launch

/** What 영구 삭제 asks about: the entries chosen or one of them ([name] says which), or the whole trash. */
private sealed interface Purge {
    data class Some(val ids: List<Long>, val name: String? = null) : Purge

    data class All(val count: Int) : Purge
}

/**
 * 설정 → 데이터 → 휴지통: what the user deleted waits here TrashStore.DAYS days (the conversation cleared, recorded
 * pages, schedules, and the pages the retention policy moved), the last deleted first, a page at a time as the list is
 * scrolled; it shows one kind or all. Entries are restored or deleted for good one at a time or many at once (모두
 * 선택 takes those not loaded yet too), and deleting for good asks first. What has waited its days is gone once the
 * screen opens.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun TrashScreen(graph: AppGraph, onBack: () -> Unit) {
    BackHandler(onBack = onBack)
    val changes by graph.database.changes.collectAsStateWithLifecycle()
    var kind by rememberSaveable { mutableStateOf<TrashKind?>(null) }
    var counts by remember { mutableStateOf<Map<TrashKind, Int>?>(null) }
    var entries by remember { mutableStateOf<List<TrashEntry>>(emptyList()) }
    var more by remember { mutableStateOf(false) }
    // How many entries the list holds: as many are read again when the trash changes.
    var wanted by remember { mutableIntStateOf(TrashStore.PAGE) }
    var selected by remember { mutableStateOf<Set<Long>>(emptySet()) }
    var busy by remember { mutableStateOf(false) }
    var asking by remember { mutableStateOf<Purge?>(null) }
    val list = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val resources = LocalResources.current

    LaunchedEffect(Unit) { graph.orSay(0) { graph.trash.expire() } }
    // From the first page whenever the trash changed: a restore, a purge, a delete elsewhere.
    LaunchedEffect(kind, changes) {
        graph.orSay(Unit) {
            counts = graph.trash.counts()
            val page = graph.trash.page(kind, limit = wanted)
            entries = page.entries
            more = page.more
        }
    }
    // The next page comes as the end of the list nears the screen.
    val nearEnd by remember {
        derivedStateOf {
            val last = list.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: -1
            last >= list.layoutInfo.totalItemsCount - NEAR_END
        }
    }
    LaunchedEffect(nearEnd, more, entries) {
        val last = entries.lastOrNull()
        if (!nearEnd || !more || last == null) return@LaunchedEffect
        graph.orSay(Unit) {
            val page = graph.trash.page(kind, after = last)
            entries = (entries + page.entries).distinctBy { it.id }
            more = page.more
            wanted = maxOf(TrashStore.PAGE, entries.size)
        }
    }

    fun show(next: TrashKind?) {
        if (next == kind) return
        kind = next
        wanted = TrashStore.PAGE
        selected = emptySet()
        scope.launch { list.scrollToItem(0) }
    }

    /** One action at a time; a database error is said, and the list is read again with the next change. */
    fun act(work: suspend () -> Unit) {
        if (busy) return
        busy = true
        scope.launch {
            try {
                graph.orSay(Unit) { work() }
            } finally {
                busy = false
            }
        }
    }

    fun restore(ids: Collection<Long>) = act {
        val restored = graph.restore(ids)
        selected = selected - ids.toSet()
        graph.navigator.say(
            if (restored.count == 0) {
                resources.getString(R.string.trash_gone)
            } else {
                listOfNotNull(
                    resources.getString(R.string.trash_restored, restored.count),
                    when (restored.chat) {
                        ChatPlace.BEFORE -> resources.getString(R.string.trash_restored_chat_before)
                        ChatPlace.AFTER -> resources.getString(R.string.trash_restored_chat_after)
                        null -> null
                    },
                ).joinToString(" ")
            },
        )
    }

    fun purge(target: Purge) = act {
        when (target) {
            is Purge.All -> {
                graph.trash.purgeAll()
                selected = emptySet()
                graph.navigator.say(resources.getString(R.string.trash_emptied))
            }
            is Purge.Some -> {
                val purged = graph.trash.purge(target.ids)
                selected = selected - target.ids.toSet()
                graph.navigator.say(resources.getString(R.string.trash_purged, purged))
            }
        }
    }

    val total = counts?.values?.sum() ?: 0
    val listed = if (kind == null) total else counts?.get(kind) ?: 0
    Column(Modifier.fillMaxSize()) {
        TopAppBar(
            title = { Text(stringResource(R.string.trash_title)) },
            navigationIcon = {
                IconButton(onClick = onBack) {
                    Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = stringResource(R.string.trash_back))
                }
            },
            actions = {
                if (total > 0) {
                    TextButton(onClick = { asking = Purge.All(total) }, enabled = !busy) {
                        Text(stringResource(R.string.trash_empty_all), color = MaterialTheme.colorScheme.error)
                    }
                }
            },
        )
        val shown = counts
        if (shown != null && total > 0) {
            Kinds(shown, kind, ::show)
            Row(
                Modifier
                    .fillMaxWidth()
                    .horizontalScroll(rememberScrollState())
                    .padding(horizontal = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                if (selected.isEmpty()) {
                    TextButton(
                        onClick = { act { selected = graph.trash.ids(kind).toSet() } },
                        enabled = !busy && listed > 0,
                    ) { Text(stringResource(R.string.trash_select_all, listed)) }
                } else {
                    Text(
                        stringResource(R.string.trash_selected, selected.size),
                        Modifier.padding(horizontal = 8.dp),
                        style = MaterialTheme.typography.labelLarge,
                    )
                    TextButton(onClick = { restore(selected) }, enabled = !busy) {
                        Text(stringResource(R.string.trash_restore))
                    }
                    TextButton(onClick = { asking = Purge.Some(selected.toList()) }, enabled = !busy) {
                        Text(stringResource(R.string.trash_purge), color = MaterialTheme.colorScheme.error)
                    }
                    TextButton(onClick = { selected = emptySet() }) { Text(stringResource(R.string.trash_select_none)) }
                }
            }
        }
        LazyColumn(Modifier.fillMaxSize(), state = list, contentPadding = PaddingValues(bottom = 16.dp)) {
            item(key = "lead") {
                Text(
                    stringResource(R.string.trash_lead, TrashStore.DAYS),
                    Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (shown != null && entries.isEmpty()) {
                item(key = "empty") {
                    Text(
                        stringResource(if (total == 0) R.string.trash_empty else R.string.trash_none_of_kind),
                        Modifier.padding(16.dp),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            items(entries, key = { it.id }) { entry ->
                Entry(
                    entry,
                    graph.texts,
                    checked = entry.id in selected,
                    busy = busy,
                    onToggle = { on -> selected = if (on) selected + entry.id else selected - entry.id },
                    onRestore = { restore(listOf(entry.id)) },
                    onPurge = { name -> asking = Purge.Some(listOf(entry.id), name) },
                )
                HorizontalDivider()
            }
            if (more) {
                item(key = "more") {
                    Box(
                        Modifier
                            .fillMaxWidth()
                            .padding(16.dp),
                        contentAlignment = Alignment.Center,
                    ) { CircularProgressIndicator(Modifier.size(24.dp), strokeWidth = 2.dp) }
                }
            }
        }
    }
    asking?.let { target ->
        Confirm(
            title = stringResource(if (target is Purge.All) R.string.trash_empty_title else R.string.trash_purge_title),
            body = when (target) {
                is Purge.All -> stringResource(R.string.trash_empty_body, target.count)
                is Purge.Some -> target.name?.let { stringResource(R.string.trash_purge_body_one, it) }
                    ?: stringResource(R.string.trash_purge_body_many, target.ids.size)
            },
            confirm = stringResource(if (target is Purge.All) R.string.trash_empty_all else R.string.trash_purge),
            onConfirm = {
                asking = null
                purge(target)
            },
            onDismiss = { asking = null },
        )
    }
}

/** The kinds the trash holds, each with how many: all of them, and those of each kind it has (and the one chosen). */
@Composable
private fun Kinds(counts: Map<TrashKind, Int>, kind: TrashKind?, onKind: (TrashKind?) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 16.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        FilterChip(
            selected = kind == null,
            onClick = { onKind(null) },
            label = { Text(stringResource(R.string.trash_count, stringResource(R.string.trash_all), counts.values.sum())) },
        )
        for (option in TrashKind.entries) {
            val count = counts[option] ?: 0
            if (count == 0 && option != kind) continue
            FilterChip(
                selected = kind == option,
                onClick = { onKind(option) },
                label = { Text(stringResource(R.string.trash_count, kindLabel(option), count)) },
            )
        }
    }
}

@Composable
private fun kindLabel(kind: TrashKind): String = stringResource(
    when (kind) {
        TrashKind.CHAT -> R.string.trash_chat
        TrashKind.CAPTURE -> R.string.trash_capture
        TrashKind.SCHEDULE -> R.string.trash_schedule
    },
)

/**
 * An entry: chosen by a tap on its row, restored or deleted for good by its own buttons, which name it. The line above
 * it says what it was and when it went, and the line below tells it from others of its kind.
 */
@Composable
private fun Entry(
    entry: TrashEntry,
    texts: Texts,
    checked: Boolean,
    busy: Boolean,
    onToggle: (Boolean) -> Unit,
    onRestore: () -> Unit,
    onPurge: (String) -> Unit,
) {
    val capture = entry.capture
    val schedule = entry.schedule
    val name = when {
        schedule != null -> stringResource(
            R.string.schedule_row,
            texts.days(schedule.days),
            schedule.time,
            texts.siteName(schedule.site),
            schedule.query,
        )
        capture != null -> entry.title.ifEmpty { texts.siteName(capture.site) }
        else -> entry.title.ifEmpty { kindLabel(entry.kind) }
    }
    val detail = when {
        schedule != null -> stringResource(if (schedule.enabled) R.string.trash_schedule_on else R.string.trash_schedule_off)
        capture != null -> texts.siteName(capture.site) + " · " +
            stringResource(R.string.records_counts, capture.adCount, capture.productCount, capture.resultCount)
        else -> stringResource(R.string.trash_chat_detail, entry.messages)
    }
    val heading = listOfNotNull(
        kindLabel(entry.kind),
        stringResource(R.string.trash_deleted_at, texts.time(entry.deletedAt)),
        if (entry.retired) stringResource(R.string.trash_retired) else null,
    ).joinToString(" · ")
    val restoreLabel = stringResource(R.string.trash_restore_one, name)
    ListItem(
        modifier = Modifier.toggleable(value = checked, role = Role.Checkbox, onValueChange = onToggle),
        leadingContent = { Checkbox(checked = checked, onCheckedChange = null) },
        overlineContent = { Text(heading, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        headlineContent = { Text(name, maxLines = 2, overflow = TextOverflow.Ellipsis) },
        supportingContent = { Text(detail, maxLines = 1, overflow = TextOverflow.Ellipsis) },
        trailingContent = {
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(
                    onClick = onRestore,
                    enabled = !busy,
                    modifier = Modifier.semantics { contentDescription = restoreLabel },
                ) { Text(stringResource(R.string.trash_restore)) }
                IconButton(onClick = { onPurge(name) }, enabled = !busy) {
                    Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.trash_purge_one, name))
                }
            }
        },
    )
}

/** How near the end of the list the next page is asked for, in rows. */
private const val NEAR_END = 10
