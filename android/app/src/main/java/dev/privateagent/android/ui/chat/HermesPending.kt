package dev.privateagent.android.ui.chat

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import dev.privateagent.android.R
import dev.privateagent.android.agent.ChatAgent
import dev.privateagent.android.agent.HermesChat
import dev.privateagent.android.hermes.HermesSession
import dev.privateagent.android.util.flag
import dev.privateagent.android.util.text
import org.json.JSONObject

/** Which planner the chat uses, as the desktop's badge shows it. */
@Composable
fun EngineBadge(engine: ChatAgent.Engine) {
    val (label, description) = when (engine) {
        ChatAgent.Engine.HERMES -> R.string.engine_hermes to R.string.engine_hermes_description
        ChatAgent.Engine.DIRECT -> R.string.engine_direct to R.string.engine_direct_description
    }
    val explanation = stringResource(description)
    Surface(
        color = MaterialTheme.colorScheme.secondaryContainer,
        contentColor = MaterialTheme.colorScheme.onSecondaryContainer,
        shape = RoundedCornerShape(8.dp),
        modifier = Modifier
            .padding(end = 12.dp)
            .semantics { contentDescription = explanation },
    ) {
        Text(
            stringResource(label),
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 2.dp),
            style = MaterialTheme.typography.labelMedium,
        )
    }
}

/**
 * The answer being made: the steps Hermes took, its draft, and the approval or the questions the turn waits on (the
 * desktop's pending bubble).
 */
@Composable
fun HermesPending(
    view: HermesChat.TurnView,
    onDecide: (String, String) -> Unit,
    onAnswer: (String, JSONObject) -> Unit,
) {
    Row(Modifier.fillMaxWidth()) {
        Surface(
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier.widthIn(max = 340.dp),
        ) {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                val steps = stringResource(R.string.hermes_steps)
                if (view.steps.isNotEmpty()) {
                    Column(Modifier.semantics { contentDescription = steps }) {
                        for (step in view.steps.takeLast(6)) {
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                if (step.done) {
                                    Icon(Icons.Filled.Check, contentDescription = null, modifier = Modifier.size(14.dp))
                                } else {
                                    CircularProgressIndicator(Modifier.size(12.dp), strokeWidth = 1.5.dp)
                                }
                                Spacer(Modifier.size(6.dp))
                                Text(
                                    step.text,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    maxLines = 2,
                                )
                            }
                        }
                    }
                }
                Row(
                    verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                ) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.size(8.dp))
                    Text(
                        HermesChat.visibleDraft(view.draft).ifEmpty { stringResource(R.string.hermes_working) },
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
                val interaction = view.interaction
                when (interaction?.kind) {
                    HermesSession.InteractionKind.APPROVAL -> ApprovalPrompt(interaction, onDecide)
                    HermesSession.InteractionKind.CLARIFY -> ClarifyPrompt(interaction, onAnswer)
                    null -> Unit
                }
            }
        }
    }
}

/**
 * A command Hermes' guard escalated: the user runs it once or refuses it, and the turn goes on. Where the question
 * offers it, the same kind of command may be allowed for this conversation, or this very command for good; what each
 * means is said under the buttons. An override of the guard's own refusal is one run only.
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ApprovalPrompt(interaction: HermesChat.Interaction, onDecide: (String, String) -> Unit) {
    val payload = interaction.payload
    val session = payload.flag("allow_session")
    val permanent = payload.flag("allow_permanent")
    OutlinedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(stringResource(R.string.approval_title), style = MaterialTheme.typography.titleSmall)
            Text(
                payload.text("command"),
                style = MaterialTheme.typography.bodySmall,
                fontFamily = FontFamily.Monospace,
                maxLines = 8,
            )
            payload.text("description").takeIf { it.isNotEmpty() }?.let {
                Text(it, style = MaterialTheme.typography.bodySmall)
            }
            FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                Button(onClick = { onDecide(interaction.id, "once") }) { Text(stringResource(R.string.approval_once)) }
                if (session) {
                    OutlinedButton(onClick = { onDecide(interaction.id, "session") }) {
                        Text(stringResource(R.string.approval_session))
                    }
                }
                if (permanent) {
                    OutlinedButton(onClick = { onDecide(interaction.id, "always") }) {
                        Text(stringResource(R.string.approval_always))
                    }
                }
                TextButton(onClick = { onDecide(interaction.id, "deny") }) { Text(stringResource(R.string.approval_deny)) }
            }
            if (payload.flag("smart_denied")) {
                Text(
                    stringResource(R.string.approval_smart_denied),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
            for (help in listOfNotNull(
                R.string.approval_session_help.takeIf { session },
                R.string.approval_always_help.takeIf { permanent },
            )) {
                Text(
                    stringResource(help),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/**
 * The questions Hermes asked to go on, as the desktop asks them: a choice (or several) or an answer in the user's own
 * words each. A question with choices has a box for any other answer too, which Hermes counts on (ClarifyAnswers).
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ClarifyPrompt(interaction: HermesChat.Interaction, onAnswer: (String, JSONObject) -> Unit) {
    val questions = remember(interaction.id) { ClarifyQuestion.of(interaction.payload) }
    val chosen = remember(interaction.id) { mutableStateMapOf<String, List<String>>() }
    val written = remember(interaction.id) { mutableStateMapOf<String, String>() }
    OutlinedCard(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(stringResource(R.string.clarify_title), style = MaterialTheme.typography.titleSmall)
            for (question in questions) {
                Text(question.text, style = MaterialTheme.typography.bodyMedium)
                val own = written[question.id].orEmpty()
                if (question.choices.isNotEmpty()) {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        for (choice in question.choices) {
                            val picked = chosen[question.id].orEmpty()
                            FilterChip(
                                // A single answer in the user's own words takes the place of a choice.
                                selected = choice in picked && (question.multiple || own.isBlank()),
                                onClick = {
                                    if (!question.multiple) written[question.id] = ""
                                    chosen[question.id] = when {
                                        !question.multiple -> listOf(choice)
                                        choice in picked -> picked - choice
                                        else -> picked + choice
                                    }
                                },
                                label = {
                                    Text(
                                        ClarifyAnswers.recommended(choice)
                                            ?.let { stringResource(R.string.clarify_recommended, it) } ?: choice,
                                    )
                                },
                            )
                        }
                    }
                }
                val hint = if (question.choices.isEmpty()) R.string.clarify_answer_hint else R.string.clarify_other_hint
                OutlinedTextField(
                    value = own,
                    onValueChange = { written[question.id] = it.take(ClarifyAnswers.MAX_WRITTEN) },
                    placeholder = { Text(stringResource(hint)) },
                    modifier = Modifier.fillMaxWidth(),
                    maxLines = 3,
                )
            }
            Button(
                onClick = { onAnswer(interaction.id, ClarifyAnswers.json(questions, chosen, written)) },
                modifier = Modifier.align(Alignment.End),
            ) {
                Text(stringResource(R.string.clarify_send))
            }
        }
    }
}
