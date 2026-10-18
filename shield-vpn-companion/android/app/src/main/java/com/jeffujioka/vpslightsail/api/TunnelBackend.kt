package com.jeffujioka.vpslightsail.api

interface TunnelBackend {
    /** True while the WireGuard tunnel is actually up (backend truth, not intent). */
    val isConnected: Boolean
    /** Brings the tunnel up with this config. Throws on failure; never silently no-ops. */
    fun connect(config: String)
    /** Brings the tunnel down. Idempotent: a no-op when already down. */
    fun disconnect()
}
