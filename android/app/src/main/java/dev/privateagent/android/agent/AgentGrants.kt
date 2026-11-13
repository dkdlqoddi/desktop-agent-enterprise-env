package dev.privateagent.android.agent

import android.content.Context
import androidx.core.content.edit
import dev.privateagent.android.util.strings
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import org.json.JSONArray
import org.json.JSONException

/**
 * What the chat's agent runs without asking, which is only what the user chose.
 *
 * [allowed]: the commands answered with "always", each kept to the letter. They are kept with the app's settings on
 * this phone ([Store]), which no backup and no device transfer carries (data_extraction_rules.xml), so a list never
 * arrives from elsewhere. The one way onto the list is the user's answer to a command the agent asked about
 * (HermesChat.decide); the settings screen only takes commands off it.
 *
 * [session]: the kinds of command answered with "this conversation" (Hermes' detector keys, as the worker tells
 * them). They are kept in memory only: they end when the conversation is cleared, and with the app.
 */
class AgentGrants(private val store: Store) {
    /** Where the commands allowed for good are kept. */
    interface Store {
        fun load(): List<String>

        fun save(commands: List<String>)
    }

    private val allowedState = MutableStateFlow(
        store.load().mapNotNull(ApprovalRules::keepable).distinct().take(ApprovalRules.MAX_ALLOWED),
    )
    val allowed: StateFlow<List<String>> = allowedState

    private val sessionState = MutableStateFlow<List<String>>(emptyList())
    val session: StateFlow<List<String>> = sessionState

    /** Keeps [command] to run unasked from now on; false when it is no command to keep, or the list is full. */
    @Synchronized
    fun allow(command: String): Boolean {
        val line = ApprovalRules.keepable(command) ?: return false
        val current = allowedState.value
        if (line in current) return true
        if (current.size >= ApprovalRules.MAX_ALLOWED) return false
        save(current + line)
        return true
    }

    /** Asks about [command] again from now on. */
    @Synchronized
    fun revoke(command: String) {
        val current = allowedState.value
        if (command in current) save(current - command)
    }

    /** A "this conversation" answer: the kinds of command it covers. */
    @Synchronized
    fun grant(keys: List<String>) {
        var current = sessionState.value
        for (key in keys.mapNotNull(ApprovalRules::sessionKey)) {
            if (key !in current && current.size < ApprovalRules.MAX_SESSION) current = current + key
        }
        sessionState.value = current
    }

    /** Asks again about every kind of command allowed for this conversation (it was cleared, or the user said so). */
    @Synchronized
    fun forgetSession() {
        sessionState.value = emptyList()
    }

    private fun save(commands: List<String>) {
        store.save(commands)
        allowedState.value = commands
    }

    /** A list that lives as long as this object: a build without the settings (tests). */
    class MemoryStore(private var commands: List<String> = emptyList()) : Store {
        override fun load(): List<String> = commands

        override fun save(commands: List<String>) {
            this.commands = commands
        }
    }

    /** The list in the app's private settings, as a JSON array. */
    class PrefsStore(context: Context) : Store {
        private val prefs = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

        override fun load(): List<String> = try {
            JSONArray(prefs.getString(ALLOWED, null) ?: "[]").strings()
        } catch (_: JSONException) {
            emptyList()
        }

        override fun save(commands: List<String>) {
            prefs.edit { putString(ALLOWED, JSONArray(commands).toString()) }
        }

        private companion object {
            const val FILE = "agent"
            const val ALLOWED = "allowed_commands"
        }
    }
}
