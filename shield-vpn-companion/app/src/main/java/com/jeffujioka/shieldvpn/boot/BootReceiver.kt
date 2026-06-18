package com.jeffujioka.shieldvpn.boot

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import com.jeffujioka.shieldvpn.persistence.ConfigStore
import com.jeffujioka.shieldvpn.vpn.WireGuardTunnel

class BootReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED &&
            intent.action != "android.intent.action.LOCKED_BOOT_COMPLETED"
        ) return

        val store = ConfigStore(context)
        val config = store.lastConfig

        if (config != null && store.tunnelEnabled) {
            Log.d(TAG, "Boot: restarting tunnel with saved config")
            val tunnelIntent = Intent(context, WireGuardTunnel::class.java).apply {
                putExtra(WireGuardTunnel.EXTRA_CONFIG, config)
            }
            context.startForegroundService(tunnelIntent)
        } else {
            Log.d(TAG, "Boot: no saved config or tunnel disabled, skipping")
        }
    }

    companion object {
        private const val TAG = "BootReceiver"
    }
}
