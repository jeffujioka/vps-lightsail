package com.jeffujioka.vpslightsail

import com.jeffujioka.vpslightsail.vpn.TunnelConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class TunnelConfigTest {

    private val validConf = """
        [Interface]
        PrivateKey = EIv2XB4v7GWNW69nzzGHF+bQAPRiSm1xMALPCkpBt2o=
        Address = 10.8.0.4/32
        DNS = 1.1.1.1

        [Peer]
        PublicKey = Cl4s7JNo/TGQdwF9EXIadX+yNnSJ0tJ7XEKGvmQPuWE=
        Endpoint = 54.94.30.112:51820
        AllowedIPs = 0.0.0.0/0
        PersistentKeepalive = 25
    """.trimIndent()

    @Test
    fun `parse valid config extracts interface fields`() {
        val config = TunnelConfig.parse(validConf)

        assertEquals("EIv2XB4v7GWNW69nzzGHF+bQAPRiSm1xMALPCkpBt2o=", config.privateKey)
        assertEquals("10.8.0.4/32", config.address)
        assertEquals("1.1.1.1", config.dns)
    }

    @Test
    fun `parse valid config extracts peer fields`() {
        val config = TunnelConfig.parse(validConf)

        assertEquals("Cl4s7JNo/TGQdwF9EXIadX+yNnSJ0tJ7XEKGvmQPuWE=", config.peerPublicKey)
        assertEquals("54.94.30.112:51820", config.endpoint)
        assertEquals("0.0.0.0/0", config.allowedIPs)
        assertEquals(25, config.persistentKeepalive)
    }

    @Test(expected = IllegalArgumentException::class)
    fun `parse empty string throws IllegalArgumentException`() {
        TunnelConfig.parse("")
    }

    @Test
    fun `toConfString produces valid ini format`() {
        val config = TunnelConfig.parse(validConf)
        val output = config.toConfString()

        assertTrue("Should contain [Interface] section", output.contains("[Interface]"))
        assertTrue("Should contain [Peer] section", output.contains("[Peer]"))
        assertTrue(
            "Should contain PrivateKey line",
            output.contains("PrivateKey = EIv2XB4v7GWNW69nzzGHF+bQAPRiSm1xMALPCkpBt2o=")
        )
    }
}
