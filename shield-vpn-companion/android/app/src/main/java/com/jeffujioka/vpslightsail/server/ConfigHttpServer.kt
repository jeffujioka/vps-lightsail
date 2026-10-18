package com.jeffujioka.vpslightsail.server

import com.jeffujioka.vpslightsail.api.CompanionContract
import com.jeffujioka.vpslightsail.api.HttpRouter
import fi.iki.elonen.NanoHTTPD

class ConfigHttpServer(port: Int, private val router: HttpRouter) : NanoHTTPD(port) {

    override fun serve(session: IHTTPSession): Response {
        val body = if (session.method == Method.POST) readBody(session) else ""
        val query = session.parameters.mapValues { it.value.firstOrNull() ?: "" }
        val r = router.route(session.method.name, session.uri, query,
            session.headers["authorization"], body)
        return newFixedLengthResponse(Response.Status.lookup(r.status)
            ?: Response.Status.INTERNAL_ERROR, r.mime, r.body)
    }

    private fun readBody(session: IHTTPSession): String {
        val len = session.headers["content-length"]?.toIntOrNull() ?: return ""
        if (len <= 0) return ""
        val cap = minOf(len, CompanionContract.MAX_CONFIG_BYTES + 1)   // oversize ⇒ "too large"
        val buf = ByteArray(cap)
        var off = 0
        while (off < cap) {
            val n = session.inputStream.read(buf, off, cap - off)
            if (n < 0) break
            off += n
        }
        return String(buf, 0, off, Charsets.UTF_8)
    }
}
