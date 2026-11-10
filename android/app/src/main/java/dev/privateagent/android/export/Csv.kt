package dev.privateagent.android.export

/**
 * CSV as RFC 4180 writes it, for spreadsheet apps: UTF-8 with a byte order mark (so they read Korean text right), CRLF
 * line breaks, and a field quoted when it holds a comma, a quote, a line break or a space at either end. The records
 * hold text from web pages, so a field that a spreadsheet would run as a formula (=, +, -, @, tab or CR first) starts
 * with an apostrophe instead.
 */
object Csv {
    const val BOM = "\uFEFF"
    private const val LINE_BREAK = "\r\n"
    private val FORMULA_START = charArrayOf('=', '+', '-', '@', '\t', '\r')

    fun field(value: String?): String {
        var text = value.orEmpty()
        if (text.isNotEmpty() && text[0] in FORMULA_START) text = "'$text"
        val quoted = text.any { it == ',' || it == '"' || it == '\n' || it == '\r' } ||
            text.startsWith(' ') || text.endsWith(' ')
        return if (quoted) "\"" + text.replace("\"", "\"\"") + "\"" else text
    }

    fun row(fields: List<String?>): String = fields.joinToString(",") { field(it) } + LINE_BREAK

    fun document(header: List<String>, rows: List<List<String?>>): String = buildString {
        append(BOM)
        append(row(header))
        for (fields in rows) append(row(fields))
    }
}
