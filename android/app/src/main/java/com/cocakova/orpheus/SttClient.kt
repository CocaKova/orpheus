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

    fun transcribe(audio: File, model: String, raw: Boolean = false): String {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM)
            .addFormDataPart("file", audio.name, audio.asRequestBody("audio/wav".toMediaType()))
            .addFormDataPart("response_format", "json")
            .apply { if (model.isNotBlank()) addFormDataPart("model", model) }
            // Orpheus extension; only sent when the user opts out of cleanup
            .apply { if (raw) addFormDataPart("clean", "false") }
            .build()
        val request = Request.Builder()
            .url(endpoint)
            .apply { if (apiKey.isNotBlank()) header("Authorization", "Bearer $apiKey") }
            .post(body)
            .build()
        client.newCall(request).execute().use { resp ->
            val text = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                throw IOException("STT server error ${resp.code}: ${text.take(200)}")
            }
            return JSONObject(text).optString("text").trim()
        }
    }
}
