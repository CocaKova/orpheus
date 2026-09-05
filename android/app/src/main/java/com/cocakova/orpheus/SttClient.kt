package com.cocakova.orpheus

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.asRequestBody
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.util.concurrent.TimeUnit

/** What the server heard and what it made of it. Only [text] is guaranteed. */
data class Transcript(
    val text: String,
    /** Untouched recognizer output, when the server reports it (Orpheus). */
    val raw: String? = null,
    /** Non-empty when the server's content guard fell back to the pre-passed text. */
    val guard: String = "",
    val style: String = "",
)

/**
 * Universal OpenAI-compatible transcription client. Accepts a bare host, a /v1
 * base, or the full /v1/audio/transcriptions path — works with Orpheus, OpenAI,
 * Groq, faster-whisper servers, anything speaking the standard shape.
 */
class SttClient(baseUrl: String, private val apiKey: String) {

    private val endpoint: String = baseUrl.trim().trimEnd('/').let {
        when {
            it.endsWith("/audio/transcriptions") -> it
            it.endsWith("/v1") -> "$it/audio/transcriptions"
            else -> "$it/v1/audio/transcriptions"
        }
    }

    private val client = OkHttpClient.Builder()
        .connectTimeout(5, TimeUnit.SECONDS)
        .readTimeout(120, TimeUnit.SECONDS)
        .writeTimeout(120, TimeUnit.SECONDS)
        .build()

    /**
     * Reachability check against GET /v1/models — served by every
     * OpenAI-compatible endpoint. Returns the first model id (may be empty).
     */
    fun testConnection(): String {
        val request = Request.Builder()
            .url(endpoint.removeSuffix("/audio/transcriptions") + "/models")
            .apply { if (apiKey.isNotBlank()) header("Authorization", "Bearer $apiKey") }
            .get()
            .build()
        client.newCall(request).execute().use { resp ->
            if (!resp.isSuccessful) throw IOException("HTTP ${resp.code}")
            return JSONObject(resp.body?.string().orEmpty())
                .optJSONArray("data")?.optJSONObject(0)?.optString("id").orEmpty()
        }
    }

    /**
     * Uploads [audio] and returns the transcript. A 503 means the server is
     * still loading its model (Orpheus takes ~30 s after a restart), so that
     * one is retried with backoff for up to ~40 s instead of losing the take.
     */
    fun transcribe(
        audio: File,
        model: String,
        raw: Boolean = false,
        context: DictationContext? = null,
        dictionary: String = "",
        keepPeriod: Boolean = false,
    ): Transcript {
        var wait = 2_000L
        var waited = 0L
        while (true) {
            val body = MultipartBody.Builder().setType(MultipartBody.FORM)
                .addFormDataPart("file", audio.name, audio.asRequestBody("audio/wav".toMediaType()))
                .addFormDataPart("response_format", "verbose_json")
                .apply { if (model.isNotBlank()) addFormDataPart("model", model) }
                .apply { if (dictionary.isNotBlank()) addFormDataPart("prompt", dictionary) }
                // Orpheus extensions; harmless to servers that ignore unknown fields
                .apply { if (raw) addFormDataPart("clean", "false") }
                .apply { if (keepPeriod) addFormDataPart("trailing_period", "keep") }
                .apply {
                    if (context != null) {
                        if (context.before.isNotEmpty()) addFormDataPart("context_before", context.before)
                        if (context.after.isNotEmpty()) addFormDataPart("context_after", context.after)
                        if (context.app.isNotEmpty()) addFormDataPart("app", context.app)
                    }
                }
                .build()
            val request = Request.Builder()
                .url(endpoint)
                .apply { if (apiKey.isNotBlank()) header("Authorization", "Bearer $apiKey") }
                .post(body)
                .build()
            client.newCall(request).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (resp.code == 503 && waited < MAX_503_WAIT_MS) {
                    Thread.sleep(wait)
                    waited += wait
                    wait = (wait * 2).coerceAtMost(16_000L)
                    return@use
                }
                if (!resp.isSuccessful) {
                    throw IOException("STT server error ${resp.code}: ${text.take(200)}")
                }
                val o = JSONObject(text)
                return Transcript(
                    text = o.optString("text").trim(),
                    raw = if (o.has("raw_text")) o.optString("raw_text").trim() else null,
                    guard = o.optString("guard"),
                    style = o.optString("style"),
                )
            }
        }
    }

    private companion object {
        const val MAX_503_WAIT_MS = 40_000L
    }
}
