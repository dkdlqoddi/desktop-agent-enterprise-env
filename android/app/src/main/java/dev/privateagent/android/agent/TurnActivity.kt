package dev.privateagent.android.agent

import dev.privateagent.android.util.objects
import dev.privateagent.android.util.text
import dev.privateagent.android.util.wholeOrNull
import org.json.JSONArray
import org.json.JSONException
import org.json.JSONObject

/** What a step of a turn was: one of Hermes' own tools, one of the app's actions, a command asked about, a question. */
enum class StepKind(val key: String) {
    TOOL("tool"),
    ACTION("action"),
    APPROVAL("approval"),
    QUESTION("question"),
    ;

    companion object {
        fun of(key: String?) = entries.firstOrNull { it.key == key }
    }
}

/**
 * One thing a turn did. [result] is how it ended, in the worker's own words: done or failed (an action), once,
 * session, always or deny (a command), submitted or cancelled (a question), and timeout or stopped for a question or
 * a command nobody answered.
 */
data class ActivityStep(val kind: StepKind, val text: String, val note: String, val result: String, val at: Long)

/**
 * What the turn an answer ended did on the way (the desktop's agent_activity): the steps kept, in order, and how many
 * more there were. It is stored with the message and shown under it.
 */
data class Activity(val steps: List<ActivityStep>, val more: Int) {
    val count: Int get() = steps.size + more

    fun stepsJson(): String {
        val list = JSONArray()
        for (step in steps) {
            list.put(
                JSONObject().put("kind", step.kind.key).put("text", step.text).put("note", step.note)
                    .put("result", step.result).put("at", step.at),
            )
        }
        return list.toString()
    }

    companion object {
        /** The activity as it was stored; steps of a kind this version does not know are left out. */
        fun parse(steps: String?, more: Int): Activity? {
            val list = try {
                JSONArray(steps ?: return null)
            } catch (_: JSONException) {
                return null
            }
            val parsed = list.objects().mapNotNull { step ->
                val kind = StepKind.of(step.text("kind")) ?: return@mapNotNull null
                ActivityStep(kind, step.text("text"), step.text("note"), step.text("result"), step.wholeOrNull("at") ?: 0)
            }
            return if (parsed.isEmpty() && more <= 0) null else Activity(parsed, more.coerceAtLeast(0))
        }
    }
}

/**
 * Gathers a turn's activity as it happens. Each step is kept short and without the secrets a command or a tool's
 * preview may quote ([Redact]); past [MAX_STEPS] the steps are only counted. Safe to call from the worker's thread
 * and the chat's at once.
 */
class ActivityRecorder(private val now: () -> Long = System::currentTimeMillis) {
    private val steps = ArrayList<ActivityStep>()
    private var more = 0

    @Synchronized
    fun step(kind: StepKind, text: String, note: String = "", result: String = "") {
        if (steps.size >= MAX_STEPS) {
            more += 1
            return
        }
        steps += ActivityStep(kind, Redact.text(text).take(STEP_CHARS), Redact.text(note).take(STEP_CHARS), result, now())
    }

    @Synchronized
    fun snapshot(): Activity? = if (steps.isEmpty()) null else Activity(steps.toList(), more)

    companion object {
        const val MAX_STEPS = 80
        const val STEP_CHARS = 300

        /** A question step's words: the first question, and how many more were asked with it. */
        fun questionText(questions: JSONArray?): String {
            val asked = questions?.objects().orEmpty()
            val first = asked.firstOrNull()?.text("question").orEmpty()
            return if (asked.size > 1) "$first 외 ${asked.size - 1}개" else first
        }
    }
}
