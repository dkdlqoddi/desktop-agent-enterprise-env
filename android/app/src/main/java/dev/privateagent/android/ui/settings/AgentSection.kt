package dev.privateagent.android.ui.settings

import androidx.compose.foundation.layout.Row
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import dev.privateagent.android.AppGraph
import dev.privateagent.android.R

/**
 * 설정 → 채팅 에이전트, as the desktop's 관리 → 채팅 에이전트: the commands the agent runs without asking (each can only
 * be taken off the list here; the one way onto it is the answer "always" to a command it asked about) and the kinds
 * of command allowed for this conversation.
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
