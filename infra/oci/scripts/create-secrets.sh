#!/usr/bin/env bash
# Create PROTO's secrets in OCI Vault from Cloud Shell. Values are generated here or typed with
# read -s; none is ever printed, written to disk or passed on a command line.
#
#   bash infra/oci/scripts/create-secrets.sh            # reads ~/proto-vars.json and ~/proto.tfstate
#
# Generated: the bearer tokens, the TradingView webhook secret, the MT5 bridge token, the
# Ed25519 permit key pair (the API gets the private seed, the executor only the public key) and
# both database URLs with a random password. Typed: the Binance Spot testnet key and secret.
# A secret that already exists is left untouched, so the script is safe to run again.
set -euo pipefail

vars="${PROTO_VARS:-$HOME/proto-vars.json}"
state="${PROTO_TFSTATE:-$HOME/proto.tfstate}"
[ -f "$vars" ] || { echo "missing $vars" >&2; exit 1; }
[ -f "$state" ] || { echo "missing $state (oci resource-manager stack get-stack-tf-state)" >&2; exit 1; }

read -r comp vault key < <(python3 - "$vars" "$state" <<'PY'
import json, sys
v = json.load(open(sys.argv[1])); o = json.load(open(sys.argv[2]))["outputs"]
print(v["compartment_ocid"], o["vault_id"]["value"], o["secrets_key_id"]["value"])
PY
)

# A secret exists only when the list returns its OCID. The OCI CLI prints nothing at all for an
# empty list (not "0", not "[]"), so a count-based check read every missing secret as present.
exists() {
  local id
  id="$(oci vault secret list --compartment-id "$comp" --vault-id "$vault" --name "$1" \
    --lifecycle-state ACTIVE --query 'data[0].id' --raw-output 2>/dev/null)"
  [[ "$id" == ocid1.* ]]
}

# put <name>: reads the value on stdin. The request goes to the CLI as a 0600 JSON file in a
# private temp dir (--from-json), never in argv, so the value shows neither in ps nor in history.
put() {
  local name="$1" dir
  dir="$(umask 077 && mktemp -d)"
  # The program goes in -c because stdin carries the value.
  python3 -c '
import base64, json, os, sys
path, comp, vault, key, name = sys.argv[1:]
value = sys.stdin.read()
if not value:
    sys.exit(f"empty value for {name}")
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"compartmentId": comp, "vaultId": vault, "keyId": key, "secretName": name,
               "secretContentContent": base64.b64encode(value.encode()).decode()}, f)
' "$dir/req.json" "$comp" "$vault" "$key" "$name" || { rm -rf "$dir"; return 1; }
  if oci vault secret create-base64 --from-json "file://$dir/req.json" >/dev/null; then
    rm -rf "$dir"; echo "created $name"
  else
    rm -rf "$dir"; echo "failed  $name" >&2; return 1
  fi
}

rand() { python3 -c 'import secrets; print(secrets.token_hex(32), end="")'; }

ensure() {  # ensure <name> <generator...>
  local name="$1"; shift
  if exists "$name"; then echo "exists  $name"; else "$@" | put "$name"; fi
}

for name in proto-operator-token proto-executor-token proto-watchdog-api-token \
            proto-watchdog-executor-token proto-tradingview-secret proto-mt5-bridge-token; do
  ensure "$name" rand
done

# The permit pair is created together: a private seed without its public key (or the reverse)
# would leave the executor unable to verify permits.
if exists proto-permit-private-key && exists proto-permit-public-key; then
  echo "exists  proto-permit-private-key / proto-permit-public-key"
elif exists proto-permit-private-key || exists proto-permit-public-key; then
  echo "only one half of the permit key pair exists: delete it and run again" >&2; exit 1
else
  pair="$(python3 "$(dirname "$0")/ed25519_pair.py")"
  printf '%s' "${pair%% *}" | put proto-permit-private-key
  printf '%s' "${pair##* }" | put proto-permit-public-key
  unset pair
fi

db_url() {  # db_url <user>: the password lives only inside the URL secret
  python3 -c 'import secrets, sys; u = sys.argv[1]
print(f"postgresql+asyncpg://{u}:{secrets.token_hex(24)}@127.0.0.1:5432/{u}", end="")' "$1"
}
ensure proto-api-database-url db_url proto
ensure proto-executor-database-url db_url executor

for name in proto-binance-testnet-key proto-binance-testnet-secret; do
  if exists "$name"; then echo "exists  $name"; continue; fi
  # The prompt reads the terminal, so it works even when this script is run from setup.sh.
  if [ -n "${PROTO_NO_PROMPT:-}" ] || ! { exec 3</dev/tty; } 2>/dev/null; then
    echo "missing $name: add it in the Vault console (runbook, 'Binance testnet key')"; continue
  fi
  read -rsp "$name (testnet.binance.vision; Enter to skip for now): " value <&3; echo
  exec 3<&-
  if [ -z "$value" ]; then echo "skipped $name: run this script again once you have it"; continue; fi
  printf '%s' "$value" | put "$name"
  unset value
done
