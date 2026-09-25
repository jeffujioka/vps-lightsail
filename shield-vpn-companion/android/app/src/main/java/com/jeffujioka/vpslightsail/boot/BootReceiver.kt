package com.jeffujioka.vpslightsail.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.jeffujioka.vpslightsail.VpnControlService

class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED &&
            intent.action != "android.intent.action.LOCKED_BOOT_COMPLETED"
        ) return

        Log.d(TAG, "Boot: starting VpnControlService")
        context.startForegroundService(Intent(context, VpnControlService::class.java))
    }

    companion object {
        private const val TAG = "BootReceiver"
    }
}
