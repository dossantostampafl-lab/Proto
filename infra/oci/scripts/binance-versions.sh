#!/usr/bin/env bash
# The Binance testnet pair's versions in the Vault, each tested against the testnet, from Cloud Shell.
#
#   bash infra/oci/scripts/binance-versions.sh             list and test every version of the secret
#   bash infra/oci/scripts/binance-versions.sh restore N   make version N of the secret current again
#
# For when setup.sh reports "Binance key refused": a new version of proto-binance-testnet-secret
# that cannot sign (on 2026-10-28 it held the Alpaca paper secret). The working one is usually still
# in the Vault as an earlier version; restoring it needs no new key. Prints lengths and results,
# never a value. Then run setup.sh again so the executor reloads it.
set -uo pipefail

vars="${PROTO_VARS:-$HOME/proto-vars.json}"
state="${PROTO_TFSTATE:-$HOME/proto.tfstate}"
url="${PROTO_BINANCE_TESTNET_URL:-https://testnet.binance.vision}"
here="$(cd "$(dirname "$0")" && pwd)"
V="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["outputs"]["vault_id"]["value"])' "$state")"
C="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["compartment_ocid"])' "$vars")"

secret_id() {
  oci vault secret list --compartment-id "$C" --vault-id "$V" --name "proto-binance-testnet-$1" \
    --lifecycle-state ACTIVE --all --query 'data[0].id' --raw-output
}
current() { oci vault secret get --secret-id "$1" --query 'data."current-version-number"' --raw-output; }
value() {
  oci secrets secret-bundle get --secret-id "$1" --version-number "$2" \
    --query 'data."secret-bundle-content".content' --raw-output 2>/dev/null | base64 -d 2>/dev/null
}

sid="$(secret_id secret)"
[[ "$sid" == ocid1.* ]] || { echo "proto-binance-testnet-secret not found in the Vault" >&2; exit 1; }

if [ "${1:-}" = restore ]; then
  [[ "${2:-}" =~ ^[0-9]+$ ]] || { echo "usage: $0 restore <version number>" >&2; exit 2; }
  oci vault secret update --secret-id "$sid" --current-version-number "$2" --force >/dev/null \
    || { echo "could not restore version $2" >&2; exit 1; }
  echo "proto-binance-testnet-secret: current version is now v$(current "$sid"); run setup.sh again"
  exit 0
fi

kid="$(secret_id key)"
key_now="$(current "$kid")"
secret_now="$(current "$sid")"
alpaca="$(oci secrets secret-bundle get-secret-bundle-by-name --vault-id "$V" \
  --secret-name proto-alpaca-paper-secret --query 'data."secret-bundle-content".content' \
  --raw-output 2>/dev/null | base64 -d 2>/dev/null || true)"
echo "API key: current version v$key_now. Secret: current version v$secret_now."
for v in $(oci vault secret-version list --secret-id "$sid" --all \
  --query 'data[]."version-number"' --raw-output | tr -d '[],'); do
  # One temporary env file per version, as the executor host reads it; removed right away.
  tmp="$(umask 077 && mktemp)"
  printf 'PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_KEY=%s\nPROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_SECRET=%s\n' \
    "$(value "$kid" "$key_now")" "$(value "$sid" "$v")" >"$tmp"
  mark=""; [ "$v" = "$secret_now" ] && mark=" (current)"
  if result="$(PROTO_ALPACA_SECRET="$alpaca" PROTO_BINANCE_TESTNET_URL="$url" \
      python3 "$here/check-binance-pair.py" "$tmp" 2>&1)"; then
    echo "secret v$v$mark: OK, Binance accepts it${result:+ ($result)}"
  else
    result="${result#BINANCE PAIR REFUSED: }"
    echo "secret v$v$mark: refused, ${result%%; the executor*}"
  fi
  rm -f "$tmp"
done
unset alpaca
echo "To use a version that works: bash $0 restore <N>, then run setup.sh again."
