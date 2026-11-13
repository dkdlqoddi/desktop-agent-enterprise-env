package dev.privateagent.android.agent

import dev.privateagent.android.util.flag
import dev.privateagent.android.util.text
import org.json.JSONObject

/**
 * What the user's answer to a command the agent paused on becomes, as the desktop's agent_chat.approve_command. The
 * answer is one of four: "once" runs it this time, "session" lets the same kind of command run for the rest of the
 * conversation, "always" lets that very command run from now on, "deny" refuses it. A scope the question did not
 * offer counts as "once": the guardian's own refusal can only be overridden for one run, and a command that is not
 * one plain line, or that holds what reads as a secret, cannot be kept to the letter.
 */
object ApprovalRules {
    val DECISIONS = setOf("once", "session", "always", "deny")

    /** How many commands are kept to run unasked, and the longest one. */
    const val MAX_ALLOWED = 200
    const val COMMAND_CHARS = 1000

    /** How many kinds of command a conversation is allowed, and the longest name of one. */
    const val MAX_SESSION = 100
    const val KEY_CHARS = 200

    /** The decision applied, and the command it was about (kept only for "always"). */
    data class Applied(val decision: String, val command: String)

    /** A command as the list of those that run unasked keeps it: one short line without control characters. */
    fun keepable(command: String): String? {
        val line = command.trim()
        if (line.isEmpty() || line.length > COMMAND_CHARS) return null
        if (line.any { it.code < 32 || it.code == 127 }) return null
        return line
    }

    /** The name of a kind of command (one of Hermes' detector keys) as a conversation's grants keep it. */
    fun sessionKey(key: String): String? = key.take(KEY_CHARS).takeIf { it.isNotEmpty() }

    /** [decision] as it applies to the question [asked] (the approval frame's payload). */
    fun settle(decision: String, asked: JSONObject): Applied {
        val command = asked.text("command").trim()
        if (decision !in DECISIONS) return Applied("deny", command)
        // A command the app's own check finds a secret in is not kept either, whatever Hermes' check made of it.
        val kept = asked.flag("allow_permanent") && keepable(command) != null && Redact.text(command) == command
        val applied = when {
            decision == "always" && !kept -> "once"
            decision == "session" && !asked.flag("allow_session") -> "once"
            else -> decision
        }
        return Applied(applied, command)
    }
}
