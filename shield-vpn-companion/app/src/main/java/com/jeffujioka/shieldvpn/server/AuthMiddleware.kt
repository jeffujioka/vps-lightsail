package com.jeffujioka.shieldvpn.server

class AuthMiddleware(private val expectedToken: String) {
    fun isAuthorized(authHeader: String?): Boolean {
        if (authHeader.isNullOrBlank()) return false
        val parts = authHeader.split(" ", limit = 2)
        if (parts.size != 2) return false
        if (parts[0] != "Bearer") return false
        return parts[1] == expectedToken
    }
}
