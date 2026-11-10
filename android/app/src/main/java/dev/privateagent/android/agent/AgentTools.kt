package dev.privateagent.android.agent

import dev.privateagent.android.llm.ToolDefinition
import dev.privateagent.android.util.cleanText
import dev.privateagent.android.util.whole
import dev.privateagent.android.web.BrowserUrls
import dev.privateagent.android.web.Sites
import org.json.JSONArray
import org.json.JSONObject

/** Which records list_records and the records screen show. */
enum class RecordKindFilter(val key: String) {
    AD("ad"),
    PRODUCT("product"),
    RESULT("result"),

    /** Ads the user clicked. */
    CLICKED("clicked"),
    ;

    companion object {
        fun of(key: String?): RecordKindFilter? = entries.firstOrNull { it.key == key }
    }
}

enum class ScheduleDays(val key: String) {
    DAILY("daily"),
    WEEKDAYS("weekdays"),
    ;

    companion object {
        fun of(key: String?): ScheduleDays? = entries.firstOrNull { it.key == key }
    }
}

/** An action the LLM asked for, after its arguments were checked. */
sealed interface AgentAction {
    val tool: String

    /** The checked arguments, as saved with the action's chat message. */
    fun toJson(): JSONObject

    data class WebCapture(val site: String, val query: String) : AgentAction {
        override val tool get() = AgentTools.WEB_CAPTURE
        override fun toJson(): JSONObject = JSONObject().put("site", site).put("query", query)
    }

    /** [site] is the known site [url] belongs to, if any. */
    data class OpenSite(val site: String?, val url: String) : AgentAction {
        override val tool get() = AgentTools.OPEN_SITE
        override fun toJson(): JSONObject = JSONObject().put("url", url).apply { if (site != null) put("site", site) }
    }

    data class ListRecords(
        val site: String? = null,
        val query: String? = null,
        val kind: RecordKindFilter? = null,
        val limit: Int = AgentTools.DEFAULT_LIMIT,
    ) : AgentAction {
        override val tool get() = AgentTools.LIST_RECORDS
        override fun toJson(): JSONObject = JSONObject().put("limit", limit).apply {
            if (site != null) put("site", site)
            if (query != null) put("query", query)
            if (kind != null) put("kind", kind.key)
        }
    }

    /** [time] is HH:MM, 24-hour. */
    data class ScheduleCapture(val site: String, val query: String, val time: String, val days: ScheduleDays) :
        AgentAction {
        override val tool get() = AgentTools.SCHEDULE_CAPTURE
        override fun toJson(): JSONObject =
            JSONObject().put("site", site).put("query", query).put("time", time).put("days", days.key)
    }
}

/** Why an action was not taken; the chat asks the user with it. */
sealed interface ToolProblem {
    data class UnknownTool(val name: String) : ToolProblem
    data class UnknownSite(val value: String) : ToolProblem
    data object MissingSite : ToolProblem
    data object MissingQuery : ToolProblem
    data object QueryTooLong : ToolProblem
    data class BadUrl(val value: String) : ToolProblem
    data class InsecureUrl(val value: String) : ToolProblem
    data object MissingTarget : ToolProblem
    data class BadTime(val value: String) : ToolProblem
    data class BadDays(val value: String) : ToolProblem
    data class BadKind(val value: String) : ToolProblem
    data object BadArguments : ToolProblem
}

sealed interface Validation {
    data class Valid(val action: AgentAction) : Validation
    data class Invalid(val problem: ToolProblem) : Validation
}

object AgentTools {
    const val WEB_CAPTURE = "web_capture"
    const val OPEN_SITE = "open_site"
    const val LIST_RECORDS = "list_records"
    const val SCHEDULE_CAPTURE = "schedule_capture"
    const val DEFAULT_LIMIT = 20
    const val MAX_LIMIT = 50
    const val MAX_QUERY = 100

    /** The tools the LLM may call, with the site keys of the shared site list. */
    fun definitions(sites: Sites): List<ToolDefinition> {
        val siteNames = sites.all.joinToString(", ") { "${it.key}(${it.name})" }
        fun site(description: String) = JSONObject().put("type", "string").put("enum", JSONArray(sites.keys))
            .put("description", description)
        fun text(description: String) = JSONObject().put("type", "string").put("description", description)
        fun schema(properties: JSONObject, required: List<String>) = JSONObject().put("type", "object")
            .put("properties", properties).put("required", JSONArray(required)).put("additionalProperties", false)
        return listOf(
            ToolDefinition(
                WEB_CAPTURE,
                "사용자가 지켜보는 앱 안 브라우저에서 사이트의 검색 결과 페이지를 열고, 화면에 보이는 광고와 상품을 " +
                    "기기 안 기록에 저장합니다. 사용자가 화면에서 승인해야 실행됩니다. 사이트: $siteNames",
                schema(
                    JSONObject().put("site", site("검색할 사이트")).put("query", text("검색어")),
                    listOf("site", "query"),
                ),
            ),
            ToolDefinition(
                OPEN_SITE,
                "앱 안 브라우저에서 사이트 홈이나 https 주소를 열어 사용자가 직접 둘러보게 합니다. 사용자가 직접 누른 " +
                    "광고는 도착 페이지와 함께 기록됩니다. site나 url 중 하나를 줍니다. 사용자가 승인해야 실행됩니다.",
                schema(
                    JSONObject().put("site", site("홈페이지를 열 사이트")).put("url", text("열 https 주소")),
                    emptyList(),
                ),
            ),
            ToolDefinition(
                LIST_RECORDS,
                "기기에 저장된 광고·상품 기록을 찾아 개수와 최근 항목을 돌려줍니다. 승인 없이 바로 실행됩니다. " +
                    "kind: ad(광고), product(상품), result(검색 결과), clicked(사용자가 누른 광고).",
                schema(
                    JSONObject().put("site", site("사이트로 거르기")).put("query", text("검색어나 제목으로 거르기"))
                        .put(
                            "kind",
                            JSONObject().put("type", "string")
                                .put("enum", JSONArray(RecordKindFilter.entries.map { it.key })),
                        )
                        .put(
                            "limit",
                            JSONObject().put("type", "integer").put("minimum", 1).put("maximum", MAX_LIMIT)
                                .put("description", "돌려받을 최근 항목 수"),
                        ),
                    emptyList(),
                ),
            ),
            ToolDefinition(
                SCHEDULE_CAPTURE,
                "정해진 시각에 광고 기록을 알림으로 알려 주는 예약을 만듭니다. 사용자가 알림을 누르면 앱 안 " +
                    "브라우저에서 기록합니다. 사용자가 화면에서 승인해야 만들어집니다.",
                schema(
                    JSONObject().put("site", site("기록할 사이트")).put("query", text("검색어"))
                        .put(
                            "time",
                            JSONObject().put("type", "string").put("pattern", "^[0-2][0-9]:[0-5][0-9]$")
                                .put("description", "24시간 형식 HH:MM, 예: 09:00"),
                        )
                        .put(
                            "days",
                            JSONObject().put("type", "string").put("enum", JSONArray(ScheduleDays.entries.map { it.key }))
                                .put("description", "daily(매일) 또는 weekdays(평일)"),
                        ),
                    listOf("site", "query", "time", "days"),
                ),
            ),
        )
    }

    /** The 24-hour HH:MM of [value] ("9:05" reads as 09:05), or null. */
    fun parseTime(value: String): String? {
        val match = Regex("^(\\d{1,2}):(\\d{2})$").matchEntire(value.trim()) ?: return null
        val hour = match.groupValues[1].toInt()
        val minute = match.groupValues[2].toInt()
        if (hour !in 0..23 || minute !in 0..59) return null
        return hour.toString().padStart(2, '0') + ":" + minute.toString().padStart(2, '0')
    }
}

/** Checks the tool calls of a reply strictly: the name, the site, the query, the address, the time and the kind. */
class ToolValidator(private val sites: Sites) {
    fun validate(name: String, input: JSONObject?): Validation {
        if (name !in TOOLS) return Validation.Invalid(ToolProblem.UnknownTool(cleanText(name, 40)))
        if (input == null) return Validation.Invalid(ToolProblem.BadArguments)
        return try {
            Validation.Valid(
                when (name) {
                    AgentTools.WEB_CAPTURE -> AgentAction.WebCapture(site(input), query(input))
                    AgentTools.OPEN_SITE -> openSite(input)
                    AgentTools.LIST_RECORDS -> listRecords(input)
                    else -> scheduleCapture(input)
                },
            )
        } catch (rejected: Rejected) {
            Validation.Invalid(rejected.problem)
        }
    }

    private class Rejected(val problem: ToolProblem) : Exception()

    private fun site(input: JSONObject): String {
        val value = string(input, "site")?.takeIf { it.isNotBlank() } ?: throw Rejected(ToolProblem.MissingSite)
        return sites.resolve(value)?.key ?: throw Rejected(ToolProblem.UnknownSite(cleanText(value, 40)))
    }

    private fun query(input: JSONObject): String {
        val raw = string(input, "query") ?: throw Rejected(ToolProblem.MissingQuery)
        val query = cleanText(raw, AgentTools.MAX_QUERY + 1)
        if (query.isEmpty()) throw Rejected(ToolProblem.MissingQuery)
        if (query.length > AgentTools.MAX_QUERY) throw Rejected(ToolProblem.QueryTooLong)
        return query
    }

    private fun openSite(input: JSONObject): AgentAction.OpenSite {
        val url = string(input, "url")?.trim().orEmpty()
        if (url.isNotEmpty()) {
            return when (val check = BrowserUrls.normalize(url)) {
                is BrowserUrls.Check.Ok -> AgentAction.OpenSite(sites.forUrl(check.url)?.key, check.url)
                is BrowserUrls.Check.Insecure -> throw Rejected(ToolProblem.InsecureUrl(cleanText(url, 200)))
                BrowserUrls.Check.Invalid -> throw Rejected(ToolProblem.BadUrl(cleanText(url, 200)))
            }
        }
        if (string(input, "site").isNullOrBlank()) throw Rejected(ToolProblem.MissingTarget)
        val site = sites.byKey(site(input))!!
        return AgentAction.OpenSite(site.key, site.home)
    }

    private fun listRecords(input: JSONObject): AgentAction.ListRecords {
        val site = if (string(input, "site").isNullOrBlank()) null else site(input)
        val query = string(input, "query")?.let { cleanText(it, AgentTools.MAX_QUERY) }?.ifEmpty { null }
        val kindValue = string(input, "kind")?.trim().orEmpty()
        val kind = if (kindValue.isEmpty()) {
            null
        } else {
            RecordKindFilter.of(kindValue.lowercase()) ?: throw Rejected(ToolProblem.BadKind(cleanText(kindValue, 40)))
        }
        val limit = limit(input.opt("limit"))
        return AgentAction.ListRecords(site, query, kind, limit)
    }

    private fun scheduleCapture(input: JSONObject): AgentAction.ScheduleCapture {
        val site = site(input)
        val query = query(input)
        val timeValue = string(input, "time").orEmpty()
        val time = AgentTools.parseTime(timeValue) ?: throw Rejected(ToolProblem.BadTime(cleanText(timeValue, 20)))
        val daysValue = string(input, "days")?.trim().orEmpty()
        val days = if (daysValue.isEmpty()) {
            ScheduleDays.DAILY
        } else {
            ScheduleDays.of(daysValue.lowercase()) ?: throw Rejected(ToolProblem.BadDays(cleanText(daysValue, 20)))
        }
        return AgentAction.ScheduleCapture(site, query, time, days)
    }

    /** A count from 1 to MAX_LIMIT: larger asks get the most, anything that is no number gets the default. */
    private fun limit(value: Any?): Int {
        val number = whole(value) ?: (value as? String)?.trim()?.toLongOrNull() ?: return AgentTools.DEFAULT_LIMIT
        return number.coerceIn(1L, AgentTools.MAX_LIMIT.toLong()).toInt()
    }

    /** A string argument; a number counts as its digits (a model may send a query of digits as a number). */
    private fun string(input: JSONObject, key: String): String? = when (val value = input.opt(key)) {
        is String -> value
        is Number -> whole(value)?.toString() ?: value.toString()
        else -> null
    }

    private companion object {
        val TOOLS = setOf(
            AgentTools.WEB_CAPTURE,
            AgentTools.OPEN_SITE,
            AgentTools.LIST_RECORDS,
            AgentTools.SCHEDULE_CAPTURE,
        )
    }
}
