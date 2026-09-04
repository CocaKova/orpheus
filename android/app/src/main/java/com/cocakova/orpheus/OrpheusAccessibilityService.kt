package com.cocakova.orpheus

import android.accessibilityservice.AccessibilityService
import android.animation.ValueAnimator
import android.app.KeyguardManager
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.content.res.Configuration
import android.graphics.PixelFormat
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import android.view.Gravity
import android.view.WindowInsets
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.view.accessibility.AccessibilityWindowInfo
import android.view.animation.AccelerateInterpolator
import android.view.animation.DecelerateInterpolator
import android.view.animation.OvershootInterpolator
import android.widget.Toast
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/**
 * Floats the Orpheus orb over whatever you are typing into. Tap / hold the
 * orb to dictate; the transcript is inserted at the cursor of the focused
 * field (paste, or a clipboard-free rewrite when the user prefers), spaced
 * to fit the text around it. The text around the cursor and the app name
 * travel with the audio so the server can match casing and tone.
 *
 * Staying put is the whole point, so the orb is built to not go missing:
 *
 *  - ONE overlay window for the life of the service. Showing and hiding is
 *    alpha + touchability, never add/remove, so there is no window to fail
 *    to re-add and no half-finished animation to strand it.
 *  - THREE signals say "you're typing": an IME window in the window list,
 *    IME insets reported to our own window, and an editable field holding
 *    input focus. Any one shows the orb; hiding needs all of them gone and
 *    a short grace period, so a transient glitch in one never blinks it out.
 *  - Re-checks after every event and a slow heartbeat while the screen is
 *    on, so a missed or coalesced event cannot leave the orb hidden.
 *  - The orb belongs to whatever you are typing into, so it is gone whenever
 *    there is no such thing: screen off, or the keyguard up. An accessibility
 *    overlay draws above the lock screen, and the lock screen's own password
 *    field answers "yes" to every keyboard signal we have, so this is a
 *    check, not a side effect of the others.
 *  - Nothing in the event path is allowed to throw: an exception here kills
 *    the process and the orb with it.
 *  - A quiet keep-alive foreground service (opt-out) holds the process at
 *    foreground priority so app sleepers leave it alone. Every connect,
 *    disconnect and failure lands in [ServiceHealth] for the dashboard.
 *
 * A failed upload keeps the recording: the ring turns amber, a tap retries,
 * a hold records fresh. Nothing dictated is ever thrown away by a hiccup.
 */
private const val TAG = "Orpheus"

class OrpheusAccessibilityService : AccessibilityService() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var windowManager: WindowManager
    private var keyguard: KeyguardManager? = null
    private lateinit var prefs: Prefs
    private lateinit var log: TranscriptLog

    private var bubble: BubbleView? = null
    private var params: WindowManager.LayoutParams? = null
    private var attached = false
    private var attachAttempts = 0
    private var shown = false
    private var state = BubbleState.IDLE
    private var recorder: WavRecorder? = null
    private var dragStartX = 0
    private var dragStartY = 0
    private var snapAnimator: ValueAnimator? = null

    // keyboard signals, each refreshed independently
    private var imeWindow = false
    private var imeInsets = false
    private var editableFocused = false
    private var screenOn = true
    private var lastKeepAliveTry = 0L
    private var screenReceiver: BroadcastReceiver? = null

    override fun onServiceConnected() {
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager
        keyguard = getSystemService(KeyguardManager::class.java)
        prefs = Prefs(this)
        log = TranscriptLog(this)
        instance = this
        connectedSince = System.currentTimeMillis()
        ServiceHealth.log(this, ServiceHealth.CONNECTED, "sdk ${Build.VERSION.SDK_INT}")
        screenOn = runCatching { (getSystemService(Context.POWER_SERVICE) as PowerManager).isInteractive }
            .getOrDefault(true)
        if (bubble == null) createBubble()
        attachBubble()
        registerScreenReceiver()
        applyKeepAlive()
        evaluate("connected")
        handler.removeCallbacks(heartbeat)
        handler.postDelayed(heartbeat, HEARTBEAT_MS)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Never let this throw: an exception here kills the process and the orb.
        runCatching {
            evaluate("event")
            scheduleSettle()
        }.onFailure { Log.w(TAG, "event handling failed: $it") }
    }

    override fun onInterrupt() {}

    override fun onUnbind(intent: Intent?): Boolean {
        ServiceHealth.log(this, ServiceHealth.UNBOUND)
        return super.onUnbind(intent)
    }

    override fun onLowMemory() {
        ServiceHealth.log(this, ServiceHealth.LOW_MEMORY)
        super.onLowMemory()
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        // rotation / fold: keep the orb on the screen that now exists
        val p = params ?: return
        p.x = clampX(p.x)
        p.y = clampY(p.y)
        if (attached) runCatching { windowManager.updateViewLayout(bubble, p) }
    }

    override fun onDestroy() {
        ServiceHealth.log(this, ServiceHealth.DESTROYED, "uptime ${(System.currentTimeMillis() - connectedSince) / 1000}s")
        connectedSince = 0L
        instance = null
        handler.removeCallbacksAndMessages(null)
        screenReceiver?.let { runCatching { unregisterReceiver(it) } }
        screenReceiver = null
        recorder?.cancel()
        recorder = null
        snapAnimator?.cancel()
        if (attached) runCatching { windowManager.removeView(bubble) }
        attached = false
        KeepAliveService.stop(this)
        scope.cancel()
        super.onDestroy()
    }

    // -------- bubble window --------

    private val density get() = resources.displayMetrics.density
    private val orbMargin get() = ((BubbleView.VIEW_DP * (1f - BubbleView.ORB_FRACTION)) / 2f * density).toInt()

    private fun screenWidth(): Int = if (Build.VERSION.SDK_INT >= 30) {
        runCatching { windowManager.currentWindowMetrics.bounds.width() }.getOrDefault(resources.displayMetrics.widthPixels)
    } else resources.displayMetrics.widthPixels

    private fun screenHeight(): Int = if (Build.VERSION.SDK_INT >= 30) {
        runCatching { windowManager.currentWindowMetrics.bounds.height() }.getOrDefault(resources.displayMetrics.heightPixels)
    } else resources.displayMetrics.heightPixels

    private fun createBubble() {
        val size = (BubbleView.VIEW_DP * density).toInt()
        val p = WindowManager.LayoutParams(
            size, size,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            BASE_FLAGS or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE, // starts hidden
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = if (prefs.bubbleX >= 0) prefs.bubbleX else rightEdgeX(size)
            y = if (prefs.bubbleY >= 0) prefs.bubbleY else (screenHeight() * 0.52f).toInt()
        }
        params = p
        val b = BubbleView(
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
                if (attached) runCatching { windowManager.updateViewLayout(bubble, p) }
            },
            onDragEnd = {
                if (prefs.snapToEdge) snapToEdge() else savePosition()
            },
        ).apply {
            pending = PendingTake.exists
            hidden = true
            alpha = 0f
            scaleX = 0.6f
            scaleY = 0.6f
        }
        if (Build.VERSION.SDK_INT >= 30) {
            // The window manager tells every window whether the IME is up. Free second opinion.
            b.setOnApplyWindowInsetsListener { _, insets ->
                val visible = runCatching { insets.isVisible(WindowInsets.Type.ime()) }.getOrDefault(false)
                if (visible != imeInsets) {
                    imeInsets = visible
                    runCatching { evaluate("insets") }
                }
                insets
            }
        }
        bubble = b
    }

    /** Adds the one overlay window; retries with backoff if the token isn't ready yet. */
    private fun attachBubble() {
        val b = bubble ?: return
        if (attached) return
        runCatching { windowManager.addView(b, params) }
            .onSuccess {
                attached = true
                attachAttempts = 0
            }
            .onFailure { e ->
                if (e is IllegalStateException && e.message?.contains("already") == true) {
                    attached = true
                    return
                }
                attachAttempts++
                ServiceHealth.log(this, ServiceHealth.ATTACH_FAILED, "try $attachAttempts: ${e.javaClass.simpleName}: ${e.message}")
                if (attachAttempts <= ATTACH_RETRIES) {
                    handler.postDelayed({ attachBubble(); if (attached) evaluate("attached") }, ATTACH_RETRY_MS * attachAttempts)
                }
            }
    }

    private fun setTouchable(touchable: Boolean) {
        val p = params ?: return
        val want = if (touchable) BASE_FLAGS else BASE_FLAGS or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
        if (p.flags == want) return
        p.flags = want
        if (attached) runCatching { windowManager.updateViewLayout(bubble, p) }
    }

    /** Keep the whole bubble on screen — losing it off an edge means losing dictation. */
    private fun clampX(x: Int): Int {
        val size = params?.width ?: 0
        return x.coerceIn(0, (screenWidth() - size).coerceAtLeast(0))
    }

    private fun clampY(y: Int): Int {
        val size = params?.height ?: 0
        return y.coerceIn(0, (screenHeight() - size).coerceAtLeast(0))
    }

    /** The orb (not its glow margin) sits 8dp off the right edge. */
    private fun rightEdgeX(size: Int) = screenWidth() - size + orbMargin - (8 * density).toInt()

    /**
     * Left edge keeps a wider inset: the system back gesture owns the first
     * ~20dp and a drag that starts there goes to Android, not the orb.
     */
    private fun leftEdgeX() = (24 * density).toInt() - orbMargin

    private fun snapToEdge() {
        val p = params ?: return
        val size = p.width
        val centre = p.x + size / 2
        val target = clampX(if (centre < screenWidth() / 2) leftEdgeX() else rightEdgeX(size))
        prefs.bubbleX = target // the saved spot is the settled one
        prefs.bubbleY = p.y
        if (target == p.x) return
        snapAnimator?.cancel()
        snapAnimator = ValueAnimator.ofInt(p.x, target).apply {
            duration = 180L
            interpolator = DecelerateInterpolator()
            addUpdateListener {
                p.x = it.animatedValue as Int
                if (attached) runCatching { windowManager.updateViewLayout(bubble, p) }
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

    // -------- keyboard signals + visibility --------

    private fun imeWindowPresent(): Boolean = runCatching {
        windows.any { it.type == AccessibilityWindowInfo.TYPE_INPUT_METHOD }
    }.getOrDefault(false)

    /** One IPC into the active window, cheap enough for the heartbeat; insertion does the fuller search. */
    private fun editableHasFocus(): Boolean = runCatching {
        rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT)?.isEditable == true
    }.getOrDefault(false)

    /**
     * Re-reads the signals and shows or (after a grace period) hides the orb.
     * Cheap enough to call on every event and from the heartbeat.
     */
    private fun evaluate(reason: String) {
        if (bubble == null) return
        if (!available()) {
            // Nothing to type into, and no reason to read the keyguard's nodes.
            cancelPendingHide()
            if (shown) hide()
            return
        }
        imeWindow = imeWindowPresent()
        // Only ask the app about focus when the window list says no keyboard —
        // it is an IPC into the focused app, not worth doing when we already know.
        editableFocused = if (imeWindow) true else editableHasFocus()
        val want = wantVisible()
        if (want) {
            if (hidePending) {
                hidePending = false
                handler.removeCallbacks(hideRunnable)
            }
            if (!shown) show(reason)
        } else if (shown && !hidePending) {
            hidePending = true
            handler.postDelayed(hideRunnable, HIDE_GRACE_MS)
        }
    }

    /** Is there a screen, unlocked, for the orb to belong to? */
    private fun available(): Boolean =
        screenOn && runCatching { keyguard?.isKeyguardLocked != true }.getOrDefault(true)

    private fun wantVisible(): Boolean = when {
        !available() -> false // the lock screen is not ours to decorate
        state != BubbleState.IDLE -> true // never vanish mid-take
        imeWindow -> true
        editableFocused && (imeInsets || prefs.followCursor) -> true
        else -> false
    }

    private var hidePending = false

    private fun cancelPendingHide() {
        if (!hidePending) return
        hidePending = false
        handler.removeCallbacks(hideRunnable)
    }

    private val hideRunnable = Runnable {
        hidePending = false
        if (!available()) { hide(); return@Runnable }
        // re-check before acting: the glitch that started this may already be over
        imeWindow = imeWindowPresent()
        editableFocused = if (imeWindow) true else editableHasFocus()
        if (!wantVisible()) hide() else Log.i(TAG, "orb: hide cancelled on re-check")
    }

    private val settleRunnable = Runnable { runCatching { evaluate("settle") } }
    private val settleLateRunnable = Runnable { runCatching { evaluate("settle-late") } }

    /** Events describe transitions in flight; look again once things have landed. */
    private fun scheduleSettle() {
        handler.removeCallbacks(settleRunnable)
        handler.removeCallbacks(settleLateRunnable)
        handler.postDelayed(settleRunnable, SETTLE_MS)
        handler.postDelayed(settleLateRunnable, SETTLE_LATE_MS)
    }

    private val heartbeat = object : Runnable {
        override fun run() {
            if (screenOn) {
                runCatching { evaluate("heartbeat") }
                if (prefs.keepAlive && !KeepAliveService.running) applyKeepAlive()
            }
            handler.postDelayed(this, HEARTBEAT_MS)
        }
    }

    private fun show(reason: String) {
        val b = bubble ?: return
        if (!attached) attachBubble()
        if (!attached) return
        shown = true
        Log.i(TAG, "orb: show ($reason) ime=$imeWindow insets=$imeInsets editable=$editableFocused")
        // re-clamp the saved spot: rotation/display changes can strand it off-screen
        params?.let { p ->
            val nx = clampX(p.x); val ny = clampY(p.y)
            if (nx != p.x || ny != p.y) { p.x = nx; p.y = ny; runCatching { windowManager.updateViewLayout(b, p) } }
        }
        b.pending = PendingTake.exists
        b.hidden = false
        setTouchable(true)
        b.animate().cancel()
        b.animate().alpha(1f).scaleX(1f).scaleY(1f)
            .setDuration(if (b.alpha < 0.05f) 220L else 160L)
            .setInterpolator(OvershootInterpolator(1.4f))
            .withEndAction(null)
            .start()
    }

    private fun hide() {
        val b = bubble ?: return
        if (!shown) return
        shown = false
        Log.i(TAG, "orb: hide ime=$imeWindow insets=$imeInsets editable=$editableFocused")
        setTouchable(false)
        b.animate().cancel()
        b.animate().alpha(0f).scaleX(0.6f).scaleY(0.6f)
            .setDuration(160L)
            .setInterpolator(AccelerateInterpolator())
            .withEndAction { if (!shown) b.hidden = true } // stop drawing once faded
            .start()
    }

    private fun setState(s: BubbleState) {
        state = s
        bubble?.state = s
        runCatching { evaluate("state") }
    }

    private fun registerScreenReceiver() {
        if (screenReceiver != null) return
        val r = object : BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                when (intent?.action) {
                    Intent.ACTION_SCREEN_OFF -> {
                        screenOn = false
                        // The orb is out of reach behind the lock screen, so a take
                        // in flight would have no way to be stopped: end it here and
                        // let the usual path transcribe and keep it.
                        if (state == BubbleState.RECORDING) runCatching { finishRecording() }
                        runCatching { evaluate("screen-off") }
                    }
                    Intent.ACTION_SCREEN_ON, Intent.ACTION_USER_PRESENT -> {
                        screenOn = true
                        runCatching { evaluate("screen-on") }
                        scheduleSettle()
                    }
                }
            }
        }
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_OFF)
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_USER_PRESENT)
        }
        runCatching {
            if (Build.VERSION.SDK_INT >= 33) registerReceiver(r, filter, Context.RECEIVER_EXPORTED) // protected broadcasts: only the system can send these
            else registerReceiver(r, filter)
            screenReceiver = r
        }
    }

    /** Start or stop the keep-alive service to match the preference. Safe to call often. */
    fun applyKeepAlive() {
        if (!prefs.keepAlive) {
            KeepAliveService.stop(this)
            return
        }
        if (KeepAliveService.running) return
        val now = SystemClock.uptimeMillis()
        if (now - lastKeepAliveTry < KEEPALIVE_RETRY_MS) return
        lastKeepAliveTry = now
        KeepAliveService.start(this)
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
        val ctx = if (prefs.sendContext) runCatching { captureContext() }.getOrDefault(DictationContext.NONE)
            else DictationContext.NONE
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
                if (t.text.isBlank()) toast("Heard nothing")
                else runCatching { deliver(t, ctx) }.onFailure { e ->
                    // the transcript is safe on the clipboard; do not let delivery take the orb down
                    Log.w(TAG, "delivery failed: $e")
                    copyToClipboard(this@OrpheusAccessibilityService, t.text)
                    bubble?.flashFailure()
                    toast("Couldn't insert. Copied to clipboard.")
                }
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
            val ok = runCatching { insertIntoFocusedField(text) }.getOrDefault(false)
            if (ok) {
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
        runCatching { Toast.makeText(this, msg, Toast.LENGTH_SHORT).show() }
    }

    companion object {
        /** Wall-clock time this process's service connected; 0 when it isn't. Read by the dashboard. */
        @Volatile var connectedSince: Long = 0L
            private set

        /** The live service, for the dashboard to poke (keep-alive toggle). */
        @Volatile var instance: OrpheusAccessibilityService? = null
            private set

        private const val BASE_FLAGS = WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
        private const val HIDE_GRACE_MS = 350L      // a blip in the signals must outlast this to hide the orb
        private const val SETTLE_MS = 300L          // re-check after an event, once the transition lands
        private const val SETTLE_LATE_MS = 1200L    // and again, for slow keyboards
        private const val HEARTBEAT_MS = 2500L      // background re-check while the screen is on
        private const val ATTACH_RETRIES = 6
        private const val ATTACH_RETRY_MS = 600L
        private const val KEEPALIVE_RETRY_MS = 30_000L
    }
}
