#!/usr/bin/env bash
#
# Server bootstrap for the WireGuard VPN host. Runs as root on a fresh Ubuntu 24.04
# Lightsail instance. Canonical copy: cli/vps_lightsail/core/resources/provision.sh
# (repo-root provision.sh is a symlink). Shared by the agent, the CLI and Terraform.
#
#   VPS_PEERS="shield iphone"   peer names in config order; peers[i] gets 10.8.0.(2+i)
#   PROVISION_DRY_RUN=1         only render keys + wg0.conf into $WG_DIR (tests)
#   WG_DIR=/etc/wireguard       WG_IFACE=<egress NIC, default: from `ip route`>
set -euo pipefail

VPS_PEERS="${VPS_PEERS:?VPS_PEERS must list peer names, e.g. VPS_PEERS='shield iphone'}"
WG_DIR="${WG_DIR:-/etc/wireguard}"
DRY_RUN="${PROVISION_DRY_RUN:-0}"

validate_peers() {
  local seen="" n=0 name
  for name in $VPS_PEERS; do
    [[ "$name" =~ ^[a-z][a-z0-9_]{0,15}$ ]] || { echo "invalid peer name: $name" >&2; exit 2; }
    [[ " $seen " == *" $name "* ]] && { echo "duplicate peer: $name" >&2; exit 2; }
    [[ "$name" == "server" ]] && { echo "reserved peer name: server" >&2; exit 2; }
    seen="$seen $name"; n=$((n + 1))
  done
  [ "$n" -ge 1 ] && [ "$n" -le 253 ] || { echo "need 1..253 peers" >&2; exit 2; }
}

system_setup() {
  cloud-init status --wait >/dev/null 2>&1 || true      # apt lock is held until cloud-init ends
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get -o Dpkg::Options::=--force-confold upgrade -y -qq
  sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
  systemctl restart ssh                                  # keeps TrustedUserCAKeys (D8 certs)
  ufw default deny incoming
  ufw default allow outgoing
  ufw allow 22/tcp
  ufw allow 51820/udp
  sed -i 's/DEFAULT_FORWARD_POLICY="DROP"/DEFAULT_FORWARD_POLICY="ACCEPT"/' /etc/default/ufw
  ufw --force enable
  apt-get install -y -qq wireguard
  echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-wireguard.conf   # idempotent (was >> sysctl.conf)
  sysctl -q --system
}

# Keys are generated only when missing, so a re-run keeps every client config valid.
ensure_key() {
  local name="$1"
  if [ ! -s "$WG_DIR/${name}_private.key" ]; then
    (umask 077; wg genkey > "$WG_DIR/${name}_private.key")
  fi
  wg pubkey < "$WG_DIR/${name}_private.key" > "$WG_DIR/${name}_public.key"
}

render_wg0() {
  local iface="${WG_IFACE:-$(ip route | awk '/^default/ {print $5; exit}')}" i=0 name
  {
    echo "[Interface]"
    echo "Address = 10.8.0.1/24"
    echo "ListenPort = 51820"
    echo "PrivateKey = $(cat "$WG_DIR/server_private.key")"
    echo "PostUp = iptables -A FORWARD -i wg0 -j ACCEPT; iptables -A FORWARD -o wg0 -j ACCEPT; iptables -t nat -A POSTROUTING -o $iface -j MASQUERADE"
    echo "PostDown = iptables -D FORWARD -i wg0 -j ACCEPT; iptables -D FORWARD -o wg0 -j ACCEPT; iptables -t nat -D POSTROUTING -o $iface -j MASQUERADE"
    for name in $VPS_PEERS; do
      echo ""
      echo "# peer: $name"
      echo "[Peer]"
      echo "PublicKey = $(cat "$WG_DIR/${name}_public.key")"
      echo "AllowedIPs = 10.8.0.$((2 + i))/32"
      i=$((i + 1))
    done
  } > "$WG_DIR/wg0.conf.new"
  chmod 600 "$WG_DIR/wg0.conf.new"
  mv "$WG_DIR/wg0.conf.new" "$WG_DIR/wg0.conf"
}

validate_peers
[ "$DRY_RUN" = 1 ] || system_setup
mkdir -p "$WG_DIR"; chmod 700 "$WG_DIR"
ensure_key server
for name in $VPS_PEERS; do ensure_key "$name"; done
render_wg0
[ "$DRY_RUN" = 1 ] && exit 0

systemctl enable wg-quick@wg0
systemctl restart wg-quick@wg0

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
