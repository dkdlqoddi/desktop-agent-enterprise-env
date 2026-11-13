package dev.privateagent.android.ui.settings

import android.content.res.Resources
import android.text.format.Formatter
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
import androidx.compose.ui.platform.LocalResources
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.privateagent.android.AppGraph
import dev.privateagent.android.R
import dev.privateagent.android.agent.AgentMemory
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 설정 → 채팅 에이전트, as the desktop's 관리 → 채팅 에이전트: the commands the agent runs without asking (each can only
 * be taken off the list here; the one way onto it is the answer "always" to a command it asked about), the kinds of
 * command allowed for this conversation, what is closed to it, and what it remembers. A phone's agent has no terminal
 * and no file tools, so there are no folders to add and no command patterns to refuse.
 */
@Composable
internal fun AgentSection(graph: AppGraph) {
    val allowed by graph.grants.allowed.collectAsStateWithLifecycle()
    val session by graph.grants.session.collectAsStateWithLifecycle()
    val revoked = stringResource(R.string.agent_allowed_revoked)
    Section(stringResource(R.string.settings_agent)) {
        Heading(stringResource(R.string.agent_allowed_title))
        Note(stringResource(R.string.agent_allowed_help))
        if (allowed.isEmpty()) Note(stringResource(R.string.agent_allowed_empty))
        for (command in allowed) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    command,
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.bodySmall,
                    fontFamily = FontFamily.Monospace,
                )
                IconButton(onClick = {
                    graph.grants.revoke(command)
                    graph.navigator.say(revoked)
                }) {
                    Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.agent_allowed_revoke, command))
                }
            }
        }

        Heading(stringResource(R.string.agent_session_title))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                if (session.isEmpty()) {
                    stringResource(R.string.agent_session_none)
                } else {
                    stringResource(R.string.agent_session_count, session.size)
                },
                modifier = Modifier.weight(1f),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            if (session.isNotEmpty()) {
                TextButton(onClick = graph.grants::forgetSession) { Text(stringResource(R.string.agent_session_forget)) }
            }
        }

        Heading(stringResource(R.string.agent_protected_title))
        Text(stringResource(R.string.agent_protected_body), style = MaterialTheme.typography.bodyMedium)

        MemoryBlock(graph)
    }
}

@Composable
private fun Heading(text: String) {
    Text(text, style = MaterialTheme.typography.titleSmall)
}

@Composable
private fun Note(text: String) {
    Text(text, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}

/** An entry being written: a new one ([index] below zero) or the one at [index], with the words it starts from. */
private data class Editing(val target: AgentMemory.Target, val index: Int, val text: String)

/**
 * What the agent remembers: its two lists, each entry to correct or delete and new ones to add, and the records of
 * the turns it ran, to delete whole. The agent writes all of it by itself as it works, so it is read again whenever a
 * turn ends; nothing of it is changed while one runs.
 */
@Composable
private fun MemoryBlock(graph: AppGraph) {
    val scope = rememberCoroutineScope()
    val resources = LocalResources.current
    val context = LocalContext.current
    val busy by graph.agent.busy.collectAsStateWithLifecycle()
    var view by remember { mutableStateOf<AgentMemory.View?>(null) }
    LaunchedEffect(busy) { view = withContext(Dispatchers.IO) { graph.memory.read() } }
    var editing by remember { mutableStateOf<Editing?>(null) }
    var removing by remember { mutableStateOf<Editing?>(null) }
    var forgetting by remember { mutableStateOf(false) }
    val saved = stringResource(R.string.memory_saved)
    val cleared = stringResource(R.string.settings_cleared)

    /** Saves [entries] as the list of [target]; [done] runs once they are saved. */
    fun save(target: AgentMemory.Target, entries: List<String>, done: () -> Unit) {
        scope.launch {
            try {
                view = withContext(Dispatchers.IO) { graph.memory.save(target, entries) }
                done()
                graph.navigator.say(saved)
            } catch (refused: AgentMemory.MemoryException) {
                graph.navigator.say(problem(resources, refused))
            }
        }
    }

    Heading(stringResource(R.string.memory_title))
    Note(stringResource(R.string.memory_help))
    for (target in AgentMemory.Target.entries) {
        val list = view?.of(target)
        val title = stringResource(if (target == AgentMemory.Target.MEMORY) R.string.memory_notes else R.string.memory_user)
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(title, modifier = Modifier.weight(1f), style = MaterialTheme.typography.labelLarge)
            if (list != null) Note(stringResource(R.string.memory_size, list.chars, list.limit))
        }
        if (list != null && list.entries.isEmpty()) Note(stringResource(R.string.memory_empty))
        list?.entries?.forEachIndexed { index, entry ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    entry,
                    modifier = Modifier.weight(1f),
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 4,
                    overflow = TextOverflow.Ellipsis,
                )
                IconButton(onClick = { editing = Editing(target, index, entry) }) {
                    Icon(Icons.Filled.Edit, contentDescription = stringResource(R.string.memory_edit, entry.take(40)))
                }
                IconButton(onClick = { removing = Editing(target, index, entry) }) {
                    Icon(Icons.Filled.Delete, contentDescription = stringResource(R.string.memory_delete, entry.take(40)))
                }
            }
        }
        TextButton(onClick = { editing = Editing(target, -1, "") }, enabled = list != null) {
            Text(stringResource(R.string.memory_add))
        }
    }

    Heading(stringResource(R.string.memory_records_title))
    val records = view?.records
    Note(
        if (records == null || records.files == 0) {
            stringResource(R.string.memory_records_none)
        } else {
            stringResource(R.string.memory_records, records.files, Formatter.formatShortFileSize(context, records.bytes))
        },
    )
    OutlinedButton(onClick = { forgetting = true }, enabled = records != null && records.files > 0) {
        Text(stringResource(R.string.memory_forget))
    }

    editing?.let { edit ->
        var text by remember(edit) { mutableStateOf(edit.text) }
        AlertDialog(
            onDismissRequest = { editing = null },
            title = { Text(stringResource(if (edit.index < 0) R.string.memory_add_title else R.string.memory_edit_title)) },
            text = {
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it },
                    modifier = Modifier.fillMaxWidth(),
                    label = { Text(stringResource(R.string.memory_entry)) },
                    minLines = 3,
                    maxLines = 8,
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        val current = view?.of(edit.target)?.entries.orEmpty()
                        val entries = if (edit.index < 0) {
                            current + text
                        } else {
                            current.toMutableList().also { if (edit.index < it.size) it[edit.index] = text }
                        }
                        save(edit.target, entries) { editing = null }
                    },
                    enabled = text.isNotBlank(),
                ) { Text(stringResource(R.string.settings_save)) }
            },
            dismissButton = { TextButton(onClick = { editing = null }) { Text(stringResource(R.string.confirm_cancel)) } },
        )
    }
    removing?.let { remove ->
        Confirm(
            title = stringResource(R.string.memory_delete_title),
            body = remove.text,
            confirm = stringResource(R.string.confirm_clear),
            onConfirm = {
                removing = null
                // The entry as it was shown: the list may have changed since (a turn ended meanwhile).
                val entries = view?.of(remove.target)?.entries.orEmpty().filterIndexed { index, entry ->
                    index != remove.index || entry != remove.text
                }
                save(remove.target, entries) {}
            },
            onDismiss = { removing = null },
        )
    }
    if (forgetting) {
        Confirm(
            title = stringResource(R.string.memory_forget_title),
            body = stringResource(R.string.memory_forget_body),
            confirm = stringResource(R.string.confirm_clear),
            onConfirm = {
                forgetting = false
                scope.launch {
                    try {
                        // The worker holds its records open, and a turn that runs would wait here: told at once.
                        if (graph.agent.busy.value) throw AgentMemory.MemoryException(AgentMemory.Problem.BUSY)
                        graph.hermesSession.stopWorker()
                        view = withContext(Dispatchers.IO) { graph.memory.forget() }
                        graph.navigator.say(cleared)
                    } catch (refused: AgentMemory.MemoryException) {
                        graph.navigator.say(problem(resources, refused))
                    }
                }
            },
            onDismiss = { forgetting = false },
        )
    }
}

/** Why the memory was not changed, in the screen's words. */
private fun problem(resources: Resources, refused: AgentMemory.MemoryException): String = when (refused.problem) {
    AgentMemory.Problem.BUSY -> resources.getString(R.string.memory_busy)
    AgentMemory.Problem.INVALID -> resources.getString(R.string.memory_invalid)
    AgentMemory.Problem.DELIMITER -> resources.getString(R.string.memory_delimiter)
    AgentMemory.Problem.TOO_LONG -> resources.getString(R.string.memory_too_long, refused.limit, refused.size)
    AgentMemory.Problem.WRITE -> resources.getString(R.string.memory_write_failed)
    AgentMemory.Problem.PARTIAL -> resources.getString(R.string.memory_partial)
}
