#!/usr/bin/env bash
set -euo pipefail

# ── defaults ──────────────────────────────────────────────────────────────────
REGION="${AWS_REGION:-sa-east-1}"
ACTION="${1:-}"
MARIA_PROJECT="~/projects/guicheffer/vps-lightsail"
MARIA_ENV='export PATH="$HOME/bin:$PATH" && export AWS_PROFILE=personal'

# ── help ──────────────────────────────────────────────────────────────────────
usage() {
  cat <<'EOF'
┌─────────────────────────────────────────────────────────────────┐
│  run-on-maria.sh — VPN setup via Maria's Mac (all network ops)  │
└─────────────────────────────────────────────────────────────────┘

USAGE
  ./run-on-maria.sh <command> [options]

COMMANDS
  setup       Create the VPS + WireGuard in AWS Lightsail
  teardown    Destroy all resources (stops billing)
  status      Show WireGuard peers and handshake info
  config      Re-fetch client configs (mac-vpn.conf + iPhone QR)
  plan        Dry-run — show what Terraform would create/destroy

OPTIONS
  --region <code>   AWS region (default: sa-east-1 / São Paulo)
  --help, -h        Show this help

COMMON REGIONS
  sa-east-1     São Paulo, Brazil     (IP brasileiro)
  us-east-1     Virginia, USA         (conteúdo US)
  eu-west-1     Ireland               (conteúdo EU)
  ap-northeast-1  Tokyo, Japan        (conteúdo JP)

EXAMPLES
  ./run-on-maria.sh setup                    # VPN em São Paulo
  ./run-on-maria.sh setup --region us-east-1 # VPN nos EUA
  ./run-on-maria.sh teardown                 # destrói tudo
  ./run-on-maria.sh status                   # verifica peers

PREREQUISITES (on Maria's Mac — already installed)
  ~/bin/aws          AWS CLI v2
  ~/bin/terraform    Terraform
  ~/.aws/            credentials com profile "personal"
  ~/.ssh/id_ed25519  SSH key pair

HOW IT WORKS
  1. SSH into Maria's Mac (must be on same network)
  2. Runs terraform apply on the project cloned at:
     ~/projects/guicheffer/vps-lightsail/terraform
  3. Fetches WireGuard configs back to this Mac
  4. All network traffic goes through Maria — nothing installed locally

AFTER SETUP
  • Mac:    WireGuard app → + → Import tunnel → mac-vpn.conf
  • iPhone: WireGuard app → + → Create from QR code → scan terminal output
  • Test:   connect VPN, visit https://ifconfig.me

COST
  ~$5/month flat (Lightsail nano). Static IP free while attached.
  Run "teardown" when not using to stop charges.
EOF
}

# ── arg parsing ───────────────────────────────────────────────────────────────
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$ACTION" || "$ACTION" == "--help" || "$ACTION" == "-h" ]]; then
  usage
  exit 0
fi

AZ="${REGION}a"
TF_VARS="TF_VAR_aws_region=$REGION TF_VAR_availability_zone=$AZ"

log() { echo "▶ $*"; }

maria() {
  ssh maria "$MARIA_ENV && $1"
}

# ── commands ──────────────────────────────────────────────────────────────────
case "$ACTION" in
  setup)
    log "Creating VPN in $REGION via Maria..."
    maria "cd $MARIA_PROJECT/terraform && terraform init -input=false -no-color"
    maria "cd $MARIA_PROJECT/terraform && $TF_VARS terraform apply -auto-approve -no-color"

    log "Fetching client configs..."
    SERVER_IP=$(maria "cd $MARIA_PROJECT/terraform && terraform output -raw server_ip")
    KEY_PATH=$(maria "cd $MARIA_PROJECT/terraform && terraform output -raw ssh_private_key_path")

    maria "cd $MARIA_PROJECT && bash fetch-config.sh $SERVER_IP $KEY_PATH"

    log "Pulling mac-vpn.conf to local machine..."
    scp maria:"$MARIA_PROJECT/mac-vpn.conf" "$(dirname "$0")/mac-vpn.conf"

    # ── Auto-push VPN config to discovered devices ──────────────────────────────
    discover_and_push_devices() {
      local server_ip="$1" key_path="$2" server_public="$3"

      echo ""
      echo "🔍 Scanning for devices on the network (5s)..."
      local devices=()
      local ips=()

      # Browse for _vpnconfig._tcp services (5 second timeout)
      local browse_output
      browse_output=$(timeout 5 dns-sd -B _vpnconfig._tcp local 2>/dev/null || true)

      # Extract instance names
      local names=()
      while IFS= read -r line; do
        if [[ "$line" == *"Add"*"_vpnconfig._tcp"* ]]; then
          local name
          name=$(echo "$line" | awk -F'_vpnconfig._tcp.' '{print $2}' | xargs)
          [ -n "$name" ] && names+=("$name")
        fi
      done <<< "$browse_output"

      if [ ${#names[@]} -eq 0 ]; then
        echo "  No devices found."
        read -rp "  Scan again? [y/N]: " retry
        if [[ "$retry" =~ ^[Yy]$ ]]; then
          discover_and_push_devices "$server_ip" "$key_path" "$server_public"
          return
        fi
        return
      fi

      # Resolve each name to an IP
      for name in "${names[@]}"; do
        local resolved
        resolved=$(timeout 3 dns-sd -G v4 "${name}.local" 2>/dev/null | grep -m1 "Addr" | awk '{print $NF}' || true)
        if [ -n "$resolved" ]; then
          devices+=("$name")
          ips+=("$resolved")
        fi
      done

      if [ ${#devices[@]} -eq 0 ]; then
        echo "  Devices found but could not resolve IPs."
        return
      fi

      # Display interactive menu
      echo ""
      echo "  Found ${#devices[@]} device(s):"
      for i in "${!devices[@]}"; do
        echo "    $((i+1))) ${devices[$i]} (${ips[$i]})"
      done
      echo "    a) All"
      echo ""
      read -rp "  Configure which? [a]: " selection
      selection="${selection:-a}"

      # Parse selection
      local selected_indices=()
      if [[ "$selection" == "a" || "$selection" == "all" ]]; then
        for i in "${!devices[@]}"; do selected_indices+=("$i"); done
      else
        IFS=',' read -ra parts <<< "$selection"
        for part in "${parts[@]}"; do
          local idx=$((part - 1))
          if [ "$idx" -ge 0 ] && [ "$idx" -lt "${#devices[@]}" ]; then
            selected_indices+=("$idx")
          fi
        done
      fi

      if [ ${#selected_indices[@]} -eq 0 ]; then
        echo "  No valid selection."
        return
      fi

      # Allocate peer IPs starting at .2
      local peer_base=2
      echo ""
      for idx in "${selected_indices[@]}"; do
        local device_name="${devices[$idx]}"
        local device_ip="${ips[$idx]}"
        local peer_ip="10.8.0.$peer_base"

        log "Configuring $device_name ($device_ip) as peer $peer_ip..."

        # Generate keypair and add peer on server
        local peer_private peer_public
        peer_private=$(maria "ssh -i $key_path -o StrictHostKeyChecking=no ubuntu@$server_ip 'wg genkey'" 2>/dev/null)
        peer_public=$(echo "$peer_private" | maria "ssh -i $key_path -o StrictHostKeyChecking=no ubuntu@$server_ip 'wg pubkey'" 2>/dev/null)

        maria "ssh -i $key_path -o StrictHostKeyChecking=no ubuntu@$server_ip 'sudo bash -s'" <<PEER_EOF
wg set wg0 peer $peer_public allowed-ips ${peer_ip}/32
grep -q "$peer_public" /etc/wireguard/wg0.conf 2>/dev/null || {
  echo "" >> /etc/wireguard/wg0.conf
  echo "[Peer]" >> /etc/wireguard/wg0.conf
  echo "PublicKey = $peer_public" >> /etc/wireguard/wg0.conf
  echo "AllowedIPs = ${peer_ip}/32" >> /etc/wireguard/wg0.conf
}
PEER_EOF

        # Build config for this device
        local device_conf="[Interface]
PrivateKey = $peer_private
Address = ${peer_ip}/32
DNS = 1.1.1.1

[Peer]
PublicKey = $server_public
Endpoint = ${server_ip}:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25"

        # Look for SHIELD_VPN_TOKEN_<SANITIZED_NAME> or fallback to SHIELD_VPN_TOKEN
        local sanitized
        sanitized=$(echo "$device_name" | tr -cs 'a-zA-Z0-9' '_' | tr '[:lower:]' '[:upper:]')
        local token_var="SHIELD_VPN_TOKEN_${sanitized}"
        local token="${!token_var:-${SHIELD_VPN_TOKEN:-}}"

        if [ -z "$token" ]; then
          echo "  ⚠️  No token for $device_name (set $token_var or SHIELD_VPN_TOKEN)"
          echo "  📋 Config saved to ${device_name// /_}-vpn.conf"
          echo "$device_conf" > "$(dirname "$0")/${device_name// /_}-vpn.conf"
        else
          local http_status
          http_status=$(curl -s -o /dev/null -w "%{http_code}" \
            -X POST "http://${device_ip}:8080/config" \
            -H "Authorization: Bearer $token" \
            -H "Content-Type: text/plain" \
            -d "$device_conf")

          if [[ "$http_status" == "200" ]]; then
            echo "  ✅ $device_name activated (peer $peer_ip)"
          else
            echo "  ⚠️  $device_name returned HTTP $http_status (token correct?)"
            echo "$device_conf" > "$(dirname "$0")/${device_name// /_}-vpn.conf"
          fi
        fi

        peer_base=$((peer_base + 1))
      done
    }

    # Fetch server public key and call discovery
    SERVER_PUBLIC=$(maria "ssh -i $KEY_PATH -o StrictHostKeyChecking=no ubuntu@$SERVER_IP 'sudo cat /etc/wireguard/server_public.key'")
    discover_and_push_devices "$SERVER_IP" "$KEY_PATH" "$SERVER_PUBLIC"
    # ────────────────────────────────────────────────────────────────────────────

    echo ""
    echo "✅ VPN ready! Server: $SERVER_IP ($REGION)"
    echo ""
    echo "  Devices configured via auto-push above."
    echo "  Any device without a token got a .conf file saved locally."
    echo "  Test → connect VPN, visit https://ifconfig.me"
    echo ""
    echo "  To tear down: ./run-on-maria.sh teardown"
    ;;

  teardown)
    log "Destroying all resources in $REGION via Maria..."
    maria "cd $MARIA_PROJECT/terraform && $TF_VARS terraform destroy -auto-approve -no-color"
    rm -f "$(dirname "$0")/mac-vpn.conf"
    echo "✅ All resources destroyed. No more charges."
    ;;

  status)
    log "Checking WireGuard status..."
    SERVER_IP=$(maria "cd $MARIA_PROJECT/terraform && terraform output -raw server_ip 2>/dev/null" || true)
    if [[ -z "$SERVER_IP" ]]; then
      echo "No running VPN found (no Terraform state)."
      exit 1
    fi
    KEY_PATH=$(maria "cd $MARIA_PROJECT/terraform && terraform output -raw ssh_private_key_path")
    maria "ssh -i $KEY_PATH -o StrictHostKeyChecking=no ubuntu@$SERVER_IP 'sudo wg show'"
    ;;

  config)
    log "Re-fetching client configs..."
    SERVER_IP=$(maria "cd $MARIA_PROJECT/terraform && terraform output -raw server_ip")
    KEY_PATH=$(maria "cd $MARIA_PROJECT/terraform && terraform output -raw ssh_private_key_path")
    maria "cd $MARIA_PROJECT && bash fetch-config.sh $SERVER_IP $KEY_PATH"
    scp maria:"$MARIA_PROJECT/mac-vpn.conf" "$(dirname "$0")/mac-vpn.conf"
    echo "✅ mac-vpn.conf updated."
    ;;

  plan)
    log "Dry-run (plan) for $REGION..."
    maria "cd $MARIA_PROJECT/terraform && terraform init -input=false -no-color"
    maria "cd $MARIA_PROJECT/terraform && $TF_VARS terraform plan -input=false -no-color"
    ;;

  *)
    echo "Unknown command: $ACTION"
    usage
    exit 1
    ;;
esac
