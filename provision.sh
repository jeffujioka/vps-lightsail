#!/usr/bin/env bash
#
# Server bootstrap for the WireGuard VPN host.
# Runs as root on a fresh Ubuntu 24.04 Lightsail instance.
# Shared by setup.sh (piped to `sudo bash`) and the Terraform provisioner,
# so the provisioning logic lives in one place.
set -e

apt-get update -qq && apt-get upgrade -y -qq

sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart ssh

ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 51820/udp
sed -i 's/DEFAULT_FORWARD_POLICY="DROP"/DEFAULT_FORWARD_POLICY="ACCEPT"/' /etc/default/ufw
ufw --force enable

apt-get install -y wireguard qrencode

echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
sysctl -p

cd /etc/wireguard
wg genkey | tee server_private.key | wg pubkey | tee server_public.key
wg genkey | tee mac_private.key    | wg pubkey | tee mac_public.key
wg genkey | tee iphone_private.key | wg pubkey | tee iphone_public.key
chmod 600 /etc/wireguard/*.key

IFACE=$(ip route | grep default | awk '{print $5}')
cat > /etc/wireguard/wg0.conf << EOF
[Interface]
Address = 10.8.0.1/24
ListenPort = 51820
PrivateKey = $(cat server_private.key)
PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -A FORWARD -o wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o $IFACE -j MASQUERADE
PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o $IFACE -j MASQUERADE

[Peer]
PublicKey = $(cat mac_public.key)
AllowedIPs = 10.8.0.2/32

[Peer]
PublicKey = $(cat iphone_public.key)
AllowedIPs = 10.8.0.3/32
EOF
chmod 600 /etc/wireguard/wg0.conf

systemctl enable wg-quick@wg0
systemctl start wg-quick@wg0

# Network watchdog. On 2026-09-11 an unattended systemd upgrade re-exec'd
# PID 1, systemd-networkd hit "Could not set route: Connection timed out",
# and the box sat offline for two weeks with nothing noticing. Every 2 min:
# after 2 failed checks reconfigure the NIC, after 5 (~10 min) reboot.
# The counter lives in /run (tmpfs), so it resets on every boot.
cat > /usr/local/sbin/net-watchdog << 'EOF'
#!/bin/sh
STATE=/run/net-watchdog.failures
if ping -c 3 -W 3 1.1.1.1 >/dev/null 2>&1 || ping -c 3 -W 3 8.8.8.8 >/dev/null 2>&1; then
  rm -f "$STATE"
  exit 0
fi
n=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$STATE"
IFACE=$(ip -o link show | awk -F': ' '$2 ~ /^en/ {print $2; exit}')
logger -t net-watchdog "connectivity check failed ($n/5) on $IFACE"
if [ "$n" -ge 5 ]; then
  logger -t net-watchdog "rebooting"
  systemctl reboot
elif [ "$n" -ge 2 ]; then
  networkctl reconfigure "$IFACE" || systemctl restart systemd-networkd
fi
EOF
chmod 755 /usr/local/sbin/net-watchdog

cat > /etc/systemd/system/net-watchdog.service << 'EOF'
[Unit]
Description=Reconfigure or reboot when outbound connectivity is lost

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/net-watchdog
EOF

cat > /etc/systemd/system/net-watchdog.timer << 'EOF'
[Unit]
Description=Run net-watchdog every 2 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now net-watchdog.timer
