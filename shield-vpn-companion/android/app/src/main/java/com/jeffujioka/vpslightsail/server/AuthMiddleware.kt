package com.jeffujioka.vpslightsail.server

import java.security.MessageDigest

class AuthMiddleware(private val expectedToken: String) {
    fun isAuthorized(authHeader: String?): Boolean {
        if (authHeader.isNullOrBlank()) return false
        val parts = authHeader.split(" ", limit = 2)
        if (parts.size != 2) return false
        if (parts[0] != "Bearer") return false
        return MessageDigest.isEqual(parts[1].toByteArray(), expectedToken.toByteArray())   // F10: constant time
    }
}
