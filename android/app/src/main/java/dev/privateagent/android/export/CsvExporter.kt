package dev.privateagent.android.export

import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import dev.privateagent.android.R
import dev.privateagent.android.agent.Texts
import dev.privateagent.android.data.ItemRow
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** Writes records to a CSV file in the app's cache and shares it through the system share sheet. */
object CsvExporter {
    private const val DIR = "exports"
    private val STAMP: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")
    private val FILE_STAMP: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss")

    fun rows(items: List<ItemRow>, texts: Texts): List<List<String?>> {
        val zone = ZoneId.systemDefault()
        return items.map { row(it, texts, zone) }
    }

    private fun row(item: ItemRow, texts: Texts, zone: ZoneId): List<String?> {
        fun time(millis: Long?) = millis?.let { STAMP.format(Instant.ofEpochMilli(it).atZone(zone)) }
        return listOf(
            time(item.capturedAt),
            texts.siteName(item.site),
            item.query,
            texts.kind(item.kind),
            item.position.toString(),
            item.title,
            item.price,
            item.displayUrl,
            item.linkUrl,
            item.imageUrl,
            time(item.clickedAt),
            item.clickedUrl,
            item.landingUrl,
            item.landingTitle,
            item.pageUrl,
            item.text,
        )
    }

    /** The CSV file of [items] (earlier exports are removed); returns its content URI. */
    suspend fun export(context: Context, items: List<ItemRow>, texts: Texts): Uri =
        write(context, texts) { each ->
            items.forEach(each)
            items.size
        }.first

    /**
     * The CSV file of every item [items] hands over, written as they come: an export is of all the records the filter
     * finds, however many (earlier exports are removed). Returns its content URI, or null when there was no item.
     */
    suspend fun exportAll(context: Context, texts: Texts, items: suspend ((ItemRow) -> Unit) -> Int): Uri? {
        val (uri, count) = write(context, texts, items)
        return uri.takeIf { count > 0 }
    }

    private suspend fun write(
        context: Context,
        texts: Texts,
        items: suspend ((ItemRow) -> Unit) -> Int,
    ): Pair<Uri, Int> = withContext(Dispatchers.IO) {
        val dir = File(context.cacheDir, DIR).apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() }
        val zone = ZoneId.systemDefault()
        val name = "records-" + FILE_STAMP.format(Instant.now().atZone(zone)) + ".csv"
        val file = File(dir, name)
        val header = context.resources.getStringArray(R.array.csv_header).toList()
        val count = file.bufferedWriter(Charsets.UTF_8).use { out ->
            out.write(Csv.BOM)
            out.write(Csv.row(header))
            items { item -> out.write(Csv.row(row(item, texts, zone))) }
        }
        FileProvider.getUriForFile(context, context.packageName + ".files", file) to count
    }

    fun share(context: Context, uri: Uri) {
        val send = Intent(Intent.ACTION_SEND).apply {
            type = "text/csv"
            putExtra(Intent.EXTRA_STREAM, uri)
            clipData = ClipData.newRawUri("", uri)
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        }
        context.startActivity(
            Intent.createChooser(send, context.getString(R.string.records_export_chooser))
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),
        )
    }
}
