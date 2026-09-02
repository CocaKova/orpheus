package com.cocakova.orpheus

import java.io.File

/** Where the dictation is going: the text around the cursor and the app it lives in. */
data class DictationContext(
    val before: String,
    val after: String,
    val app: String,
) {
    companion object {
        const val BEFORE_CHARS = 400
        const val AFTER_CHARS = 120
        val NONE = DictationContext("", "", "")
    }
}

/**
 * Space the dictation so it slots into the existing text: a leading space
 * after a word, none after a newline or an open bracket, a trailing space
 * when a word follows. Punctuation that attaches to the previous word
 * (", and so on") gets no leading space.
 */
fun fitSpacing(text: String, before: String, after: String): String {
    if (text.isEmpty()) return text
    val sb = StringBuilder()
    val prev = before.lastOrNull()
    val first = text.first()
    val needsLead = prev != null && !prev.isWhitespace() && prev !in "([{\"'“‘/" &&
        first !in ",.;:!?)]}\n"
    if (needsLead) sb.append(' ')
    sb.append(text)
    val next = after.firstOrNull()
    val last = text.last()
    if (next != null && (next.isLetterOrDigit()) && !last.isWhitespace()) sb.append(' ')
    return sb.toString()
}

/**
 * The recording whose upload failed. Kept (file and all) until it is retried
 * successfully or discarded, so a server hiccup never eats a dictation. Lives
 * in the app process, which the accessibility service and the dashboard share.
 */
object PendingTake {
    @Volatile var file: File? = null
    @Volatile var context: DictationContext? = null
    @Volatile var error: String = ""
    @Volatile var at: Long = 0L

    fun set(f: File, ctx: DictationContext?, err: String) {
        file = f; context = ctx; error = err; at = System.currentTimeMillis()
    }

    fun clear(delete: Boolean) {
        val f = file
        file = null; context = null; error = ""
        if (delete) f?.delete()
    }

    val exists: Boolean get() = file?.exists() == true
}
