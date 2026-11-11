package dev.privateagent.android

import android.util.Base64
import androidx.test.ext.junit.runners.AndroidJUnit4
import dev.privateagent.android.security.SecretBox
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Test
import org.junit.runner.RunWith
import java.security.KeyStore
import java.util.UUID

/** The Android Keystore part of the API Key storage, which only a device has. */
@RunWith(AndroidJUnit4::class)
class SecretBoxTest {
    private val alias = "test_" + UUID.randomUUID()
    private val box = SecretBox(alias)

    @After
    fun removeKey() {
        KeyStore.getInstance("AndroidKeyStore").apply { load(null) }.deleteEntry(alias)
    }

    @Test
    fun aSealedSecretOpensOnlyForItsPurpose() {
        val sealed = box.seal("sk-test-1234567890", "llm_api_key")
        assertFalse(sealed.contains("sk-test"))
        assertEquals("sk-test-1234567890", box.open(sealed, "llm_api_key"))
        assertNull(box.open(sealed, "another_purpose"))
    }

    @Test
    fun everySealIsDifferentAndAChangedOneDoesNotOpen() {
        val first = box.seal("same", "p")
        val second = box.seal("same", "p")
        assertNotEquals(first, second)
        val bytes = Base64.decode(first.removePrefix("v1:"), Base64.NO_WRAP)
        bytes[bytes.size - 1] = (bytes[bytes.size - 1].toInt() xor 1).toByte()
        assertNull(box.open("v1:" + Base64.encodeToString(bytes, Base64.NO_WRAP), "p"))
        assertNull(box.open("garbage", "p"))
    }

    @Test
    fun withoutItsKeyNothingOpens() {
        val sealed = box.seal("secret", "p")
        removeKey()
        assertNull(box.open(sealed, "p"))
    }
}
