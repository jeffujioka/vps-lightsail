package com.jeffujioka.vpslightsail

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.jeffujioka.vpslightsail.discovery.MdnsAnnouncer
import com.jeffujioka.vpslightsail.persistence.ConfigStore
import com.jeffujioka.vpslightsail.server.AuthMiddleware
import com.jeffujioka.vpslightsail.server.ConfigHttpServer
import com.jeffujioka.vpslightsail.vpn.WireGuardTunnel

/**
 * Persistent foreground service that owns the HTTP server, mDNS, and WireGuard tunnel.
 * Running as a foreground service allows GoBackend to start GoBackend$VpnService
 * from within this context, even when the TV UI is idle.
 */
class VpnControlService : Service() {

    private lateinit var configStore: ConfigStore
    private lateinit var tunnel: WireGuardTunnel
    private lateinit var httpServer: ConfigHttpServer
    private lateinit var mdnsAnnouncer: MdnsAnnouncer

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIF_ID, buildNotification("Waiting for config..."))

        configStore = ConfigStore(this)
        tunnel = WireGuardTunnel(this)

        val auth = AuthMiddleware(configStore.authToken)
        httpServer = ConfigHttpServer(
            port = HTTP_PORT,
            auth = auth,
            onConfigReceived = { configBody -> handleConfigReceived(configBody) }
        )
        try {
            httpServer.start()
            Log.i(TAG, "HTTP server started on port $HTTP_PORT")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start HTTP server: ${e.message}", e)
        }

        mdnsAnnouncer = MdnsAnnouncer(this, HTTP_PORT, configStore.authToken)
        try {
            mdnsAnnouncer.register()
            Log.i(TAG, "mDNS registered on port $HTTP_PORT")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to register mDNS: ${e.message}", e)
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_DISCONNECT -> disconnectOnly()
            ACTION_RECONNECT -> reconnectSavedConfig()
        }
        return START_STICKY
    }

    override fun onDestroy() {
        mdnsAnnouncer.unregister()
        httpServer.stop()
        tunnel.disconnect()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun handleConfigReceived(configBody: String) {
        if (configBody.isBlank()) {
            Log.i(TAG, "Empty config — disconnecting")
            configStore.tunnelEnabled = false
            configStore.lastConfig = null
            tunnel.disconnect()
            updateNotification("Waiting for config...")
        } else {
            Log.i(TAG, "Config received — starting tunnel")
            configStore.lastConfig = configBody
            configStore.tunnelEnabled = true
            try {
                tunnel.connect(configBody)
                updateNotification("VPN Connected")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to connect: ${e.message}", e)
                updateNotification("Connection failed: ${e.message}")
            }
            broadcastState()
        }
    }

    private fun disconnectOnly() {
        Log.i(TAG, "Disconnecting tunnel (keeping config for reconnect)")
        configStore.tunnelEnabled = false
        tunnel.disconnect()
        updateNotification("Disconnected — tap Reconnect to restore")
        broadcastState()
    }

    fun reconnectSavedConfig() {
        val config = configStore.lastConfig ?: return
        Log.i(TAG, "Reconnecting in 1.5s...")
        Thread {
            // Let GoBackend$VpnService fully stop before restarting
            Thread.sleep(1500)
            configStore.tunnelEnabled = true
            try {
                tunnel.connect(config)
                updateNotification("VPN Connected")
            } catch (e: Exception) {
                configStore.tunnelEnabled = false
                Log.e(TAG, "Reconnect failed: ${e.message}", e)
            }
            broadcastState()
        }.start()
    }

    private fun updateNotification(text: String) {
        val nm = getSystemService(NotificationManager::class.java)
        nm.notify(NOTIF_ID, buildNotification(text))
    }

    private fun buildNotification(text: String) =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentTitle("vps-lightsail VPN")
            .setContentText(text)
            .setOngoing(true)
            .build()

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID, "VPN Status", NotificationManager.IMPORTANCE_LOW
        )
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    private fun broadcastState() {
        sendBroadcast(Intent(ACTION_STATE_CHANGED))
    }

    companion object {
        const val HTTP_PORT = 8080
        const val ACTION_DISCONNECT = "com.jeffujioka.vpslightsail.DISCONNECT"
        const val ACTION_RECONNECT = "com.jeffujioka.vpslightsail.RECONNECT"
        const val ACTION_STATE_CHANGED = "com.jeffujioka.vpslightsail.STATE_CHANGED"
        private const val NOTIF_ID = 1
        private const val CHANNEL_ID = "vpn_control"
        private const val TAG = "VpnControlService"
    }
}
