package com.jeffujioka.vpslightsail.api

object StatusJson {
    fun render(s: CompanionStatus): String = buildString {
        append("{\"api\": ").append(CompanionContract.API_VERSION)
        append(", \"tunnel\": \"").append(if (s.tunnelUp) "up" else "down").append('"')
        append(", \"has_config\": ").append(s.hasConfig)
        append(", \"address\": ")
        append(s.address?.let { "\"" + it.replace("\\", "\\\\").replace("\"", "\\\"") + "\"" } ?: "null")
        append('}')
    }
}
