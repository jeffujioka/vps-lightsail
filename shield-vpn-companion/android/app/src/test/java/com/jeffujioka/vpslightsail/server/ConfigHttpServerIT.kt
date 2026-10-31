// $T/server/ConfigHttpServerIT.kt — spec-04 §7.2: the real NanoHTTPD adapter on an ephemeral port, real sockets
package com.jeffujioka.vpslightsail.server

import com.jeffujioka.vpslightsail.api.CONF
import com.jeffujioka.vpslightsail.api.CompanionApi
import com.jeffujioka.vpslightsail.api.FakeTunnelBackend
import com.jeffujioka.vpslightsail.api.HttpRouter
import com.jeffujioka.vpslightsail.api.InMemoryStateStore
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.HttpURLConnection
import java.net.Socket
import java.net.URL

class ConfigHttpServerIT {
    private val token = "it-token"
    private val tunnel = FakeTunnelBackend()
    private val store = InMemoryStateStore()
    private val server = ConfigHttpServer(0, HttpRouter(AuthMiddleware(token), CompanionApi(tunnel, store)))
    private val staged = """{"api": 2, "tunnel": "down", "has_config": true, "address": "10.8.0.2/32"}"""

    private class Reply(val code: Int, val type: String?, val bytes: ByteArray) { val text get() = String(bytes, Charsets.UTF_8) }

    private fun call(method: String, path: String, body: String? = null, auth: String? = "Bearer $token"): Reply {
        val c = URL("http://127.0.0.1:${server.listeningPort}$path").openConnection() as HttpURLConnection
        c.requestMethod = method
        auth?.let { c.setRequestProperty("Authorization", it) }
        if (method == "POST") { c.doOutput = true; c.outputStream.use { it.write((body ?: "").toByteArray(Charsets.UTF_8)) } }
        val code = c.responseCode
        return Reply(code, c.contentType, (if (code >= 400) c.errorStream else c.inputStream).use { it.readBytes() })
    }

    @Before fun start() = server.start()
    @After fun stop() = server.stop()

    @Test fun `staged push, status, connect, disconnect, status over the wire`() {
        call("POST", "/config?activate=false", CONF).let { assertEquals(200, it.code); assertEquals("OK — config stored", it.text) }
        call("GET", "/status").let { assertEquals(200, it.code); assertEquals("application/json", it.type); assertEquals(staged, it.text) }
        call("POST", "/connect").let { assertEquals(200, it.code); assertEquals("Connected", it.text) }
        assertEquals(staged.replace("down", "up"), call("GET", "/status").text)
        call("POST", "/disconnect").let { assertEquals(200, it.code); assertEquals("Disconnected", it.text) }
        assertEquals(staged, call("GET", "/status").text)
    }
    @Test fun `409 No stored config over the wire`() =
        call("POST", "/connect").let { assertEquals(409, it.code); assertEquals("No stored config", it.text) }

    @Test fun `403 Unauthorized over the wire`() =
        call("GET", "/status", auth = "Bearer wrong").let { assertEquals(403, it.code); assertEquals("Unauthorized", it.text) }

    @Test fun `400 Empty config over the wire`() =
        call("POST", "/config", "").let { assertEquals(400, it.code); assertEquals("Empty config", it.text) }

    @Test fun `an 8 KiB push split inside a multibyte character arrives intact (F7)`() {
        val body = CONF + "\n" + "# São Paulo — padding line\n".repeat(320)             // 9.8 KB of UTF-8
        val bytes = body.toByteArray(Charsets.UTF_8)
        val cut = (bytes.size / 2 until bytes.size).first { bytes[it] == 0xC3.toByte() } + 1   // between the 2 bytes of 'ã'
        val head = "POST /config?activate=false HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer $token\r\n" +
            "Content-Length: ${bytes.size}\r\nConnection: close\r\n\r\n"
        val reply = Socket("127.0.0.1", server.listeningPort).use { s ->
            s.tcpNoDelay = true
            s.getOutputStream().apply { write(head.toByteArray(Charsets.US_ASCII) + bytes.copyOfRange(0, cut)); flush() }
            Thread.sleep(300)                                                              // second TCP segment
            s.getOutputStream().apply { write(bytes.copyOfRange(cut, bytes.size)); flush() }
            String(s.getInputStream().readBytes(), Charsets.UTF_8)
        }
        assertTrue(reply, reply.startsWith("HTTP/1.1 200 ") && reply.endsWith("\r\n\r\nOK — config stored"))
        assertTrue(bytes.size > 8 * 1024); assertEquals(body, store.lastConfig)
    }
    @Test fun `the em dash of OK — tunnel activated goes out as UTF-8`() {
        val r = call("POST", "/config", CONF)
        assertEquals(200, r.code); assertEquals("text/plain; charset=utf-8", r.type)
        assertArrayEquals(byteArrayOf(0xE2.toByte(), 0x80.toByte(), 0x94.toByte()), r.bytes.copyOfRange(3, 6))
        assertEquals("OK — tunnel activated", r.text)
    }
}
