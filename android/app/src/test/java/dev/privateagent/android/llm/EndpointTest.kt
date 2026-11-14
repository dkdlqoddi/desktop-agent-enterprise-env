package dev.privateagent.android.llm

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class EndpointTest {
    private fun ok(input: String) = (Endpoint.normalize(input) as Endpoint.Check.Ok).url

    private fun rejected(input: String) = (Endpoint.normalize(input) as Endpoint.Check.Rejected).problem

    @Test
    fun anAddressWithoutASchemeIsSavedAsHttps() {
        assertEquals("https://api.example.com/v1/chat/completions", ok("api.example.com/v1/chat/completions"))
        assertEquals("https://localhost:8443/v1/messages", ok("localhost:8443/v1/messages"))
        // A server on the same Wi-Fi is no exception: without http:// typed, it is https.
        assertEquals("https://192.0.2.10:11434/v1/chat/completions", ok("192.0.2.10:11434/v1/chat/completions"))
        assertEquals("https://llm.test/v1?next=http://elsewhere.test", ok("llm.test/v1?next=http://elsewhere.test"))
    }

    @Test
    fun anHttpsAddressStaysAsTyped() {
        assertEquals("https://api.example.com/v1/messages", ok("https://api.example.com/v1/messages"))
        assertEquals("https://api.example.com/v1?api-version=2", ok("  https://api.example.com/v1?api-version=2  "))
        assertEquals("https://api.example.com/v1", ok("HTTPS://api.example.com/v1"))
    }

    @Test
    fun aPlainHttpAddressIsUsedOnlyWhenTyped() {
        assertEquals("http://192.0.2.10:11434/v1/chat/completions", ok("http://192.0.2.10:11434/v1/chat/completions"))
        assertEquals("http://127.0.0.1:11434/v1/chat/completions", ok("HTTP://127.0.0.1:11434/v1/chat/completions"))
        assertEquals("http://llm.test/v1/messages", ok("  Http://llm.test/v1/messages "))
    }

    @Test
    fun anHttpAddressMeetsTheSameRules() {
        assertEquals(Endpoint.Problem.CREDENTIALS, rejected("http://user:secret@llm.test/v1"))
        assertEquals(Endpoint.Problem.FRAGMENT, rejected("http://llm.test/v1#part"))
        assertEquals(Endpoint.Problem.INVALID, rejected("http:///v1"))
        assertEquals(Endpoint.Problem.INVALID, rejected("http://llm.test/v 1"))
    }

    @Test
    fun otherAddressesAreRefused() {
        assertEquals(Endpoint.Problem.EMPTY, rejected("   "))
        assertEquals(Endpoint.Problem.UNSUPPORTED_SCHEME, rejected("ftp://api.example.com/v1"))
        assertEquals(Endpoint.Problem.UNSUPPORTED_SCHEME, rejected("ws://llm.test/v1"))
        assertEquals(Endpoint.Problem.UNSUPPORTED_SCHEME, rejected("file:///data/local/tmp/x"))
        assertEquals(Endpoint.Problem.CREDENTIALS, rejected("https://user:secret@api.example.com/v1"))
        assertEquals(Endpoint.Problem.FRAGMENT, rejected("https://api.example.com/v1#part"))
        assertEquals(Endpoint.Problem.INVALID, rejected("https://api.example.com/v 1"))
        assertEquals(Endpoint.Problem.INVALID, rejected("https:///v1"))
    }

    @Test
    fun theKeyBelongsToTheServerNotThePath() {
        assertEquals("https://api.example.com:443", Endpoint.origin("https://API.example.com/v1/chat?x=1"))
        assertEquals("https://api.example.com:8443", Endpoint.origin("https://api.example.com:8443/v1"))
        assertEquals(Endpoint.origin("https://api.example.com/v1/a"), Endpoint.origin("https://api.example.com/v2/b"))
        assertNull(Endpoint.origin("not a url"))
    }

    @Test
    fun aKeyEnteredForHttpsIsNotOneForHttp() {
        assertEquals("http://llm.test:80", Endpoint.origin("http://LLM.test/v1/chat/completions"))
        assertEquals("http://llm.test:11434", Endpoint.origin("http://llm.test:11434/v1"))
        // The same host and even the same port over the other scheme is another server.
        assertNotEquals(Endpoint.origin("https://llm.test/v1"), Endpoint.origin("http://llm.test/v1"))
        assertNotEquals(Endpoint.origin("https://llm.test:8080/v1"), Endpoint.origin("http://llm.test:8080/v1"))
        assertNotEquals(Endpoint.origin("https://llm.test/v1"), Endpoint.origin("http://llm.test:443/v1"))
    }

    @Test
    fun onlyAnHttpAddressOffThisPhoneIsUnencryptedOnTheNetwork() {
        assertTrue(Endpoint.unencrypted("http://192.0.2.10:11434/v1/chat/completions"))
        assertTrue(Endpoint.unencrypted("http://llm.test/v1"))
        assertTrue(Endpoint.unencrypted("http://localhost.example.com/v1"))
        // On the phone itself, nothing crosses the network.
        assertFalse(Endpoint.unencrypted("http://localhost:11434/v1"))
        assertFalse(Endpoint.unencrypted("http://LOCALHOST/v1"))
        assertFalse(Endpoint.unencrypted("http://llm.localhost/v1"))
        assertFalse(Endpoint.unencrypted("http://127.0.0.1:8080/v1"))
        assertFalse(Endpoint.unencrypted("http://127.1.2.3/v1"))
        assertFalse(Endpoint.unencrypted("http://[::1]:8080/v1"))
        assertFalse(Endpoint.unencrypted("https://192.0.2.10/v1"))
        assertFalse(Endpoint.unencrypted("not a url"))
    }

    @Test
    fun theHostShownNeverIncludesThePathOrQuery() {
        assertEquals("api.example.com", Endpoint.host("https://api.example.com/v1?key=secret"))
    }
}
