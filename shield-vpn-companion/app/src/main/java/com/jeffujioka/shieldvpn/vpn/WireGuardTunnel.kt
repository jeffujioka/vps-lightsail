package com.jeffujioka.shieldvpn.vpn

import android.net.VpnService

class WireGuardTunnel : VpnService() {

    override fun onStartCommand(intent: android.content.Intent?, flags: Int, startId: Int): Int {
        // TODO: establish WireGuard tunnel from stored config
        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        // TODO: tear down tunnel
    }
}
