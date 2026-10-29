#!/usr/bin/env bash
# Reach a PROTO instance from Cloud Shell through the Bastion. No public SSH port anywhere.
#
#   bash infra/oci/deploy/bastion-run.sh bootstrap <api|executor|watchdog> [domain]
#   bash infra/oci/deploy/bastion-run.sh status    <api|executor|watchdog>
#   bash infra/oci/deploy/bastion-run.sh ssh       <api|executor|watchdog>
#   bash infra/oci/deploy/bastion-run.sh diag      executor
#
# diag (read-only) shows the executor host's containers, its masked logs, and whether the Binance
# testnet pair it runs with is the one in the Vault (fingerprints, never values) and is accepted by
# Binance from that host's own address.
#
# Opens a short port-forwarding session to the instance's port 22 (no agent plugin needed),
# then SSHes through it with ~/.ssh/proto. Reads ~/proto-vars.json and ~/proto.tfstate.
set -euo pipefail

action="${1:?bootstrap|status|ssh|diag}" role="${2:?api|executor|watchdog}" domain="${3:-}"
vars="${PROTO_VARS:-$HOME/proto-vars.json}"
state="${PROTO_TFSTATE:-$HOME/proto.tfstate}"
key="$HOME/.ssh/proto"
here="$(cd "$(dirname "$0")" && pwd)"

read -r region bastion vault ip executor_ip api_ip < <(python3 - "$vars" "$state" "$role" <<'PY'
import json, sys
v = json.load(open(sys.argv[1])); o = json.load(open(sys.argv[2]))["outputs"]
ips = o["private_ips"]["value"]
print(v.get("region", "sa-valparaiso-1"), o["bastion_id"]["value"], o["vault_id"]["value"],
      ips[sys.argv[3]], ips["executor"], ips["api"])
PY
)

# Cloud Shell reaches the Bastion from inside Oracle's network, not from the address ifconfig.me
# reports, so an allowlist of that address closes every session right after authentication.
# Access rests on IAM (who may create a session) and the session's own key, not on the source IP.
allowed="$(oci bastion bastion get --bastion-id "$bastion" \
  --query 'data."client-cidr-block-allow-list"' --raw-output)"
case "$allowed" in
  *'"0.0.0.0/0"'*) ;;
  *) echo "note: the Bastion allows only $allowed; from Cloud Shell, operator_cidrs must be [\"0.0.0.0/0\"]" >&2 ;;
esac

echo "opening a Bastion session to $role ($ip)..."
session="$(oci bastion session create-port-forwarding --bastion-id "$bastion" \
  --display-name "proto-$role-$(date +%s)" --target-private-ip "$ip" --target-port 22 \
  --ssh-public-key-file "$key.pub" --session-ttl 1800 \
  --wait-for-state SUCCEEDED --wait-for-state FAILED --wait-interval-seconds 5 \
  --query "data.resources[0].identifier" \
  --raw-output)"
[ "$(oci bastion session get --session-id "$session" --query 'data."lifecycle-state"' \
  --raw-output)" = ACTIVE ] || { echo "the Bastion session did not become active" >&2; exit 1; }

opts="-i $key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20"
proxy="ssh $opts -W %h:%p -p 22 ${session}@host.bastion.${region}.oci.oraclecloud.com"
ssh_to() {
  # shellcheck disable=SC2086
  ssh $opts -o ProxyCommand="$proxy" "opc@$ip" "$@"
}

# A new session reads ACTIVE before the Bastion accepts its key ("Permission denied (publickey)"
# for up to a minute or two), so wait until a no-op connection gets through.
for attempt in $(seq 12); do
  ssh_to true 2>/dev/null && break
  [ "$attempt" = 12 ] && { echo "still refused: retry, and check that ~/.ssh/proto.pub is the key the instance was created with" >&2; exit 1; }
  echo "waiting for the Bastion to accept the key ($attempt/12)..."
  sleep 15
done

case "$action" in
  bootstrap)
    tar -C "$here/.." -cz deploy scripts \
      | ssh_to "d=\$(mktemp -d) && tar -xz -C \$d && sudo bash \$d/deploy/bootstrap.sh \
          $role $vault $executor_ip $api_ip '$domain'; rc=\$?; rm -rf \$d; exit \$rc" ;;
  status)
    ssh_to "sudo systemctl --no-pager status proto-secrets proto-deploy.timer | grep -E 'Active|Trigger'; \
      sudo podman ps --format '{{.Names}}  {{.Status}}'; \
      sudo journalctl -u proto-deploy --no-pager -n 15 -o cat" ;;
  ssh)
    ssh_to ;;
  diag)
    [ "$role" = executor ] || { echo "diag is for the executor" >&2; exit 2; }
    # The Vault's pair as Cloud Shell reads it, as fingerprints only.
    for half in key secret; do
      oci secrets secret-bundle get-secret-bundle-by-name --vault-id "$vault" \
        --secret-name "proto-binance-testnet-$half" \
        --query 'data."secret-bundle-content".content' --raw-output | base64 -d \
        | python3 -c 'import hashlib,sys; v=sys.stdin.read(); print("Vault:    %s %d car. impressao %s" % (sys.argv[1], len(v), hashlib.sha256(v.encode()).hexdigest()[:10]))' "$half"
    done
    ssh_to 'sudo bash -s' <<'REMOTE'
echo "== host"; uptime; free -m | sed -n 1,2p
echo "== containers"; podman ps -a --format '{{.Names}}  {{.Status}}'
echo "== secrets"; systemctl is-active proto-secrets; stat -c 'executor.env %y' /run/proto/executor.env 2>&1
python3 - <<'PY'
import hashlib, hmac, json, time, urllib.error, urllib.request
env = {}
for line in open("/run/proto/executor.env"):
    if "=" in line:
        name, value = line.rstrip("\n").split("=", 1)
        env[name] = value
k = env.get("PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_KEY", "")
s = env.get("PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_SECRET", "")
fp = lambda v: hashlib.sha256(v.encode()).hexdigest()[:10]
print("Servidor: key %d car. impressao %s" % (len(k), fp(k)))
print("Servidor: secret %d car. impressao %s" % (len(s), fp(s)))
q = "timestamp=%d&recvWindow=10000" % int(time.time() * 1000)
sig = hmac.new(s.encode(), q.encode(), hashlib.sha256).hexdigest()
req = urllib.request.Request("https://testnet.binance.vision/api/v3/account?" + q + "&signature=" + sig,
                             headers={"X-MBX-APIKEY": k})
try:
    urllib.request.urlopen(req, timeout=20)
    print("Binance testnet a partir DESTE servidor: 200 (funcionando)")
except urllib.error.HTTPError as e:
    body = json.loads(e.read() or b"{}")
    print("Binance testnet a partir DESTE servidor: %s codigo %s (%s)" % (e.code, body.get("code"), body.get("msg")))
except Exception as e:
    print("Binance testnet a partir DESTE servidor: sem resposta (%s)" % type(e).__name__)
PY
mask='import os,sys
vals=[l.split("=",1)[1].strip() for f in sys.argv[1:] if os.path.exists(f) for l in open(f) if "=" in l]
for line in sys.stdin:
    for v in vals:
        if len(v) >= 16: line = line.replace(v, "***")
    sys.stdout.write("  | " + line)'
for c in proto-executor proto-executor-alpaca; do
  echo "== last lines of $c"
  podman logs --tail 20 "$c" 2>&1 | python3 -c "$mask" /run/proto/executor.env /run/proto/executor-alpaca.env
done
echo "== deploy"; journalctl -u proto-deploy -n 12 --no-pager -o cat
REMOTE
    ;;
  *) echo "unknown action: $action" >&2; exit 2 ;;
esac
oci bastion session delete --session-id "$session" --force >/dev/null 2>&1 || true
