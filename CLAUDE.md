# CLAUDE.md

## WHAT THIS IS

Personal WireGuard VPN on AWS Lightsail. ~$5/mo.
Pick any region → get a dedicated IP there → route traffic through it.

## FILES

- `setup.sh` / `teardown.sh` / `fetch-config.sh` — thin wrappers for `vps-lightsail setup` / `teardown` / `export --qr`
- `provision.sh` — symlink → `cli/vps_lightsail/core/resources/provision.sh`, the one server script (`VPS_PEERS`)
- `swap-ip.sh` — swap static IP (if flagged or just want a fresh one); every peer config dies → `make config`
- `cli/` — the `vps-lightsail` CLI and agent; all AWS/SSH logic lives in `vps_lightsail.core`
- `README.md` → start here

## HOW IT WORKS

```
Device → WireGuard (UDP 51820) → Lightsail VPS → internet
```

Server: Ubuntu 24.04, wg0 on 10.8.0.1/24
Peers (config order): shield 10.8.0.2 (companion) | iphone 10.8.0.3 (QR); ad-hoc peers take the next free 10.8.0.x

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

Friends don't need an AWS account. Add them as an ad-hoc WireGuard peer on the running server —
no PEM: the CLI opens its SSH session through Lightsail (`get-instance-access-details`).

```bash
vps-lightsail push --name friend    # next free 10.8.0.x → dist/friend-vpn.conf (0600) + QR
```

Send them `dist/friend-vpn.conf` (or let them scan the QR). Running it again adds another peer;
the old one stays on the server until `teardown`. Declarative alternative: add
`{name: friend, type: qr}` to the agent YAML before `vps-lightsail setup --config agent.yaml`.

Re-send the Shield's config with `vps-lightsail push --peer shield`; rewrite every conf with
`vps-lightsail export --qr`.

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
