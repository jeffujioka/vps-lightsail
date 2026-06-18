package com.jeffujioka.shieldvpn.vpn

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Intent
import android.net.VpnService
import androidx.core.app.NotificationCompat
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import com.wireguard.config.Interface
import com.wireguard.config.Peer

class WireGuardTunnel : VpnService() {

    private var backend: GoBackend? = null
    private var activeTunnel: WgTunnel? = null

    override fun onCreate() {
        super.onCreate()
        backend = GoBackend(this)
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val configString = intent?.getStringExtra(EXTRA_CONFIG)

        if (configString.isNullOrBlank()) {
            disconnectTunnel()
            stopSelf()
            return START_NOT_STICKY
        }

        showNotification("Connecting...")

        try {
            val tunnelConfig = TunnelConfig.parse(configString)
            val wgConfig = buildWgConfig(tunnelConfig)

            val tunnel = WgTunnel("shield-vpn")
            activeTunnel = tunnel

            backend?.setState(tunnel, Tunnel.State.UP, wgConfig)

            showNotification("Connected to ${tunnelConfig.endpoint}")
        } catch (e: Exception) {
            showNotification("Connection failed: ${e.message}")
            stopSelf()
        }

        return START_STICKY
    }

    override fun onDestroy() {
        disconnectTunnel()
        super.onDestroy()
    }

    private fun disconnectTunnel() {
        try {
            activeTunnel?.let { tunnel ->
                backend?.setState(tunnel, Tunnel.State.DOWN, null)
            }
        } catch (_: Exception) {
            // best-effort teardown
        } finally {
            activeTunnel = null
        }
    }

    private fun buildWgConfig(tunnelConfig: TunnelConfig): Config {
        val iface = Interface.Builder()
            .parsePrivateKey(tunnelConfig.privateKey)
            .parseAddresses(tunnelConfig.address)
            .parseDnsServers(tunnelConfig.dns)
            .build()

        val peer = Peer.Builder()
            .parsePublicKey(tunnelConfig.peerPublicKey)
            .parseEndpoint(tunnelConfig.endpoint)
            .parseAllowedIPs(tunnelConfig.allowedIPs)
            .parsePersistentKeepalive(tunnelConfig.persistentKeepalive.toString())
            .build()

        return Config.Builder()
            .setInterface(iface)
            .addPeer(peer)
            .build()
    }

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "VPN Status",
            NotificationManager.IMPORTANCE_LOW
        )
        val notificationManager = getSystemService(NotificationManager::class.java)
        notificationManager.createNotificationChannel(channel)
    }

    private fun showNotification(text: String) {
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentTitle("Shield VPN")
            .setContentText(text)
            .setOngoing(true)
            .build()

        startForeground(NOTIFICATION_ID, notification)
    }

    private inner class WgTunnel(private val name: String) : Tunnel {
        override fun getName(): String = name
        override fun onStateChange(newState: Tunnel.State) {
            // State changes are handled via showNotification calls above
        }
    }

    companion object {
        const val EXTRA_CONFIG = "extra_config"
        private const val CHANNEL_ID = "vpn_status"
        private const val NOTIFICATION_ID = 1
    }
}
