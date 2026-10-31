// WireGuardTunnelTest.kt — D2 (Review Focus 5): no android.* call runs on the JVM
package com.jeffujioka.vpslightsail.vpn

import com.wireguard.android.backend.BackendException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Test

class WireGuardTunnelTest {
    @Test fun `a message-less BackendException surfaces its reason name, other failures pass through (D2)`() {
        val e = WireGuardTunnel.failureOf(BackendException(BackendException.Reason.VPN_NOT_AUTHORIZED))
        assertEquals("VPN_NOT_AUTHORIZED", e.message)
        val parse = IllegalArgumentException("Missing [Peer] section")
        assertSame(parse, WireGuardTunnel.failureOf(parse))
    }
}
