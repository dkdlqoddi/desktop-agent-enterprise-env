package dev.privateagent.android.agent

import android.content.Context
import dev.privateagent.android.R
import dev.privateagent.android.data.ActionState
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.data.ItemFilter
import dev.privateagent.android.data.RecordStore
import dev.privateagent.android.data.StoreException
import dev.privateagent.android.llm.Endpoint
import dev.privateagent.android.schedule.Notifications
import dev.privateagent.android.schedule.ScheduleManager
import dev.privateagent.android.ui.Navigator
import dev.privateagent.android.ui.Tab
import dev.privateagent.android.web.BrowserController
import dev.privateagent.android.web.CaptureOutcome
import dev.privateagent.android.web.ItemKind
import dev.privateagent.android.web.Sites
import kotlinx.coroutines.CancellationException
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter

/**
 * The app's own actions, run once their arguments were checked ([ToolValidator]): when the user approves a card (the
 * chat without Hermes), or when the bundled Hermes agent calls one (the Hermes chat). Every page is opened in the
 * visible browser and only read; nothing on it is clicked.
 */
class AppActions(
    private val context: Context,
    private val records: RecordStore,
    private val browser: BrowserController,
    private val scheduler: ScheduleManager,
    private val notifications: Notifications,
    private val navigator: Navigator,
    private val sites: () -> Sites,
    private val texts: Texts,
) {
    /** What an action did: the card's end state and result line, its capture, and what the model is told. */
    class Outcome(val state: ActionState, val line: String, val captureId: Long? = null, val result: JSONObject) {
        val done get() = state == ActionState.DONE
    }

    /** The records list_records asked for: a line for the card, a short JSON for the LLM, and a plain summary. */
    class Found(val line: String, val forLlm: String, val plain: String, val items: JSONArray)

    suspend fun run(action: AgentAction, source: CaptureSource = CaptureSource.CHAT): Outcome = try {
        when (action) {
            is AgentAction.WebCapture -> {
                val site = sites().byKey(action.site)!!
                navigator.show(Tab.BROWSER)
                captured(captureSafely { browser.capture(site, action.query, source, sites().searchUrl(site, action.query)) })
            }
            AgentAction.CapturePage -> {
                navigator.show(Tab.BROWSER)
                captured(captureSafely { browser.captureCurrentPage(source) })
            }
            is AgentAction.OpenSite -> {
                navigator.show(Tab.BROWSER)
                when (val failure = browser.open(action.url)) {
                    null -> {
                        val name = action.site?.let { sites().byKey(it)?.name } ?: Endpoint.host(action.url)
                        val line = context.getString(R.string.result_opened, name)
                        Outcome(ActionState.DONE, line, result = JSONObject().put("text", line).put("url", action.url))
                    }
                    else -> failed(texts.failure(failure))
                }
            }
            is AgentAction.ScheduleCapture -> {
                val row = scheduler.create(action.site, action.query, action.time, action.days)
                val next = row.nextRunAt?.let { texts.time(it) }.orEmpty()
                var line = context.getString(R.string.result_schedule_created, next)
                if (!notifications.canPost()) line += " " + context.getString(R.string.result_schedule_no_notifications)
                Outcome(ActionState.DONE, line, result = JSONObject().put("text", line).put("routine_id", row.id))
            }
            is AgentAction.ListRecords -> {
                val found = listRecords(action)
                Outcome(ActionState.DONE, found.line, result = JSONObject().put("text", found.plain).put("records", found.items))
            }
        }
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (failure: StoreException) {
        failed(texts.storage(failure.problem))
    } catch (_: Exception) {
        failed(context.getString(R.string.llm_error_unexpected))
    }

    suspend fun listRecords(action: AgentAction.ListRecords): Found {
        val filter = ItemFilter(site = action.site, kind = action.kind, search = action.query.orEmpty())
        val counts = records.counts(filter)
        val found = records.items(filter, action.limit)
        val zone = ZoneId.systemDefault()
        val items = JSONArray(
            found.map { item ->
                JSONObject()
                    .put("site", texts.siteName(item.site))
                    .put("query", item.query)
                    .put("kind", item.kind.key)
                    .put("title", item.title.take(80))
                    .put("price", item.price)
                    .put("captured_at", STAMP.format(Instant.ofEpochMilli(item.capturedAt).atZone(zone)))
                    .apply { if (item.clickedAt != null) put("clicked", true) }
            },
        )
        val forLlm = JSONObject()
            .put("now", STAMP.format(ZonedDateTime.now(zone)))
            .put("total", counts.total)
            .put("counts", JSONObject().put("ad", counts.ad).put("product", counts.product).put("result", counts.result))
            .put("items", items)
            .toString()
        val line = if (counts.total == 0) {
            context.getString(R.string.result_list_none)
        } else {
            context.getString(R.string.result_list, counts.total)
        }
        val plain = buildString {
            append(line)
            if (counts.total > 0) {
                append(" (").append(texts.countList(counts)).append(")")
                for (item in found.take(PLAIN_ITEMS)) {
                    append("\n• ").append(texts.siteName(item.site)).append(" · ").append(texts.kind(item.kind))
                        .append(" · ").append(item.title.take(60))
                    if (item.price.isNotEmpty()) append(" · ").append(item.price)
                }
            }
        }
        return Found(line, forLlm, plain, items)
    }

    private suspend fun captureSafely(block: suspend () -> CaptureOutcome): CaptureOutcome? = try {
        block()
    } catch (cancelled: CancellationException) {
        throw cancelled
    } catch (failure: StoreException) {
        CaptureOutcome.NotSaved(failure.problem)
    } catch (_: Exception) {
        null
    }

    /** A capture's card line, and for the model the titles of the ads and products it recorded. */
    private suspend fun captured(outcome: CaptureOutcome?): Outcome = when (outcome) {
        is CaptureOutcome.Saved -> {
            val line = texts.outcome(outcome)
            val saved = records.items(ItemFilter(captureId = outcome.captureId), CAPTURE_TITLES)
            fun titles(kind: ItemKind) = JSONArray(saved.filter { it.kind == kind }.map { it.title.take(80) })
            Outcome(
                ActionState.DONE,
                line,
                outcome.captureId,
                JSONObject().put("text", line).put("capture_id", outcome.captureId)
                    .put("ads", titles(ItemKind.AD)).put("products", titles(ItemKind.PRODUCT)),
            )
        }
        null -> failed(context.getString(R.string.llm_error_unexpected))
        else -> failed(texts.outcome(outcome))
    }

    private fun failed(line: String) = Outcome(ActionState.FAILED, line, result = JSONObject().put("text", line))

    private companion object {
        const val PLAIN_ITEMS = 5
        const val CAPTURE_TITLES = 40
        val STAMP: DateTimeFormatter = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")
    }
}
