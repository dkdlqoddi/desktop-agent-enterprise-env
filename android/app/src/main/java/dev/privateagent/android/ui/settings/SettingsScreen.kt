package dev.privateagent.android.ui.settings

import android.Manifest
import android.content.Intent
import android.content.res.Resources
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowDropDown
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.privateagent.android.AppGraph
import dev.privateagent.android.BuildConfig
import dev.privateagent.android.R
import dev.privateagent.android.agent.ChatAgent
import dev.privateagent.android.data.ScheduleRow
import dev.privateagent.android.llm.ApiFormat
import dev.privateagent.android.llm.Endpoint
import dev.privateagent.android.llm.LlmException
import dev.privateagent.android.ui.orSay
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(graph: AppGraph) {
    // 데이터 → 휴지통 opens over the settings; the settings are where they were scrolled to when it closes.
    var trash by rememberSaveable { mutableStateOf(false) }
    val scroll = rememberScrollState()
    if (trash) {
        TrashScreen(graph, onBack = { trash = false })
        return
    }
    Column(
        Modifier
            .fillMaxSize()
            .imePadding(),
    ) {
        TopAppBar(title = { Text(stringResource(R.string.settings_title)) })
        Column(
            Modifier
                .fillMaxSize()
                .verticalScroll(scroll)
                .padding(bottom = 24.dp),
        ) {
            LlmSection(graph)
            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            // What the bundled agent runs unasked, what is closed to it and what it remembers; a build without it has
            // none of that.
            if (graph.agent.engine == ChatAgent.Engine.HERMES) {
                AgentSection(graph)
                HorizontalDivider(Modifier.padding(vertical = 8.dp))
            }
            ScheduleSection(graph)
            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            BrowserSection(graph)
            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            DataSection(graph, openTrash = { trash = true })
            HorizontalDivider(Modifier.padding(vertical = 8.dp))
            Section(stringResource(R.string.settings_about)) {
                Text(stringResource(R.string.settings_about_body), style = MaterialTheme.typography.bodyMedium)
                Text(
                    stringResource(R.string.settings_version, BuildConfig.VERSION_NAME),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
internal fun Section(title: String, content: @Composable () -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Text(title, style = MaterialTheme.typography.titleMedium, color = MaterialTheme.colorScheme.primary)
        content()
    }
}

@Composable
private fun LlmSection(graph: AppGraph) {
    val saved by graph.settings.llm.collectAsStateWithLifecycle()
    var format by rememberSaveable { mutableStateOf(saved.format) }
    var endpoint by rememberSaveable { mutableStateOf(saved.endpoint) }
    var model by rememberSaveable { mutableStateOf(saved.model) }
    // The key typed in this screen only; a saved key is never read back into it.
    var key by remember { mutableStateOf("") }
    var status by remember { mutableStateOf<String?>(null) }
    var statusIsError by remember { mutableStateOf(false) }
    var testing by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val resources = LocalResources.current

    fun endpointProblem(problem: Endpoint.Problem): String = resources.getString(
        when (problem) {
            Endpoint.Problem.EMPTY -> R.string.endpoint_error_empty
            Endpoint.Problem.INSECURE_HTTP -> R.string.endpoint_error_http
            Endpoint.Problem.UNSUPPORTED_SCHEME -> R.string.endpoint_error_scheme
            Endpoint.Problem.INVALID -> R.string.endpoint_error_invalid
            Endpoint.Problem.CREDENTIALS -> R.string.endpoint_error_credentials
            Endpoint.Problem.FRAGMENT -> R.string.endpoint_error_fragment
        },
    )

    /** Saves the form; false (with the reason shown) when it cannot be saved. */
    fun save(): Boolean {
        val check = Endpoint.normalize(endpoint)
        if (check is Endpoint.Check.Rejected) {
            status = endpointProblem(check.problem)
            statusIsError = true
            return false
        }
        if (model.isBlank()) {
            status = resources.getString(R.string.model_error_empty)
            statusIsError = true
            return false
        }
        val url = (check as Endpoint.Check.Ok).url
        val result = try {
            graph.settings.saveLlm(format, url, model, key)
        } catch (_: Exception) {
            status = resources.getString(R.string.settings_key_failed)
            statusIsError = true
            return false
        }
        endpoint = url
        key = ""
        status = resources.getString(if (result.keyCleared) R.string.settings_key_cleared_host else R.string.settings_saved)
        statusIsError = result.keyCleared
        return true
    }

    Section(stringResource(R.string.settings_llm)) {
        Text(stringResource(R.string.settings_format), style = MaterialTheme.typography.labelLarge)
        SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
            val formats = listOf(ApiFormat.OPENAI to R.string.format_openai, ApiFormat.ANTHROPIC to R.string.format_anthropic)
            formats.forEachIndexed { index, (option, label) ->
                SegmentedButton(
                    selected = format == option,
                    onClick = { format = option },
                    shape = SegmentedButtonDefaults.itemShape(index, formats.size),
                ) { Text(stringResource(label)) }
            }
        }
        OutlinedTextField(
            value = endpoint,
            onValueChange = { endpoint = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text(stringResource(R.string.settings_endpoint)) },
            placeholder = {
                Text(
                    stringResource(
                        if (format == ApiFormat.ANTHROPIC) R.string.settings_endpoint_hint_anthropic else R.string.settings_endpoint_hint_openai,
                    ),
                )
            },
            supportingText = { Text(stringResource(R.string.settings_endpoint_help)) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, autoCorrectEnabled = false),
        )
        OutlinedTextField(
            value = model,
            onValueChange = { model = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text(stringResource(R.string.settings_model)) },
            keyboardOptions = KeyboardOptions(autoCorrectEnabled = false),
        )
        OutlinedTextField(
            value = key,
            onValueChange = { key = it },
            modifier = Modifier.fillMaxWidth(),
            singleLine = true,
            label = { Text(stringResource(R.string.settings_key)) },
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, autoCorrectEnabled = false),
            supportingText = {
                Text(stringResource(if (saved.hasKey) R.string.settings_key_saved else R.string.settings_key_none))
            },
        )
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
            Button(onClick = { save() }, enabled = !testing) { Text(stringResource(R.string.settings_save)) }
            OutlinedButton(
                onClick = {
                    if (!save()) return@OutlinedButton
                    testing = true
                    status = resources.getString(R.string.settings_testing)
                    statusIsError = false
                    scope.launch {
                        try {
                            val connection = withContext(Dispatchers.IO) { graph.settings.connection() }
                                ?: return@launch
                            graph.llm.ping(connection)
                            status = resources.getString(R.string.settings_test_ok)
                            statusIsError = false
                        } catch (error: LlmException) {
                            status = graph.texts.llmError(error)
                            statusIsError = true
                        } catch (cancelled: CancellationException) {
                            throw cancelled
                        } catch (_: Exception) {
                            status = resources.getString(R.string.llm_error_unexpected)
                            statusIsError = true
                        } finally {
                            testing = false
                        }
                    }
                },
                enabled = !testing,
            ) { Text(stringResource(R.string.settings_test)) }
            if (testing) CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp)
            if (saved.hasKey) {
                TextButton(onClick = {
                    graph.settings.clearKey()
                    status = resources.getString(R.string.settings_key_removed)
                    statusIsError = false
                }) { Text(stringResource(R.string.settings_key_clear)) }
            }
        }
        status?.let {
            Text(
                it,
                style = MaterialTheme.typography.bodyMedium,
                color = if (statusIsError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
        Text(
            stringResource(R.string.settings_privacy),
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun ScheduleSection(graph: AppGraph) {
    val changes by graph.database.changes.collectAsStateWithLifecycle()
    var schedules by remember { mutableStateOf<List<ScheduleRow>>(emptyList()) }
    LaunchedEffect(changes) { schedules = graph.orSay(schedules) { graph.schedules.all() } }
    var deleting by remember { mutableStateOf<ScheduleRow?>(null) }
    var canNotify by remember { mutableStateOf(graph.notifications.canPost()) }
    var asked by remember { mutableIntStateOf(0) }
    LifecycleResumeEffect(asked) {
        canNotify = graph.notifications.canPost()
        onPauseOrDispose { }
    }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        canNotify = graph.notifications.canPost()
        asked += 1
    }
    val scope = rememberCoroutineScope()
    val context = LocalContext.current
    val toggleLabel = stringResource(R.string.schedule_toggle)

    Section(stringResource(R.string.settings_schedules)) {
        if (schedules.isNotEmpty() && !canNotify) {
            Text(
                stringResource(R.string.notifications_off),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.error,
            )
            OutlinedButton(onClick = {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU && asked == 0) {
                    permission.launch(Manifest.permission.POST_NOTIFICATIONS)
                } else {
                    // The app's own notification settings, where the user turns them on.
                    context.startActivity(
                        Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS)
                            .putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName),
                    )
                }
            }) { Text(stringResource(R.string.notifications_allow)) }
        }
        if (schedules.isEmpty()) {
            Text(
                stringResource(R.string.settings_schedules_empty),
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        for (row in schedules) {
            ListItem(
                headlineContent = {
                    Text(stringResource(R.string.schedule_row, graph.texts.days(row.days), row.time, graph.texts.siteName(row.site), row.query))
                },
                supportingContent = {
                    Text(
                        if (row.enabled) {
                            row.nextRunAt?.let { stringResource(R.string.schedule_next, graph.texts.time(it)) }.orEmpty()
                        } else {
                            stringResource(R.string.schedule_off)
                        },
                    )
                },
                trailingContent = {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Switch(
                            checked = row.enabled,
                            onCheckedChange = { enabled ->
                                scope.launch { graph.orSay(Unit) { graph.scheduler.setEnabled(row.id, enabled) } }
                            },
                            modifier = Modifier.semantics { contentDescription = toggleLabel },
                        )
                        IconButton(onClick = { deleting = row }) {
                            Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.schedule_delete))
                        }
                    }
                },
            )
        }
    }
    val moved = stringResource(R.string.trash_moved_schedule)
    deleting?.let { row ->
        Confirm(
            title = stringResource(R.string.schedule_delete_title),
            body = stringResource(
                R.string.schedule_delete_body,
                stringResource(R.string.schedule_row, graph.texts.days(row.days), row.time, graph.texts.siteName(row.site), row.query),
            ),
            confirm = stringResource(R.string.schedule_delete_confirm),
            onConfirm = {
                deleting = null
                scope.launch {
                    graph.orSay(Unit) {
                        // To the trash, from where 되돌리기 (or 휴지통) brings it back to remind from its next time on.
                        graph.sayMoved(moved, listOfNotNull(graph.scheduler.delete(row.id)))
                    }
                }
            },
            onDismiss = { deleting = null },
        )
    }
}

@Composable
private fun BrowserSection(graph: AppGraph) {
    val recordClicks by graph.settings.recordClicks.collectAsStateWithLifecycle()
    var confirming by remember { mutableStateOf(false) }
    val cleared = stringResource(R.string.settings_cleared)
    Section(stringResource(R.string.settings_browser)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.settings_record_clicks), style = MaterialTheme.typography.bodyLarge)
                Text(
                    stringResource(R.string.settings_record_clicks_help),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.size(8.dp))
            Switch(checked = recordClicks, onCheckedChange = graph.settings::setRecordClicks)
        }
        OutlinedButton(onClick = { confirming = true }) { Text(stringResource(R.string.settings_clear_browser)) }
    }
    if (confirming) {
        Confirm(
            title = stringResource(R.string.settings_clear_browser_title),
            body = stringResource(R.string.settings_clear_browser_body),
            confirm = stringResource(R.string.confirm_clear),
            onConfirm = {
                confirming = false
                graph.browser.clearData()
                graph.navigator.say(cleared)
            },
            onDismiss = { confirming = false },
        )
    }
}

/**
 * What the app keeps: the conversation and the records go to the trash when they are cleared, and the trash, where they
 * wait 30 days, opens from here ([openTrash]).
 */
@Composable
private fun DataSection(graph: AppGraph, openTrash: () -> Unit) {
    var confirm by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()
    val changes by graph.database.changes.collectAsStateWithLifecycle()
    var inTrash by remember { mutableIntStateOf(0) }
    LaunchedEffect(changes) { inTrash = graph.orSay(inTrash) { graph.trash.counts().values.sum() } }
    val resources = LocalResources.current
    Section(stringResource(R.string.settings_data)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            OutlinedButton(onClick = { confirm = CHAT }) { Text(stringResource(R.string.settings_clear_chat)) }
            OutlinedButton(onClick = { confirm = RECORDS }) { Text(stringResource(R.string.settings_clear_records)) }
        }
        RecordsKept(graph)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(stringResource(R.string.trash_title), style = MaterialTheme.typography.bodyLarge)
                Text(
                    stringResource(R.string.settings_trash_help, inTrash),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.size(8.dp))
            OutlinedButton(onClick = openTrash) { Text(stringResource(R.string.settings_trash_open)) }
        }
    }
    when (confirm) {
        CHAT -> Confirm(
            title = stringResource(R.string.settings_clear_chat_title),
            body = stringResource(R.string.settings_clear_chat_body),
            confirm = stringResource(R.string.confirm_clear),
            onConfirm = {
                confirm = null
                scope.launch {
                    graph.orSay(Unit) {
                        // To the trash, whole; what was allowed for the conversation ends with it, restored or not.
                        val entry = graph.agent.clear()
                        if (entry == null) {
                            graph.navigator.say(resources.getString(R.string.trash_nothing_chat))
                        } else {
                            graph.sayMoved(resources.getString(R.string.trash_moved_chat), listOf(entry))
                        }
                    }
                }
            },
            onDismiss = { confirm = null },
        )
        RECORDS -> Confirm(
            title = stringResource(R.string.settings_clear_records_title),
            body = stringResource(R.string.settings_clear_records_body),
            confirm = stringResource(R.string.confirm_clear),
            onConfirm = {
                confirm = null
                scope.launch {
                    graph.orSay(Unit) {
                        val entries = graph.records.clear()
                        // The items of a page that is gone are no longer a filter.
                        graph.navigator.recordsFilter.value = graph.navigator.recordsFilter.value.copy(captureId = null)
                        if (entries.isEmpty()) {
                            graph.navigator.say(resources.getString(R.string.trash_nothing_records))
                        } else {
                            graph.sayMoved(resources.getString(R.string.trash_moved_records, entries.size), entries)
                        }
                    }
                }
            },
            onDismiss = { confirm = null },
        )
    }
}

/**
 * How long the recorded pages are kept: until they are deleted (the default), or a number of days, the desktop's
 * choices. The older ones go to the trash at each start of the app and once a day (Upkeep).
 */
@Composable
private fun RecordsKept(graph: AppGraph) {
    val kept by graph.settings.recordsKept.collectAsStateWithLifecycle()
    var open by remember { mutableStateOf(false) }
    val resources = LocalResources.current
    val label = keepLabel(resources, kept)
    val choice = stringResource(R.string.settings_records_kept_choice, label)
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(stringResource(R.string.settings_records_kept), style = MaterialTheme.typography.bodyLarge)
            Text(
                if (kept == 0) {
                    stringResource(R.string.settings_records_kept_off)
                } else {
                    stringResource(R.string.settings_records_kept_on, label)
                },
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Spacer(Modifier.size(8.dp))
        Box {
            OutlinedButton(onClick = { open = true }, modifier = Modifier.semantics { contentDescription = choice }) {
                Text(label)
                Icon(Icons.Filled.ArrowDropDown, contentDescription = null)
            }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
                // A number of days set otherwise stays among the choices while it is the one kept.
                for (days in (KEEP_DAYS + kept).distinct().sorted()) {
                    DropdownMenuItem(
                        text = { Text(keepLabel(resources, days)) },
                        onClick = {
                            open = false
                            graph.settings.setRecordsKept(days)
                            graph.navigator.say(
                                if (days == 0) {
                                    resources.getString(R.string.settings_records_kept_saved_off)
                                } else {
                                    resources.getString(R.string.settings_records_kept_saved_on, keepLabel(resources, days))
                                },
                            )
                        },
                    )
                }
            }
        }
    }
}

private fun keepLabel(resources: Resources, days: Int): String = when (days) {
    0 -> resources.getString(R.string.keep_forever)
    365 -> resources.getString(R.string.keep_year)
    else -> resources.getString(R.string.keep_days, days)
}

/** How long the records may be kept, as on the desktop (web-records.tsx KEEP): until deleted, or this many days. */
private val KEEP_DAYS = listOf(0, 7, 30, 90, 180, 365)

private const val CHAT = "chat"
private const val RECORDS = "records"

@Composable
internal fun Confirm(title: String, body: String, confirm: String, onConfirm: () -> Unit, onDismiss: () -> Unit) {
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(title) },
        text = { Text(body) },
        confirmButton = {
            TextButton(onClick = onConfirm) {
                Text(confirm, color = MaterialTheme.colorScheme.error)
            }
        },
        dismissButton = { TextButton(onClick = onDismiss) { Text(stringResource(R.string.confirm_cancel)) } },
    )
}
