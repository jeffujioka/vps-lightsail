<img src="https://upload.wikimedia.org/wikipedia/commons/9/93/Amazon_Web_Services_Logo.svg" width="60" alt="AWS" align="right" />

# vps-lightsail

[![macOS](https://img.shields.io/badge/macOS-13%2B-black?logo=apple&logoColor=white)](https://www.apple.com/macos/)
[![iOS](https://img.shields.io/badge/iOS-16%2B-black?logo=apple&logoColor=white)](https://www.apple.com/ios/)
[![AWS Lightsail](https://img.shields.io/badge/AWS-Lightsail-FF9900?logo=amazonaws&logoColor=white)](https://aws.amazon.com/lightsail/)
[![WireGuard](https://img.shields.io/badge/WireGuard-VPN-88171A?logo=wireguard&logoColor=white)](https://www.wireguard.com/)
[![cost](https://img.shields.io/badge/cost-~%245%2Fmo-brightgreen)](COSTS.md)

```mermaid
graph LR
    A[Your Device] -->|WireGuard tunnel| B[AWS Lightsail VPS<br/>your region]
    B -->|local IP| C[Streaming / Web]
    style B fill:#ff9900,color:#000
```

---

## What you need

- AWS account + CLI v2 — `brew install awscli`
- WireGuard app — [Mac](https://apps.apple.com/app/wireguard/id1451685025) · [iPhone](https://apps.apple.com/app/wireguard/id1441195209)

---

## Setup

```bash
git clone https://github.com/guicheffer/vps-lightsail
cd vps-lightsail

# configure AWS CLI
aws configure --profile personal
```

```bash
make setup                        # São Paulo (default)
make setup REGION=eu-west-1       # Ireland
make setup REGION=ap-east-1       # Hong Kong
```

Prints the iPhone QR code, writes `dist/<peer>-vpn.conf` (0600) for every peer and pushes the Shield's config to its companion app. Peers come from `--config agent.yaml` (the agent YAML) or the default `shield` (companion, 10.8.0.2) + `iphone` (QR, 10.8.0.3).

```bash
make help      # all commands
make status    # check connected peers
make swap-ip   # get a fresh IP
make teardown  # destroy everything
```

Check available bundles per region:
```bash
aws lightsail get-bundles --region sa-east-1 \
  --query 'bundles[?supportedPlatforms[0]==`LINUX_UNIX`].[bundleId,price]' \
  --output table
```

### Alternative: Terraform

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with your region/keys
cd ..

make tf-setup     # terraform apply + make config (dist/shield-vpn.conf + iPhone QR)
```

`make tf-setup` runs `terraform apply` — same instance name, static IP `vps-vpn-static-ip` and peers as `make setup` (`peers` is a Terraform variable) — then `make config`, i.e. `vps-lightsail export --qr`. Run `make config` any time to re-fetch the configs.

---

## Connect

**Mac**
1. Open WireGuard app
2. `+` → Import tunnel → select a `dist/<peer>-vpn.conf` (add a Mac peer first: `vps-lightsail push --name mac`, or a `{name: mac, type: qr}` entry in the agent YAML before `setup --config`)
3. Activate

**iPhone**
1. Install [WireGuard](https://apps.apple.com/app/wireguard/id1441195209)
2. `+` → Create from QR code → scan terminal output
3. Activate

**Apple TV**
1. Install [VPN Client Bee](https://apps.apple.com/app/vpn-client-bee/id1633151022) from the App Store
2. Select **WireGuard** → **Custom Server List**
3. Add server manually with the values from its own `dist/<peer>-vpn.conf` (one peer per device)
4. Connect

**Verify IP**
```
https://ifconfig.me
```

---

## Peers

```bash
vps-lightsail export --qr                  # rewrite dist/<peer>-vpn.conf + print QR (= make config)
vps-lightsail push --peer shield           # re-send the Shield's config and activate it
vps-lightsail push --peer shield --stage   # store only (companion API v2); connect later from HA
vps-lightsail push --name laptop             # extra ad-hoc peer → dist/laptop-vpn.conf + QR
vps-lightsail push --device <shield-ip>    # legacy: new ad-hoc peer pushed to that device
```

Declarative peers (config order → `10.8.0.(2+i)`) keep their keys until teardown; ad-hoc peers
take the next free `10.8.0.x` and stay on the server until teardown.

---

## Streaming

With VPN active, open any geo-restricted stream in a browser or app.
The site sees the VPS region IP — not your real location.

> Note: AWS IPs are datacenter IPs. Most services work fine.
> If blocked, run `./swap-ip.sh` to get a fresh IP.

---

## Teardown

```bash
./teardown.sh               # or: make teardown
```

Disconnects the Shield first, releases every static IP (by name and by attachment), deletes the instance and the legacy key pair: zero instances, zero static IPs, no leftover charges. Removes `dist/*-vpn.conf`. `vps-lightsail repair` releases orphaned static IPs left by an interrupted run.

---

## Swap IP

```bash
./swap-ip.sh
```

Releases the current IP and attaches a new one under the same name (the agent keeps tracking it). Every peer config dies (the endpoint changes): run `make config` (rewrites `dist/*-vpn.conf`, prints a new QR) and `vps-lightsail push --peer shield`.

---

## Cost

→ See [COSTS.md](COSTS.md) for detailed breakdown and comparison with commercial VPNs.

---

<sub>As seen from <a href="https://guicheffer.me/vps">guicheffer.me/vps</a></sub>
