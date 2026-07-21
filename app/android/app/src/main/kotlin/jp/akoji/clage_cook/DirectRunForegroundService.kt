package jp.akoji.clage_cook

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

class DirectRunForegroundService : Service() {
    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val acknowledgementId = intent?.getStringExtra(EXTRA_ACKNOWLEDGEMENT_ID)
        val operation = intent?.getStringExtra(EXTRA_OPERATION) ?: "conference"
        try {
            val notification = buildNotification(operation)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                startForeground(
                    NOTIFICATION_ID,
                    notification,
                    ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC,
                )
            } else {
                startForeground(NOTIFICATION_ID, notification)
            }
            if (!acknowledge(acknowledgementId, null)) {
                stopForeground(STOP_FOREGROUND_REMOVE)
                stopSelf(startId)
            }
        } catch (error: RuntimeException) {
            acknowledge(acknowledgementId, error)
            stopSelf(startId)
        }
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onTaskRemoved(rootIntent: Intent?) {
        // The Flutter engine belongs to the app task. Do not leave a native
        // notification/service orphan after the user explicitly swipes it away.
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
        super.onTaskRemoved(rootIntent)
    }

    override fun onTimeout(startId: Int, fgsType: Int) {
        // Android 15+ removes foreground status before this callback. Stop
        // within the platform grace period instead of risking a process crash.
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf(startId)
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val channel = NotificationChannel(
            NOTIFICATION_CHANNEL_ID,
            "Direct BYOK実行",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "AI会議の実行中に通信を維持します"
            setShowBadge(false)
        }
        getSystemService(NotificationManager::class.java)
            .createNotificationChannel(channel)
    }

    private fun buildNotification(operation: String): Notification {
        val openApp = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            openApp,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val detail = if (operation == "regeneration") {
            "回答を再生成しています"
        } else {
            "AI会議を実行しています"
        }
        val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            Notification.Builder(this, NOTIFICATION_CHANNEL_ID)
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return builder
            .setSmallIcon(R.mipmap.launcher_icon)
            .setContentTitle("Clage Cook")
            .setContentText(detail)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setCategory(Notification.CATEGORY_SERVICE)
            .build()
    }

    companion object {
        private const val ACTION_START = "jp.akoji.clage_cook.action.START_DIRECT_RUN_GUARD"
        private const val EXTRA_OPERATION = "operation"
        private const val EXTRA_ACKNOWLEDGEMENT_ID = "acknowledgement_id"

        private const val NOTIFICATION_CHANNEL_ID = "direct_byok_runs"
        private const val NOTIFICATION_ID = 2401

        private val startAcknowledgements =
            ConcurrentHashMap<String, (RuntimeException?) -> Unit>()

        fun start(
            context: android.content.Context,
            operation: String,
            callback: (RuntimeException?) -> Unit,
        ) {
            val acknowledgementId = UUID.randomUUID().toString()
            startAcknowledgements[acknowledgementId] = callback
            val intent = Intent(context, DirectRunForegroundService::class.java).apply {
                action = ACTION_START
                putExtra(EXTRA_OPERATION, operation)
                putExtra(EXTRA_ACKNOWLEDGEMENT_ID, acknowledgementId)
            }
            try {
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(intent)
                } else {
                    context.startService(intent)
                }
            } catch (error: RuntimeException) {
                acknowledge(acknowledgementId, error)
            }
            Handler(Looper.getMainLooper()).postDelayed(
                {
                    val timeoutError = IllegalStateException(
                        "Foreground service start acknowledgement timed out.",
                    )
                    if (acknowledge(acknowledgementId, timeoutError)) {
                        context.stopService(
                            Intent(context, DirectRunForegroundService::class.java),
                        )
                    }
                },
                5_500L,
            )
        }

        private fun acknowledge(
            acknowledgementId: String?,
            error: RuntimeException?,
        ): Boolean {
            if (acknowledgementId == null) return false
            val callback = startAcknowledgements.remove(acknowledgementId) ?: return false
            callback.invoke(error)
            return true
        }
    }
}
