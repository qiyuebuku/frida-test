package com.example.screenshotassistant.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.example.screenshotassistant.capture.Actions

class CaptureCommandReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val actionId = intent.getStringExtra("action") ?: return
        Log.d("CaptureCommand", "received action=$actionId")

        val action = Actions.ALL.find { it.id == actionId }
        if (action == null) {
            Log.e("CaptureCommand", "unknown action: $actionId")
            return
        }

        val service = FloatingWindowService.instance
        if (service == null) {
            Log.e("CaptureCommand", "FloatingWindowService not running")
            return
        }

        service.executeActionFromBroadcast(action)
    }
}
