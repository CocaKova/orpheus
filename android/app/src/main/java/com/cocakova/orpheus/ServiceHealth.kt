package com.cocakova.orpheus

import android.content.Context
import android.util.Log
import org.json.JSONObject
import java.io.File

/** One lifecycle event of the orb service, kept so the dashboard can show why the orb went away. */
data class HealthEvent(val ts: Long, val event: String, val detail: String)

/**
 * A small on-device log of service lifecycle: connects, disconnects, crashes,
 * overlay attach failures, keep-alive refusals. It exists because the orb
 * disappearing is invisible in hindsight — this is the paper trail the
 * dashboard reads when you ask "why did it drop out?". Local file only.
 */
object ServiceHealth {
    private const val TAG = "Orpheus"
    private const val FILE = "health.jsonl"
    private const val KEEP_LINES = 300
    private const val TRIM_AT = 450

    const val CONNECTED = "connected"
    const val DESTROYED = "destroyed"
    const val UNBOUND = "unbound"
    const val CRASH = "crash"
    const val ATTACH_FAILED = "attach-failed"
    const val KEEPALIVE_FAILED = "keepalive-failed"
    const val MIC_FGS_FAILED = "mic-fgs-failed"
    const val LOW_MEMORY = "low-memory"

    @Synchronized
    fun log(context: Context, event: String, detail: String = "") {
        Log.i(TAG, "health: $event ${detail.take(200)}")
        runCatching {
            val f = File(context.applicationContext.filesDir, FILE)
            f.appendText(
                JSONObject().put("ts", System.currentTimeMillis()).put("ev", event)
                    .put("d", detail.take(300)).toString() + "\n"
            )
            if (f.length() > TRIM_AT * 120L) {
                val lines = f.readLines()
                if (lines.size > TRIM_AT) f.writeText(lines.takeLast(KEEP_LINES).joinToString("\n") + "\n")
            }
        }.onFailure { Log.w(TAG, "health log write failed: ${it.message}") }
    }

    @Synchronized
    fun recent(context: Context, sinceMs: Long): List<HealthEvent> {
        val f = File(context.applicationContext.filesDir, FILE)
        if (!f.exists()) return emptyList()
        val floor = System.currentTimeMillis() - sinceMs
        return f.readLines().mapNotNull { line ->
            runCatching {
                val o = JSONObject(line)
                HealthEvent(o.getLong("ts"), o.getString("ev"), o.optString("d"))
            }.getOrNull()
        }.filter { it.ts >= floor }
    }
}
