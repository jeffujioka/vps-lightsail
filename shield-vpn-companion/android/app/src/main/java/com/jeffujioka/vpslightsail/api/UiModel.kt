package com.jeffujioka.vpslightsail.api

enum class ToggleButton { CONNECT, DISCONNECT, HIDDEN }

data class UiModel(val statusLine: String, val configLine: String, val errorLine: String?,
                   val button: ToggleButton) {
    companion object {
        fun from(s: CompanionStatus) = UiModel(
            statusLine = if (s.tunnelUp) "Tunnel: UP" else "Tunnel: DOWN",
            configLine = if (s.hasConfig) "Config: stored (${s.address ?: "unparseable"})"
                         else "Config: none — waiting for push",
            errorLine = s.lastError?.let { "Last error: $it" },
            button = when {
                s.tunnelUp -> ToggleButton.DISCONNECT
                s.hasConfig -> ToggleButton.CONNECT
                else -> ToggleButton.HIDDEN
            },
        )
    }
}
