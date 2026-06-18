package com.jeffujioka.shieldvpn

import android.app.Application

class ShieldVpnApp : Application() {

    override fun onCreate() {
        super.onCreate()
        // TODO: initialize singletons (config store, tunnel manager, HTTP server)
    }
}
