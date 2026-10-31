# iOS VPN Controller — PRD

## Problem Statement

The user manages a personal WireGuard VPN on AWS Lightsail using a Python CLI (`cli/vps_lightsail`) that runs on macOS. This CLI requires a terminal, SSH access, and the AWS CLI installed — making it impossible to manage the VPN from an iPhone. The user wants to setup, monitor, push configs to companion devices, and teardown the VPN entirely from their phone without needing a computer nearby.

## Solution

A native iOS app (SwiftUI) that replicates the CLI's controller functionality. The app talks to AWS Lightsail via the official Swift SDK for infrastructure lifecycle, and to a lightweight Management API (Go binary installed on the VPS via cloud-init) for WireGuard peer operations — eliminating the need for SSH from the phone. Device discovery uses the same `_vpnconfig._tcp` mDNS protocol the CLI already uses, and config push uses the same HTTP endpoint the macOS companion already exposes.

## User Stories

1. As a VPN owner, I want to create a new VPN server from my iPhone, so that I can set up my VPN from anywhere without a laptop.
2. As a VPN owner, I want to choose which AWS region to deploy in, so that I can route traffic through different countries.
3. As a VPN owner, I want to see the VPN server status (running, IP, region, uptime), so that I know my infrastructure is healthy.
4. As a VPN owner, I want to see a map showing where my VPS is located, so that I can visually confirm the region.
5. As a VPN owner, I want to scan for companion devices on my local network, so that I can push WireGuard configs to them.
6. As a VPN owner, I want to push a WireGuard config to a discovered device, so that device connects to my VPN automatically.
7. As a VPN owner, I want to see the list of connected peers on my server, so that I know who is using the VPN.
8. As a VPN owner, I want to disconnect a specific device (triggering wg-quick down on the companion and removing the peer from the server), so that I can revoke access.
9. As a VPN owner, I want to teardown all AWS resources from my phone, so that I stop paying when I don't need the VPN.
10. As a VPN owner, I want to see my current public IP and geolocation, so that I can verify traffic is routing through the VPN region.
11. As a VPN owner, I want to store my AWS credentials securely on the device, so that I don't have to re-enter them every time.
12. As a VPN owner, I want the app to show progress during long operations (VPS creation, waiting for SSH, provisioning), so that I know things are working.
13. As a VPN owner, I want to see transfer stats per peer (rx/tx bytes, last handshake), so that I can monitor usage.
14. As a VPN owner, I want the app to confirm before destructive actions (teardown), so that I don't accidentally destroy my VPN.
15. As a VPN owner, I want the setup to be fully automated (no manual SSH steps), so that I just tap "Setup" and wait.
16. As a VPN owner, I want the Management API on the server to be secured with a token, so that only my app can manage peers.
17. As a VPN owner, I want the Management API token to be generated during setup and stored in my Keychain, so that auth is zero-config.
18. As a VPN owner, I want to add a friend as a peer and generate a config file I can share with them, so that friends can use my VPN.

## Implementation Decisions

### Architecture

- **Three-tier:** iPhone App (SwiftUI) → AWS Lightsail API + VPS Management API → WireGuard
- **No SSH from iOS.** All server-side operations go through either the AWS SDK (infrastructure lifecycle) or the Management API (WireGuard peer operations).
- The Management API is installed on the VPS during provisioning via cloud-init/user-data, alongside WireGuard. No manual SSH step required.

### Management API (Go binary on the VPS)

- Single statically-linked Go binary, cross-compiled for linux/arm64 (Lightsail uses Graviton).
- Listens on a configurable port (default 8443, TLS with self-signed cert generated at install time).
- Auth: Bearer token generated at provisioning time, stored in `/etc/wg-mgmt/token`.
- Endpoints:
  - `GET /health` — returns 200
  - `GET /status` — returns JSON with interface info and peer list (parsed from `wg show wg0 dump`)
  - `POST /peers` — generates keypair, adds peer to wg0, returns `{private_key, public_key, peer_ip, config}` (full .conf content)
  - `DELETE /peers/{public_key}` — removes peer from running config and wg0.conf
- The binary runs as a systemd service (`wg-mgmt.service`), started after `wg-quick@wg0`.
- Firewall: Lightsail port 8443 opened during setup (alongside 22 and 51820).

### iOS App Architecture

- **Swift 6, iOS 17+, SwiftUI only.**
- **Observation framework** (`@Observable`) for state management — no Combine, no ObservableObject.
- **Structured concurrency** (async/await, TaskGroup) for all async work.
- **Dependency injection via protocols** — every external service is behind a protocol for testability.

### Service Layer (protocols)

| Protocol | Responsibility | Real Implementation |
|----------|---------------|---------------------|
| `LightsailClient` | Create/destroy VPS, get instance status, manage static IPs, open ports | AWS SDK for Swift |
| `ManagementClient` | Talk to the Go Management API on the VPS (peers, status) | URLSession with pinned cert |
| `DeviceDiscovery` | Browse `_vpnconfig._tcp` on local network | NWBrowser |
| `ConfigPushClient` | Push WireGuard config to companion devices via HTTP | URLSession |
| `SecureStorage` | Store/retrieve AWS credentials and API tokens | iOS Keychain (kSecClass) |

### AWS Credentials

- User enters Access Key ID + Secret Access Key once during onboarding.
- Stored encrypted in iOS Keychain (kSecAttrAccessibleWhenUnlockedThisDeviceOnly).
- AWS SDK for Swift initialized with a custom `CredentialProvider` that reads from Keychain.
- No STS/Cognito for v1 — direct IAM credentials (same as the CLI uses today). STS rotation is a future enhancement.

### UI Design

- **Dashboard tab:** Status card (VPS state + region map via MapKit), public IP card, quick actions (setup/teardown).
- **Peers tab:** List of connected peers with transfer stats, swipe-to-disconnect.
- **Devices tab:** mDNS-discovered companion devices, tap to push config.
- **Settings tab:** AWS credentials, region preference, Management API token display.
- SF Symbols throughout. Animated state transitions. Pull-to-refresh on peers/status.

### Provisioning Flow (setup)

1. App calls Lightsail `createInstances` with user-data script that installs WireGuard + Management API.
2. App polls `getInstance` until state = "running".
3. App calls `allocateStaticIp` + `attachStaticIp`.
4. App calls `putInstancePublicPorts` (22/tcp, 51820/udp, 8443/tcp).
5. App polls Management API `/health` endpoint until it responds (server provisioned).
6. App stores server IP + Management API token in Keychain.
7. Dashboard shows "Ready" with region map pin.

The user-data script is embedded in the app bundle as a resource — same logic as the existing `provision.sh` but extended to also install the Management API binary and generate the auth token.

### Code Sharing with Existing macOS Companion

- The iOS controller is a **separate Xcode target** in the existing `VPSLightsail.xcodeproj`.
- A new Swift package `VPNControllerCore` contains the service protocols, models, and ViewModels — testable without UIKit/SwiftUI.
- `ShieldVPNCore` (existing) stays for the companion role. The two packages share model types (like `TunnelConfig`, `TunnelStatus`) via a shared `VPNModels` package if needed.

### Relationship to Existing CLI

- The iOS app does NOT replace the CLI — both are valid ways to manage the same VPS.
- The Management API is additive: existing SSH-based management (from CLI) still works.
- Same WireGuard config format, same mDNS protocol, same companion HTTP endpoints.

## Testing Decisions

### What makes a good test

Tests verify external behavior through the service protocol boundary. They do NOT test:
- Internal state or private methods
- UI layout details (no snapshot tests in v1)
- Network layer implementation (URLSession internals)

Tests DO verify:
- ViewModel state transitions given mocked service responses
- Correct API calls are made for each user action
- Error states are surfaced correctly
- Business logic (peer IP allocation, config generation, provisioning flow state machine)

### Modules tested

| Module | What to test | Seam |
|--------|-------------|------|
| `VPNControllerCore` (ViewModels) | State transitions, action dispatching, error handling | Mock service protocols |
| `VPNControllerCore` (Models) | Config generation, parsing, peer IP allocation | Pure functions, no mocks |
| Management API (Go) | HTTP endpoints, auth, WireGuard CLI interaction | httptest + mock wg binary |

### Prior art

- `ShieldVPNCore/Tests/` — existing Swift package tests using protocol injection (CommandRunner mock pattern)
- `cli/tests/test_push.py` — httpx mock pattern for HTTP client tests
- `cli/tests/test_disconnect.py` — mock pattern for device interaction tests

The iOS tests follow the same pattern as `ShieldVPNCoreTests`: inject a mock conforming to the protocol, call the ViewModel method, assert state changes.

## Out of Scope

- **VPN connection on the iPhone itself** — requires paid Apple Developer account ($99/yr) for NetworkExtension. Not included in v1.
- **IKEv2 / .mobileconfig on iOS** — deferred to a future phase.
- **Multi-user / team features** — this is a personal VPN tool.
- **App Store distribution** — runs via Xcode on personal device (free provisioning).
- **Offline mode / caching** — the app needs network to function.
- **Push notifications** — no background monitoring of VPS state.
- **iPad-specific layout** — iPhone-first, iPad gets the iPhone layout scaled.
- **macOS Catalyst** — the macOS companion app already exists for macOS.
- **STS token rotation** — direct IAM creds for v1; STS is a future enhancement.
- **Management API TLS cert pinning** — self-signed cert trusted on first use (TOFU) for v1.

## Further Notes

- The Management API opens a new attack surface on the VPS. Mitigations: Bearer token auth, Lightsail firewall (port 8443 only open to the app), TLS encryption, single-purpose endpoints with no shell injection vectors.
- The Go binary should be vendored in the repo under `server/wg-mgmt/` and cross-compiled in CI. The user-data script downloads it from a known location (GitHub release or S3 bucket) during provisioning.
- The existing `provision.sh` script will be extended (or a new `provision-with-api.sh` created) to also install the Management API binary + systemd unit.
- The iOS app requires iOS 17+ for `@Observable` and modern SwiftUI navigation APIs.
- AWS SDK for Swift is modular — only `AWSLightsail` package needed (not the entire SDK).
