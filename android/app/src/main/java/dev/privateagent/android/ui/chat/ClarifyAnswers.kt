package dev.privateagent.android.ui.chat

import dev.privateagent.android.util.strings
import dev.privateagent.android.util.text
import org.json.JSONArray
import org.json.JSONObject

/** One question of a clarify frame: its id (qid), its text, its choices and whether several may be chosen. */
class ClarifyQuestion(val id: String, val text: String, val choices: List<String>, val multiple: Boolean) {
    companion object {
        fun of(payload: JSONObject): List<ClarifyQuestion> {
            val list = payload.optJSONArray("questions") ?: JSONArray()
            return (0 until list.length()).mapNotNull { list.optJSONObject(it) }.map { question ->
                ClarifyQuestion(
                    id = question.text("qid"),
                    text = question.text("question"),
                    choices = question.optJSONArray("choices")?.strings().orEmpty(),
                    multiple = question.optBoolean("multi_select"),
                )
            }
        }
    }
}

/**
 * The answers of a clarify card, as Hermes takes them back. Hermes offers at most four choices and counts on a box for
 * any other answer (its "Other" row), as the desktop's card has one: an answer in the user's own words takes the place
 * of the choice, or joins the choices of a question that takes several.
 */
object ClarifyAnswers {
    /** As long as the desktop's box takes. */
    const val MAX_WRITTEN = 2000

    /** The mark Hermes puts on the choice it recommends (the first); the answer keeps it, Hermes takes it off. */
    private val RECOMMENDED = Regex(" \\(Recommended\\)$")

    /** The choice without Hermes' recommendation mark, when it has one (the card says so in its own words). */
    fun recommended(choice: String): String? =
        if (RECOMMENDED.containsMatchIn(choice)) choice.replace(RECOMMENDED, "") else null

    /**
     * {qid: answer}, with a list for a question that takes several. [chosen] are the choices picked, [written] the
     * answers in the user's own words; a question with neither is left out (Hermes reads it as unanswered).
     */
    fun json(
        questions: List<ClarifyQuestion>,
        chosen: Map<String, List<String>>,
        written: Map<String, String>,
    ): JSONObject {
        val json = JSONObject()
        for (question in questions) {
            val own = written[question.id].orEmpty().trim().take(MAX_WRITTEN)
            val picked = chosen[question.id].orEmpty().filter { it.isNotBlank() }
            when {
                question.multiple -> {
                    val values = if (own.isEmpty()) picked else picked + own
                    if (values.isNotEmpty()) json.put(question.id, JSONArray(values))
                }
                own.isNotEmpty() -> json.put(question.id, own)
                picked.isNotEmpty() -> json.put(question.id, picked.first())
            }
        }
        return json
    }
}
