package com.cocakova.orpheus

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import kotlin.math.exp
import kotlin.math.min
import kotlin.math.sin

enum class BubbleState { IDLE, RECORDING, TRANSCRIBING }

/**
 * The floating Orpheus bubble. Plain Canvas view (Compose in an overlay window
 * needs lifecycle-owner plumbing that isn't worth it for one circle).
 *
 * Gestures: tap toggles recording, press-and-hold records while held
 * (release = stop), drag moves the bubble — in ANY state, including while
 * hold-recording (walk-and-talk: the orb follows your finger, release still
 * stops the take).
 *
 * All motion is time-based and framerate-independent: every animated value
 * eases toward its target with an exponential approach driven by the real
 * frame delta, so state changes crossfade instead of popping.
 */
class BubbleView(
    context: Context,
    private val onTap: () -> Unit,
    private val onHoldStart: () -> Unit,
    private val onHoldEnd: () -> Unit,
    private val onDragStart: () -> Unit,
    private val onDragTo: (dx: Int, dy: Int) -> Unit,
    private val onDragEnd: () -> Unit,
) : View(context) {

    var state: BubbleState = BubbleState.IDLE
        set(value) {
            if (field != value) pulse = 1f // small pop on every state change
            field = value
            postInvalidateOnAnimation()
        }

    private val levels = FloatArray(BAR_COUNT)
    private var levelHead = 0

    /** Thread-safe enough for a waveform: postInvalidateOnAnimation is safe off the UI thread. */
    fun pushAmplitude(a: Float) {
        levels[levelHead % BAR_COUNT] = a.coerceIn(0f, 1f)
        levelHead++
        postInvalidateOnAnimation()
    }

    // -------- animated values (eased every frame in onDraw) --------
    private var lastFrameMs = 0L
    private val barHeights = FloatArray(BAR_COUNT) { IDLE_HEIGHTS[it] }
    private var accent = COLOR_IDLE
    private var pressScale = 1f     // eased toward pressTarget
    private var pressTarget = 1f
    private var pulse = 0f          // decaying pop on state change
    private var spinDeg = 0f        // transcribing spinner, continuous
    private var arcPhase = 0f       // spinner sweep breathing
    private var idlePhase = 0f      // idle breathing

    private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeCap = Paint.Cap.ROUND }
    private val arcPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val arcRect = RectF()

    /** Exponential approach factor for a time constant tau (ms) at frame delta dt (ms). */
    private fun ease(tau: Float, dt: Float) = 1f - exp(-dt / tau)

    private fun lerpColor(from: Int, to: Int, f: Float): Int {
        val g = f.coerceIn(0f, 1f)
        return Color.argb(
            (Color.alpha(from) + (Color.alpha(to) - Color.alpha(from)) * g).toInt(),
            (Color.red(from) + (Color.red(to) - Color.red(from)) * g).toInt(),
            (Color.green(from) + (Color.green(to) - Color.green(from)) * g).toInt(),
            (Color.blue(from) + (Color.blue(to) - Color.blue(from)) * g).toInt(),
        )
    }

    override fun onDraw(canvas: Canvas) {
        val now = SystemClock.uptimeMillis()
        val dt = if (lastFrameMs == 0L) 16f else min((now - lastFrameMs).toFloat(), 64f)
        lastFrameMs = now

        // ---- advance animated values ----
        val accentTarget = if (state == BubbleState.RECORDING) COLOR_RECORDING else COLOR_IDLE
        accent = lerpColor(accent, accentTarget, ease(COLOR_TAU_MS, dt))
        pressScale += (pressTarget - pressScale) * ease(PRESS_TAU_MS, dt)
        pulse *= exp(-dt / PULSE_DECAY_MS)
        idlePhase += dt / 1000f
        val scale = pressScale + pulse * PULSE_GAIN

        for (i in 0 until BAR_COUNT) {
            val target = when (state) {
                BubbleState.RECORDING -> 0.18f + 0.82f * levels[(levelHead + i) % BAR_COUNT]
                else -> IDLE_HEIGHTS[i] *
                    (1f + BREATH_GAIN * sin(idlePhase * BREATH_HZ * TWO_PI + i * 0.9f))
            }
            // fast attack, slow release = liquid waveform
            val tau = if (target > barHeights[i]) BAR_ATTACK_MS else BAR_RELEASE_MS
            barHeights[i] += (target - barHeights[i]) * ease(tau, dt)
        }

        // ---- draw ----
        val cx = width / 2f
        val cy = height / 2f
        val radius = (minOf(width, height) / 2f - dp(2f)) * scale

        bgPaint.color = COLOR_BG
        canvas.drawCircle(cx, cy, radius, bgPaint)
        ringPaint.color = accent
        ringPaint.strokeWidth = dp(1.5f)
        canvas.drawCircle(cx, cy, radius, ringPaint)

        if (state == BubbleState.TRANSCRIBING) {
            spinDeg = (spinDeg + dt * SPIN_DEG_PER_MS) % 360f
            arcPhase += dt / 1000f
            val sweep = 95f + 45f * sin(arcPhase * ARC_BREATH_HZ * TWO_PI)
            val inset = radius * 0.5f
            arcRect.set(cx - radius + inset, cy - radius + inset, cx + radius - inset, cy + radius - inset)
            arcPaint.color = accent
            arcPaint.strokeWidth = dp(2.5f)
            canvas.drawArc(arcRect, spinDeg, sweep, false, arcPaint)
        } else {
            barPaint.color = accent
            barPaint.strokeWidth = dp(3f)
            val gap = dp(5.5f) * scale
            val startX = cx - gap * (BAR_COUNT - 1) / 2f
            for (i in 0 until BAR_COUNT) {
                val half = radius * 0.52f * barHeights[i]
                val x = startX + i * gap
                canvas.drawLine(x, cy - half, x, cy + half, barPaint)
            }
        }
        // idle breathing, color fades, and the spinner all want the next frame
        postInvalidateOnAnimation()
    }

    // -------- gestures --------
    private val slop = ViewConfiguration.get(context).scaledTouchSlop
    private var downRawX = 0f
    private var downRawY = 0f
    private var dragging = false
    private var holdFired = false
    private val holdRunnable = Runnable {
        if (!dragging) {
            holdFired = true
            onHoldStart()
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downRawX = event.rawX
                downRawY = event.rawY
                dragging = false
                holdFired = false
                pressTarget = PRESS_SCALE
                postDelayed(holdRunnable, HOLD_TIMEOUT_MS)
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = event.rawX - downRawX
                val dy = event.rawY - downRawY
                // drag engages past slop in EVERY state — even mid-hold-recording
                if (!dragging && dx * dx + dy * dy > slop.toFloat() * slop) {
                    dragging = true
                    removeCallbacks(holdRunnable)
                    onDragStart()
                }
                if (dragging) onDragTo(dx.toInt(), dy.toInt())
                return true
            }
            MotionEvent.ACTION_UP -> {
                removeCallbacks(holdRunnable)
                pressTarget = 1f
                if (dragging) onDragEnd()
                when {
                    holdFired -> onHoldEnd()
                    !dragging -> onTap()
                }
                return true
            }
            MotionEvent.ACTION_CANCEL -> {
                removeCallbacks(holdRunnable)
                pressTarget = 1f
                if (dragging) onDragEnd()
                if (holdFired) onHoldEnd()
                return true
            }
        }
        return super.onTouchEvent(event)
    }

    private fun dp(v: Float) = v * resources.displayMetrics.density

    companion object {
        private const val BAR_COUNT = 5
        private const val HOLD_TIMEOUT_MS = 350L
        private val IDLE_HEIGHTS = floatArrayOf(0.35f, 0.62f, 0.88f, 0.62f, 0.35f)

        private const val COLOR_IDLE = 0xFFB39DFF.toInt()
        private const val COLOR_RECORDING = 0xFFFF5252.toInt()
        private const val COLOR_BG = 0xEE201A33.toInt()

        private const val TWO_PI = (2 * Math.PI).toFloat()
        private const val COLOR_TAU_MS = 140f     // idle<->recording crossfade
        private const val PRESS_TAU_MS = 80f      // finger-down shrink response
        private const val PRESS_SCALE = 0.90f
        private const val PULSE_DECAY_MS = 220f   // state-change pop decay
        private const val PULSE_GAIN = 0.10f
        private const val BAR_ATTACK_MS = 45f
        private const val BAR_RELEASE_MS = 260f
        private const val BREATH_HZ = 0.30f       // idle breathing speed
        private const val BREATH_GAIN = 0.14f
        private const val SPIN_DEG_PER_MS = 0.36f // one revolution per second
        private const val ARC_BREATH_HZ = 0.75f   // spinner sweep breathing
    }
}
