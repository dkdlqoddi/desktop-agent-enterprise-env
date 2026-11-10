package dev.privateagent.android.export

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class CsvTest {
    @Test
    fun plainFieldsStayAsTheyAre() {
        assertEquals("노트북 A", Csv.field("노트북 A"))
        assertEquals("https://shop.example.com/p/1?a=b", Csv.field("https://shop.example.com/p/1?a=b"))
        assertEquals("", Csv.field(null))
        assertEquals("", Csv.field(""))
    }

    @Test
    fun fieldsWithSeparatorsQuotesOrLineBreaksAreQuoted() {
        assertEquals("\"1,299,000원\"", Csv.field("1,299,000원"))
        assertEquals("\"a,b\"", Csv.field("a,b"))
        assertEquals("\"say \"\"hi\"\"\"", Csv.field("say \"hi\""))
        assertEquals("\"line\nbreak\"", Csv.field("line\nbreak"))
        assertEquals("\"carriage\rreturn\"", Csv.field("carriage\rreturn"))
        assertEquals("\" padded \"", Csv.field(" padded "))
    }

    @Test
    fun fieldsASpreadsheetWouldRunAsFormulasAreDefused() {
        assertEquals(
            "\"'=HYPERLINK(\"\"https://shop.example.com\"\")\"",
            Csv.field("=HYPERLINK(\"https://shop.example.com\")"),
        )
        assertEquals("'+1", Csv.field("+1"))
        assertEquals("'-1", Csv.field("-1"))
        assertEquals("'@SUM(A1)", Csv.field("@SUM(A1)"))
        assertEquals("\"'=1,2\"", Csv.field("=1,2"))
        assertEquals("'\tx", Csv.field("\tx"))
    }

    @Test
    fun rowsEndWithCrLf() {
        assertEquals("a,\"b,c\",,d\r\n", Csv.row(listOf("a", "b,c", null, "d")))
    }

    @Test
    fun theDocumentStartsWithAUtf8ByteOrderMark() {
        val text = Csv.document(listOf("제목", "가격"), listOf(listOf("노트북", "1,000원"), listOf("의자", "500원")))
        assertEquals("\uFEFF제목,가격\r\n노트북,\"1,000원\"\r\n의자,500원\r\n", text)
        val bytes = text.toByteArray(Charsets.UTF_8)
        assertArrayEquals(byteArrayOf(0xEF.toByte(), 0xBB.toByte(), 0xBF.toByte()), bytes.copyOfRange(0, 3))
    }
}
