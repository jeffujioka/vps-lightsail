// $T/api/CompanionApiTest.kt — spec-04 §7.1 (22 + 1) + Review Focus 1-2 (D5)
package com.jeffujioka.vpslightsail.api

import org.junit.Assert.*
import org.junit.Test

class CompanionApiTest {
    private val tunnel = FakeTunnelBackend()
    private val store = InMemoryStateStore()
    private var changes = 0
    private val sleeps = mutableListOf<Long>()
    private val api = CompanionApi(tunnel, store, onChange = { changes++ }, sleeper = { sleeps += it })

    @Test fun `default push stores, connects and sets intent`() {
        assertEquals(ApiResponse(200, "OK — tunnel activated"), api.storeConfig(CONF, activate = true))
        assertEquals(CONF, store.lastConfig); assertTrue(store.tunnelEnabled); assertEquals(CONF, tunnel.lastConfig)
    }
    @Test fun `activate=false stores without connecting, intent off`() {
        assertEquals(ApiResponse(200, "OK — config stored"), api.storeConfig(CONF, activate = false))
        assertEquals(CONF, store.lastConfig); assertFalse(store.tunnelEnabled); assertTrue(tunnel.calls.isEmpty())
    }
    @Test fun `activate=false while up tears the old tunnel down (R5, U3)`() {
        api.storeConfig(CONF, activate = true)
        assertEquals(200, api.storeConfig(conf("10.8.0.9/32"), activate = false).status)
        assertFalse(tunnel.up); assertFalse(store.tunnelEnabled); assertEquals(conf("10.8.0.9/32"), store.lastConfig)
        assertEquals(listOf("connect", "disconnect"), tunnel.calls)
    }
    @Test fun `blank body is 400 Empty config`() {
        assertEquals(ApiResponse(400, "Empty config"), api.storeConfig(" \n", activate = true))
        assertNull(store.lastConfig); assertTrue(tunnel.calls.isEmpty())
    }
    @Test fun `unparseable body is 400 Invalid config and not stored`() {
        assertEquals(ApiResponse(400, "Invalid config: Missing [Peer] section"),
            api.storeConfig("[Interface]\nAddress = 10.8.0.2/32", activate = true))
        assertNull(store.lastConfig)
    }
    @Test fun `body over 16 KiB is 400 Invalid config too large`() {
        val big = CONF + "\n" + "#".repeat(CompanionContract.MAX_CONFIG_BYTES)
        assertEquals(ApiResponse(400, "Invalid config: too large"), api.storeConfig(big, activate = false))
    }
    @Test fun `connect failure on push is 500, intent off, config kept, lastError set (R4)`() {
        tunnel.failNextConnect = IllegalStateException("VPN_NOT_AUTHORIZED")
        assertEquals(ApiResponse(500, "Failed: VPN_NOT_AUTHORIZED"), api.storeConfig(CONF, activate = true))
        assertFalse(store.tunnelEnabled); assertEquals(CONF, store.lastConfig); assertEquals("VPN_NOT_AUTHORIZED", api.lastError)
        assertEquals(listOf("connect", "disconnect"), tunnel.calls)
    }
    @Test fun `connect without a stored config is 409`() {
        assertEquals(ApiResponse(409, "No stored config"), api.connect()); assertTrue(tunnel.calls.isEmpty())
    }
    @Test fun `connect brings the stored config up and sets intent`() {
        api.storeConfig(CONF, activate = false)
        assertEquals(ApiResponse(200, "Connected"), api.connect())
        assertTrue(tunnel.up); assertTrue(store.tunnelEnabled); assertEquals(CONF, tunnel.lastConfig)
    }
    @Test fun `connect when already up is a no-op that only sets intent`() {
        api.storeConfig(CONF, activate = true); store.tunnelEnabled = false
        assertEquals(ApiResponse(200, "Connected"), api.connect())
        assertEquals(listOf("connect"), tunnel.calls); assertTrue(store.tunnelEnabled)
    }
    @Test fun `connect failure is 500 and clears intent`() {
        api.storeConfig(CONF, activate = false); tunnel.failNextConnect = RuntimeException("UNABLE_TO_START_VPN")
        assertEquals(ApiResponse(500, "Failed: UNABLE_TO_START_VPN"), api.connect())
        assertFalse(store.tunnelEnabled); assertFalse(tunnel.up)
    }
    @Test fun `disconnect brings the tunnel down and keeps the stored config`() {
        api.storeConfig(CONF, activate = true)
        assertEquals(ApiResponse(200, "Disconnected"), api.disconnect())
        assertFalse(tunnel.up); assertFalse(store.tunnelEnabled); assertEquals(CONF, store.lastConfig)
    }
    @Test fun `disconnect is idempotent`() {
        assertEquals(200, api.disconnect().status); assertEquals(200, api.disconnect().status)
        assertFalse(tunnel.up); assertEquals(listOf("disconnect", "disconnect"), tunnel.calls)
    }
    @Test fun `status reports the backend, not the intent`() {
        store.lastConfig = CONF; store.tunnelEnabled = true                 // intent on, backend down
        assertEquals(CompanionStatus(false, true, "10.8.0.2/32", true, null), api.status())
    }
    @Test fun `address is the first entry of a multi-address Interface`() {
        store.lastConfig = conf("10.8.0.2/32, fd00::2/128"); assertEquals("10.8.0.2/32", api.status().address)
    }
    @Test fun `address is null when the stored config does not parse`() {
        store.lastConfig = "garbage"
        api.status().let { assertTrue(it.hasConfig); assertNull(it.address) }
    }
    @Test fun `reconcile restores the tunnel when intent and config are set (R1)`() {
        store.lastConfig = CONF; store.tunnelEnabled = true
        api.reconcile()
        assertTrue(tunnel.up); assertNull(api.lastError); assertEquals(listOf(0L), sleeps)
    }
    @Test fun `reconcile retries at 0, 5 and 15 s, then keeps intent and reports Restore failed (R1)`() {
        store.lastConfig = CONF; store.tunnelEnabled = true
        val retrying = CompanionApi(tunnel, store,
            sleeper = { sleeps += it; tunnel.failNextConnect = IllegalStateException("VPN_NOT_AUTHORIZED") })
        retrying.reconcile()
        assertEquals(listOf(0L, 5_000L, 15_000L), sleeps); assertTrue(store.tunnelEnabled); assertFalse(tunnel.up)
        assertEquals("Restore failed: VPN_NOT_AUTHORIZED", retrying.lastError)
    }
    @Test fun `reconcile stops retrying once the intent is cleared between attempts`() {
        store.lastConfig = CONF; store.tunnelEnabled = true; tunnel.failNextConnect = IllegalStateException("boom")
        val r = CompanionApi(tunnel, store, sleeper = { sleeps += it; if (it == 5_000L) store.tunnelEnabled = false })
        r.reconcile()
        assertEquals(listOf(0L, 5_000L), sleeps); assertEquals(listOf("connect", "disconnect"), tunnel.calls)
        assertNull(r.lastError)
    }
    @Test fun `reconcile with intent but no config clears the intent (R2)`() {
        store.tunnelEnabled = true
        api.reconcile()
        assertFalse(store.tunnelEnabled); assertTrue(tunnel.calls.isEmpty())
    }
    @Test fun `reconcile without intent does nothing (R3)`() {
        store.lastConfig = CONF
        api.reconcile()
        assertFalse(store.tunnelEnabled); assertTrue(tunnel.calls.isEmpty()); assertEquals(listOf(0L), sleeps)
    }
    @Test fun `external DOWN clears intent and sets lastError, our own DOWN does not (R6)`() {
        val queued = mutableListOf<() -> Unit>()                             // the app executor, drained by hand
        val deferred = CompanionApi(tunnel, store, dispatch = { queued += it })
        tunnel.onState = deferred::onBackendStateChanged
        deferred.storeConfig(CONF, activate = true)
        deferred.disconnect(); deferred.connect()                              // own DOWN + UP, classified when fired
        tunnel.up = false                                                      // backend dies before the queue drains
        queued.toList().forEach { it() }
        assertTrue(store.tunnelEnabled); assertNull(deferred.lastError)        // own DOWN never counts as a revoke
        deferred.onBackendStateChanged(false); queued.last()()                 // system revoke, outside any transition
        assertFalse(store.tunnelEnabled); assertEquals("Tunnel stopped by the system", deferred.lastError)
    }
    @Test fun `onChange fires once per mutation, no-ops and failures included (bug #7)`() {
        api.storeConfig(CONF, activate = false); api.connect(); api.connect()   // stage, connect, no-op connect
        api.disconnect(); api.disconnect(); api.storeConfig("", activate = true) // disconnect, no-op, 400
        tunnel.failNextConnect = RuntimeException("x"); api.connect()            // 500
        assertEquals(7, changes)
    }
    @Test fun `a malformed key line is named but never echoed (D1)`() {
        val r = api.storeConfig(CONF.lines().drop(1).joinToString("\n"), activate = false)   // no [Interface] header
        assertEquals(ApiResponse(400, "Invalid config: Key-value pair found outside of a section"), r)
        assertFalse(r.body.contains("PrivateKey"))
    }
    @Test fun `a backend exception without a message reports its type, never null`() {
        tunnel.failNextConnect = IllegalStateException()
        assertEquals(ApiResponse(500, "Failed: IllegalStateException"), api.storeConfig(CONF, activate = true))
    }
}
