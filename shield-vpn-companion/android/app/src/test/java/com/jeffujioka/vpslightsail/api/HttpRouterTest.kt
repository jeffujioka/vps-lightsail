// $T/api/HttpRouterTest.kt — spec-04 §7.1 (10); texts per spec-01 §7 + M17
package com.jeffujioka.vpslightsail.api

import com.jeffujioka.vpslightsail.server.AuthMiddleware
import org.junit.Assert.*
import org.junit.Test

class HttpRouterTest {
    private val tunnel = FakeTunnelBackend()
    private val store = InMemoryStateStore()
    private val router = HttpRouter(AuthMiddleware("s3cret"), CompanionApi(tunnel, store))
    private val ok = "Bearer s3cret"
    private fun get(path: String, auth: String? = ok) = router.route("GET", path, emptyMap(), auth, "")
    private fun post(path: String, query: Map<String, String> = emptyMap(), auth: String? = ok, body: String = CONF) =
        router.route("POST", path, query, auth, body)

    @Test fun `health needs no auth and answers ok`() = assertEquals(ApiResponse(200, "ok"), get("/health", auth = null))

    @Test fun `every protected route is 403 without or with a wrong token`() {
        for (auth in listOf(null, "Bearer wrong")) {
            assertEquals(ApiResponse(403, "Unauthorized"), get("/status", auth))
            for (p in listOf("/config", "/connect", "/disconnect")) assertEquals(ApiResponse(403, "Unauthorized"), post(p, auth = auth))
        }
        assertTrue(tunnel.calls.isEmpty()); assertNull(store.lastConfig)
    }
    @Test fun `auth is checked before the body`() =
        assertEquals(ApiResponse(403, "Unauthorized"), post("/config", auth = "Bearer wrong", body = ""))

    @Test fun `config without activate activates`() {
        assertEquals(ApiResponse(200, "OK — tunnel activated"), post("/config")); assertTrue(tunnel.up)
    }
    @Test fun `activate=true activates`() =
        assertEquals(ApiResponse(200, "OK — tunnel activated"), post("/config", mapOf("activate" to "true")))

    @Test fun `activate=false only stores`() {
        assertEquals(ApiResponse(200, "OK — config stored"), post("/config", mapOf("activate" to "false")))
        assertFalse(tunnel.up); assertEquals(CONF, store.lastConfig)
    }
    @Test fun `any other activate value is 400 and stores nothing`() {
        assertEquals(ApiResponse(400, "Invalid activate value"), post("/config", mapOf("activate" to "yes")))
        assertNull(store.lastConfig)
    }
    @Test fun `status is application json`() {
        store.lastConfig = CONF
        assertEquals(ApiResponse(200, """{"api": 2, "tunnel": "down", "has_config": true, "address": "10.8.0.2/32"}""",
            "application/json"), get("/status"))
    }
    @Test fun `unknown path is 404 even without auth`() = assertEquals(ApiResponse(404, "Not found"), get("/nope", auth = null))

    @Test fun `GET config is 404`() = assertEquals(ApiResponse(404, "Not found"), get("/config"))
}
