package com.cocakova.orpheus

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder

/**
 * Short-lived microphone foreground service, alive only while the bubble is
 * recording — keeps the mic feed unrestricted on Android 12+ while the app
 * has no visible activity.
 */
class RecordingForegroundService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Dictation", NotificationManager.IMPORTANCE_LOW)
        )
        val notif = Notification.Builder(this, CHANNEL)
            .setContentTitle("Orpheus is listening")
            .setSmallIcon(R.drawable.ic_stat_orpheus)
            .setOngoing(true)
            .build()
        // A refused promotion must not throw out of here: that kills the process
        // and the orb with it. The recorder keeps going; the silence check
        // catches a muted mic ("Heard nothing") rather than a crash.
        runCatching {
            if (Build.VERSION.SDK_INT >= 30) {
                startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE)
            } else {
                startForeground(NOTIF_ID, notif)
            }
        }.onFailure {
            ServiceHealth.log(this, ServiceHealth.MIC_FGS_FAILED, "${it.javaClass.simpleName}: ${it.message}")
            stopSelf()
        }
        return START_NOT_STICKY
    }

    companion object {
        private const val CHANNEL = "orpheus_dictation"
        private const val NOTIF_ID = 1

        fun start(ctx: Context) {
            runCatching {
                ctx.startForegroundService(Intent(ctx, RecordingForegroundService::class.java))
            }
        }

        fun stop(ctx: Context) {
            ctx.stopService(Intent(ctx, RecordingForegroundService::class.java))
        }
    }
}
