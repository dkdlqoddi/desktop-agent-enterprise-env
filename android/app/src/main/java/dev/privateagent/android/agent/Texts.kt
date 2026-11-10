package dev.privateagent.android.agent

import android.content.Context
import dev.privateagent.android.R
import dev.privateagent.android.data.CaptureSource
import dev.privateagent.android.llm.LlmException
import dev.privateagent.android.web.BrowserController
import dev.privateagent.android.web.BrowserFailure
import dev.privateagent.android.web.CaptureCounts
import dev.privateagent.android.web.CaptureOutcome
import dev.privateagent.android.web.ItemKind
import dev.privateagent.android.web.Sites
import dev.privateagent.android.util.text
import org.json.JSONObject
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale

/** The words the app says, from the string resources: results, problems, errors, labels and times. */
class Texts(private val context: Context, private val sites: () -> Sites) {
    private fun s(id: Int, vararg args: Any): String = context.getString(id, *args)

    fun siteName(key: String?): String = when (key) {
        null, "" -> s(R.string.site_generic)
        BrowserController.GENERIC -> s(R.string.site_generic)
        else -> sites().byKey(key)?.name ?: key
    }

    /** "광고 5건, 상품 32건": the kinds that have any, or "". */
    fun countList(counts: CaptureCounts): String = buildList {
        if (counts.ad > 0) add(s(R.string.count_ad, counts.ad))
        if (counts.product > 0) add(s(R.string.count_product, counts.product))
        if (counts.result > 0) add(s(R.string.count_result, counts.result))
    }.joinToString(", ")

    /** The result line of a capture: "광고 5건, 상품 32건을 기록했습니다." */
    fun counts(counts: CaptureCounts): String {
        val list = countList(counts)
        return if (list.isEmpty()) s(R.string.result_nothing) else s(R.string.result_recorded, list)
    }

    fun outcome(outcome: CaptureOutcome): String = when (outcome) {
        is CaptureOutcome.Saved -> counts(outcome.counts)
        CaptureOutcome.Blocked -> s(R.string.result_blocked)
        is CaptureOutcome.Failed -> failure(outcome.failure)
    }

    fun failure(failure: BrowserFailure): String = when (failure) {
        BrowserFailure.NO_BROWSER -> s(R.string.result_no_browser)
        BrowserFailure.LOAD_FAILED -> s(R.string.result_load_failed)
        BrowserFailure.TIMEOUT -> s(R.string.result_timeout)
        BrowserFailure.MOVED -> s(R.string.result_moved)
        BrowserFailure.SCRIPT -> s(R.string.result_script)
    }

    fun problem(problem: ToolProblem): String = when (problem) {
        is ToolProblem.UnknownTool -> s(R.string.problem_unknown_tool, problem.name)
        is ToolProblem.UnknownSite -> s(R.string.problem_unknown_site, problem.value, siteList())
        ToolProblem.MissingSite -> s(R.string.problem_missing_site, siteList())
        ToolProblem.MissingQuery -> s(R.string.problem_missing_query)
        ToolProblem.QueryTooLong -> s(R.string.problem_query_too_long, AgentTools.MAX_QUERY)
        is ToolProblem.BadUrl -> s(R.string.problem_bad_url, problem.value)
        is ToolProblem.InsecureUrl -> s(R.string.problem_insecure_url, problem.value)
        ToolProblem.MissingTarget -> s(R.string.problem_missing_target)
        is ToolProblem.BadTime -> s(R.string.problem_bad_time, problem.value)
        is ToolProblem.BadDays -> s(R.string.problem_bad_days, problem.value)
        is ToolProblem.BadKind -> s(R.string.problem_bad_kind, problem.value)
        ToolProblem.BadArguments -> s(R.string.problem_bad_arguments)
    }

    fun llmError(error: LlmException): String {
        val status = error.status ?: 0
        return when (error.kind) {
            LlmException.Kind.AUTH -> s(R.string.llm_error_auth, status)
            LlmException.Kind.REQUEST -> s(R.string.llm_error_request, status)
            LlmException.Kind.RATE_LIMIT -> s(R.string.llm_error_rate)
            LlmException.Kind.SERVER -> s(R.string.llm_error_server, status)
            LlmException.Kind.STATUS -> s(R.string.llm_error_status, status)
            LlmException.Kind.REDIRECT -> s(R.string.llm_error_redirect, status)
            LlmException.Kind.TIMEOUT -> s(R.string.llm_error_timeout)
            LlmException.Kind.NETWORK -> s(R.string.llm_error_network)
            LlmException.Kind.TLS -> s(R.string.llm_error_tls)
            LlmException.Kind.INSECURE -> s(R.string.llm_error_insecure)
            LlmException.Kind.FORMAT -> s(R.string.llm_error_format)
            LlmException.Kind.REFUSED -> s(R.string.llm_error_refused)
        }
    }

    fun days(days: ScheduleDays): String = when (days) {
        ScheduleDays.DAILY -> s(R.string.days_daily)
        ScheduleDays.WEEKDAYS -> s(R.string.days_weekdays)
    }

    fun kind(kind: ItemKind): String = when (kind) {
        ItemKind.AD -> s(R.string.kind_ad)
        ItemKind.PRODUCT -> s(R.string.kind_product)
        ItemKind.RESULT -> s(R.string.kind_result)
    }

    fun filter(kind: RecordKindFilter): String = when (kind) {
        RecordKindFilter.AD -> s(R.string.kind_ad)
        RecordKindFilter.PRODUCT -> s(R.string.kind_product)
        RecordKindFilter.RESULT -> s(R.string.kind_result)
        RecordKindFilter.CLICKED -> s(R.string.kind_clicked)
    }

    fun source(source: CaptureSource): String = when (source) {
        CaptureSource.CHAT -> s(R.string.source_chat)
        CaptureSource.SCHEDULE -> s(R.string.source_schedule)
        CaptureSource.MANUAL -> s(R.string.source_manual)
        CaptureSource.CLICK -> s(R.string.source_click)
    }

    /** "오늘 09:05", or "11월 10일 09:05" for another day. */
    fun time(millis: Long): String {
        val zone = ZoneId.systemDefault()
        val moment = Instant.ofEpochMilli(millis).atZone(zone)
        val clock = moment.format(CLOCK)
        return if (moment.toLocalDate() == LocalDate.now(zone)) {
            s(R.string.time_today, clock)
        } else {
            moment.format(DAY) + " " + clock
        }
    }

    /** What an action card does, in a sentence, from its tool and saved arguments. */
    fun describe(tool: String?, args: JSONObject): String {
        val site = args.text("site")
        val query = args.text("query")
        return when (tool) {
            AgentTools.WEB_CAPTURE -> {
                val sentence = s(R.string.describe_web_capture, siteName(site), query)
                if (args.has("schedule_id")) s(R.string.describe_scheduled_run, sentence) else sentence
            }
            AgentTools.OPEN_SITE -> {
                val url = args.text("url")
                val known = sites().byKey(site)
                if (known != null && known.home == url) {
                    s(R.string.describe_open_site, known.name)
                } else {
                    s(R.string.describe_open_url, url)
                }
            }
            AgentTools.SCHEDULE_CAPTURE -> {
                val days = ScheduleDays.of(args.text("days")) ?: ScheduleDays.DAILY
                s(R.string.describe_schedule, days(days), args.text("time"), siteName(site), query)
            }
            AgentTools.LIST_RECORDS -> s(R.string.describe_list_records, filterSummary(args))
            else -> s(R.string.describe_unknown)
        }
    }

    fun filterSummary(args: JSONObject): String {
        val parts = buildList {
            args.text("site").takeIf { it.isNotEmpty() }?.let { add(siteName(it)) }
            RecordKindFilter.of(args.text("kind"))?.let { add(filter(it)) }
            args.text("query").takeIf { it.isNotEmpty() }?.let { add("'$it'") }
        }
        return if (parts.isEmpty()) s(R.string.filter_everything) else parts.joinToString(" · ")
    }

    fun toolTitle(tool: String?): String = when (tool) {
        AgentTools.WEB_CAPTURE -> s(R.string.action_web_capture)
        AgentTools.OPEN_SITE -> s(R.string.action_open_site)
        AgentTools.SCHEDULE_CAPTURE -> s(R.string.action_schedule)
        AgentTools.LIST_RECORDS -> s(R.string.action_list_records)
        else -> s(R.string.describe_unknown)
    }

    private fun siteList(): String = sites().all.joinToString(", ") { it.name }

    private companion object {
        val CLOCK: DateTimeFormatter = DateTimeFormatter.ofPattern("HH:mm", Locale.KOREAN)
        val DAY: DateTimeFormatter = DateTimeFormatter.ofPattern("M월 d일", Locale.KOREAN)
    }
}
