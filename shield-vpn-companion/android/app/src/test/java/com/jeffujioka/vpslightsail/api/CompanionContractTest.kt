package com.jeffujioka.vpslightsail.api

import org.junit.Assert.assertEquals
import org.junit.Test

class CompanionContractTest {
    @Test fun `TXT records carry the token and api=2, token first`() {
        val txt = CompanionContract.txtRecords("t")
        assertEquals(mapOf("token" to "t", "api" to "2"), txt)
        assertEquals(listOf("token", "api"), txt.keys.toList())
    }
}
