package com.cocakova.orpheus

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder

/**
 * A quiet foreground service that does nothing but exist. Its job is
 * priority: while it runs, Android (and Samsung's app sleeper in particular)
 * treats the process as foreground work and leaves the accessibility
 * service, and therefore the orb, alone. The notification sits collapsed
 * at minimum importance, off the lock screen.
 *
 * Started by the accessibility service on connect when the "stay running"
 * preference is on, stopped when the service goes away or the user turns it off.
 */
class KeepAliveService : Service() {

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        running = true
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL, "Background", NotificationManager.IMPORTANCE_MIN).apply {
                description = "Keeps the orb available while you type. No sound, no badge."
                setShowBadge(false)
            }
        )
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notif = Notification.Builder(this, CHANNEL)
            .setContentTitle("Orpheus is on")
            .setContentText("The orb appears whenever you type. Tap to open.")
            .setSmallIcon(R.drawable.ic_stat_orpheus)
            .setContentIntent(open)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setVisibility(Notification.VISIBILITY_SECRET)
            .setCategory(Notification.CATEGORY_SERVICE)
            .build()
        val ok = runCatching {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(NOTIF_ID, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            } else {
                startForeground(NOTIF_ID, notif)
            }
        }.onFailure {
            ServiceHealth.log(this, ServiceHealth.KEEPALIVE_FAILED, "${it.javaClass.simpleName}: ${it.message}")
        }.isSuccess
        if (!ok) {
            running = false
            stopSelf()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onDestroy() {
        running = false
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "orpheus_keepalive"
        private const val NOTIF_ID = 2

        /** True while the service instance is alive in this process. */
        @Volatile var running = false
            private set

        fun start(ctx: Context): Boolean = runCatching {
            ctx.startForegroundService(Intent(ctx, KeepAliveService::class.java))
        }.onFailure {
            ServiceHealth.log(ctx, ServiceHealth.KEEPALIVE_FAILED, "start: ${it.javaClass.simpleName}: ${it.message}")
        }.isSuccess

        fun stop(ctx: Context) {
            runCatching { ctx.stopService(Intent(ctx, KeepAliveService::class.java)) }
        }
    }
}
