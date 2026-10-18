#!/usr/bin/env bash
# Destroy everything, no leftover charges. Thin wrapper over `vps-lightsail teardown` (spec-06 §1).
#   AWS_PROFILE=work AWS_REGION=eu-west-1 ./teardown.sh [--config agent.yaml]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE="${AWS_PROFILE:-personal}"
REGION="${AWS_REGION:-sa-east-1}"
command -v uv >/dev/null || { echo "uv not found — install it with ./bootstrap.sh cli" >&2; exit 1; }

exec uv run --project "$SCRIPT_DIR/cli" vps-lightsail teardown --region "$REGION" --profile "$PROFILE" "$@"
