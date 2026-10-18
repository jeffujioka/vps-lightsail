package com.jeffujioka.vpslightsail.api

data class ApiResponse(val status: Int, val body: String, val mime: String = MIME_TEXT) {
    companion object {
        const val MIME_TEXT = "text/plain; charset=utf-8"
        const val MIME_JSON = "application/json"
    }
}

data class CompanionStatus(
    val tunnelUp: Boolean,
    val hasConfig: Boolean,
    val address: String?,
    val intentEnabled: Boolean,
    val lastError: String?,
)
