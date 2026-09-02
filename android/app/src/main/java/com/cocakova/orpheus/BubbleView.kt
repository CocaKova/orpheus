package com.cocakova.orpheus

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RadialGradient
import android.graphics.RectF
import android.graphics.Shader
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import kotlin.math.exp
import kotlin.math.min
import kotlin.math.sin

enum class BubbleState { IDLE, RECORDING, TRANSCRIBING }

/**
 * The floating Orpheus orb. Plain Canvas view (Compose in an overlay window
 * needs lifecycle-owner plumbing that isn't worth it for one circle).
 *
 * Look: a glass orb — gradient body, off-centre highlight, thin accent ring
 * and a soft outer glow that swells with your voice. Five waveform bars are
 * the mark; they become a spinner while transcribing, a check mark on a
 * successful paste and a shake on failure. An amber ring means a failed take
 * is waiting to be retried.
 *
 * Gestures: tap toggles recording, press-and-hold records while held
 * (release = stop), drag moves the orb — in ANY state, including while
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
            lastInteractMs = SystemClock.uptimeMillis()
            postInvalidateOnAnimation()
        }

    /** A failed take is waiting: the ring turns amber until it is retried or dropped. */
    var pending: Boolean = false
        set(value) {
            field = value
            postInvalidateOnAnimation()
        }

    private enum class Feedback { NONE, SUCCESS, FAILURE }
    private var feedback = Feedback.NONE
    private var feedbackStartMs = 0L

    /** Green check: the text landed. */
    fun flashSuccess() = flash(Feedback.SUCCESS)

    /** Red shake: something went wrong. */
    fun flashFailure() = flash(Feedback.FAILURE)

    private fun flash(kind: Feedback) {
        feedback = kind
        feedbackStartMs = SystemClock.uptimeMillis()
        lastInteractMs = feedbackStartMs
        pulse = 1f
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
    private var lastInteractMs = SystemClock.uptimeMillis()
    private val barHeights = FloatArray(BAR_COUNT) { IDLE_HEIGHTS[it] }
    private var accent = COLOR_IDLE
    private var pressScale = 1f     // eased toward pressTarget
    private var pressTarget = 1f
    private var pulse = 0f          // decaying pop on state change
    private var spinDeg = 0f        // transcribing spinner, continuous
    private var arcPhase = 0f       // spinner sweep breathing
    private var idlePhase = 0f      // idle breathing
    private var glow = 0f           // outer glow strength, follows the voice
    private var dim = 1f            // whole-orb opacity, rests when untouched
    private var barsAlpha = 1f      // bars fade out under the check mark

    private val bodyPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val glowPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val highlightPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeCap = Paint.Cap.ROUND }
    private val arcPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val markPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
    }
    private val arcRect = RectF()
    private val highlightRect = RectF()
    private val checkPath = Path()

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

    private fun withAlpha(color: Int, alpha: Float): Int =
        (color and 0x00FFFFFF) or ((alpha.coerceIn(0f, 1f) * 255).toInt() shl 24)

    override fun onDraw(canvas: Canvas) {
        val now = SystemClock.uptimeMillis()
        val dt = if (lastFrameMs == 0L) 16f else min((now - lastFrameMs).toFloat(), 64f)
        lastFrameMs = now

        // ---- feedback lifetime ----
        val fbAge = now - feedbackStartMs
        if (feedback != Feedback.NONE && fbAge > FEEDBACK_MS) feedback = Feedback.NONE
        val fbProgress = (fbAge / FEEDBACK_MS.toFloat()).coerceIn(0f, 1f)

        // ---- advance animated values ----
        val accentTarget = when {
            feedback == Feedback.SUCCESS -> COLOR_SUCCESS
            feedback == Feedback.FAILURE -> COLOR_FAILURE
            state == BubbleState.RECORDING -> COLOR_RECORDING
            pending -> COLOR_PENDING
            else -> COLOR_IDLE
        }
        accent = lerpColor(accent, accentTarget, ease(COLOR_TAU_MS, dt))
        pressScale += (pressTarget - pressScale) * ease(PRESS_TAU_MS, dt)
        pulse *= exp(-dt / PULSE_DECAY_MS)
        idlePhase += dt / 1000f
        val scale = pressScale + pulse * PULSE_GAIN

        // latest voice level drives the glow: quick swell, slow fade
        val latest = if (state == BubbleState.RECORDING) levels[(levelHead + BAR_COUNT - 1) % BAR_COUNT] else 0f
        val glowTarget = when {
            feedback != Feedback.NONE -> 0.9f * (1f - fbProgress)
            state == BubbleState.RECORDING -> 0.25f + 0.75f * min(1f, latest * 2.2f)
            state == BubbleState.TRANSCRIBING -> 0.35f + 0.2f * sin(arcPhase * ARC_BREATH_HZ * TWO_PI)
            else -> 0.10f + 0.06f * sin(idlePhase * BREATH_HZ * TWO_PI)
        }
        glow += (glowTarget - glow) * ease(if (glowTarget > glow) GLOW_ATTACK_MS else GLOW_RELEASE_MS, dt)

        // rest when nothing has happened for a while; wake on any touch or state
        val resting = state == BubbleState.IDLE && feedback == Feedback.NONE && !pending &&
            pressTarget == 1f && now - lastInteractMs > DIM_AFTER_MS
        dim += ((if (resting) DIM_ALPHA else 1f) - dim) * ease(DIM_TAU_MS, dt)

        val showMark = feedback == Feedback.SUCCESS
        barsAlpha += ((if (showMark) 0f else 1f) - barsAlpha) * ease(MARK_TAU_MS, dt)

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
        val cx = width / 2f + shakeOffset(fbProgress)
        val cy = height / 2f
        // the orb sits inside the view with room for the glow around it
        val outer = minOf(width, height) / 2f
        val radius = outer * ORB_FRACTION * scale

        val layer = canvas.saveLayerAlpha(0f, 0f, width.toFloat(), height.toFloat(), (dim * 255).toInt())

        // glow: accent, fading to nothing at the view edge
        val glowRadius = radius + (outer - radius) * (0.45f + 0.55f * glow)
        glowPaint.shader = RadialGradient(
            cx, cy, glowRadius,
            intArrayOf(withAlpha(accent, 0.55f * glow), withAlpha(accent, 0.18f * glow), withAlpha(accent, 0f)),
            floatArrayOf(radius / glowRadius * 0.92f, radius / glowRadius, 1f),
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, glowRadius, glowPaint)

        // body: lit from the upper left
        bodyPaint.shader = RadialGradient(
            cx - radius * 0.35f, cy - radius * 0.4f, radius * 1.6f,
            intArrayOf(COLOR_BODY_LIGHT, COLOR_BODY, COLOR_BODY_DARK),
            floatArrayOf(0f, 0.55f, 1f),
            Shader.TileMode.CLAMP,
        )
        canvas.drawCircle(cx, cy, radius, bodyPaint)

        // specular highlight
        highlightRect.set(
            cx - radius * 0.55f, cy - radius * 0.82f,
            cx + radius * 0.15f, cy - radius * 0.28f,
        )
        highlightPaint.shader = RadialGradient(
            highlightRect.centerX(), highlightRect.centerY(), highlightRect.width() * 0.6f,
            intArrayOf(0x38FFFFFF, 0x00FFFFFF), null, Shader.TileMode.CLAMP,
        )
        canvas.drawOval(highlightRect, highlightPaint)

        // ring
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
            if (barsAlpha > 0.02f) {
                barPaint.color = withAlpha(accent, barsAlpha)
                barPaint.strokeWidth = dp(3f)
                val gap = dp(5.5f) * scale
                val startX = cx - gap * (BAR_COUNT - 1) / 2f
                for (i in 0 until BAR_COUNT) {
                    val half = radius * 0.52f * barHeights[i]
                    val x = startX + i * gap
                    canvas.drawLine(x, cy - half, x, cy + half, barPaint)
                }
            }
            if (barsAlpha < 0.98f) {
                // check mark draws itself in over the first third of the flash
                val draw = (fbProgress / 0.35f).coerceIn(0f, 1f)
                markPaint.color = withAlpha(accent, 1f - barsAlpha)
                markPaint.strokeWidth = dp(3f)
                val s = radius * 0.5f
                checkPath.reset()
                checkPath.moveTo(cx - s * 0.7f, cy + s * 0.05f)
                val midX = cx - s * 0.2f
                val midY = cy + s * 0.5f
                val endX = cx + s * 0.75f
                val endY = cy - s * 0.45f
                if (draw < 0.5f) {
                    val t = draw / 0.5f
                    checkPath.lineTo(cx - s * 0.7f + (midX - (cx - s * 0.7f)) * t, cy + s * 0.05f + (midY - (cy + s * 0.05f)) * t)
                } else {
                    checkPath.lineTo(midX, midY)
                    val t = (draw - 0.5f) / 0.5f
                    checkPath.lineTo(midX + (endX - midX) * t, midY + (endY - midY) * t)
                }
                canvas.drawPath(checkPath, markPaint)
            }
        }
        canvas.restoreToCount(layer)
        // idle breathing, color fades, and the spinner all want the next frame
        postInvalidateOnAnimation()
    }

    /** Horizontal jitter for the failure flash, dying out over the first half. */
    private fun shakeOffset(progress: Float): Float {
        if (feedback != Feedback.FAILURE) return 0f
        val envelope = (1f - progress / 0.5f).coerceIn(0f, 1f)
        return sin(progress * 34f) * dp(3.5f) * envelope
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
        lastInteractMs = SystemClock.uptimeMillis()
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
        /** Window size the orb wants, dp. The orb itself is [ORB_FRACTION] of it; the rest is glow. */
        const val VIEW_DP = 76
        const val ORB_FRACTION = 0.79f

        private const val BAR_COUNT = 5
        private const val HOLD_TIMEOUT_MS = 350L
        private val IDLE_HEIGHTS = floatArrayOf(0.35f, 0.62f, 0.88f, 0.62f, 0.35f)

        private const val COLOR_IDLE = 0xFFB39DFF.toInt()
        private const val COLOR_RECORDING = 0xFFFF5252.toInt()
        private const val COLOR_PENDING = 0xFFFFC46B.toInt()
        private const val COLOR_SUCCESS = 0xFF5EE0A0.toInt()
        private const val COLOR_FAILURE = 0xFFFF5252.toInt()
        private const val COLOR_BODY_LIGHT = 0xFF3C3160.toInt()
        private const val COLOR_BODY = 0xF2201A33.toInt()
        private const val COLOR_BODY_DARK = 0xF2140F24.toInt()

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
        private const val GLOW_ATTACK_MS = 60f
        private const val GLOW_RELEASE_MS = 320f
        private const val FEEDBACK_MS = 800L
        private const val MARK_TAU_MS = 90f
        private const val DIM_AFTER_MS = 3500L
        private const val DIM_ALPHA = 0.72f
        private const val DIM_TAU_MS = 400f
    }
}
