#!/usr/bin/env bash
# Pull this instance's secrets from OCI Vault into /run/proto/<role>.env (tmpfs, 0600).
#
#   fetch-secrets.sh <api|executor|watchdog>[-alpaca] <vault-ocid>
#
# Authenticates as the instance itself (instance principal): no API key on the box. Each role
# reads only the secret names its IAM policy allows (infra/oci/iam.tf); a missing secret fails
# the unit, so the service that needs it never starts half-configured. Values are never echoed.
#
# For the executor the Binance pair is checked before the new file replaces the one in use
# (proto-check-binance-pair): a pair that cannot sign leaves the previous file in place and exits 3,
# so a mistaken new Vault version never reaches the running executor's next restart.
set -euo pipefail

role="${1:?role}"
vault="${2:?vault ocid}"
out_dir="${PROTO_RUN_DIR:-/run/proto}"
out="${out_dir}/${role}.env"

case "$role" in
  api)
    pairs="OPERATOR_API_TOKEN=proto-operator-token
DATABASE_URL=proto-api-database-url
EXECUTOR_API_TOKEN=proto-executor-token
WATCHDOG_API_TOKEN=proto-watchdog-api-token
RISK_PERMIT_PRIVATE_KEY=proto-permit-private-key
TRADINGVIEW_WEBHOOK_SECRET=proto-tradingview-secret" ;;
  executor)
    pairs="EXECUTOR_API_TOKEN=proto-executor-token
EXECUTOR_WATCHDOG_TOKEN=proto-watchdog-executor-token
EXECUTOR_PERMIT_PUBLIC_KEY=proto-permit-public-key
EXECUTOR_DATABASE_URL=proto-executor-database-url
PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_KEY=proto-binance-testnet-key
PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_SECRET=proto-binance-testnet-secret" ;;
  watchdog)
    pairs="WATCHDOG_API_TOKEN=proto-watchdog-api-token
WATCHDOG_EXECUTOR_TOKEN=proto-watchdog-executor-token" ;;
  # Optional Alpaca (US equities, PAPER account) roles, fetched by proto-deploy only when
  # /etc/proto/alpaca.conf exists. Kept apart from the roles above on purpose: a missing Alpaca
  # secret fails only this fetch, so the Alpaca executor waits and nothing else stops.
  executor-alpaca)
    pairs="EXECUTOR_API_TOKEN=proto-alpaca-executor-token
EXECUTOR_WATCHDOG_TOKEN=proto-watchdog-alpaca-token
EXECUTOR_PERMIT_PUBLIC_KEY=proto-permit-public-key
PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_KEY=proto-alpaca-paper-key
PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_SECRET=proto-alpaca-paper-secret" ;;
  api-alpaca)
    pairs="ALPACA_EXECUTOR_API_TOKEN=proto-alpaca-executor-token" ;;
  watchdog-alpaca)
    pairs="WATCHDOG_ALPACA_EXECUTOR_TOKEN=proto-watchdog-alpaca-token" ;;
  *) echo "unknown role: $role" >&2; exit 2 ;;
esac

umask 077
mkdir -p "$out_dir"
[ "$out_dir" != /run/proto ] || mountpoint -q "$out_dir" || mount -t tmpfs -o size=1m,mode=0700 tmpfs "$out_dir"
tmp="$(mktemp "${out_dir}/.${role}.XXXXXX")"
trap 'rm -f "$tmp"' EXIT

while IFS='=' read -r var name; do
  [ -n "$var" ] || continue
  value="$(oci secrets secret-bundle get-secret-bundle-by-name \
    --auth instance_principal --vault-id "$vault" --secret-name "$name" \
    --query 'data."secret-bundle-content".content' --raw-output | base64 -d)"
  if [ -z "$value" ]; then
    echo "secret $name is empty or unreadable" >&2
    exit 1
  fi
  printf '%s=%s\n' "$var" "$value" >>"$tmp"
done <<<"$pairs"

if [ "$role" = executor ]; then
  # The Alpaca paper secret, when this host may read it, to catch the two being mixed up.
  alpaca="$(oci secrets secret-bundle get-secret-bundle-by-name \
    --auth instance_principal --vault-id "$vault" --secret-name proto-alpaca-paper-secret \
    --query 'data."secret-bundle-content".content' --raw-output 2>/dev/null | base64 -d 2>/dev/null || true)"
  if ! PROTO_ALPACA_SECRET="$alpaca" "${PROTO_CHECK_BINANCE:-/usr/local/sbin/proto-check-binance-pair}" "$tmp"; then
    unset alpaca
    echo "kept the previous $out (restore a working version: infra/oci/scripts/binance-versions.sh)" >&2
    exit 3
  fi
  unset alpaca
fi

mv "$tmp" "$out"
trap - EXIT
echo "wrote $(grep -c '=' "$out") secrets for $role to $out"
