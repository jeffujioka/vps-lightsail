# CLAUDE.md

## WHAT THIS IS

Personal WireGuard VPN on AWS Lightsail. ~$5/mo.
Pick any region → get a dedicated IP there → route traffic through it.

## FILES

- `setup.sh` — creates VPS + WireGuard from scratch
- `teardown.sh` — destroys everything, no charges left
- `swap-ip.sh` — swap static IP (if flagged or just want a fresh one)
- `README.md` → start here

## HOW IT WORKS

```
Device → WireGuard (UDP 51820) → Lightsail VPS → internet
```

Server: Ubuntu 24.04, wg0 on 10.8.0.1/24
Mac peer: 10.8.0.2 | iPhone peer: 10.8.0.3

## REGION IS A PARAM

```bash
AWS_REGION=sa-east-1 ./setup.sh   # São Paulo
AWS_REGION=eu-west-1 ./setup.sh   # Ireland
```

Default: `sa-east-1`. Override before running.

## AWS PREREQS

IAM user with `lightsail:*` inline policy + access key:
```bash
aws configure --profile personal   # region: sa-east-1
```

## WIREGUARD APPS

- Mac: https://apps.apple.com/app/wireguard/id1451685025
- iPhone: https://apps.apple.com/app/wireguard/id1441195209
- Apple TV: no native app — AirPlay from Mac, or GL.iNet travel router

## USEFUL LINKS

- Lightsail pricing: https://aws.amazon.com/lightsail/pricing/
- Lightsail regions: https://lightsail.aws.amazon.com/ls/docs/en_us/articles/understanding-regions-and-availability-zones-in-amazon-lightsail
- WireGuard quickstart: https://www.wireguard.com/quickstart/
- GL.iNet WireGuard client: https://docs.gl-inet.com/router/en/4/tutorials/wireguard_client/

## ADDING A FRIEND AS PEER

Friends don't need an AWS account. Just add them as a WireGuard peer on the running server:

```bash
SERVER_IP=<ip>
KEY=~/.ssh/id_ed25519   # or vps-vpn.pem for shell-based setup

ssh -i $KEY ubuntu@$SERVER_IP "sudo bash" << 'EOF'
cd /etc/wireguard
wg genkey | tee friend_private.key | wg pubkey | tee friend_public.key
chmod 600 friend_private.key
FRIEND_PUB=$(cat friend_public.key)
wg set wg0 peer $FRIEND_PUB allowed-ips 10.8.0.4/32
echo "" >> wg0.conf
echo "[Peer]" >> wg0.conf
echo "PublicKey = $FRIEND_PUB" >> wg0.conf
echo "AllowedIPs = 10.8.0.4/32" >> wg0.conf
echo "FRIEND_PRIVATE: $(cat friend_private.key)"
echo "SERVER_PUBLIC:  $(cat server_public.key)"
EOF
```

Then build a `.conf` for them (replace values):
```ini
[Interface]
PrivateKey = <friend_private>
Address = 10.8.0.4/32
DNS = 1.1.1.1

[Peer]
PublicKey = <server_public>
Endpoint = <server_ip>:51820
AllowedIPs = 0.0.0.0/0
PersistentKeepalive = 25
```

Each additional peer uses a new IP: `10.8.0.5/32`, `10.8.0.6/32`, etc.

## COST STRATEGY

- **Always on**: $5/mo flat — fine for frequent use
- **Kill when done**: ~$0.007/hr → watching 4h/week = ~$0.12/mo
  - `make tf-teardown` after watching, `make tf-setup` next time
  - New setup = new WireGuard keys = re-import config on all devices
- **Stop/start** (`vps-lightsail stop` / `start`): keeps keys, peers and static IP, but
  **does not save money** — Lightsail bills stopped instances. Use it to make sure no client
  routes traffic. `stop` first sends `POST /disconnect` to companion devices, because a device
  whose tunnel is still up (`AllowedIPs = 0.0.0.0/0`) loses internet when the server is down.

## COSTS

- $5/mo flat (nano bundle, 512GB transfer included)
- Static IP free while attached — release before teardown or it keeps charging
- swap-ip.sh is free (detach + release + allocate + attach)
