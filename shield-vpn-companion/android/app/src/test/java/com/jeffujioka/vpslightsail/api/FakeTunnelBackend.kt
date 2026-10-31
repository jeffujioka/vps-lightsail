package com.jeffujioka.vpslightsail.api

class FakeTunnelBackend : TunnelBackend {
    var up = false; var failNextConnect: Exception? = null; var lastConfig: String? = null
    val calls = mutableListOf<String>()
    var onState: (Boolean) -> Unit = {}       // simulates GoBackend's synchronous callback
    override val isConnected get() = up
    override fun connect(config: String) { calls += "connect"
        failNextConnect?.let { failNextConnect = null; throw it }; up = true; lastConfig = config; onState(true) }
    override fun disconnect() { calls += "disconnect"; if (up) { up = false; onState(false) } }
}
