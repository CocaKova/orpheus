package com.cocakova.orpheus

import android.content.Context
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration

enum class BubbleState { IDLE, RECORDING, TRANSCRIBING }

/**
 * The floating Orpheus bubble. Plain Canvas view (Compose in an overlay window
 * needs lifecycle-owner plumbing that isn't worth it for one circle).
 *
 * Gestures: tap toggles recording, press-and-hold records while held
 * (release = stop), drag moves the bubble.
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

    private val bgPaint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val ringPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE }
    private val barPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply { strokeCap = Paint.Cap.ROUND }
    private val arcPaint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
    }
    private val arcRect = RectF()

    override fun onDraw(canvas: Canvas) {
        val cx = width / 2f
        val cy = height / 2f
        val radius = minOf(width, height) / 2f - dp(2f)

        val accent = if (state == BubbleState.RECORDING) 0xFFFF5252.toInt() else 0xFFB39DFF.toInt()
        bgPaint.color = 0xEE201A33.toInt()
        canvas.drawCircle(cx, cy, radius, bgPaint)
        ringPaint.color = accent
        ringPaint.strokeWidth = dp(1.5f)
        canvas.drawCircle(cx, cy, radius, ringPaint)

        if (state == BubbleState.TRANSCRIBING) {
            val inset = radius * 0.5f
            arcRect.set(cx - radius + inset, cy - radius + inset, cx + radius - inset, cy + radius - inset)
            arcPaint.color = accent
            arcPaint.strokeWidth = dp(2.5f)
            val rot = (System.currentTimeMillis() % 1000L) / 1000f * 360f
            canvas.drawArc(arcRect, rot, 110f, false, arcPaint)
            postInvalidateOnAnimation()
            return
        }

        barPaint.color = accent
        barPaint.strokeWidth = dp(3f)
        val gap = dp(5.5f)
        val startX = cx - gap * (BAR_COUNT - 1) / 2f
        for (i in 0 until BAR_COUNT) {
            val level = if (state == BubbleState.RECORDING) {
                0.18f + 0.82f * levels[(levelHead + i) % BAR_COUNT]
            } else {
                IDLE_HEIGHTS[i]
            }
            val half = radius * 0.52f * level
            val x = startX + i * gap
            canvas.drawLine(x, cy - half, x, cy + half, barPaint)
        }
        if (state == BubbleState.RECORDING) postInvalidateOnAnimation()
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
                postDelayed(holdRunnable, HOLD_TIMEOUT_MS)
                return true
            }
            MotionEvent.ACTION_MOVE -> {
                val dx = event.rawX - downRawX
                val dy = event.rawY - downRawY
                if (!dragging && !holdFired && dx * dx + dy * dy > slop.toFloat() * slop) {
                    dragging = true
                    removeCallbacks(holdRunnable)
                    onDragStart()
                }
                if (dragging) onDragTo(dx.toInt(), dy.toInt())
                return true
            }
            MotionEvent.ACTION_UP -> {
                removeCallbacks(holdRunnable)
                when {
                    dragging -> onDragEnd()
                    holdFired -> onHoldEnd()
                    else -> onTap()
                }
                return true
            }
            MotionEvent.ACTION_CANCEL -> {
                removeCallbacks(holdRunnable)
                if (dragging) onDragEnd() else if (holdFired) onHoldEnd()
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
    }
}
