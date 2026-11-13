package dev.privateagent.android.ui.chat

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material.icons.filled.KeyboardArrowUp
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SuggestionChip
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringArrayResource
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.privateagent.android.AppGraph
import dev.privateagent.android.R
import dev.privateagent.android.agent.Activity
import dev.privateagent.android.agent.ActivityStep
import dev.privateagent.android.agent.AgentTools
import dev.privateagent.android.agent.ChatAgent
import dev.privateagent.android.agent.HermesChat
import dev.privateagent.android.agent.RecordKindFilter
import dev.privateagent.android.agent.StepKind
import dev.privateagent.android.agent.Texts
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.ChatMessage
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.data.MessageRole
import dev.privateagent.android.data.NoticeKind
import dev.privateagent.android.ui.Tab
import dev.privateagent.android.ui.orSay
import dev.privateagent.android.util.text
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.json.JSONException
import org.json.JSONObject

/** The turn view of a build without Hermes: never a turn. */
private val NO_TURN: StateFlow<HermesChat.TurnView?> = MutableStateFlow(null)

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(graph: AppGraph) {
    val agent = graph.agent
    val changes by graph.database.changes.collectAsStateWithLifecycle()
    var messages by remember { mutableStateOf<List<ChatMessage>>(emptyList()) }
    LaunchedEffect(changes) { messages = graph.orSay(messages) { graph.messages.all() } }
    val busy by agent.busy.collectAsStateWithLifecycle()
    val hermes = agent.hermes
    val turn by (hermes?.view ?: NO_TURN).collectAsStateWithLifecycle()
    val engine = remember { agent.engine }
    val llm by graph.settings.llm.collectAsStateWithLifecycle()
    var input by rememberSaveable { mutableStateOf("") }
    val listState = rememberLazyListState()
    LaunchedEffect(messages.size, busy) {
        val last = messages.size + (if (busy) 1 else 0) - 1
        if (last >= 0) listState.animateScrollToItem(last)
    }

    // Android 13+ asks before a schedule's notifications can show; the schedule is made whatever the answer.
    val context = LocalContext.current
    var approving by remember { mutableStateOf<Long?>(null) }
    val permission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {
        approving?.let(agent::approve)
        approving = null
    }
    val approve: (ChatMessage) -> Unit = { message ->
        val needsPermission = message.tool == AgentTools.SCHEDULE_CAPTURE &&
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        if (needsPermission) {
            approving = message.id
            permission.launch(Manifest.permission.POST_NOTIFICATIONS)
        } else {
            agent.approve(message.id)
        }
    }
    val send = {
        if (input.isNotBlank() && !busy) {
            agent.send(input)
            input = ""
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .imePadding(),
    ) {
        TopAppBar(title = { Text(stringResource(R.string.chat_title)) }, actions = { EngineBadge(engine) })
        if (!llm.configured) SetupCard(onOpen = { graph.navigator.show(Tab.SETTINGS) }, Modifier.padding(horizontal = 16.dp))
        LazyColumn(
            state = listState,
            modifier = Modifier
                .weight(1f)
                .fillMaxWidth(),
            contentPadding = PaddingValues(16.dp),
            verticalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            if (messages.isEmpty()) item(key = "hint") { ChatHint(engine, onExample = { input = it }) }
            items(messages, key = { it.id }) { message ->
                MessageItem(
                    message = message,
                    texts = graph.texts,
                    onApprove = { approve(message) },
                    onCancel = { agent.cancel(message.id) },
                    onViewCapture = { id -> graph.navigator.showCapture(id) },
                    onViewRecords = { filter -> graph.navigator.showRecords(filter) },
                    onOpenBrowser = { graph.navigator.show(Tab.BROWSER) },
                    onOpenSettings = { graph.navigator.show(Tab.SETTINGS) },
                )
            }
            if (busy) {
                item(key = "waiting") {
                    val current = turn
                    if (hermes != null && current != null) {
                        HermesPending(current, onDecide = hermes::decide, onAnswer = hermes::answer)
                    } else {
                        Waiting()
                    }
                }
            }
        }
        Row(
            Modifier
                .fillMaxWidth()
                .padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text(stringResource(R.string.chat_input_hint)) },
                maxLines = 4,
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                keyboardActions = KeyboardActions(onSend = { send() }),
            )
            Spacer(Modifier.size(8.dp))
            if (busy) {
                IconButton(onClick = agent::stop) {
                    Icon(Icons.Filled.Close, contentDescription = stringResource(R.string.chat_stop))
                }
            } else {
                FilledIconButton(onClick = send, enabled = input.isNotBlank()) {
                    Icon(Icons.AutoMirrored.Filled.Send, contentDescription = stringResource(R.string.chat_send))
                }
            }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun ChatHint(engine: ChatAgent.Engine, onExample: (String) -> Unit) {
    Column(
        Modifier
            .fillMaxWidth()
            .padding(vertical = 24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(stringResource(R.string.chat_empty_title), style = MaterialTheme.typography.titleLarge)
        Text(
            stringResource(
                if (engine == ChatAgent.Engine.HERMES) R.string.chat_empty_body_hermes else R.string.chat_empty_body,
            ),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center,
        )
        FlowRow(
            horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterHorizontally),
            modifier = Modifier.fillMaxWidth(),
        ) {
            for (example in stringArrayResource(R.array.chat_examples)) {
                SuggestionChip(onClick = { onExample(example) }, label = { Text(example) })
            }
        }
    }
}

@Composable
private fun SetupCard(onOpen: () -> Unit, modifier: Modifier = Modifier) {
    OutlinedCard(modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(stringResource(R.string.chat_setup_title), style = MaterialTheme.typography.titleSmall)
            Text(stringResource(R.string.chat_setup_body), style = MaterialTheme.typography.bodyMedium)
            TextButton(onClick = onOpen, modifier = Modifier.align(Alignment.End)) {
                Text(stringResource(R.string.chat_open_settings))
            }
        }
    }
}

@Composable
private fun Waiting() {
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
    ) {
        CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
        Spacer(Modifier.size(8.dp))
        Text(
            stringResource(R.string.chat_waiting),
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
fun MessageItem(
    message: ChatMessage,
    texts: Texts,
    onApprove: () -> Unit,
    onCancel: () -> Unit,
    onViewCapture: (Long) -> Unit,
    onViewRecords: (ItemFilter) -> Unit,
    onOpenBrowser: () -> Unit,
    onOpenSettings: () -> Unit,
) {
    Column {
        when (message.role) {
            MessageRole.USER -> Bubble(message.text, mine = true)
            MessageRole.ASSISTANT -> Bubble(message.text, mine = false)
            MessageRole.ACTION -> ActionCard(message, texts, onApprove, onCancel, onViewCapture, onViewRecords, onOpenBrowser)
            MessageRole.NOTICE -> when (message.notice) {
                NoticeKind.SETUP -> SetupCard(onOpen = onOpenSettings)
                NoticeKind.ERROR -> Surface(
                    color = MaterialTheme.colorScheme.errorContainer,
                    contentColor = MaterialTheme.colorScheme.onErrorContainer,
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(message.text, Modifier.padding(12.dp), style = MaterialTheme.typography.bodyMedium)
                }
                else -> Text(
                    message.text,
                    modifier = Modifier.fillMaxWidth(),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                )
            }
        }
        // What the turn this message ended did on the way.
        message.activity?.let { ActivityNote(it) }
    }
}

/**
 * What a turn did on the way to the message above (the desktop's 한 일 N건): closed until asked for, then the tools
 * it ran, the app's actions, the commands it asked about with how each was decided, and its questions, in order.
 */
@Composable
fun ActivityNote(activity: Activity) {
    var open by rememberSaveable { mutableStateOf(false) }
    Column {
        TextButton(onClick = { open = !open }, contentPadding = PaddingValues(horizontal = 8.dp)) {
            Icon(
                if (open) Icons.Filled.KeyboardArrowUp else Icons.Filled.KeyboardArrowDown,
                contentDescription = null,
                modifier = Modifier.size(16.dp),
            )
            Spacer(Modifier.size(4.dp))
            Text(stringResource(R.string.activity_count, activity.count), style = MaterialTheme.typography.labelMedium)
        }
        if (open) {
            val list = stringResource(R.string.activity_list)
            Column(
                Modifier
                    .padding(start = 12.dp, end = 4.dp, bottom = 4.dp)
                    .semantics { contentDescription = list },
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                for (step in activity.steps) ActivityRow(step)
                if (activity.more > 0) {
                    Text(
                        stringResource(R.string.activity_more, activity.more),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        }
    }
}

@Composable
private fun ActivityRow(step: ActivityStep) {
    val kind = when (step.kind) {
        StepKind.TOOL -> R.string.activity_kind_tool
        StepKind.ACTION -> R.string.activity_kind_action
        StepKind.APPROVAL -> R.string.activity_kind_approval
        StepKind.QUESTION -> R.string.activity_kind_question
    }
    // How it ended, in the chat's words; a result this version does not know is shown as it was kept.
    val result = when (step.result) {
        "once" -> R.string.activity_result_once
        "session" -> R.string.activity_result_session
        "always" -> R.string.activity_result_always
        "deny" -> R.string.activity_result_deny
        "timeout" -> R.string.activity_result_timeout
        "stopped" -> R.string.activity_result_stopped
        "done" -> R.string.activity_result_done
        "failed" -> R.string.activity_result_failed
        "submitted" -> R.string.activity_result_submitted
        "cancelled" -> R.string.activity_result_cancelled
        else -> null
    }
    Column {
        Row(verticalAlignment = Alignment.Top) {
            Text(
                stringResource(kind),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(end = 6.dp),
            )
            Text(
                step.text,
                style = MaterialTheme.typography.bodySmall,
                fontFamily = if (step.kind == StepKind.APPROVAL) FontFamily.Monospace else null,
                modifier = Modifier.weight(1f),
            )
            val ended = result?.let { stringResource(it) } ?: step.result
            if (ended.isNotEmpty()) {
                Text(
                    ended,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 6.dp),
                )
            }
        }
        if (step.note.isNotEmpty()) {
            Text(step.note, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
private fun Bubble(text: String, mine: Boolean) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        Surface(
            color = if (mine) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerHigh,
            contentColor = if (mine) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurface,
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.widthIn(max = 320.dp),
        ) {
            SelectionContainer {
                Text(text, Modifier.padding(horizontal = 14.dp, vertical = 10.dp), style = MaterialTheme.typography.bodyLarge)
            }
        }
    }
}

/** A tool call the user approves before it runs: 승인 대기 → 실행 중 → 완료 / 실패 / 취소. */
@Composable
fun ActionCard(
    message: ChatMessage,
    texts: Texts,
    onApprove: () -> Unit,
    onCancel: () -> Unit,
    onViewCapture: (Long) -> Unit,
    onViewRecords: (ItemFilter) -> Unit,
    onOpenBrowser: () -> Unit,
) {
    val args = remember(message.args) {
        try {
            JSONObject(message.args ?: "{}")
        } catch (_: JSONException) {
            JSONObject()
        }
    }
    val state = message.state ?: ActionState.FAILED
    OutlinedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(texts.toolTitle(message.tool), style = MaterialTheme.typography.titleSmall, modifier = Modifier.weight(1f))
                StateBadge(state)
            }
            Text(texts.describe(message.tool, args), style = MaterialTheme.typography.bodyMedium)
            message.result?.takeIf { it.isNotEmpty() }?.let { result ->
                Text(
                    result,
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (state == ActionState.FAILED) {
                        MaterialTheme.colorScheme.error
                    } else {
                        MaterialTheme.colorScheme.onSurfaceVariant
                    },
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                )
            }
            when (state) {
                ActionState.PENDING -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = onCancel) { Text(stringResource(R.string.action_cancel)) }
                    Spacer(Modifier.size(8.dp))
                    Button(onClick = onApprove) { Text(stringResource(R.string.action_approve)) }
                }
                ActionState.RUNNING -> LinearProgressIndicator(Modifier.fillMaxWidth().height(4.dp))
                ActionState.DONE -> {
                    val captureId = message.captureId
                    if (captureId != null) {
                        TextButton(onClick = { onViewCapture(captureId) }, modifier = Modifier.align(Alignment.End)) {
                            Text(stringResource(R.string.action_view_records))
                        }
                    } else if (message.tool == AgentTools.LIST_RECORDS) {
                        val filter = ItemFilter(
                            site = args.text("site").ifEmpty { null },
                            kind = RecordKindFilter.of(args.text("kind")),
                            search = args.text("query"),
                        )
                        TextButton(onClick = { onViewRecords(filter) }, modifier = Modifier.align(Alignment.End)) {
                            Text(stringResource(R.string.action_view_records))
                        }
                    } else if (message.tool == AgentTools.OPEN_SITE) {
                        TextButton(onClick = onOpenBrowser, modifier = Modifier.align(Alignment.End)) {
                            Text(stringResource(R.string.action_view_browser))
                        }
                    }
                }
                ActionState.FAILED -> if (message.tool == AgentTools.WEB_CAPTURE || message.tool == AgentTools.OPEN_SITE) {
                    TextButton(onClick = onOpenBrowser, modifier = Modifier.align(Alignment.End)) {
                        Text(stringResource(R.string.action_view_browser))
                    }
                }
                ActionState.CANCELLED -> Unit
            }
        }
    }
}

@Composable
private fun StateBadge(state: ActionState) {
    val colors = MaterialTheme.colorScheme
    val (label, container, content) = when (state) {
        ActionState.PENDING -> Triple(R.string.action_state_pending, colors.tertiaryContainer, colors.onTertiaryContainer)
        ActionState.RUNNING -> Triple(R.string.action_state_running, colors.secondaryContainer, colors.onSecondaryContainer)
        ActionState.DONE -> Triple(R.string.action_state_done, colors.primaryContainer, colors.onPrimaryContainer)
        ActionState.FAILED -> Triple(R.string.action_state_failed, colors.errorContainer, colors.onErrorContainer)
        ActionState.CANCELLED -> Triple(R.string.action_state_cancelled, colors.surfaceContainerHighest, colors.onSurface)
    }
    Surface(color = container, contentColor = content, shape = RoundedCornerShape(8.dp)) {
        Text(
            stringResource(label),
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
            style = MaterialTheme.typography.labelMedium,
        )
    }
}
