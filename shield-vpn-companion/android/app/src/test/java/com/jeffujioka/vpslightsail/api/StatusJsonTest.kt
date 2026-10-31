package com.jeffujioka.vpslightsail.api

import org.junit.Assert.assertEquals
import org.junit.Test

class StatusJsonTest {
    @Test fun `up with a config renders the spec-01 golden`() = assertEquals(
        """{"api": 2, "tunnel": "up", "has_config": true, "address": "10.8.0.2/32"}""",
        StatusJson.render(CompanionStatus(true, true, "10.8.0.2/32", intentEnabled = true, lastError = null)))

    @Test fun `down without a config renders address null and never intent or lastError (R14)`() = assertEquals(
        """{"api": 2, "tunnel": "down", "has_config": false, "address": null}""",
        StatusJson.render(CompanionStatus(false, false, null, intentEnabled = true, lastError = "Restore failed: x")))

    @Test fun `quotes and backslashes in the address are escaped`() = assertEquals(
        """{"api": 2, "tunnel": "down", "has_config": true, "address": "a\"b\\c"}""",
        StatusJson.render(CompanionStatus(false, true, "a\"b\\c", intentEnabled = false, lastError = null)))
}
