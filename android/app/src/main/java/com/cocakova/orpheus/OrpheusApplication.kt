package com.cocakova.orpheus

import android.app.Application

/**
 * Process-wide hooks. The only one that matters: an uncaught exception
 * anywhere in this process takes the accessibility service, and the orb,
 * down with it. Write it to the health log before Android kills us so the
 * dashboard can show what happened instead of the orb just being gone.
 */
class OrpheusApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, e ->
            runCatching {
                val where = e.stackTrace.firstOrNull { it.className.startsWith("com.cocakova.orpheus") }
                    ?: e.stackTrace.firstOrNull()
                ServiceHealth.log(
                    this, ServiceHealth.CRASH,
                    "${e.javaClass.simpleName}: ${e.message ?: ""} @ ${where?.fileName}:${where?.lineNumber} (${thread.name})"
                )
            }
            previous?.uncaughtException(thread, e)
        }
    }
}
