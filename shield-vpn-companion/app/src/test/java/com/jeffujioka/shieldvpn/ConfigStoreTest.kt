package com.jeffujioka.shieldvpn

import com.jeffujioka.shieldvpn.persistence.ConfigStore
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfigStoreTest {

    @Test
    fun `generateToken produces 64-char hex string`() {
        val token = ConfigStore.generateToken()
        assertEquals(64, token.length)
        assertTrue("Token should only contain hex characters", token.matches(Regex("[0-9a-f]+")))
    }

    @Test
    fun `generateToken produces unique values`() {
        val token1 = ConfigStore.generateToken()
        val token2 = ConfigStore.generateToken()
        assertNotEquals(token1, token2)
    }
}
