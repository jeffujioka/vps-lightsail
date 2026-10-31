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
import androidx.core.content.ContextCompat
import com.jeffujioka.vpslightsail.api.ToggleButton
import com.jeffujioka.vpslightsail.api.UiModel

/** Truthful UI (spec-04 §6): every label comes from app.api.status() — the backend, never the intent. */
class MainActivity : AppCompatActivity() {

    private lateinit var statusText: TextView
    private lateinit var configText: TextView
    private lateinit var errorText: TextView
    private lateinit var tokenText: TextView
    private lateinit var vpnToggleButton: Button

    private val stateReceiver = object : BroadcastReceiver() {        // bug #7: every api mutation lands here
        override fun onReceive(context: Context, intent: Intent) = refreshUI()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        statusText = findViewById(R.id.statusText)
        configText = findViewById(R.id.configText)
        errorText = findViewById(R.id.errorText)
        tokenText = findViewById(R.id.tokenText)
        vpnToggleButton = findViewById(R.id.vpnToggleButton)
        vpnToggleButton.setOnClickListener { onToggleVpn() }
        VpnService.prepare(this)?.let { startActivityForResult(it, VPN_REQUEST_CODE) }   // consent, once (spec-04 §5.6)
    }

    override fun onResume() {
        super.onResume()
        ContextCompat.registerReceiver(this, stateReceiver, IntentFilter(VpnControlService.ACTION_STATE_CHANGED),
            ContextCompat.RECEIVER_NOT_EXPORTED)
        refreshUI()
    }

    override fun onPause() {
        super.onPause()
        unregisterReceiver(stateReceiver)
    }

    private val app get() = application as ShieldVpnApp

    private fun refreshUI() {                       // main thread; api.status() is lock-free
        val ui = UiModel.from(app.api.status())
        statusText.text = ui.statusLine
        configText.text = ui.configLine
        errorText.text = ui.errorLine.orEmpty()
        errorText.visibility = if (ui.errorLine == null) View.GONE else View.VISIBLE
        tokenText.text = app.configStore.authToken
        vpnToggleButton.isEnabled = true
        when (ui.button) {
            ToggleButton.DISCONNECT -> { vpnToggleButton.visibility = View.VISIBLE; vpnToggleButton.text = "Disconnect" }
            ToggleButton.CONNECT -> { vpnToggleButton.visibility = View.VISIBLE; vpnToggleButton.text = "Connect" }
            ToggleButton.HIDDEN -> vpnToggleButton.visibility = View.GONE
        }
    }

    private fun onToggleVpn() {
        val action = if (app.api.status().tunnelUp) VpnControlService.ACTION_DISCONNECT
                     else VpnControlService.ACTION_CONNECT
        vpnToggleButton.isEnabled = false          // re-enabled by the next STATE_CHANGED refresh
        startService(Intent(this, VpnControlService::class.java).setAction(action))
    }

    companion object {
        private const val VPN_REQUEST_CODE = 100
    }
}
