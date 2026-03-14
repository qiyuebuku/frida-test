package com.example.screenshotassistant

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build

class ScreenshotAssistantApp : Application() {

    companion object {
        const val CHANNEL_ID_CAPTURE = "screen_capture"
        const val CHANNEL_ID_FLOATING = "floating_window"
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
    }

    private fun createNotificationChannels() {
        val captureChannel = NotificationChannel(
            CHANNEL_ID_CAPTURE,
            getString(R.string.notification_channel_name),
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = getString(R.string.notification_channel_description)
        }

        val floatingChannel = NotificationChannel(
            CHANNEL_ID_FLOATING,
            "悬浮窗服务",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "悬浮窗服务运行状态"
        }

        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(captureChannel)
        manager.createNotificationChannel(floatingChannel)
    }
}
