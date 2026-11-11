package dev.privateagent.android.agent

import dev.privateagent.android.TestSites
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ToolValidatorTest {
    private val validator = ToolValidator(TestSites.sites)

    private fun valid(name: String, input: JSONObject): AgentAction {
        val result = validator.validate(name, input)
        assertTrue("expected a valid call, got $result", result is Validation.Valid)
        return (result as Validation.Valid).action
    }

    private fun problem(name: String, input: JSONObject?): ToolProblem {
        val result = validator.validate(name, input)
        assertTrue("expected a rejected call, got $result", result is Validation.Invalid)
        return (result as Validation.Invalid).problem
    }

    @Test
    fun webCaptureTakesASiteKeyAndATrimmedQuery() {
        assertEquals(
            AgentAction.WebCapture("coupang", "노트북"),
            valid(AgentTools.WEB_CAPTURE, JSONObject().put("site", "coupang").put("query", "  노트북 ")),
        )
    }

    @Test
    fun aSiteMayBeNamedByItsDisplayNameOrInAnyCase() {
        assertEquals("coupang", (valid(AgentTools.WEB_CAPTURE, JSONObject().put("site", "쿠팡").put("query", "x")) as AgentAction.WebCapture).site)
        assertEquals("amazon", (valid(AgentTools.WEB_CAPTURE, JSONObject().put("site", "Amazon").put("query", "x")) as AgentAction.WebCapture).site)
    }

    @Test
    fun anUnknownSiteIsAskedAbout() {
        assertEquals(ToolProblem.UnknownSite("ebay"), problem(AgentTools.WEB_CAPTURE, JSONObject().put("site", "ebay").put("query", "x")))
        assertEquals(ToolProblem.MissingSite, problem(AgentTools.WEB_CAPTURE, JSONObject().put("query", "x")))
        assertEquals(ToolProblem.MissingSite, problem(AgentTools.WEB_CAPTURE, JSONObject().put("site", " ").put("query", "x")))
    }

    @Test
    fun theQueryIsRequiredAndShort() {
        assertEquals(ToolProblem.MissingQuery, problem(AgentTools.WEB_CAPTURE, JSONObject().put("site", "naver")))
        assertEquals(ToolProblem.MissingQuery, problem(AgentTools.WEB_CAPTURE, JSONObject().put("site", "naver").put("query", " \n ")))
        assertEquals(
            ToolProblem.QueryTooLong,
            problem(AgentTools.WEB_CAPTURE, JSONObject().put("site", "naver").put("query", "가".repeat(101))),
        )
        // Line breaks and control characters become single spaces.
        assertEquals(
            AgentAction.WebCapture("naver", "캠핑 의자"),
            valid(AgentTools.WEB_CAPTURE, JSONObject().put("site", "naver").put("query", "캠핑\n\t의자\u0000")),
        )
    }

    @Test
    fun aQueryOfDigitsMayComeAsANumber() {
        assertEquals(
            AgentAction.WebCapture("google", "3080"),
            valid(AgentTools.WEB_CAPTURE, JSONObject().put("site", "google").put("query", 3080)),
        )
    }

    @Test
    fun unknownToolsAndUnreadableArgumentsAreRefused() {
        assertEquals(ToolProblem.UnknownTool("delete_everything"), problem("delete_everything", JSONObject()))
        assertEquals(ToolProblem.BadArguments, problem(AgentTools.WEB_CAPTURE, null))
    }

    @Test
    fun openSiteBySiteOpensItsHome() {
        assertEquals(
            AgentAction.OpenSite("naver", "https://www.naver.com/"),
            valid(AgentTools.OPEN_SITE, JSONObject().put("site", "naver")),
        )
    }

    @Test
    fun openSiteByUrlDefaultsToHttpsAndNamesAKnownSite() {
        assertEquals(
            AgentAction.OpenSite(null, "https://shop.example.com/sale"),
            valid(AgentTools.OPEN_SITE, JSONObject().put("url", "shop.example.com/sale")),
        )
        assertEquals(
            AgentAction.OpenSite("amazon", "https://www.amazon.com/dp/B000TEST"),
            valid(AgentTools.OPEN_SITE, JSONObject().put("url", "https://www.amazon.com/dp/B000TEST")),
        )
    }

    @Test
    fun openSiteRefusesHttpAndOtherSchemes() {
        assertEquals(
            ToolProblem.InsecureUrl("http://shop.example.com"),
            problem(AgentTools.OPEN_SITE, JSONObject().put("url", "http://shop.example.com")),
        )
        assertTrue(problem(AgentTools.OPEN_SITE, JSONObject().put("url", "javascript:alert(1)")) is ToolProblem.BadUrl)
        assertTrue(problem(AgentTools.OPEN_SITE, JSONObject().put("url", "intent://scan#Intent;end")) is ToolProblem.BadUrl)
        assertTrue(problem(AgentTools.OPEN_SITE, JSONObject().put("url", "https://user:pw@shop.example.com")) is ToolProblem.BadUrl)
        assertEquals(ToolProblem.MissingTarget, problem(AgentTools.OPEN_SITE, JSONObject()))
        assertEquals(ToolProblem.UnknownSite("ebay"), problem(AgentTools.OPEN_SITE, JSONObject().put("site", "ebay")))
    }

    @Test
    fun listRecordsHasDefaultsAndClampsTheLimit() {
        assertEquals(AgentAction.ListRecords(null, null, null, AgentTools.DEFAULT_LIMIT), valid(AgentTools.LIST_RECORDS, JSONObject()))
        assertEquals(
            AgentAction.ListRecords("coupang", "노트북", RecordKindFilter.CLICKED, AgentTools.MAX_LIMIT),
            valid(
                AgentTools.LIST_RECORDS,
                JSONObject().put("site", "coupang").put("query", "노트북").put("kind", "clicked").put("limit", 500),
            ),
        )
        assertEquals(1, (valid(AgentTools.LIST_RECORDS, JSONObject().put("limit", 0)) as AgentAction.ListRecords).limit)
        assertEquals(7, (valid(AgentTools.LIST_RECORDS, JSONObject().put("limit", "7")) as AgentAction.ListRecords).limit)
        assertEquals(
            AgentTools.DEFAULT_LIMIT,
            (valid(AgentTools.LIST_RECORDS, JSONObject().put("limit", 2.5)) as AgentAction.ListRecords).limit,
        )
    }

    @Test
    fun listRecordsRefusesAnUnknownKindOrSite() {
        assertEquals(ToolProblem.BadKind("video"), problem(AgentTools.LIST_RECORDS, JSONObject().put("kind", "video")))
        assertEquals(ToolProblem.UnknownSite("ebay"), problem(AgentTools.LIST_RECORDS, JSONObject().put("site", "ebay")))
    }

    @Test
    fun scheduleCaptureReadsTheTimeAndDays() {
        assertEquals(
            AgentAction.ScheduleCapture("coupang", "노트북", "09:05", ScheduleDays.WEEKDAYS),
            valid(
                AgentTools.SCHEDULE_CAPTURE,
                JSONObject().put("site", "coupang").put("query", "노트북").put("time", "9:05").put("days", "weekdays"),
            ),
        )
        assertEquals(
            ScheduleDays.DAILY,
            (
                valid(
                    AgentTools.SCHEDULE_CAPTURE,
                    JSONObject().put("site", "coupang").put("query", "노트북").put("time", "21:00"),
                ) as AgentAction.ScheduleCapture
                ).days,
        )
    }

    @Test
    fun scheduleCaptureRefusesTimesAndDaysItCannotRead() {
        val base = { JSONObject().put("site", "coupang").put("query", "노트북") }
        assertEquals(ToolProblem.BadTime("25:00"), problem(AgentTools.SCHEDULE_CAPTURE, base().put("time", "25:00")))
        assertEquals(ToolProblem.BadTime("12:60"), problem(AgentTools.SCHEDULE_CAPTURE, base().put("time", "12:60")))
        assertEquals(ToolProblem.BadTime("9시"), problem(AgentTools.SCHEDULE_CAPTURE, base().put("time", "9시")))
        assertEquals(ToolProblem.BadTime(""), problem(AgentTools.SCHEDULE_CAPTURE, base()))
        assertEquals(
            ToolProblem.BadDays("monthly"),
            problem(AgentTools.SCHEDULE_CAPTURE, base().put("time", "09:00").put("days", "monthly")),
        )
    }

    @Test
    fun savedActionsValidateToThemselves() {
        val actions = listOf(
            AgentAction.WebCapture("amazon", "headphones"),
            AgentAction.OpenSite("google", "https://www.google.com/"),
            AgentAction.OpenSite(null, "https://shop.example.com/a?b=c"),
            AgentAction.ListRecords("naver", "의자", RecordKindFilter.AD, 10),
            AgentAction.ScheduleCapture("coupang", "노트북", "09:00", ScheduleDays.DAILY),
        )
        for (action in actions) assertEquals(action, valid(action.tool, JSONObject(action.toJson().toString())))
    }

    @Test
    fun toolDefinitionsUseTheSharedSiteKeys() {
        val definitions = AgentTools.definitions(TestSites.sites)
        assertEquals(
            listOf(AgentTools.WEB_CAPTURE, AgentTools.OPEN_SITE, AgentTools.LIST_RECORDS, AgentTools.SCHEDULE_CAPTURE),
            definitions.map { it.name },
        )
        val siteEnum = definitions.first().schema.getJSONObject("properties").getJSONObject("site").getJSONArray("enum")
        assertEquals(TestSites.sites.keys, (0 until siteEnum.length()).map { siteEnum.getString(it) })
        assertEquals(setOf("coupang", "amazon", "naver", "google"), TestSites.sites.keys.toSet())
    }
}
