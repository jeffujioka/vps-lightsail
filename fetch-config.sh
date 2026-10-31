#!/usr/bin/env bash
#
# Writes dist/<peer>-vpn.conf for every configured peer and prints the QR of the qr peers.
# Thin wrapper around `vps-lightsail export --qr` (spec-06 §1): the server, its address and
# the SSH session come from AWS inside the CLI — no AWS/SSH logic lives here.
#
# Usage: fetch-config.sh [--peer NAME] [--region R] [--profile P] [--config PATH]
#        (the legacy positional <server_ip> <ssh_key_path> are ignored with a warning)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

dropped=0
while [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; do
  dropped=$((dropped + 1))
  shift
done
if [ "$dropped" -gt 0 ]; then
  echo "fetch-config.sh: ignoring $dropped positional argument(s) — the server and its key now come from AWS (see: vps-lightsail export --help)" >&2
fi

exec uv run --project "$SCRIPT_DIR/cli" vps-lightsail export --qr "$@"
