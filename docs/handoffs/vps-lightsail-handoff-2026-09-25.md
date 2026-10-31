# Handoff — vps-lightsail: jeff-ubuntu as 24/7 control plane

**Date:** 2026-09-25
**Repo:** `/Users/JFUJIOK/projects/guicheffer/vps-lightsail` (Mac) · `~/projects/guicheffer/vps-lightsail` (jeff)
**Branch:** `feat/apple-port` @ `0846907` — 37 commits ahead of `origin/main`, **never pushed** (deliberate)
**Next session focus:** the deferred fixes round (see *Queued work*)

---

## 1. What this repo is

Read `CLAUDE.md` at the repo root first — it covers the VPN architecture, cost model, region
parameterisation and peer setup. Not repeated here.

Beyond what `CLAUDE.md` describes, the repo now has three layers:

| Layer | Path | State |
|---|---|---|
| Infra (shell + Terraform) | `setup.sh`, `teardown.sh`, `provision.sh`, `terraform/` | stable, on `main` |
| Python CLI (orchestrator) | `cli/vps_lightsail/` | works on both machines |
| Companion apps | `shield-vpn-companion/{android,apple}/` | Android ✅ · macOS code-complete but **never built** · iOS is a non-compiling stub |

---

## 2. Where things stand

**Goal of this session:** make `jeff-ubuntu` (always-on, `192.168.1.54`) the control plane for
vps-lightsail, replacing Maria's Mac which is powered off most of the time. Home Assistant
automation on top of it is a *later* goal, explicitly out of scope for now.

### Done and verified

| | Evidence |
|---|---|
| Repo on jeff, identical HEAD | `git rev-parse HEAD` matches on both machines |
| Rebase onto `origin/main` | 0 conflicts; integrity check showed diff old-tip→new-tip = exactly the 3 files from the 2 upstream commits |
| `bootstrap.sh` (new, see `8a59b4e`) | `shellcheck` clean, in CI list, runs idempotently on both platforms |
| CLI on jeff | `uv` 0.12.19 + AWS CLI v2 2.37.4; `vps-lightsail --help` runs |
| Scoped AWS identity | jeff authenticates as `user/vps-lightsail`; `iam list-users` correctly **denied** |
| mDNS discovery from jeff | `scan` finds `ShieldVPN (192.168.1.43:8080)`, health OK — **python-zeroconf coexists with avahi-daemon**, which was the main unresolved technical risk |
| Android build on jeff | Gradle 8.7 / JVM 17.0.20.1, APK 17.9 MB, 12/12 unit tests pass |

### Deliberately NOT done

- **Fase 7 (`teardown` + `setup` from jeff) — deferred by user decision.** The live Lightsail
  instance `personal-vps-vpn` in `sa-east-1` is running. Its only purpose was to
  move the server SSH key onto jeff; it adds no capability.
  ⚠️ **Correction:** "untouched and running" was false — the instance had been **offline since
  2026-09-11**. See §11.
- **Fase 8b (`adb install` to the Shield)** — blocked on an unresolved identity question, below.
- **macOS companion token wiring** — moot, the app has no binary.

---

## 3. Commits from this session

Five new commits, newest first. Read the messages — they carry the rationale, do not re-derive it.

```
0846907  fix(bootstrap): make sourcing env.sh return 0 without env.local.sh
8a59b4e  feat: add bootstrap.sh to install CLI and Android build dependencies
8bfbf85  refactor(android): move HTTP, mDNS and tunnel into VpnControlService
12aaf96  chore(android): drop corporate truststore from gradle.properties
71b6a96  chore: gitignore credentials, generated env.sh, and build artifacts
```

Rebase safety nets still present: `refs/backup/pre-rebase-main`, `refs/backup/pre-rebase-apple-port`.
Delete once you trust the result.

---

## 4. Queued work — the fixes round

All specified and agreed during this session, none started. The first three files are **already
modified in the working tree** (uncommitted, survived a stash/pop through the rebase):

| # | Item | Where | Note |
|---|---|---|---|
| 1 | Gate destructive `setup()` behind `--force` | `cli/vps_lightsail/infra.py` (+18 uncommitted) | Current uncommitted diff deletes instance/IP/keypair **with no confirmation**. Also does not wait for Lightsail's async delete, so a fast re-run still races. |
| 2 | `run-on-maria.sh` → `run-remote.sh [HOST]`, default `jeff` | `run-on-maria.sh` (−299 uncommitted) | Also fix stale `conf-push/` path (renamed to `cli/` in `2e8f89e`) |
| 3 | Same stale `conf-push/` path | `setup.sh:92` (uncommitted) | Will fail as written |
| 4 | Non-interactive CLI: `--yes` on teardown, `--device` bypassing the prompt | `cli/vps_lightsail/cli.py` | Needed for `ssh jeff -- vps-lightsail ...` without a TTY, and a prerequisite for the HA work |
| 5 | Scope `cmd_status` to the right interface | `cli/vps_lightsail/cli.py` | Runs bare `sudo wg show`; on jeff that reports the unrelated `10.99.0.0/24` tunnel and `nordlynx` as if they were vps-lightsail. **It lies today.** |
| 6 | Android: boot no longer reconnects | `boot/BootReceiver.kt`, `VpnControlService.kt` | Regression from `8bfbf85`. `tunnelEnabled` survives reboot as `true` but nothing restores the tunnel → UI shows "Connected" over a dead tunnel |
| 7 | Android: `broadcastState()` missing on disconnect path | `VpnControlService.kt` | A remote `POST /disconnect` tears down the tunnel but never refreshes the Activity |
| 8 | Android: `WireGuardTunnel.isConnected` is dead code | `vpn/WireGuardTunnel.kt` | Zero call sites; looks like the intended fix for #6 |

Dead code worth deciding on: `infra.remove_peer()` / `infra.list_peers()` (added in `590f5b7`,
never wired up) and `infra.status()` (reimplemented inline in `cmd_status`).

---

## 5. Open questions for the user

1. **Which host is the Shield?** `AGENTS.md` says `192.168.1.8`; the companion app answers on
   `192.168.1.43`. **Both respond to ping.** Must be resolved before any `adb connect`.
2. **When to do the deferred cut** (`teardown` + `setup` from jeff). Recommended *after* the fixes
   round, so `run-remote.sh` exists and the Mac is not locked out of management in the interim.
3. **Build the macOS companion?** 21 commits of Swift with no binary. Until it runs, it cannot be
   discovered over mDNS and cannot be auto-pushed to.
4. **Credential rotation** — see *Security* below.

---

## 6. Environment traps (hard-won; do not rediscover)

- **macOS `/usr/libexec/java_home -v 17` exits 0 returning a Java 11 path** when 17 is absent. It
  silently falls back instead of failing. `bootstrap.sh` now verifies by running `javac`.
- **The Mac cannot build Android.** Netskope MITM breaks the TLS handshake to
  `services.gradle.org`. The `-Djavax.net.ssl.trustStore=/tmp/custom-cacerts` flag that used to
  work points at a file that **no longer exists** (`/tmp` is cleared on reboot), so it was already
  dead config. jeff is not proxied and reaches it in 27 ms. **Build Android on jeff, not the Mac.**
- **`env.sh` must be sourced** before any Gradle work: `source ./env.sh`. It is generated per
  machine and gitignored. Non-interactive `ssh` does not read `~/.bashrc`, which is why a
  sourceable file exists at all.
- **Do not use `[ -f x ] && . x` as the last line of a sourced file** — it makes `source` return 1.
  This bit us once; see `0846907`.
- **jeff already runs an unrelated WireGuard** (`wg0` on `10.99.0.0/24`) plus `nordlynx`. jeff is
  **manager-only** and must never become a vps-lightsail tunnel client — three tunnels competing
  for the default route on the box that runs Home Assistant, Grafana and Node-RED.
- **jeff's login shell is zsh**; `bootstrap.sh` is `#!/usr/bin/env bash` and unaffected.
- **Apple devices do not advertise their token over mDNS** (removed for good reason in `3180cf6`),
  so pushing to a Mac needs `SHIELD_VPN_TOKEN_<NAME>`. Android does advertise it.
- **The Apple app cannot be containerised or cross-compiled** — SwiftUI + `Network.framework` +
  Keychain are macOS-only. Swift-on-Linux will not help.

---

## 7. Security

- **`aws.env` in the repo root holds a plaintext AWS key with `AdministratorAccess`.** It is now
  gitignored (`71b6a96`) and has never been committed, but it is still on disk in the working
  directory of a **public** GitHub repo.
- That key belongs to IAM user `cache-b`, which is **shared with an unrelated project**
  (`~/projects/jeffujioka/obsidian-poc/cache-b-feeder`, a Telegram→Obsidian Lambda bot). Rotating
  it breaks that project too. User has deferred rotation.
- `CLAUDE.md` claims the IAM user has a `lightsail:*` inline policy. That was **not true** until
  this session; a correctly scoped `vps-lightsail` user was created. Consider updating the doc.
- jeff now holds a **separate, scoped** credential (`lightsail:*` only) in `~/.aws/credentials`,
  profile `personal`. Verified it cannot call IAM.
- Access key IDs, secrets, the AWS account ID and the server's public IP are intentionally omitted
  from this document. Retrieve them with `aws sts get-caller-identity` and
  `aws lightsail get-instances`.
- `rsync` excludes live at `/tmp/rsync-excludes.txt` — regenerate it rather than trusting it to
  persist. It must exclude at minimum: `aws.env`, `env.sh`, `env.local.sh`, `*.pem`, `*-vpn.conf`.
- **Never commit `docs/superpowers/`** — per the user's global `~/.agents/AGENTS.md`, this is
  policy enforced by the agent, deliberately *not* by `.gitignore`. `docs/` is currently untracked
  and must stay that way unless the user explicitly asks otherwise.

---

## 8. Reference material already in the repo

Do not re-summarise these; read them if relevant:

- `docs/handoff-2026-06-24.md` — prior session state (now partly stale)
- `docs/ios-controller-prd.md` — PRD for a native iOS controller app. **Not started**; proposes a
  Go management API on the VPS
- `docs/superpowers/plans/*.md` — four implementation plans. **All show 0/160 checkboxes ticked,
  but three are substantially or fully done.** The executing agent never marked them. Verify
  against `git log` before assuming anything is outstanding.

---

## 9. Suggested skills

Call the Skill tool for these in the next session:

| Skill | When |
|---|---|
| `verification-before-completion` | **Highest value here.** This session found three real bugs *only* by executing rather than reasoning. Run the thing before claiming it works. |
| `tdd` | For fixes #1, #4 and #5 — `cli/tests/` exists and `test_disconnect.py` is a good pattern to copy. `infra.py` and `cli.py` currently have **zero** test coverage. |
| `systematic-debugging` | For Android fixes #6–#8, where persisted state and live tunnel state have drifted apart. |
| `git-champs` | Branch is unpushed, rebased, and has backup refs. Worth the branch-safety discipline before any further history rewriting. |
| `grilling` | If the user reopens scope — credential rotation, building the macOS app, or starting the Home Assistant integration. It was used to settle 26 decisions this session and worked well. |
| `code-review` | Before finishing the branch. Note this is a *standards/spec conformance* tool, not a summariser. |

Skip `brainstorming` for items #1–#8: they are already specified. Use it if the Home Assistant
integration starts, which is genuinely undesigned.

---

## 10. Known constraint on the Home Assistant work (when it starts)

Recon is already done, do not repeat it: HA runs as a k3s deployment in namespace `homelab`,
`hostNetwork: true`, config on host at `/home/jeff/homelab/homeassistant`, and the host sshd
listens on `0.0.0.0:22` with passwordless sudo for `jeff`. So `shell_command` → `ssh 127.0.0.1`
works, and a working `command_line` precedent exists at `configuration.yaml:17`.

**The blocker is timing, not access:** `infra.setup()` waits for SSH with a 120 s deadline, so a
full setup takes 2–4 minutes, while HA's `shell_command` times out at 60 s by default. That forces
a fire-and-forget design plus a separate status sensor. Design it before coding it.

---

## 11. Update — 2026-09-25 evening: server outage and fixes

### What was wrong

- **Server offline since 2026-09-11 06:34 UTC.** `unattended-upgrade` upgraded systemd
  (`255.4-1ubuntu8.17`) and re-exec'd PID 1; `systemd-networkd` then logged
  `ens5: Could not set route: Connection timed out` and never recovered. Lightsail showed
  `running` throughout, with `StatusCheckFailed_Instance` failing 100% and `NetworkOut` = 0.
  Nothing alerted. Fixed by `reboot-instance`.
- **Peer IP collision.** `_push_to_devices` hard-coded `peer_base = 2`, so the Shield push
  created a second peer on `10.8.0.2`, stealing it from the provisioned Mac peer
  (`wg show` reported the Mac as `allowed ips: (none)`).

### Done (uncommitted, working tree)

| | Where | Verified |
|---|---|---|
| Push picks next free IP from `wg0.conf` (`parse_allowed_ips`, `next_free_peer_ip`, `used_peer_ips`) | `cli/vps_lightsail/{infra,cli}.py`, `cli/tests/test_peer_ip.py` | 8 new tests; against live server returns `10.8.0.4` |
| Network watchdog (systemd timer, 2 min; reconfigure NIC after 2 fails, reboot after 5) | `provision.sh` + applied live | shellcheck; happy and fail-count paths run live. Reconfigure/reboot branches **not** exercised live |
| Lightsail alarm `vps-vpn-status-check` (StatusCheckFailed ≥1 for 3×5 min → email, ALARM + OK; missing data = notBreaching so a stopped instance does not alert) | live only, not in any setup path | created; email contact method verified, test ALARM email received 2026-09-25 |
| Orphaned Mac peer `RHZqb…` removed via `infra.remove_peer` | live server | backup at `/etc/wireguard/wg0.conf.bak-2026-09-25` |

### Live peer state

| Peer | IP | Device |
|---|---|---|
| `6TXd…` | `10.8.0.3` | iPhone (imported by QR from `iphone_private.key`) |
| `uFsg…` | `10.8.0.2` | Shield (from CLI push; no key file on server) |

Both handshaking; egress confirmed `54.20.19.201`, São Paulo/BR. The Mac has no WireGuard client
and no companion app installed — it is not a VPN client.

### Still open

- The alarm is not recreated by `setup` (CLI, `setup.sh`, Terraform). After the deferred cut,
  re-run `put-alarm` or wire it into setup.
- Re-pushing the same device still adds a new peer each time (now on a fresh IP, so no
  collision, but orphans accumulate). Replacing needs a device identity in `wg0.conf`.
- `provision.sh` still provisions a Mac peer on `.2`; harmless now that push avoids used IPs.

### Later the same evening: stop/start added, instance torn down

- New `vps-lightsail stop` / `start` (`infra.stop`, `infra.start`, `cmd_stop`, `cmd_start`,
  tests in `cli/tests/test_start_stop.py`). Verified live: stop 30 s (Shield disconnected via
  companion first), start 24 s, same IP, peers and watchdog intact, egress São Paulo.
- `cmd_disconnect` help text claimed it removed server peers; it never did. Text fixed.
- Alarm switched to `treatMissingData = notBreaching` so a stopped instance does not alert.
- **Instance torn down by user decision (teardown only, no re-setup).** Verified nothing left in
  `sa-east-1`: no instance, static IP, key pair or alarm. The email contact method remains
  (free) and will be reused by the next `put-alarm`. Shield and iPhone configs are now dead;
  the next `setup` needs a re-push to the Shield and a new QR for the iPhone.
- Fase 7 is therefore half done: the teardown happened (from the Mac, not jeff); setup from
  jeff is still pending. After setup, re-create the alarm — no setup path does it yet.
