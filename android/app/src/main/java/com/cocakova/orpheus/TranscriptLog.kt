package com.cocakova.orpheus

import android.content.Context
import org.json.JSONObject
import java.io.File

data class TranscriptEntry(val ts: Long, val text: String, val words: Int)

/**
 * Append-only JSONL log of everything dictated — feeds the dashboard stats.
 * Local file only; nothing leaves the device.
 */
class TranscriptLog(context: Context) {
    private val file = File(context.filesDir, "transcripts.jsonl")

    @Synchronized
    fun append(text: String) {
        val words = text.trim().split(Regex("\\s+")).count { it.isNotBlank() }
        val obj = JSONObject()
            .put("ts", System.currentTimeMillis())
            .put("text", text)
            .put("words", words)
        file.appendText(obj.toString() + "\n")
    }

    @Synchronized
    fun readAll(): List<TranscriptEntry> {
        if (!file.exists()) return emptyList()
        return file.readLines().mapNotNull { line ->
            runCatching {
                val o = JSONObject(line)
                TranscriptEntry(o.getLong("ts"), o.getString("text"), o.optInt("words"))
            }.getOrNull()
        }
    }

    @Synchronized
    fun clear() {
        file.delete()
    }
}
