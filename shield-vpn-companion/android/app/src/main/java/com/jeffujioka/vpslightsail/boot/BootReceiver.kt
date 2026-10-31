package com.jeffujioka.vpslightsail.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.jeffujioka.vpslightsail.VpnControlService

/** Plain object (D12): a JVM test reads it without loading an Android class. */
object BootActions {
    val ALL: Set<String> = setOf(
        "android.intent.action.BOOT_COMPLETED",
        "android.intent.action.LOCKED_BOOT_COMPLETED",
        "android.intent.action.MY_PACKAGE_REPLACED",          // restore right after `adb install -r`
    )
    fun handles(action: String?): Boolean = action != null && action in ALL
}

/** Only starts the service; the restore itself is rule R1 in the service's onCreate (spec-04 §5.6). */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (!BootActions.handles(intent.action)) return
        Log.d(TAG, "${intent.action}: starting VpnControlService")
        context.startForegroundService(Intent(context, VpnControlService::class.java))
    }

    companion object {
        private const val TAG = "BootReceiver"
    }
}
