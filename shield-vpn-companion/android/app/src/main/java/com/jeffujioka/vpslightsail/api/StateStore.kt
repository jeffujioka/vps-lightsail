package com.jeffujioka.vpslightsail.api

interface StateStore {
    /** The last config pushed via /config, or null if none was ever stored. */
    var lastConfig: String?
    /** User/API intent: should the tunnel be up? Never read by status() directly. */
    var tunnelEnabled: Boolean
}
