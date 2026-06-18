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

    # ── Shield auto-push via mDNS ─────────────────────────────────────────────
    SHIELD_TOKEN="${SHIELD_VPN_TOKEN:-}"
    if [[ -n "$SHIELD_TOKEN" ]]; then
      log "Looking for Shield on the network (mDNS)..."
      SHIELD_IP=$(dns-sd -L "ShieldVPN" _vpnconfig._tcp local 2>/dev/null &
        DNSSD_PID=$!; sleep 3; kill $DNSSD_PID 2>/dev/null
        dns-sd -G v4 "ShieldVPN.local" 2>/dev/null | grep -m1 "Addr" | awk '{print $NF}') || true

      if [[ -z "$SHIELD_IP" ]]; then
        SHIELD_IP=$(maria "avahi-browse -rpt _vpnconfig._tcp 2>/dev/null | grep -m1 '=' | cut -d';' -f8" 2>/dev/null) || true
      fi

      if [[ -n "$SHIELD_IP" ]]; then
        log "Found Shield at $SHIELD_IP, adding peer + pushing config..."
        maria "ssh -i $KEY_PATH -o StrictHostKeyChecking=no ubuntu@$SERVER_IP 'sudo bash -s'" <<'PEER_EOF'
cd /etc/wireguard
if [ ! -f shield_private.key ]; then
  wg genkey | tee shield_private.key | wg pubkey | tee shield_public.key
  chmod 600 shield_private.key
  SHIELD_PUB=$(cat shield_public.key)
  wg set wg0 peer $SHIELD_PUB allowed-ips 10.8.0.4/32
  echo "" >> wg0.conf
  echo "[Peer]" >> wg0.conf
  echo "PublicKey = $SHIELD_PUB" >> wg0.conf
  echo "AllowedIPs = 10.8.0.4/32" >> wg0.conf
fi
PEER_EOF

        SHIELD_PRIVATE=$(maria "ssh -i $KEY_PATH -o StrictHostKeyChecking=no ubuntu@$SERVER_IP 'sudo cat /etc/wireguard/shield_private.key'")
        SERVER_PUBLIC=$(maria "ssh -i $KEY_PATH -o StrictHostKeyChecking=no ubuntu@$SERVER_IP 'sudo cat /etc/wireguard/server_public.key'")

        SHIELD_CONF="[Interface]
PrivateKey = $SHIELD_PRIVATE
Address = 10.8.0.4/32
DNS = 1.1.1.1

[Peer]
PublicKey = $SERVER_PUBLIC
Endpoint = $SERVER_IP:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25"

        HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
          -X POST "http://$SHIELD_IP:8080/config" \
          -H "Authorization: Bearer $SHIELD_TOKEN" \
          -H "Content-Type: text/plain" \
          -d "$SHIELD_CONF")

        if [[ "$HTTP_STATUS" == "200" ]]; then
          echo "✅ Shield VPN activated automatically!"
        else
          echo "⚠️  Shield POST returned HTTP $HTTP_STATUS (token correct?)"
        fi
      else
        log "Shield not found on network. Set SHIELD_VPN_TOKEN and ensure app is running."
      fi
    fi
    # ────────────────────────────────────────────────────────────────────────────

    echo ""
    echo "✅ VPN ready! Server: $SERVER_IP ($REGION)"
    echo ""
    echo "  Mac    → WireGuard app → + → Import tunnel → mac-vpn.conf"
    echo "  iPhone → scan the QR code printed above"
    echo "  Shield → auto-configured (if SHIELD_VPN_TOKEN was set)"
    echo "  Test   → connect, visit https://ifconfig.me"
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
