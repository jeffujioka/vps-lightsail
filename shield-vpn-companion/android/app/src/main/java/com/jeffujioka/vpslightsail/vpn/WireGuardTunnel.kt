package com.jeffujioka.vpslightsail.vpn

import android.content.Context
import android.util.Log
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import com.wireguard.config.Interface
import com.wireguard.config.Peer

class WireGuardTunnel(context: Context) {

    private val backend = GoBackend(context)
    private var activeTunnel: WgTunnel? = null

    val isConnected: Boolean
        get() = activeTunnel != null

    fun connect(configString: String) {
        val tunnelConfig = TunnelConfig.parse(configString)
        val wgConfig = buildWgConfig(tunnelConfig)

        val tunnel = WgTunnel("shield-vpn")
        activeTunnel = tunnel

        backend.setState(tunnel, Tunnel.State.UP, wgConfig)
        Log.i(TAG, "Tunnel UP — connected to ${tunnelConfig.endpoint}")
    }

    fun disconnect() {
        try {
            activeTunnel?.let { tunnel ->
                backend.setState(tunnel, Tunnel.State.DOWN, null)
            }
        } catch (e: Exception) {
            Log.w(TAG, "Error disconnecting: ${e.message}")
        } finally {
            activeTunnel = null
            Log.i(TAG, "Tunnel DOWN")
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

    private inner class WgTunnel(private val name: String) : Tunnel {
        override fun getName(): String = name
        override fun onStateChange(newState: Tunnel.State) {
            Log.d(TAG, "Tunnel state changed: $newState")
        }
    }

    companion object {
        private const val TAG = "WireGuardTunnel"
    }
}
