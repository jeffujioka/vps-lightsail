// $T/api/UiModelTest.kt — spec-04 §7.1 (4): the UI follows the backend, never the intent
package com.jeffujioka.vpslightsail.api

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class UiModelTest {
    private fun st(up: Boolean, cfg: Boolean, err: String? = null) =
        CompanionStatus(up, cfg, if (cfg) "10.8.0.2/32" else null, intentEnabled = true, lastError = err)

    @Test fun `up shows Tunnel UP and offers Disconnect`() {
        val ui = UiModel.from(st(up = true, cfg = true))
        assertEquals("Tunnel: UP", ui.statusLine); assertEquals(ToggleButton.DISCONNECT, ui.button)
    }
    @Test fun `down with a stored config offers Connect even while the intent is on`() {
        val ui = UiModel.from(st(up = false, cfg = true))
        assertEquals("Tunnel: DOWN", ui.statusLine); assertEquals("Config: stored (10.8.0.2/32)", ui.configLine)
        assertEquals(ToggleButton.CONNECT, ui.button)
    }
    @Test fun `no config hides the button and waits for a push`() {
        val ui = UiModel.from(st(up = false, cfg = false))
        assertEquals("Config: none — waiting for push", ui.configLine); assertEquals(ToggleButton.HIDDEN, ui.button)
    }
    @Test fun `error line only when lastError is set`() {
        assertNull(UiModel.from(st(up = false, cfg = true)).errorLine)
        assertEquals("Last error: Tunnel stopped by the system",
            UiModel.from(st(up = false, cfg = true, err = "Tunnel stopped by the system")).errorLine)
    }
}
