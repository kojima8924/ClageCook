package jp.akoji.clage_cook

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(
            flutterEngine.dartExecutor.binaryMessenger,
            DIRECT_RUN_GUARD_CHANNEL,
        ).setMethodCallHandler { call, result ->
            when (call.method) {
                "start" -> {
                    val operation = call.argument<String>("operation") ?: "conference"
                    requestNotificationPermissionOnce()
                    DirectRunForegroundService.start(
                        applicationContext,
                        operation,
                    ) { error ->
                        if (error == null) {
                            result.success(null)
                        } else {
                            result.error(
                                "foreground_service_start_failed",
                                "Android could not start the Direct BYOK run guard.",
                                null,
                            )
                        }
                    }
                }

                "stop" -> {
                    try {
                        applicationContext.stopService(
                            android.content.Intent(
                                applicationContext,
                                DirectRunForegroundService::class.java,
                            ),
                        )
                        result.success(null)
                    } catch (error: RuntimeException) {
                        result.error(
                            "foreground_service_stop_failed",
                            "Android could not stop the Direct BYOK run guard.",
                            null,
                        )
                    }
                }

                else -> result.notImplemented()
            }
        }
    }

    private fun requestNotificationPermissionOnce() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val preferences = getSharedPreferences(
            NOTIFICATION_PERMISSION_PREFERENCES,
            MODE_PRIVATE,
        )
        if (preferences.getBoolean(NOTIFICATION_PERMISSION_REQUESTED, false)) return
        preferences.edit().putBoolean(NOTIFICATION_PERMISSION_REQUESTED, true).apply()
        requestPermissions(
            arrayOf(Manifest.permission.POST_NOTIFICATIONS),
            NOTIFICATION_PERMISSION_REQUEST_CODE,
        )
    }

    private companion object {
        const val DIRECT_RUN_GUARD_CHANNEL = "jp.akoji.clage_cook/direct_run_guard"
        const val NOTIFICATION_PERMISSION_PREFERENCES = "direct_run_guard"
        const val NOTIFICATION_PERMISSION_REQUESTED = "notification_permission_requested"
        const val NOTIFICATION_PERMISSION_REQUEST_CODE = 2401
    }
}
