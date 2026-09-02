package com.cocakova.orpheus

import android.content.Context
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

data class TranscriptEntry(
    val ts: Long,
    val text: String,
    val words: Int,
    /** Recognizer output before cleanup, when the server reported it. */
    val raw: String? = null,
    /** Package the text was dictated into ("" when unknown). */
    val app: String = "",
    /** Non-empty when the server's content guard fell back to the pre-passed text. */
    val guard: String = "",
)
data class DayTally(val day: String, val words: Int)

/**
 * Local dictation history. Two files, two lifetimes:
 *
 *  - transcripts.jsonl — the raw text, pruned to the user's retention window
 *  - tallies.jsonl     — words-per-day counts (~20 bytes a day), kept forever
 *                        so the dashboard stats survive pruning
 *
 * Local files only; nothing leaves the device.
 */
class TranscriptLog(context: Context) {
    private val file = File(context.filesDir, "transcripts.jsonl")
    private val tallyFile = File(context.filesDir, "tallies.jsonl")
    private val dayFmt = SimpleDateFormat("yyyy-MM-dd", Locale.US)

    @Synchronized
    fun append(text: String, retentionDays: Int, raw: String? = null, app: String = "", guard: String = "") {
        val words = text.trim().split(Regex("\\s+")).count { it.isNotBlank() }
        bumpTally(words)
        if (retentionDays == RETENTION_OFF) return
        prune(retentionDays)
        val obj = JSONObject()
            .put("ts", System.currentTimeMillis())
            .put("text", text)
            .put("words", words)
        if (raw != null && raw != text) obj.put("raw", raw)
        if (app.isNotEmpty()) obj.put("app", app)
        if (guard.isNotEmpty()) obj.put("guard", guard)
        file.appendText(obj.toString() + "\n")
    }

    @Synchronized
    fun readAll(): List<TranscriptEntry> {
        if (!file.exists()) return emptyList()
        return file.readLines().mapNotNull { line ->
            runCatching {
                val o = JSONObject(line)
                TranscriptEntry(
                    o.getLong("ts"), o.getString("text"), o.optInt("words"),
                    raw = if (o.has("raw")) o.optString("raw") else null,
                    app = o.optString("app"),
                    guard = o.optString("guard"),
                )
            }.getOrNull()
        }
    }

    /** Drops entries older than the retention window. Cheap when nothing to drop. */
    @Synchronized
    fun prune(retentionDays: Int) {
        if (retentionDays <= 0 || !file.exists()) return // forever, or nothing to prune
        val cutoff = System.currentTimeMillis() - retentionDays * DAY_MS
        val lines = file.readLines()
        val kept = lines.filter { line ->
            (runCatching { JSONObject(line).getLong("ts") }.getOrNull() ?: 0) >= cutoff
        }
        if (kept.size != lines.size) {
            file.writeText(if (kept.isEmpty()) "" else kept.joinToString("\n") + "\n")
        }
    }

    @Synchronized
    fun deleteEntry(ts: Long) {
        if (!file.exists()) return
        val kept = file.readLines().filter {
            runCatching { JSONObject(it).getLong("ts") }.getOrNull() != ts
        }
        file.writeText(if (kept.isEmpty()) "" else kept.joinToString("\n") + "\n")
    }

    /** Tallies deliberately survive — the word-count stats outlive the text. */
    @Synchronized
    fun clear() {
        file.delete()
    }

    // -------- word-per-day tallies --------

    @Synchronized
    fun tallies(): List<DayTally> {
        seedTalliesIfNeeded()
        if (!tallyFile.exists()) return emptyList()
        return tallyFile.readLines().mapNotNull { line ->
            runCatching {
                val o = JSONObject(line)
                DayTally(o.getString("day"), o.getInt("words"))
            }.getOrNull()
        }
    }

    private fun bumpTally(words: Int) {
        seedTalliesIfNeeded()
        val map = readTallyMap()
        val today = dayFmt.format(Date())
        map[today] = (map[today] ?: 0) + words
        writeTallies(map)
    }

    /** One-time migration: rebuild tallies from whatever transcripts still exist. */
    private fun seedTalliesIfNeeded() {
        if (tallyFile.exists() || !file.exists()) return
        val map = LinkedHashMap<String, Int>()
        for (e in readAll()) {
            val day = dayFmt.format(Date(e.ts))
            map[day] = (map[day] ?: 0) + e.words
        }
        if (map.isNotEmpty()) writeTallies(map)
    }

    private fun readTallyMap(): LinkedHashMap<String, Int> {
        val map = LinkedHashMap<String, Int>()
        if (tallyFile.exists()) {
            for (line in tallyFile.readLines()) {
                runCatching {
                    val o = JSONObject(line)
                    map[o.getString("day")] = o.getInt("words")
                }
            }
        }
        return map
    }

    private fun writeTallies(map: Map<String, Int>) {
        val sb = StringBuilder()
        for ((day, words) in map) {
            sb.append(JSONObject().put("day", day).put("words", words)).append('\n')
        }
        tallyFile.writeText(sb.toString())
    }

    companion object {
        const val RETENTION_FOREVER = -1
        const val RETENTION_OFF = 0
        private const val DAY_MS = 24L * 60 * 60 * 1000
    }
}
