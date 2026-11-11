package dev.privateagent.android.util

import org.json.JSONArray
import org.json.JSONObject

// Readers that behave the same on Android's org.json and on the reference implementation the JVM tests use: those
// two differ on null values (optString answers "null" on Android), so every reader checks the value's type itself.

/** The string at [name], or "" when it is missing, null or not a string. */
fun JSONObject.text(name: String): String = opt(name) as? String ?: ""

/** The string at [name], or null when it is missing, null or not a string. */
fun JSONObject.textOrNull(name: String): String? = opt(name) as? String

/** The whole number at [name] (a number without a fraction), or null. */
fun JSONObject.wholeOrNull(name: String): Long? = whole(opt(name))

/** The boolean at [name], or [fallback]. */
fun JSONObject.flag(name: String, fallback: Boolean = false): Boolean = opt(name) as? Boolean ?: fallback

fun JSONObject.objectOrNull(name: String): JSONObject? = opt(name) as? JSONObject

fun JSONObject.arrayOrNull(name: String): JSONArray? = opt(name) as? JSONArray

/** The objects of an array, skipping anything else it holds. */
fun JSONArray.objects(): List<JSONObject> = (0 until length()).mapNotNull { opt(it) as? JSONObject }

/** The strings of an array, skipping anything else it holds. */
fun JSONArray.strings(): List<String> = (0 until length()).mapNotNull { opt(it) as? String }

/** A JSON number without a fraction as a Long, or null for anything else. */
fun whole(value: Any?): Long? {
    if (value !is Number) return null
    val number = value.toDouble()
    if (number.isNaN() || number.isInfinite() || number != Math.floor(number)) return null
    if (number > Long.MAX_VALUE.toDouble() || number < Long.MIN_VALUE.toDouble()) return null
    return number.toLong()
}

/** Text without control characters (line breaks and tabs become spaces), whitespace collapsed, at most [limit] long. */
fun cleanText(value: String, limit: Int): String {
    val builder = StringBuilder(minOf(value.length, limit + 1))
    var space = false
    for (char in value) {
        if (char.isWhitespace() || Character.isISOControl(char)) {
            space = builder.isNotEmpty()
            continue
        }
        if (space) {
            builder.append(' ')
            space = false
        }
        builder.append(char)
        if (builder.length >= limit) break
    }
    return builder.toString().take(limit)
}
