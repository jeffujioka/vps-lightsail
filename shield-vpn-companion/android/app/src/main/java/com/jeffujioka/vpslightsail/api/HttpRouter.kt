package com.jeffujioka.vpslightsail.api

import com.jeffujioka.vpslightsail.server.AuthMiddleware

class HttpRouter(private val auth: AuthMiddleware, private val api: CompanionApi) {

    fun route(method: String, path: String, query: Map<String, String>,
              authHeader: String?, body: String): ApiResponse {
        if (method == "GET" && path == "/health") return ApiResponse(200, "ok")
        val known = (method == "GET" && path == "/status") ||
            (method == "POST" && path in setOf("/config", "/connect", "/disconnect"))
        if (!known) return ApiResponse(404, "Not found")
        if (!auth.isAuthorized(authHeader)) return ApiResponse(403, "Unauthorized")
        return when (path) {
            "/status" -> ApiResponse(200, StatusJson.render(api.status()), ApiResponse.MIME_JSON)
            "/config" -> when (query["activate"]) {
                null, "true" -> api.storeConfig(body, activate = true)
                "false" -> api.storeConfig(body, activate = false)
                else -> ApiResponse(400, "Invalid activate value")
            }
            "/connect" -> api.connect()
            else -> api.disconnect()
        }
    }
}
