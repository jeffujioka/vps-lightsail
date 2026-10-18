package com.jeffujioka.vpslightsail.api

class InMemoryStateStore(override var lastConfig: String? = null,
                         override var tunnelEnabled: Boolean = false) : StateStore
