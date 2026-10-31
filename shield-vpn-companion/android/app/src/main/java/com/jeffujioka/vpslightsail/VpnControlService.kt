package com.jeffujioka.vpslightsail

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.jeffujioka.vpslightsail.api.CompanionContract
import com.jeffujioka.vpslightsail.api.HttpRouter
import com.jeffujioka.vpslightsail.api.UiModel
import com.jeffujioka.vpslightsail.discovery.MdnsAnnouncer
import com.jeffujioka.vpslightsail.server.AuthMiddleware
import com.jeffujioka.vpslightsail.server.ConfigHttpServer

/**
 * Foreground service hosting HTTP, mDNS and the notification (spec-04 §5.5). The tunnel, the stored
 * state and rules R1–R7 live in the process-level [ShieldVpnApp.api]; every mutation that is not an
 * HTTP request runs on [ShieldVpnApp.executor] — never on the main thread (spec-04 §2.3).
 */
class VpnControlService : Service() {

    private val app get() = application as ShieldVpnApp
    private lateinit var httpServer: ConfigHttpServer
    private lateinit var mdnsAnnouncer: MdnsAnnouncer

    private val stateReceiver = object : BroadcastReceiver() {        // main thread: status() is lock-free
        override fun onReceive(context: Context, intent: Intent) = updateNotification()
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        startForeground(NOTIF_ID, buildNotification(statusLine()))
        val token = app.configStore.authToken
        httpServer = ConfigHttpServer(HTTP_PORT, HttpRouter(AuthMiddleware(token), app.api))
        try {
            httpServer.start()
            Log.i(TAG, "HTTP server started on port $HTTP_PORT")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start HTTP server: ${e.message}", e)
        }
        mdnsAnnouncer = MdnsAnnouncer(this, HTTP_PORT, CompanionContract.txtRecords(token))
        try {
            mdnsAnnouncer.register()
            Log.i(TAG, "mDNS registered on port $HTTP_PORT")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to register mDNS: ${e.message}", e)
        }
        ContextCompat.registerReceiver(this, stateReceiver, IntentFilter(ACTION_STATE_CHANGED),
            ContextCompat.RECEIVER_NOT_EXPORTED)
        app.executor.execute { app.api.reconcile() }                    // R1–R3: boot, restart, package replace
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_CONNECT -> app.executor.execute { app.api.connect() }
            ACTION_DISCONNECT -> app.executor.execute { app.api.disconnect() }
        }
        return START_STICKY
    }

    override fun onDestroy() {              // never touches the tunnel or the intent: both belong to app.api
        mdnsAnnouncer.unregister()
        unregisterReceiver(stateReceiver)
        httpServer.stop()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    private fun statusLine(): String = UiModel.from(app.api.status()).statusLine

    private fun updateNotification() =
        getSystemService(NotificationManager::class.java).notify(NOTIF_ID, buildNotification(statusLine()))

    private fun buildNotification(text: String) =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentTitle("vps-lightsail VPN")
            .setContentText(text)
            .setOngoing(true)
            .build()

    private fun createNotificationChannel() {
        val channel = NotificationChannel(CHANNEL_ID, "VPN Status", NotificationManager.IMPORTANCE_LOW)
        getSystemService(NotificationManager::class.java).createNotificationChannel(channel)
    }

    companion object {
        const val HTTP_PORT = 8080
        const val ACTION_CONNECT = "com.jeffujioka.vpslightsail.CONNECT"
        const val ACTION_DISCONNECT = "com.jeffujioka.vpslightsail.DISCONNECT"
        const val ACTION_STATE_CHANGED = "com.jeffujioka.vpslightsail.STATE_CHANGED"
        private const val NOTIF_ID = 1
        private const val CHANNEL_ID = "vpn_control"
        private const val TAG = "VpnControlService"
    }
}
