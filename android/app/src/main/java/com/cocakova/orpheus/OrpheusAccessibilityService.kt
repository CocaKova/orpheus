package com.cocakova.orpheus

import android.accessibilityservice.AccessibilityService
import android.animation.ValueAnimator
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
import android.view.animation.DecelerateInterpolator
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
 * floats the Orpheus orb next to it. Tap / hold the orb to dictate; the
 * transcript is inserted at the cursor of the focused field (paste, or a
 * clipboard-free rewrite when the user prefers), spaced to fit the text
 * around it. The text around the cursor and the app name travel with the
 * audio so the server can match casing and tone.
 *
 * A failed upload keeps the recording: the ring turns amber, a tap retries,
 * a hold records fresh. Nothing dictated is ever thrown away by a hiccup.
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
    private var snapAnimator: ValueAnimator? = null

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
        snapAnimator?.cancel()
        if (attached) runCatching { windowManager.removeView(bubble) }
        attached = false
        scope.cancel()
        super.onDestroy()
    }

    // -------- bubble lifecycle --------

    private val density get() = resources.displayMetrics.density
    private val orbMargin get() = ((BubbleView.VIEW_DP * (1f - BubbleView.ORB_FRACTION)) / 2f * density).toInt()

    private fun createBubble() {
        val size = (BubbleView.VIEW_DP * density).toInt()
        val p = WindowManager.LayoutParams(
            size, size,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                or WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = if (prefs.bubbleX >= 0) prefs.bubbleX else rightEdgeX(size)
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
                snapAnimator?.cancel()
                dragStartX = p.x
                dragStartY = p.y
            },
            onDragTo = { dx, dy ->
                p.x = clampX(dragStartX + dx)
                p.y = clampY(dragStartY + dy)
                if (attached) windowManager.updateViewLayout(bubble, p)
            },
            onDragEnd = {
                if (prefs.snapToEdge) snapToEdge() else savePosition()
            },
        ).apply { pending = PendingTake.exists }
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

    /** The orb (not its glow margin) sits 8dp off the right edge. */
    private fun rightEdgeX(size: Int) =
        resources.displayMetrics.widthPixels - size + orbMargin - (8 * density).toInt()

    /**
     * Left edge keeps a wider inset: the system back gesture owns the first
     * ~20dp and a drag that starts there goes to Android, not the orb.
     */
    private fun leftEdgeX() = (24 * density).toInt() - orbMargin

    private fun snapToEdge() {
        val p = params ?: return
        val size = p.width
        val centre = p.x + size / 2
        val target = clampX(
            if (centre < resources.displayMetrics.widthPixels / 2) leftEdgeX() else rightEdgeX(size)
        )
        prefs.bubbleX = target // the saved spot is the settled one
        prefs.bubbleY = p.y
        if (target == p.x) return
        snapAnimator?.cancel()
        snapAnimator = ValueAnimator.ofInt(p.x, target).apply {
            duration = 180L
            interpolator = DecelerateInterpolator()
            addUpdateListener {
                p.x = it.animatedValue as Int
                if (attached) windowManager.updateViewLayout(bubble, p)
            }
            start()
        }
    }

    private fun savePosition() {
        params?.let {
            prefs.bubbleX = it.x
            prefs.bubbleY = it.y
        }
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
            b.pending = PendingTake.exists
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
            BubbleState.IDLE -> if (PendingTake.exists) retryPending() else startRecording()
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
            bubble?.flashFailure()
            Haptics.failure(this)
            toast("Mic error: ${e.message}")
            return
        }
        recorder = r
        Haptics.recordStart(this)
        setState(BubbleState.RECORDING)
    }

    private fun finishRecording() {
        val r = recorder ?: return
        recorder = null
        val file = runCatching { r.stop() }.getOrNull()
        RecordingForegroundService.stop(this)
        Haptics.recordStop(this)
        if (file == null) {
            setState(BubbleState.IDLE)
            return
        }
        if (r.silent) {
            file.delete()
            setState(BubbleState.IDLE)
            toast("Heard nothing")
            return
        }
        // grab the cursor context now, while the field that opened the keyboard still has focus
        val ctx = if (prefs.sendContext) captureContext() else DictationContext.NONE
        transcribe(file, ctx)
    }

    private fun retryPending() {
        val file = PendingTake.file ?: return
        if (!file.exists()) { PendingTake.clear(false); bubble?.pending = false; return }
        transcribe(file, PendingTake.context ?: DictationContext.NONE)
    }

    private fun transcribe(file: File, ctx: DictationContext) {
        setState(BubbleState.TRANSCRIBING)
        scope.launch {
            val result = withContext(Dispatchers.IO) {
                runCatching {
                    SttClient(prefs.sttUrl, prefs.sttApiKey)
                        .transcribe(file, prefs.sttModel, prefs.rawMode, ctx, prefs.dictionary)
                }
            }
            setState(BubbleState.IDLE)
            result.onSuccess { t ->
                PendingTake.clear(false)
                file.delete()
                bubble?.pending = false
                if (t.text.isBlank()) toast("Heard nothing") else deliver(t, ctx)
            }.onFailure { e ->
                // keep the take: the orb turns amber, tap to retry, hold to record fresh
                PendingTake.set(file, ctx, e.message ?: e.javaClass.simpleName)
                bubble?.pending = true
                bubble?.flashFailure()
                Haptics.failure(this@OrpheusAccessibilityService)
                Log.w(TAG, "transcription failed, take kept: ${e.message}")
                toast("Transcription failed. Tap the orb to retry, hold to record fresh.")
            }
        }
    }

    // -------- context + delivery --------

    /** Text around the cursor and the app it belongs to; empty when there is no editable focus. */
    private fun captureContext(): DictationContext {
        val node = findFocusedEditable() ?: return DictationContext("", "", rootInActiveWindow?.packageName?.toString().orEmpty())
        val app = node.packageName?.toString().orEmpty()
        if (node.isShowingHintText) return DictationContext("", "", app)
        val text = node.text?.toString().orEmpty()
        val start = node.textSelectionStart.let { if (it < 0) text.length else it.coerceAtMost(text.length) }
        val end = node.textSelectionEnd.let { if (it < 0) start else it.coerceIn(start, text.length) }
        return DictationContext(
            before = text.substring(0, start).takeLast(DictationContext.BEFORE_CHARS),
            after = text.substring(end).take(DictationContext.AFTER_CHARS),
            app = app,
        )
    }

    private fun deliver(t: Transcript, ctx: DictationContext) {
        val text = fitSpacing(t.text, ctx.before, ctx.after)
        log.append(t.text, prefs.retentionDays, raw = t.raw, app = ctx.app, guard = t.guard)
        if (!prefs.keepClipboard) copyToClipboard(this, t.text)
        if (insertIntoFocusedField(text)) {
            onDelivered()
            return
        }
        // Input focus can be mid-flight right after the orb tap settles — one breath, retry.
        scope.launch {
            delay(350)
            if (insertIntoFocusedField(text)) {
                onDelivered()
            } else {
                if (prefs.keepClipboard) copyToClipboard(this@OrpheusAccessibilityService, t.text)
                bubble?.flashSuccess()
                toast("Copied to clipboard")
            }
        }
    }

    private fun onDelivered() {
        bubble?.flashSuccess()
        Haptics.success(this)
    }

    /**
     * Puts [text] at the cursor of the focused field. Default order: paste
     * (respects the app's own editor), then a cursor-aware rewrite of the
     * field text. With "keep my clipboard" the rewrite goes first and paste
     * is the fallback, copying only at that point.
     */
    private fun insertIntoFocusedField(text: String): Boolean {
        val node = findFocusedEditable()
        if (node == null) {
            Log.i(TAG, "paste: no focused editable (active=${rootInActiveWindow?.packageName}, windows=${windows.size})")
            return false
        }
        Log.i(TAG, "paste: target=${node.packageName}/${node.className}")
        return if (prefs.keepClipboard) {
            rewriteAtCursor(node, text) || run {
                copyToClipboard(this, text)
                node.performAction(AccessibilityNodeInfo.ACTION_PASTE)
            }
        } else {
            node.performAction(AccessibilityNodeInfo.ACTION_PASTE) || run {
                Log.i(TAG, "paste: ACTION_PASTE refused, trying set-text")
                rewriteAtCursor(node, text)
            }
        }
    }

    /** ACTION_SET_TEXT with the dictation spliced in at the selection, cursor left after it. */
    private fun rewriteAtCursor(node: AccessibilityNodeInfo, text: String): Boolean {
        val existing = if (node.isShowingHintText) "" else (node.text?.toString() ?: "")
        val start = node.textSelectionStart.let { if (it < 0) existing.length else it.coerceAtMost(existing.length) }
        val end = node.textSelectionEnd.let { if (it < 0) start else it.coerceIn(start, existing.length) }
        val combined = existing.substring(0, start) + text + existing.substring(end)
        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, combined)
        }
        val ok = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        if (!ok) {
            Log.i(TAG, "paste: ACTION_SET_TEXT refused")
            return false
        }
        val caret = start + text.length
        val sel = Bundle().apply {
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, caret)
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, caret)
        }
        runCatching { node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, sel) }
        return true
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
