// $T/UiWiringTest.kt — plan 04b guard tests (D9): the UI shows the backend, one store per process
package com.jeffujioka.vpslightsail

import org.junit.Assert.assertEquals
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

class UiWiringTest {
    private val ns = "http://schemas.android.com/apk/res/android"
    private val main = File("src/main/java/com/jeffujioka/vpslightsail")
    private fun sources() = main.walkTopDown().filter { it.extension == "kt" }.toList()
    private fun rel(f: File) = f.relativeTo(main).path

    @Test fun `layout has configText right under statusText and a hidden red errorText (spec-04 §6)`() {
        val doc = DocumentBuilderFactory.newInstance().apply { isNamespaceAware = true }
            .newDocumentBuilder().parse(File("src/main/res/layout/activity_main.xml"))
        val views = doc.getElementsByTagName("TextView").let { l -> (0 until l.length).map { l.item(it) as Element } }
        val ids = views.map { it.getAttributeNS(ns, "id") }.filter { it.isNotEmpty() }
        assertEquals(ids.toString(), ids.indexOf("@+id/statusText") + 1, ids.indexOf("@+id/configText"))
        val err = views.single { it.getAttributeNS(ns, "id") == "@+id/errorText" }
        assertEquals("gone", err.getAttributeNS(ns, "visibility"))
        assertEquals("#ff8a80", err.getAttributeNS(ns, "textColor"))
    }

    @Test fun `the intent is never displayed - only the store and the api touch it (spec-04 §11 AC 9)`() {
        val readers = sources().filter { f -> f.readText().let { "tunnelEnabled" in it || "intentEnabled" in it } }
            .map(::rel).filterNot { it.startsWith("api/") || it.startsWith("persistence/") }
        assertEquals(emptyList<String>(), readers)
    }

    @Test fun `one ConfigStore per process - only ShieldVpnApp constructs it (spec-04 §5-2)`() {   // no '.' in JVM names
        val constructing = sources().filter { f -> f.readLines().any { "ConfigStore(" in it && "class ConfigStore(" !in it } }
            .map(::rel).sorted()
        assertEquals(listOf("ShieldVpnApp.kt"), constructing)
    }
}
