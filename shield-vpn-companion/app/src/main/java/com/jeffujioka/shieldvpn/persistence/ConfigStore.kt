package com.jeffujioka.shieldvpn.persistence

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys
import java.security.SecureRandom

class ConfigStore(context: Context) {

    private val prefs = EncryptedSharedPreferences.create(
        "shield_vpn_config",
        MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC),
        context,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )

    var authToken: String
        get() {
            val stored = prefs.getString(KEY_AUTH_TOKEN, null)
            if (stored != null) return stored
            val generated = generateToken()
            prefs.edit().putString(KEY_AUTH_TOKEN, generated).apply()
            return generated
        }
        set(value) {
            prefs.edit().putString(KEY_AUTH_TOKEN, value).apply()
        }

    var lastConfig: String?
        get() = prefs.getString(KEY_LAST_CONFIG, null)
        set(value) {
            prefs.edit().putString(KEY_LAST_CONFIG, value).apply()
        }

    var tunnelEnabled: Boolean
        get() = prefs.getBoolean(KEY_TUNNEL_ENABLED, false)
        set(value) {
            prefs.edit().putBoolean(KEY_TUNNEL_ENABLED, value).apply()
        }

    companion object {
        private const val KEY_AUTH_TOKEN = "auth_token"
        private const val KEY_LAST_CONFIG = "last_config"
        private const val KEY_TUNNEL_ENABLED = "tunnel_enabled"

        fun generateToken(): String {
            val bytes = ByteArray(32)
            SecureRandom().nextBytes(bytes)
            return bytes.joinToString("") { "%02x".format(it) }
        }
    }
}
