package com.jeffujioka.vpslightsail

import com.jeffujioka.vpslightsail.server.AuthMiddleware
import org.junit.Assert.*
import org.junit.Test

class AuthMiddlewareTest {
    private val token = "abc123def456"
    private val middleware = AuthMiddleware(token)

    @Test
    fun `valid Bearer token returns true`() {
        assertTrue(middleware.isAuthorized("Bearer $token"))
    }

    @Test
    fun `wrong token returns false`() {
        assertFalse(middleware.isAuthorized("Bearer wrongtoken"))
    }

    @Test
    fun `missing Bearer prefix returns false`() {
        assertFalse(middleware.isAuthorized(token))
    }

    @Test
    fun `null header returns false`() {
        assertFalse(middleware.isAuthorized(null))
    }

    @Test
    fun `empty header returns false`() {
        assertFalse(middleware.isAuthorized(""))
    }
}
