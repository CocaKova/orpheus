package com.cocakova.orpheus

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.NoiseSuppressor
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.concurrent.thread
import kotlin.math.sqrt

/**
 * Records 16 kHz mono 16-bit PCM straight to a WAV file — exactly what ASR
 * models want, no transcode step. Streams RMS amplitude to [onAmplitude] for
 * the live waveform (called from the capture thread).
 *
 * Uses the VOICE_RECOGNITION source (tuned for ASR, no music-style AGC) and
 * the platform noise suppressor when the device has one. Tracks the loudest
 * chunk so a take that never rose above the noise floor can be skipped
 * without a round trip.
 */
class WavRecorder(
    private val outFile: File,
    private val onAmplitude: (Float) -> Unit,
) {
    private val sampleRate = 16000

    @Volatile
    private var running = false
    private var record: AudioRecord? = null
    private var worker: Thread? = null
    private var suppressor: NoiseSuppressor? = null

    /** Loudest RMS chunk seen, 0..1. */
    @Volatile
    var peak = 0f
        private set

    /** True when nothing in the take rose above the noise floor. */
    val silent: Boolean get() = peak < SILENCE_PEAK

    @SuppressLint("MissingPermission")
    fun start() {
        val minBuf = AudioRecord.getMinBufferSize(
            sampleRate, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT
        )
        val rec = AudioRecord(
            MediaRecorder.AudioSource.VOICE_RECOGNITION, sampleRate,
            AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT,
            maxOf(minBuf, 8192)
        )
        if (rec.state != AudioRecord.STATE_INITIALIZED) {
            rec.release()
            throw IOException("Microphone unavailable")
        }
        record = rec
        if (NoiseSuppressor.isAvailable()) {
            suppressor = runCatching { NoiseSuppressor.create(rec.audioSessionId)?.apply { enabled = true } }
                .getOrNull()
        }
        running = true
        rec.startRecording()
        worker = thread(name = "orpheus-rec") {
            val buf = ShortArray(2048)
            val bytes = ByteBuffer.allocate(buf.size * 2).order(ByteOrder.LITTLE_ENDIAN)
            RandomAccessFile(outFile, "rw").use { raf ->
                raf.setLength(0)
                raf.write(ByteArray(44)) // header placeholder, patched on stop
                var total = 0L
                while (running) {
                    val n = rec.read(buf, 0, buf.size)
                    if (n <= 0) continue
                    var sum = 0.0
                    for (i in 0 until n) {
                        val s = buf[i].toDouble()
                        sum += s * s
                    }
                    val rms = (sqrt(sum / n) / 32768.0).toFloat()
                    if (rms > peak) peak = rms
                    onAmplitude(rms)
                    bytes.clear()
                    for (i in 0 until n) bytes.putShort(buf[i])
                    raf.write(bytes.array(), 0, n * 2)
                    total += n * 2
                }
                writeHeader(raf, total)
            }
        }
    }

    /** Stops recording; returns the WAV file, or null if the take was too short to be speech. */
    fun stop(): File? {
        running = false
        worker?.join(2000)
        worker = null
        record?.let {
            runCatching { it.stop() }
            it.release()
        }
        record = null
        suppressor?.let { runCatching { it.release() } }
        suppressor = null
        // 44-byte header + ~0.2s of 16kHz 16-bit audio
        return if (outFile.length() > 44 + 6400) outFile else {
            outFile.delete()
            null
        }
    }

    fun cancel() {
        stop()
        outFile.delete()
    }

    private companion object {
        // speech RMS lands around 0.05–0.3; an untouched mic idles well under 0.01
        const val SILENCE_PEAK = 0.02f
    }

    private fun writeHeader(raf: RandomAccessFile, dataLen: Long) {
        val h = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        h.put("RIFF".toByteArray())
        h.putInt((36 + dataLen).toInt())
        h.put("WAVE".toByteArray())
        h.put("fmt ".toByteArray())
        h.putInt(16)                      // fmt chunk size
        h.putShort(1)                     // PCM
        h.putShort(1)                     // mono
        h.putInt(sampleRate)
        h.putInt(sampleRate * 2)          // byte rate
        h.putShort(2)                     // block align
        h.putShort(16)                    // bits per sample
        h.put("data".toByteArray())
        h.putInt(dataLen.toInt())
        raf.seek(0)
        raf.write(h.array())
    }
}
