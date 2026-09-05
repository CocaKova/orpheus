package com.cocakova.orpheus

import android.content.Context
import android.content.SharedPreferences

/** Single SharedPreferences store shared by the dashboard and the accessibility service. */
class Prefs(context: Context) {
    private val sp: SharedPreferences =
        context.getSharedPreferences("orpheus", Context.MODE_PRIVATE)

    var sttUrl: String
        get() = sp.getString("stt_url", "") ?: ""
        set(v) = sp.edit().putString("stt_url", v).apply()

    var sttApiKey: String
        get() = sp.getString("stt_api_key", "") ?: ""
        set(v) = sp.edit().putString("stt_api_key", v).apply()

    var sttModel: String
        get() = sp.getString("stt_model", "") ?: ""
        set(v) = sp.edit().putString("stt_model", v).apply()

    /** Skip the server's LLM cleanup pass and paste raw ASR output. */
    var rawMode: Boolean
        get() = sp.getBoolean("raw_mode", false)
        set(v) = sp.edit().putBoolean("raw_mode", v).apply()

    /**
     * Names and terms the server should spell right, comma-separated. Sent as
     * the OpenAI `prompt` field, so it also works as a hint for other servers.
     */
    var dictionary: String
        get() = sp.getString("dictionary", "") ?: ""
        set(v) = sp.edit().putString("dictionary", v).apply()

    /**
     * Keep the trailing period on a lone sentence in chat apps. Off by default:
     * the chat convention is to leave it off ("on my way"), which is what the
     * server does unless this is set.
     */
    var keepPeriod: Boolean
        get() = sp.getBoolean("keep_period", false)
        set(v) = sp.edit().putBoolean("keep_period", v).apply()

    /** Send the text around the cursor and the target app so the server can match them. */
    var sendContext: Boolean
        get() = sp.getBoolean("send_context", true)
        set(v) = sp.edit().putBoolean("send_context", v).apply()

    /** Vibrate on record start/stop and on paste result. */
    var haptics: Boolean
        get() = sp.getBoolean("haptics", true)
        set(v) = sp.edit().putBoolean("haptics", v).apply()

    /**
     * Insert by rewriting the field text at the cursor instead of pasting, so
     * the clipboard is left alone. Falls back to paste when the field refuses.
     */
    var keepClipboard: Boolean
        get() = sp.getBoolean("keep_clipboard", false)
        set(v) = sp.edit().putBoolean("keep_clipboard", v).apply()

    /**
     * Keep the orb up while a text field holds input focus, even when the
     * keyboard itself is closed. Also the safety net when the keyboard is up
     * but Android's window list momentarily says otherwise.
     */
    var followCursor: Boolean
        get() = sp.getBoolean("follow_cursor", true)
        set(v) = sp.edit().putBoolean("follow_cursor", v).apply()

    /** Run a quiet foreground service so app sleepers leave the orb service alone. */
    var keepAlive: Boolean
        get() = sp.getBoolean("keep_alive", true)
        set(v) = sp.edit().putBoolean("keep_alive", v).apply()

    /** Snap the orb to the nearest screen edge after a drag. */
    var snapToEdge: Boolean
        get() = sp.getBoolean("snap_to_edge", true)
        set(v) = sp.edit().putBoolean("snap_to_edge", v).apply()

    /** Days to keep transcripts; -1 = forever, 0 = don't record new ones. */
    var retentionDays: Int
        get() = sp.getInt("retention_days", 30)
        set(v) = sp.edit().putInt("retention_days", v).apply()

    var bubbleX: Int
        get() = sp.getInt("bubble_x", -1)
        set(v) = sp.edit().putInt("bubble_x", v).apply()

    var bubbleY: Int
        get() = sp.getInt("bubble_y", -1)
        set(v) = sp.edit().putInt("bubble_y", v).apply()
}
