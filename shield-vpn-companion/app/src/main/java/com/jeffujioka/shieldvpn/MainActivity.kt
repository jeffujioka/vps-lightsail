package com.jeffujioka.shieldvpn

import android.content.Intent
import android.net.VpnService
import android.os.Bundle
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {

    private lateinit var statusText: TextView
    private lateinit var tokenText: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        statusText = findViewById(R.id.statusText)
        tokenText = findViewById(R.id.tokenText)

        val configStore = (application as ShieldVpnApp).configStore

        tokenText.text = configStore.authToken

        updateStatus(configStore.tunnelEnabled)

        val vpnIntent = VpnService.prepare(this)
        if (vpnIntent != null) {
            startActivityForResult(vpnIntent, VPN_REQUEST_CODE)
        }
    }

    override fun onResume() {
        super.onResume()
        val configStore = (application as ShieldVpnApp).configStore
        updateStatus(configStore.tunnelEnabled)
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == VPN_REQUEST_CODE && resultCode == RESULT_OK) {
            val configStore = (application as ShieldVpnApp).configStore
            updateStatus(configStore.tunnelEnabled)
        }
    }

    private fun updateStatus(tunnelEnabled: Boolean) {
        statusText.text = if (tunnelEnabled) {
            "Status: Connected"
        } else {
            "Status: Waiting for config..."
        }
    }

    companion object {
        private const val VPN_REQUEST_CODE = 100
    }
}
