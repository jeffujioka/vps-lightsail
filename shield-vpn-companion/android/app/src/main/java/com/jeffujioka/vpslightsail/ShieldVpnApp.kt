package com.jeffujioka.vpslightsail

import android.app.Application
import android.content.Intent

class ShieldVpnApp : Application() {
    override fun onCreate() {
        super.onCreate()
        startForegroundService(Intent(this, VpnControlService::class.java))
    }
}
