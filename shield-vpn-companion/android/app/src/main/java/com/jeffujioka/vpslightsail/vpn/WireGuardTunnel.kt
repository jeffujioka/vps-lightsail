package com.jeffujioka.vpslightsail.vpn

import android.content.Context
import android.os.SystemClock
import android.util.Log
import com.jeffujioka.vpslightsail.api.TunnelBackend
import com.wireguard.android.backend.BackendException
import com.wireguard.android.backend.GoBackend
import com.wireguard.android.backend.Tunnel
import com.wireguard.config.Config
import com.wireguard.config.Interface
import com.wireguard.config.Peer

class WireGuardTunnel(
    context: Context,
    private val onStateChange: (up: Boolean) -> Unit = {},
) : TunnelBackend {

    private val backend = GoBackend(context.applicationContext)
    private val wgTunnel = WgTunnel("shield-vpn")              // ONE object for the process (F8)
    @Volatile private var lastDownAtMs = 0L

    /** Kept under its historic name; now the only "tunnel up" source for /status and the UI. */
    override val isConnected: Boolean
        get() = try { backend.getState(wgTunnel) == Tunnel.State.UP } catch (e: Exception) { false }

    override fun connect(config: String) {
        val wgConfig = buildWgConfig(TunnelConfig.parse(config))
        // Replaces the old raw-Thread 1.5 s sleep: GoBackend$VpnService needs time to stop
        // after a DOWN before an UP can bind it again.
        val wait = RESTART_GUARD_MS - (SystemClock.elapsedRealtime() - lastDownAtMs)
        if (wait > 0) Thread.sleep(wait)
        try { backend.setState(wgTunnel, Tunnel.State.UP, wgConfig) }  // replaces a running config
        catch (e: Exception) { throw failureOf(e) }                    // D2
        check(isConnected) { "backend did not report UP" }
        Log.i(TAG, "Tunnel UP — ${tunnelEndpoint(config)}")
    }

    override fun disconnect() {
        if (!isConnected) return
        try { backend.setState(wgTunnel, Tunnel.State.DOWN, null) }
        catch (e: Exception) { Log.w(TAG, "Error disconnecting: ${e.message}") }
        finally { lastDownAtMs = SystemClock.elapsedRealtime(); Log.i(TAG, "Tunnel DOWN") }
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
            this@WireGuardTunnel.onStateChange(newState == Tunnel.State.UP)   // D2: explicit outer receiver
        }
    }

    companion object {
        private const val TAG = "WireGuardTunnel"
        private const val RESTART_GUARD_MS = 1_500L
        private fun tunnelEndpoint(c: String) = runCatching { TunnelConfig.parse(c).endpoint }.getOrDefault("?")

        /** D2: BackendException carries no message (only a Reason) — surface the reason name instead. */
        internal fun failureOf(e: Exception): Exception =
            if (e is BackendException) IllegalStateException(e.reason.name, e) else e
    }
}
