// plan 04b guard tests (D9): process wiring the compiler cannot check
package com.jeffujioka.vpslightsail

import com.jeffujioka.vpslightsail.boot.BootActions
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

class WiringTest {
    private val ns = "http://schemas.android.com/apk/res/android"
    private val main = File("src/main/java/com/jeffujioka/vpslightsail")

    private fun bootReceiverActions(): Set<String> {
        val doc = DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
            .newDocumentBuilder().parse(File("src/main/AndroidManifest.xml"))
        val receivers = doc.getElementsByTagName("receiver")
        val boot = (0 until receivers.length).map { receivers.item(it) as Element }
            .single { it.getAttributeNS(ns, "name") == ".boot.BootReceiver" }
        val actions = boot.getElementsByTagName("action")
        return (0 until actions.length).map { (actions.item(it) as Element).getAttributeNS(ns, "name") }.toSet()
    }

    @Test fun `the manifest delivers exactly the actions BootReceiver handles, MY_PACKAGE_REPLACED included`() {
        assertEquals(BootActions.ALL, bootReceiverActions())
        assertTrue("android.intent.action.MY_PACKAGE_REPLACED" in bootReceiverActions())
    }

    @Test fun `BootReceiver starts the service on boot and on our own package replace only`() {
        listOf("android.intent.action.BOOT_COMPLETED", "android.intent.action.LOCKED_BOOT_COMPLETED",
            "android.intent.action.MY_PACKAGE_REPLACED").forEach { assertTrue(it, BootActions.handles(it)) }
        assertFalse(BootActions.handles("android.intent.action.PACKAGE_REPLACED"))
        assertFalse(BootActions.handles(null))
    }

    @Test fun `one WireGuardTunnel per process - only ShieldVpnApp constructs it (F8)`() {
        val constructing = main.walkTopDown().filter { it.extension == "kt" }
            .filter { f -> f.readLines().any { "WireGuardTunnel(" in it && "class WireGuardTunnel(" !in it } }
            .map { it.relativeTo(main).path }.toList()
        assertEquals(listOf("ShieldVpnApp.kt"), constructing)
    }
}
