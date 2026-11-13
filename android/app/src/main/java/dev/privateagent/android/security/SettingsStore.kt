package dev.privateagent.android.security

import android.content.Context
import androidx.core.content.edit
import dev.privateagent.android.llm.ApiFormat
import dev.privateagent.android.llm.Endpoint
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.security.SecureRandom
import java.util.Base64

/** The LLM connection as the screen shows it: never the key, only whether one is saved. */
data class LlmSettings(
    val format: ApiFormat = ApiFormat.OPENAI,
    val endpoint: String = "",
    val model: String = "",
    val hasKey: Boolean = false,
) {
    val configured: Boolean get() = endpoint.isNotEmpty() && model.isNotEmpty()
}

/** The connection a request is sent with, key included; it lives only for the request. */
class LlmConnection(val format: ApiFormat, val endpoint: String, val model: String, val apiKey: String?)

/**
 * The app's settings in private SharedPreferences. The API Key is stored sealed by [SecretBox] (Android Keystore,
 * AES-GCM) together with the server it was entered for, and is sent only to that server. [file] is the preferences
 * file: the app's own, or a test's.
 */
class SettingsStore(context: Context, private val box: SecretBox = SecretBox(), file: String = FILE) {
    private val prefs = context.applicationContext.getSharedPreferences(file, Context.MODE_PRIVATE)
    private val state = MutableStateFlow(read())
    val llm: StateFlow<LlmSettings> = state

    private val recordClicksState = MutableStateFlow(prefs.getBoolean(RECORD_CLICKS, true))
    val recordClicks: StateFlow<Boolean> = recordClicksState

    data class SaveResult(val keyCleared: Boolean)

    /**
     * Saves the connection; [endpoint] is already normalized (Endpoint.normalize). A blank [newKey] keeps the saved
     * key, unless the endpoint moved to another server: that key is then removed, so it never goes to the new one.
     */
    @Synchronized
    fun saveLlm(format: ApiFormat, endpoint: String, model: String, newKey: String?): SaveResult {
        val origin = Endpoint.origin(endpoint)
        val hadKey = prefs.contains(KEY)
        var cleared = false
        prefs.edit {
            putString(FORMAT, format.key)
            putString(ENDPOINT, endpoint)
            putString(MODEL, model.trim())
            when {
                !newKey.isNullOrBlank() -> {
                    putString(KEY, box.seal(newKey.trim(), KEY_PURPOSE))
                    putString(KEY_ORIGIN, origin)
                }
                hadKey && prefs.getString(KEY_ORIGIN, null) != origin -> {
                    remove(KEY)
                    remove(KEY_ORIGIN)
                    cleared = true
                }
            }
        }
        state.value = read()
        return SaveResult(cleared)
    }

    @Synchronized
    fun clearKey() {
        prefs.edit {
            remove(KEY)
            remove(KEY_ORIGIN)
        }
        state.value = read()
    }

    /** The connection with its key, for one request; null until the endpoint and model are set. */
    fun connection(): LlmConnection? {
        val settings = state.value
        if (!settings.configured) return null
        val sealed = prefs.getString(KEY, null)
        val key = if (sealed != null && prefs.getString(KEY_ORIGIN, null) == Endpoint.origin(settings.endpoint)) {
            box.open(sealed, KEY_PURPOSE)
        } else {
            null
        }
        return LlmConnection(settings.format, settings.endpoint, settings.model, key)
    }

    fun setRecordClicks(enabled: Boolean) {
        prefs.edit { putBoolean(RECORD_CLICKS, enabled) }
        recordClicksState.value = enabled
    }

    /**
     * A random value of this installation that the app's own notifications carry: the activity runs a scheduled
     * capture only for an intent that has it, not for one another app made up.
     */
    @get:Synchronized
    val launchSecret: String
        get() = prefs.getString(LAUNCH_SECRET, null) ?: run {
            val bytes = ByteArray(24).also { SecureRandom().nextBytes(it) }
            val secret = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
            prefs.edit { putString(LAUNCH_SECRET, secret) }
            secret
        }

    private fun read() = LlmSettings(
        format = ApiFormat.of(prefs.getString(FORMAT, null)),
        endpoint = prefs.getString(ENDPOINT, null).orEmpty(),
        model = prefs.getString(MODEL, null).orEmpty(),
        hasKey = prefs.contains(KEY),
    )

    private companion object {
        const val FILE = "settings"
        const val FORMAT = "llm_format"
        const val ENDPOINT = "llm_endpoint"
        const val MODEL = "llm_model"
        const val KEY = "llm_api_key_sealed"
        const val KEY_ORIGIN = "llm_api_key_origin"
        const val KEY_PURPOSE = "llm_api_key"
        const val RECORD_CLICKS = "record_clicks"
        const val LAUNCH_SECRET = "launch_secret"
    }
}
