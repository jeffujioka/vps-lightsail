#!/usr/bin/env bash
# Usage: companion-acceptance.sh [HOST] [--expect-no-config | --call METHOD PATH]
# shellcheck disable=SC2016  # check() conditions are single-quoted on purpose (eval)
set -euo pipefail
HOST=${1:-$(avahi-browse -rpt _vpnconfig._tcp | awk -F';' '$1=="=" && $3=="IPv4" && $4=="ShieldVPN" {print $8; exit}')}
BASE="http://$HOST:8080"
TXT=$(avahi-browse -rpt _vpnconfig._tcp | awk -F';' -v h="$HOST" '$1=="=" && $3=="IPv4" && $8==h {print $10; exit}')
TOKEN=${COMPANION_TOKEN:-$(grep -o 'token=[0-9a-f]*' <<<"$TXT" | cut -d= -f2 || true)}
[ -n "$TOKEN" ] || { echo "FAIL no token"; exit 1; }
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT; umask 077
printf 'Authorization: Bearer %s\n' "$TOKEN" > "$WORK/auth"; unset TOKEN
printf 'Authorization: Bearer wrong\n' > "$WORK/bad"
if [ "${2:-}" = --call ]; then   # single authenticated call, e.g. --call POST /connect
  curl -s -m 15 -w ' %{http_code}\n' -X "$3" -H "@$WORK/auth" "$BASE$4"; exit 0; fi
mkconf() {  # $1 = address
  cat << CONF
[Interface]
PrivateKey = $(wg genkey)
Address = $1
DNS = 1.1.1.1

[Peer]
PublicKey = $(wg genkey | wg pubkey)
Endpoint = 192.0.2.1:51820
AllowedIPs = 10.8.0.0/24
PersistentKeepalive = 25
CONF
}
mkconf 10.8.0.2/32 > "$WORK/c2"; mkconf 10.8.0.9/32 > "$WORK/c9"
n=0; CODE=''; BODY=''
req() {  # method path [hdrfile] [bodyfile] -> sets CODE, BODY
  local args=(-s -m 15 -o "$WORK/out" -w '%{http_code}' -X "$1" "$BASE$2")
  [ -n "${3:-}" ] && args+=(-H "@$3")
  [ -n "${4:-}" ] && args+=(--data-binary "@$4" -H 'Content-Type: text/plain')
  CODE=$(curl "${args[@]}"); BODY=$(cat "$WORK/out")
}
check() { n=$((n+1)); if eval "$2"; then echo "PASS $n $1"; else echo "FAIL $n $1 (code=$CODE body=$BODY)"; exit 1; fi; }
st() { req GET /status "$WORK/auth"; jq -r "$1" <<<"$BODY"; }

check "mDNS TXT api=2"            '[[ "$TXT" == *"\"api=2\""* ]]'
req GET /health;                   check "health 200 ok"          '[ "$CODE" = 200 ] && [ "$BODY" = ok ]'
req GET /status;                   check "status no auth 403"      '[ "$CODE" = 403 ]'
req GET /status "$WORK/bad";       check "status bad token 403"    '[ "$CODE" = 403 ]'
if [ "${2:-}" = --expect-no-config ]; then
  req POST /connect "$WORK/auth";  check "connect w/o config 409"  '[ "$CODE" = 409 ] && [ "$BODY" = "No stored config" ]'
  check "status has_config false" '[ "$(st .has_config)" = false ] && [ "$(st .address)" = null ]'
fi
: > "$WORK/empty"
req POST /config "$WORK/auth" "$WORK/empty";            check "config empty 400"   '[ "$CODE" = 400 ] && [ "$BODY" = "Empty config" ]'
req POST /config "" "$WORK/c2";                         check "config no auth 403" '[ "$CODE" = 403 ]'
req POST '/config?activate=false' "$WORK/auth" "$WORK/c2"; check "stage 200"       '[ "$CODE" = 200 ] && [ "$BODY" = "OK — config stored" ]'
check "staged: down, config, .2"  '[ "$(st .api)" = 2 ] && [ "$(st .tunnel)" = down ] && [ "$(st .has_config)" = true ] && [ "$(st .address)" = 10.8.0.2/32 ]'
req POST /connect "$WORK/auth";    check "connect 200"            '[ "$CODE" = 200 ] && [ "$BODY" = Connected ]'
check "status up"                  '[ "$(st .tunnel)" = up ]'
req POST /disconnect "$WORK/auth"; check "disconnect 200"         '[ "$CODE" = 200 ] && [ "$BODY" = Disconnected ]'
check "down, config kept"          '[ "$(st .tunnel)" = down ] && [ "$(st .has_config)" = true ]'
req POST /disconnect "$WORK/auth"; check "disconnect idempotent"  '[ "$CODE" = 200 ]'
req POST /connect "$WORK/auth";    check "reconnect from kept config" '[ "$CODE" = 200 ] && [ "$(st .tunnel)" = up ]'
req POST /config "$WORK/auth" "$WORK/c9"; check "v1 default push activates" '[ "$CODE" = 200 ] && [ "$BODY" = "OK — tunnel activated" ]'
check "up on new address .9"       '[ "$(st .tunnel)" = up ] && [ "$(st .address)" = 10.8.0.9/32 ]'
req POST '/config?activate=false' "$WORK/auth" "$WORK/c2"; check "stage while up tears down (R5)" '[ "$CODE" = 200 ] && [ "$(st .tunnel)" = down ]'
req POST '/config?activate=maybe' "$WORK/auth" "$WORK/c2"; check "bad activate 400" '[ "$CODE" = 400 ]'
req GET /nope;                     check "unknown 404"            '[ "$CODE" = 404 ]'
echo "ALL $n PASSED (tunnel left: $(st .tunnel))"
