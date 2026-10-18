// api/CompanionApi.kt
package com.jeffujioka.vpslightsail.api

import com.jeffujioka.vpslightsail.api.CompanionContract as C
import com.jeffujioka.vpslightsail.vpn.TunnelConfig

class CompanionApi(
    private val tunnel: TunnelBackend,
    private val store: StateStore,
    private val onChange: () -> Unit = {},
    private val sleeper: (Long) -> Unit = { Thread.sleep(it) },
    private val dispatch: (() -> Unit) -> Unit = { it() },   // app: executor::execute; tests: inline
) {
    private val lock = Any()
    @Volatile private var inTransition = false
    @Volatile var lastError: String? = null
        private set

    /** Lock-free: safe on the main thread (§2.3). */
    fun status(): CompanionStatus {
        val cfg = store.lastConfig
        return CompanionStatus(tunnel.isConnected, cfg != null, cfg?.let(::addressOf),
            store.tunnelEnabled, lastError)
    }

    fun storeConfig(body: String, activate: Boolean): ApiResponse = mutate {
        if (body.isBlank()) return@mutate ApiResponse(400, C.MSG_EMPTY)
        invalidReason(body)?.let { return@mutate ApiResponse(400, "Invalid config: $it") }
        if (!activate && (tunnel.isConnected || store.tunnelEnabled)) {        // R5
            store.tunnelEnabled = false
            transition { tunnel.disconnect() }
        }
        store.lastConfig = body
        if (activate) bringUp(body, C.MSG_ACTIVATED)
        else { lastError = null; ApiResponse(200, C.MSG_STORED) }
    }

    fun connect(): ApiResponse = mutate {
        val cfg = store.lastConfig ?: return@mutate ApiResponse(409, C.MSG_NO_CONFIG)
        if (tunnel.isConnected) { store.tunnelEnabled = true; ApiResponse(200, C.MSG_CONNECTED) }
        else bringUp(cfg, C.MSG_CONNECTED)
    }

    fun disconnect(): ApiResponse = mutate {
        store.tunnelEnabled = false
        transition { tunnel.disconnect() }
        lastError = null
        ApiResponse(200, C.MSG_DISCONNECTED)
    }

    /** R1–R3. Runs on the executor; sleeps outside the lock so HTTP stays responsive. */
    fun reconcile(delaysMs: List<Long> = listOf(0L, 5_000L, 15_000L)) {
        var failure: String? = null
        for (delay in delaysMs) {
            sleeper(delay)
            val done = synchronized(lock) {
                val cfg = store.lastConfig
                when {
                    !store.tunnelEnabled -> true                                    // R3 / user changed it
                    cfg == null -> { store.tunnelEnabled = false; true }            // R2
                    tunnel.isConnected -> true
                    else -> try {
                        transition { tunnel.connect(cfg) }; lastError = null; true
                    } catch (e: Exception) {
                        failure = reasonOf(e); transition { tunnel.disconnect() }; false
                    }
                }
            }
            onChange()
            if (done) return
        }
        synchronized(lock) { lastError = "Restore failed: $failure" }                // intent kept
        onChange()
    }

    /**
     * R6. Called directly from the backend's onStateChange on ANY thread (main included).
     * Classifies the event immediately (volatile read, no lock), then does the work via
     * [dispatch] (the app executor) so the main thread never takes the lock (§2.3).
     */
    fun onBackendStateChanged(up: Boolean) {
        val external = !up && !inTransition
        if (!external) { dispatch(onChange); return }
        dispatch {
            synchronized(lock) {
                if (store.tunnelEnabled && !tunnel.isConnected) {
                    store.tunnelEnabled = false
                    lastError = "Tunnel stopped by the system"
                }
            }
            onChange()
        }
    }

    private fun bringUp(config: String, okMessage: String): ApiResponse {
        store.tunnelEnabled = true
        return try {
            transition { tunnel.connect(config) }
            lastError = null
            ApiResponse(200, okMessage)
        } catch (e: Exception) {                                                    // R4
            store.tunnelEnabled = false
            transition { tunnel.disconnect() }
            lastError = reasonOf(e)
            ApiResponse(500, "Failed: $lastError")
        }
    }

    private inline fun <T> transition(block: () -> T): T {
        inTransition = true
        try { return block() } finally { inTransition = false }
    }

    private inline fun mutate(block: () -> ApiResponse): ApiResponse {
        val r = synchronized(lock) { block() }
        onChange()
        return r
    }

    companion object {
        fun addressOf(cfg: String): String? = runCatching {
            TunnelConfig.parse(cfg).address.split(',').first().trim().ifEmpty { null }
        }.getOrNull()

        /** D1: the parser appends the offending line, which may hold a key — keep only the text before ": ". */
        fun invalidReason(body: String): String? {
            if (body.toByteArray(Charsets.UTF_8).size > C.MAX_CONFIG_BYTES) return "too large"
            val e = runCatching { TunnelConfig.parse(body) }.exceptionOrNull() ?: return null
            return e.message?.substringBefore(": ")?.ifBlank { null } ?: e.javaClass.simpleName
        }

        private fun reasonOf(e: Exception) = e.message ?: e.javaClass.simpleName
    }
}
