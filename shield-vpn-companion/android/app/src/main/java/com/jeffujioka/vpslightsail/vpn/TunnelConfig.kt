package com.jeffujioka.vpslightsail.vpn

data class TunnelConfig(
    val privateKey: String,
    val address: String,
    val dns: String,
    val peerPublicKey: String,
    val endpoint: String,
    val allowedIPs: String,
    val persistentKeepalive: Int = 25
) {
    fun toConfString(): String = buildString {
        appendLine("[Interface]")
        appendLine("PrivateKey = $privateKey")
        appendLine("Address = $address")
        appendLine("DNS = $dns")
        appendLine()
        appendLine("[Peer]")
        appendLine("PublicKey = $peerPublicKey")
        appendLine("Endpoint = $endpoint")
        appendLine("AllowedIPs = $allowedIPs")
        append("PersistentKeepalive = $persistentKeepalive")
    }

    companion object {
        fun parse(conf: String): TunnelConfig {
            if (conf.isBlank()) throw IllegalArgumentException("Config string is empty")

            val sections = mutableMapOf<String, MutableMap<String, String>>()
            var currentSection: String? = null

            for (rawLine in conf.lines()) {
                val line = rawLine.trim()
                when {
                    line.isEmpty() || line.startsWith("#") -> continue
                    line.startsWith("[") && line.endsWith("]") -> {
                        currentSection = line.substring(1, line.length - 1)
                        sections.getOrPut(currentSection) { mutableMapOf() }
                    }
                    line.contains("=") -> {
                        val delimIndex = line.indexOf('=')
                        val key = line.substring(0, delimIndex).trim()
                        val value = line.substring(delimIndex + 1).trim()
                        currentSection?.let { sections.getOrPut(it) { mutableMapOf() }[key] = value }
                            ?: throw IllegalArgumentException("Key-value pair found outside of a section: $line")
                    }
                    else -> throw IllegalArgumentException("Unrecognized line format: $line")
                }
            }

            val iface = sections["Interface"]
                ?: throw IllegalArgumentException("Missing [Interface] section")
            val peer = sections["Peer"]
                ?: throw IllegalArgumentException("Missing [Peer] section")

            fun require(section: Map<String, String>, sectionName: String, key: String): String =
                section[key] ?: throw IllegalArgumentException("Missing $key in [$sectionName]")

            val keepaliveStr = peer["PersistentKeepalive"]
            val keepalive = if (keepaliveStr != null) {
                keepaliveStr.toIntOrNull()
                    ?: throw IllegalArgumentException("PersistentKeepalive must be an integer, got: $keepaliveStr")
            } else {
                25
            }

            return TunnelConfig(
                privateKey = require(iface, "Interface", "PrivateKey"),
                address = require(iface, "Interface", "Address"),
                dns = require(iface, "Interface", "DNS"),
                peerPublicKey = require(peer, "Peer", "PublicKey"),
                endpoint = require(peer, "Peer", "Endpoint"),
                allowedIPs = require(peer, "Peer", "AllowedIPs"),
                persistentKeepalive = keepalive
            )
        }
    }
}
