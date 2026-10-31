package com.jeffujioka.vpslightsail.api

fun conf(address: String = "10.8.0.2/32") = """
    [Interface]
    PrivateKey = EIv2XB4v7GWNW69nzzGHF+bQAPRiSm1xMALPCkpBt2o=
    Address = $address
    DNS = 1.1.1.1

    [Peer]
    PublicKey = Cl4s7JNo/TGQdwF9EXIadX+yNnSJ0tJ7XEKGvmQPuWE=
    Endpoint = 192.0.2.1:51820
    AllowedIPs = 10.8.0.0/24
    PersistentKeepalive = 25
""".trimIndent()

val CONF = conf()
