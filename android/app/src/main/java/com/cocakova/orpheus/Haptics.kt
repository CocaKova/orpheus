package com.cocakova.orpheus

import android.content.Context
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/** Small confirmations you can feel without looking at the orb. */
object Haptics {
    private fun vibrator(ctx: Context): Vibrator? = runCatching {
        if (Build.VERSION.SDK_INT >= 31) {
            (ctx.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as VibratorManager).defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            ctx.getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
        }
    }.getOrNull()

    private fun play(ctx: Context, predefined: Int, fallbackMs: Long) {
        if (!Prefs(ctx).haptics) return
        val v = vibrator(ctx) ?: return
        if (!v.hasVibrator()) return
        val effect = if (Build.VERSION.SDK_INT >= 29) VibrationEffect.createPredefined(predefined)
        else VibrationEffect.createOneShot(fallbackMs, VibrationEffect.DEFAULT_AMPLITUDE)
        runCatching { v.vibrate(effect) }
    }

    fun recordStart(ctx: Context) = play(ctx, VibrationEffect.EFFECT_CLICK, 25)
    fun recordStop(ctx: Context) = play(ctx, VibrationEffect.EFFECT_DOUBLE_CLICK, 40)
    fun success(ctx: Context) = play(ctx, VibrationEffect.EFFECT_TICK, 15)
    fun failure(ctx: Context) = play(ctx, VibrationEffect.EFFECT_HEAVY_CLICK, 120)
}
