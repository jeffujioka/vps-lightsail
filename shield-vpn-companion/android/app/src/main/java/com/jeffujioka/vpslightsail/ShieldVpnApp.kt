package com.jeffujioka.vpslightsail

import android.app.Application
import android.content.Intent
import com.jeffujioka.vpslightsail.api.CompanionApi
import com.jeffujioka.vpslightsail.persistence.ConfigStore
import com.jeffujioka.vpslightsail.vpn.WireGuardTunnel
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors

class ShieldVpnApp : Application() {
    lateinit var configStore: ConfigStore; private set
    lateinit var api: CompanionApi; private set
    val executor: ExecutorService = Executors.newSingleThreadExecutor()

    override fun onCreate() {
        super.onCreate()
        configStore = ConfigStore(this)
        lateinit var apiRef: CompanionApi
        val tunnel = WireGuardTunnel(this) { up -> apiRef.onBackendStateChanged(up) }
        api = CompanionApi(tunnel, configStore,
            onChange = { sendBroadcast(Intent(VpnControlService.ACTION_STATE_CHANGED).setPackage(packageName)) },
            dispatch = { executor.execute(it) })
        apiRef = api
        startForegroundService(Intent(this, VpnControlService::class.java))
    }
}
