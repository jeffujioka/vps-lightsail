package com.jeffujioka.shieldvpn.server

import fi.iki.elonen.NanoHTTPD

class ConfigHttpServer(
    port: Int = 8080,
    private val auth: AuthMiddleware,
    private val onConfigReceived: (String) -> Unit
) : NanoHTTPD(port) {

    override fun serve(session: IHTTPSession): Response {
        return when {
            session.method == Method.POST && session.uri == "/config" -> handleConfig(session)
            session.method == Method.GET && session.uri == "/health" -> handleHealth()
            session.method == Method.POST && session.uri == "/disconnect" -> handleDisconnect(session)
            else -> newFixedLengthResponse(Response.Status.NOT_FOUND, MIME_PLAINTEXT, "Not found")
        }
    }

    private fun handleConfig(session: IHTTPSession): Response {
        val authHeader = session.headers["authorization"]
        if (!auth.isAuthorized(authHeader)) {
            return newFixedLengthResponse(Response.Status.FORBIDDEN, MIME_PLAINTEXT, "Unauthorized")
        }

        val body = readBody(session)
        if (body.isBlank()) {
            return newFixedLengthResponse(Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "Empty config")
        }

        return try {
            onConfigReceived(body)
            newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, "OK — tunnel activated")
        } catch (e: Exception) {
            newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR,
                MIME_PLAINTEXT,
                "Failed: ${e.message}"
            )
        }
    }

    private fun handleHealth(): Response {
        return newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, "ok")
    }

    private fun handleDisconnect(session: IHTTPSession): Response {
        val authHeader = session.headers["authorization"]
        if (!auth.isAuthorized(authHeader)) {
            return newFixedLengthResponse(Response.Status.FORBIDDEN, MIME_PLAINTEXT, "Unauthorized")
        }

        return try {
            onConfigReceived("")
            newFixedLengthResponse(Response.Status.OK, MIME_PLAINTEXT, "Disconnected")
        } catch (e: Exception) {
            newFixedLengthResponse(
                Response.Status.INTERNAL_ERROR,
                MIME_PLAINTEXT,
                "Failed: ${e.message}"
            )
        }
    }

    private fun readBody(session: IHTTPSession): String {
        val contentLength = session.headers["content-length"]?.toIntOrNull() ?: 0
        if (contentLength == 0) return ""
        val buffer = ByteArray(contentLength)
        session.inputStream.read(buffer, 0, contentLength)
        return String(buffer, Charsets.UTF_8)
    }
}
