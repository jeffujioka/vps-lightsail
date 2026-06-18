# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.kts.
#
# For more details, see
#   http://developer.android.com/guide/developing/tools/proguard.html

# WireGuard tunnel library
-keep class com.wireguard.** { *; }

# NanoHTTPD
-keep class fi.iki.elonen.** { *; }
