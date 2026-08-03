package com.cocakova.orpheus

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.PixelFormat
import android.os.Bundle
import android.view.Gravity
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import android.widget.Toast
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Watches for the keyboard (IME window) appearing anywhere on the device and
 * floats the Orpheus bubble next to it. Tap / hold the bubble to dictate;
 * the transcript is copied to the clipboard and pasted into the focused field.
 */
private const val TAG = "Orpheus"

class OrpheusAccessibilityService : AccessibilityService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private lateinit var windowManager: WindowManager
    private lateinit var prefs: Prefs
    private lateinit var log: TranscriptLog

    private var bubble: BubbleView? = null
    private var params: WindowManager.LayoutParams? = null
    private var attached = false
    private var hiding = false
    private var keyboardVisible = false
    private var state = BubbleState.IDLE
    private var recorder: WavRecorder? = null
    private var dragStartX = 0
    private var dragStartY = 0

    override fun onServiceConnected() {
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        prefs = Prefs(this)
        log = TranscriptLog(this)
        createBubble()
        refreshKeyboardVisibility()
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        refreshKeyboardVisibility()
    }

    override fun onInterrupt() {}

    override fun onDestroy() {
        recorder?.cancel()
        recorder = null
        if (attached) runCatching { windowManager.removeView(bubble) }
        attached = false
        scope.cancel()
        super.onDestroy()
    }

    // -------- bubble lifecycle --------

    private fun createBubble() {
        val density = resources.displayMetrics.density
        val size = (60 * density).toInt()
        val p = WindowManager.LayoutParams(
            size, size,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = if (prefs.bubbleX >= 0) prefs.bubbleX
                else resources.displayMetrics.widthPixels - size - (8 * density).toInt()
            y = if (prefs.bubbleY >= 0) prefs.bubbleY
                else (resources.displayMetrics.heightPixels * 0.52f).toInt()
        }
        params = p
        bubble = BubbleView(
            this,
            onTap = ::onBubbleTap,
            onHoldStart = { if (state == BubbleState.IDLE) startRecording() },
            onHoldEnd = { if (state == BubbleState.RECORDING) finishRecording() },
            onDragStart = {
                dragStartX = p.x
                dragStartY = p.y
            },
            onDragTo = { dx, dy ->
                p.x = clampX(dragStartX + dx)
                p.y = clampY(dragStartY + dy)
                if (attached) windowManager.updateViewLayout(bubble, p)
            },
            onDragEnd = {
                prefs.bubbleX = p.x
                prefs.bubbleY = p.y
            },
        )
    }

    /** Keep the whole bubble on screen — losing it off an edge means losing dictation. */
    private fun clampX(x: Int): Int {
        val size = params?.width ?: 0
        return x.coerceIn(0, (resources.displayMetrics.widthPixels - size).coerceAtLeast(0))
    }

    private fun clampY(y: Int): Int {
        val size = params?.height ?: 0
        return y.coerceIn(0, (resources.displayMetrics.heightPixels - size).coerceAtLeast(0))
    }

    private fun refreshKeyboardVisibility() {
        val visible = runCatching {
            windows.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
        }.getOrDefault(false)
        if (visible != keyboardVisible) {
            keyboardVisible = visible
            updateBubbleVisibility()
        }
    }

    private fun updateBubbleVisibility() {
        val b = bubble ?: return
        // stay visible mid-recording/transcription even if the keyboard closes
        val shouldShow = keyboardVisible || state != BubbleState.IDLE
        if (shouldShow) {
            if (attached) {
                if (hiding) { // re-show caught the hide mid-animation: reverse it in place
                    hiding = false
                    b.animate().cancel()
                    b.animate().alpha(1f).scaleX(1f).scaleY(1f)
                        .setDuration(160L)
                        .setInterpolator(android.view.animation.OvershootInterpolator(1.4f))
                        .start()
                }
                return
            }
            // re-clamp the saved spot: rotation/display changes can strand it off-screen
            params?.let { it.x = clampX(it.x); it.y = clampY(it.y) }
            runCatching { windowManager.addView(b, params) }
                .onSuccess {
                    attached = true
                    b.alpha = 0f
                    b.scaleX = 0.6f
                    b.scaleY = 0.6f
                    b.animate().alpha(1f).scaleX(1f).scaleY(1f)
                        .setDuration(220L)
                        .setInterpolator(android.view.animation.OvershootInterpolator(1.4f))
                        .start()
                }
        } else if (attached && !hiding) {
            hiding = true
            b.animate().cancel()
            b.animate().alpha(0f).scaleX(0.6f).scaleY(0.6f)
                .setDuration(160L)
                .setInterpolator(android.view.animation.AccelerateInterpolator())
                .withEndAction {
                    if (hiding) {
                        hiding = false
                        attached = false
                        runCatching { windowManager.removeView(b) }
                    }
                }
                .start()
        }
    }

    private fun setState(s: BubbleState) {
        state = s
        bubble?.state = s
        updateBubbleVisibility()
    }

    // -------- dictation flow --------

    private fun onBubbleTap() {
        when (state) {
            BubbleState.IDLE -> startRecording()
            BubbleState.RECORDING -> finishRecording()
            BubbleState.TRANSCRIBING -> {} // ignore taps while working
        }
    }

    private fun startRecording() {
        if (checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)
            != PackageManager.PERMISSION_GRANTED
        ) {
            toast("Open Orpheus and grant microphone access")
            openDashboard()
            return
        }
        if (prefs.sttUrl.isBlank()) {
            toast("Set the STT server URL in Orpheus first")
            openDashboard()
            return
        }
        RecordingForegroundService.start(this)
        val f = File(cacheDir, "dictation_${System.currentTimeMillis()}.wav")
        val r = WavRecorder(f) { amp -> bubble?.pushAmplitude(amp) }
        try {
            r.start()
        } catch (e: Exception) {
            RecordingForegroundService.stop(this)
            toast("Mic error: ${e.message}")
            return
        }
        recorder = r
        setState(BubbleState.RECORDING)
    }

    private fun finishRecording() {
        val r = recorder ?: return
        recorder = null
        val file = runCatching { r.stop() }.getOrNull()
        RecordingForegroundService.stop(this)
        if (file == null) {
            setState(BubbleState.IDLE)
            return
        }
        setState(BubbleState.TRANSCRIBING)
        scope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    SttClient(prefs.sttUrl, prefs.sttApiKey)
                        .transcribe(file, prefs.sttModel, prefs.rawMode)
                }
            }
            file.delete()
            setState(BubbleState.IDLE)
            result.onSuccess { text ->
                if (text.isBlank()) toast("Heard nothing") else deliver(text)
            }.onFailure { e ->
                toast("Transcription failed: ${e.message}")
            }
        }
    }

    private fun deliver(text: String) {
        copyToClipboard(this, text)
        log.append(text, prefs.retentionDays)
        if (!pasteIntoFocusedField(text)) {
            // Input focus can be mid-flight right after the orb tap settles — one breath, retry.
            scope.launch {
                delay(350)
                if (!pasteIntoFocusedField(text)) toast("Copied to clipboard")
            }
        }
    }

    /** Paste at the cursor if a text field has focus; falls back to set-text append. */
    private fun pasteIntoFocusedField(text: String): Boolean {
        val node = findFocusedEditable()
        if (node == null) {
            Log.i(TAG, "paste: no focused editable (active=${rootInActiveWindow?.packageName}, windows=${windows.size})")
            return false
        }
        Log.i(TAG, "paste: target=${node.packageName}/${node.className}")
        if (node.performAction(AccessibilityNodeInfo.ACTION_PASTE)) return true
        Log.i(TAG, "paste: ACTION_PASTE refused, trying set-text")
        val existing = if (node.isShowingHintText) "" else (node.text?.toString() ?: "")
        val args = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                if (existing.isBlank()) text else "$existing $text"
            )
        }
        return node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args).also {
            if (!it) Log.i(TAG, "paste: ACTION_SET_TEXT refused")
        }
    }

    private fun findFocusedEditable(): AccessibilityNodeInfo? {
        rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)
            ?.let { if (it.isEditable) return it }
        for (w in windows) {
            val f = w.root?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: continue
            if (f.isEditable) return f
        }
        return null
    }

    private fun openDashboard() {
        startActivity(
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
    }

    private fun toast(msg: String) {
        Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
    }
}
