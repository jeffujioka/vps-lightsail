package com.jeffujioka.vpslightsail

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.net.VpnService
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.jeffujioka.vpslightsail.persistence.ConfigStore

class MainActivity : AppCompatActivity() {

    private lateinit var statusText: TextView
    private lateinit var tokenText: TextView
    private lateinit var vpnToggleButton: Button
    private lateinit var configStore: ConfigStore

    private val stateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action == VpnControlService.ACTION_STATE_CHANGED) {
                refreshUI()
            }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        statusText = findViewById(R.id.statusText)
        tokenText = findViewById(R.id.tokenText)
        vpnToggleButton = findViewById(R.id.vpnToggleButton)
        configStore = ConfigStore(this)

        vpnToggleButton.setOnClickListener { onToggleVpn() }

        val vpnIntent = VpnService.prepare(this)
        if (vpnIntent != null) {
            startActivityForResult(vpnIntent, VPN_REQUEST_CODE)
        }
    }

    override fun onResume() {
        super.onResume()
        registerReceiver(
            stateReceiver,
            IntentFilter(VpnControlService.ACTION_STATE_CHANGED),
            RECEIVER_NOT_EXPORTED
        )
        refreshUI()
    }

    override fun onPause() {
        super.onPause()
        unregisterReceiver(stateReceiver)
    }

    private fun onToggleVpn() {
        if (configStore.tunnelEnabled) {
            startService(Intent(this, VpnControlService::class.java).apply {
                action = VpnControlService.ACTION_DISCONNECT
            })
        } else if (configStore.lastConfig != null) {
            startService(Intent(this, VpnControlService::class.java).apply {
                action = VpnControlService.ACTION_CONNECT
            })
        }
    }

    private fun refreshUI() {
        tokenText.text = configStore.authToken
        val connected = configStore.tunnelEnabled
        val hasConfig = configStore.lastConfig != null

        statusText.text = if (connected) "Status: Connected" else "Status: Waiting for config..."

        when {
            connected -> {
                vpnToggleButton.visibility = View.VISIBLE
                vpnToggleButton.text = "Disconnect"
            }
            hasConfig -> {
                vpnToggleButton.visibility = View.VISIBLE
                vpnToggleButton.text = "Reconnect"
            }
            else -> vpnToggleButton.visibility = View.GONE
        }
    }

    companion object {
        private const val VPN_REQUEST_CODE = 100
    }
}
