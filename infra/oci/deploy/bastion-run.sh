#!/usr/bin/env bash
# Reach a PROTO instance from Cloud Shell through the Bastion. No public SSH port anywhere.
#
#   bash infra/oci/deploy/bastion-run.sh bootstrap <api|executor|watchdog> [domain]
#   bash infra/oci/deploy/bastion-run.sh status    <api|executor|watchdog>
#   bash infra/oci/deploy/bastion-run.sh ssh       <api|executor|watchdog>
#
# Opens a short port-forwarding session to the instance's port 22 (no agent plugin needed),
# then SSHes through it with ~/.ssh/proto. Reads ~/proto-vars.json and ~/proto.tfstate.
set -euo pipefail

action="${1:?bootstrap|status|ssh}" role="${2:?api|executor|watchdog}" domain="${3:-}"
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

# The Bastion admits only the CIDRs in operator_cidrs; Cloud Shell's address can change.
me="$(curl -fsS https://ifconfig.me)"
allowed="$(oci bastion bastion get --bastion-id "$bastion" \
  --query 'data."client-cidr-block-allow-list"' --raw-output)"
case "$allowed" in
  *"\"$me/32\""*) ;;
  *) echo "Cloud Shell is at $me, but the Bastion allows $allowed." >&2
     echo "Set operator_cidrs to [\"$me/32\"] in the stack variables and apply, then retry." >&2
     exit 1 ;;
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

proxy="ssh -i $key -o StrictHostKeyChecking=accept-new -W %h:%p -p 22 ${session}@host.bastion.${region}.oci.oraclecloud.com"
ssh_to() {
  ssh -i "$key" -o StrictHostKeyChecking=accept-new -o ProxyCommand="$proxy" "opc@$ip" "$@"
}

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
  *) echo "unknown action: $action" >&2; exit 2 ;;
esac
oci bastion session delete --session-id "$session" --force >/dev/null 2>&1 || true
