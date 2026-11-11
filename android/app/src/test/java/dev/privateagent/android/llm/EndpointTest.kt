package dev.privateagent.android.llm

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class EndpointTest {
    private fun ok(input: String) = (Endpoint.normalize(input) as Endpoint.Check.Ok).url

    private fun rejected(input: String) = (Endpoint.normalize(input) as Endpoint.Check.Rejected).problem

    @Test
    fun anAddressWithoutASchemeIsSavedAsHttps() {
        assertEquals("https://api.example.com/v1/chat/completions", ok("api.example.com/v1/chat/completions"))
        assertEquals("https://localhost:8443/v1/messages", ok("localhost:8443/v1/messages"))
    }

    @Test
    fun anHttpsAddressStaysAsTyped() {
        assertEquals("https://api.example.com/v1/messages", ok("https://api.example.com/v1/messages"))
        assertEquals("https://api.example.com/v1?api-version=2", ok("  https://api.example.com/v1?api-version=2  "))
        assertEquals("https://api.example.com/v1", ok("HTTPS://api.example.com/v1"))
    }

    @Test
    fun aPlainHttpAddressIsRefusedNeverDowngraded() {
        assertEquals(Endpoint.Problem.INSECURE_HTTP, rejected("http://api.example.com/v1/chat/completions"))
        assertEquals(Endpoint.Problem.INSECURE_HTTP, rejected("HTTP://127.0.0.1:11434/v1/chat/completions"))
    }

    @Test
    fun otherAddressesAreRefused() {
        assertEquals(Endpoint.Problem.EMPTY, rejected("   "))
        assertEquals(Endpoint.Problem.UNSUPPORTED_SCHEME, rejected("ftp://api.example.com/v1"))
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
    fun theHostShownNeverIncludesThePathOrQuery() {
        assertEquals("api.example.com", Endpoint.host("https://api.example.com/v1?key=secret"))
    }
}
