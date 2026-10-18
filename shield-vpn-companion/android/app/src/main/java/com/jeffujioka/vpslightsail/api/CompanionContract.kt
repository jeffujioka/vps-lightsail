package com.jeffujioka.vpslightsail.api

object CompanionContract {
    const val API_VERSION = 2
    const val TXT_TOKEN = "token"
    const val TXT_API = "api"
    const val MSG_ACTIVATED = "OK — tunnel activated"
    const val MSG_STORED = "OK — config stored"
    const val MSG_CONNECTED = "Connected"
    const val MSG_DISCONNECTED = "Disconnected"
    const val MSG_NO_CONFIG = "No stored config"
    const val MSG_EMPTY = "Empty config"
    const val MAX_CONFIG_BYTES = 16 * 1024

    fun txtRecords(token: String): Map<String, String> =
        linkedMapOf(TXT_TOKEN to token, TXT_API to API_VERSION.toString())
}
