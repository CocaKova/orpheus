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

    var bubbleX: Int
        get() = sp.getInt("bubble_x", -1)
        set(v) = sp.edit().putInt("bubble_x", v).apply()

    var bubbleY: Int
        get() = sp.getInt("bubble_y", -1)
        set(v) = sp.edit().putInt("bubble_y", v).apply()
}
