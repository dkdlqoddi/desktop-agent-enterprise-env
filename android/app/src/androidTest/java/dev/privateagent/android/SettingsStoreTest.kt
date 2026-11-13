package dev.privateagent.android

import android.content.Context
import androidx.core.content.edit
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import dev.privateagent.android.llm.ApiFormat
import dev.privateagent.android.security.SecretBox
import dev.privateagent.android.security.SettingsStore
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.security.KeyStore
import java.util.UUID

/**
 * The rules of the saved API Key: it stays with the server it was entered for while the address moves on that server,
 * and goes as soon as the address names another one (another host, port or scheme), so it is never sent there. The
 * test's own preferences file and Keystore key are used and removed afterwards; the app's settings are not touched.
 */
@RunWith(AndroidJUnit4::class)
class SettingsStoreTest {
    private val context = InstrumentationRegistry.getInstrumentation().targetContext
    private val file = "settings_test_" + UUID.randomUUID().toString().replace("-", "")
    private val alias = "settings_test_" + UUID.randomUUID()
    private val store = SettingsStore(context, SecretBox(alias), file)

    @After
    fun remove() {
        context.deleteSharedPreferences(file)
        KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(alias)
    }

    private fun save(endpoint: String, key: String? = null) =
        store.saveLlm(ApiFormat.OPENAI, endpoint, "test-model", key)

    @Test
    fun aKeyStaysWithItsServerAndGoesWhenTheAddressNamesAnother() {
        assertFalse(save(SERVER + "/v1/chat/completions", "sk-test-1").keyCleared)
        assertTrue(store.llm.value.hasKey)
        assertEquals("sk-test-1", store.connection()!!.apiKey)

        // Another path, a blank key: the same server keeps its key.
        assertFalse(save(SERVER + "/v2/chat/completions", "  ").keyCleared)
        assertEquals("sk-test-1", store.connection()!!.apiKey)

        // Another server, no key given: the key goes, and the screen is told.
        assertTrue(save("https://other.example.com/v1/chat/completions").keyCleared)
        assertFalse(store.llm.value.hasKey)
        assertNull(store.connection()!!.apiKey)
    }

    @Test
    fun anotherPortOrSchemeIsAnotherServer() {
        val elsewhere = listOf(
            "https://api.example.com:8443/v1/chat/completions",
            "http://api.example.com/v1/chat/completions",
        )
        for (moved in elsewhere) {
            save(SERVER + "/v1/chat/completions", "sk-test-2")
            assertTrue(moved, save(moved).keyCleared)
            assertNull(moved, store.connection()!!.apiKey)
        }
        // The default port written out is the same server.
        save(SERVER + "/v1/chat/completions", "sk-test-2")
        assertFalse(save("https://api.example.com:443/v1/chat/completions").keyCleared)
        assertEquals("sk-test-2", store.connection()!!.apiKey)
    }

    @Test
    fun aNewKeyBelongsToTheServerItWasEnteredFor() {
        save(SERVER + "/v1/chat/completions", "sk-test-3")
        assertFalse(save("https://other.example.com/v1/chat/completions", "sk-other").keyCleared)
        assertEquals("sk-other", store.connection()!!.apiKey)
        // Back to the first server without a key: the other server's key does not follow.
        assertTrue(save(SERVER + "/v1/chat/completions").keyCleared)
        assertNull(store.connection()!!.apiKey)
    }

    @Test
    fun aKeyIsGivenOnlyForTheServerItWasSavedWith() {
        save(SERVER + "/v1/chat/completions", "sk-test-4")
        // The address changed in the file behind the store's back: the saved key is not sent to it.
        context.getSharedPreferences(file, Context.MODE_PRIVATE).edit(commit = true) {
            putString("llm_endpoint", "https://other.example.com/v1/chat/completions")
        }
        val reopened = SettingsStore(context, SecretBox(alias), file)
        assertTrue(reopened.llm.value.hasKey)
        assertNull(reopened.connection()!!.apiKey)
    }

    @Test
    fun clearingTheKeyRemovesIt() {
        save(SERVER + "/v1/chat/completions", "sk-test-5")
        store.clearKey()
        assertFalse(store.llm.value.hasKey)
        assertNull(store.connection()!!.apiKey)
        // The rest of the connection stays.
        assertEquals(SERVER + "/v1/chat/completions", store.llm.value.endpoint)
    }

    private companion object {
        const val SERVER = "https://api.example.com"
    }
}
