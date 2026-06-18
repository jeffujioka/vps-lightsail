package com.jeffujioka.shieldvpn

import android.app.Application
import android.content.Intent
import android.util.Log
import com.jeffujioka.shieldvpn.discovery.MdnsAnnouncer
import com.jeffujioka.shieldvpn.persistence.ConfigStore
import com.jeffujioka.shieldvpn.server.AuthMiddleware
import com.jeffujioka.shieldvpn.server.ConfigHttpServer
import com.jeffujioka.shieldvpn.vpn.WireGuardTunnel

class ShieldVpnApp : Application() {

    lateinit var configStore: ConfigStore
        private set

    private var httpServer: ConfigHttpServer? = null
    private var mdnsAnnouncer: MdnsAnnouncer? = null

    override fun onCreate() {
        super.onCreate()

        configStore = ConfigStore(this)

        val auth = AuthMiddleware(configStore.authToken)

        httpServer = ConfigHttpServer(
            port = HTTP_PORT,
            auth = auth,
            onConfigReceived = { configBody ->
                handleConfigReceived(configBody)
            }
        ).also { server ->
            try {
                server.start()
                Log.i(TAG, "HTTP server started on port $HTTP_PORT")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to start HTTP server: ${e.message}", e)
            }
        }

        mdnsAnnouncer = MdnsAnnouncer(this, HTTP_PORT).also { announcer ->
            try {
                announcer.register()
                Log.i(TAG, "mDNS registered on port $HTTP_PORT")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to register mDNS: ${e.message}", e)
            }
        }
    }

    override fun onTerminate() {
        mdnsAnnouncer?.unregister()
        httpServer?.stop()
        super.onTerminate()
    }

    private fun handleConfigReceived(configBody: String) {
        if (configBody.isBlank()) {
            Log.i(TAG, "Empty config received — disconnecting tunnel")
            configStore.tunnelEnabled = false
            configStore.lastConfig = null
            stopService(Intent(this, WireGuardTunnel::class.java))
        } else {
            Log.i(TAG, "Config received — starting tunnel")
            configStore.lastConfig = configBody
            configStore.tunnelEnabled = true
            val intent = Intent(this, WireGuardTunnel::class.java).apply {
                putExtra(WireGuardTunnel.EXTRA_CONFIG, configBody)
            }
            startService(intent)
        }
    }

    companion object {
        const val HTTP_PORT = 8080
        private const val TAG = "ShieldVpnApp"
    }
}
