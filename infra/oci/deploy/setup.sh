#!/usr/bin/env bash
# Put PROTO on Oracle Cloud in one go, from Cloud Shell. Safe to run again at any time.
#
#   bash <(curl -fsSL https://raw.githubusercontent.com/dossantostampafl-lab/Proto/main/infra/oci/deploy/setup.sh)
#
# 1. Syncs the Resource Manager stack with the repository and applies it, unless the plan would
#    destroy or replace something: then it stops and applies nothing.
# 2. Creates the Vault secrets that are missing (create-secrets.sh). It asks for nothing: the
#    Binance testnet key and secret are added once in the Vault console (see the runbook).
# 3. Bootstraps each instance over the Bastion (bootstrap.sh), retrying the Bastion's slow start.
# 4. Checks that each instance reads its own secrets. If one cannot, it tests the policy forms of
#    iam.tf (secret_policy_mode) with a temporary policy, keeps the first that reads and applies
#    it through the stack. Every form keeps the per-secret restriction.
# 5. Runs the deploy on each instance and reports what is running.
#
# Optional Alpaca US equities, PAPER account only (docs/venues/alpaca-paper.md): run it with
#   PROTO_ALPACA=1 bash <(curl ...setup.sh)          (PROTO_ALPACA_SYMBOLS=SPY,QQQ by default)
# to open the Alpaca port in the stack and write /etc/proto/alpaca.conf on each instance, once the
# Alpaca paper key pair is in the Vault. US agents stay off (AGENT_US_ENABLED=false).
# Prints no secret value. Reads ~/proto-vars.json and ~/.ssh/proto; logs to ~/proto-setup.log.
set -uo pipefail

BRANCH="${PROTO_BRANCH:-main}"
REPO="$HOME/Proto"
if [ "${1:-}" != "--from-repo" ]; then
  [ -d "$REPO/.git" ] || git clone -q https://github.com/dossantostampafl-lab/Proto.git "$REPO" || exit 1
  git -C "$REPO" fetch -q origin "$BRANCH" && git -C "$REPO" checkout -q -f -B proto-setup FETCH_HEAD \
    || { echo "could not fetch branch $BRANCH" >&2; exit 1; }
  exec bash "$REPO/infra/oci/deploy/setup.sh" --from-repo </dev/null
fi

LOG="$HOME/proto-setup.log"
exec > >(tee -a "$LOG") 2>&1
VARS="$HOME/proto-vars.json"
STATE="$HOME/proto.tfstate"
KEY="$HOME/.ssh/proto"
ROLES=(executor watchdog api)
declare -A FIRST_SECRET=([executor]=proto-executor-token [watchdog]=proto-watchdog-api-token [api]=proto-operator-token)
declare -A SESSION RESULT

step() { printf '\n=== %s\n' "$*"; }
note() { printf '  %s\n' "$*"; }
die() { printf '\nSTOPPED: %s\nLog: %s\n' "$*" "$LOG"; exit 1; }

for f in "$VARS" "$KEY" "$KEY.pub"; do [ -f "$f" ] || die "missing $f (see docs/runbooks/oracle-setup.md, step 2)"; done
var() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2], ""))' "$VARS" "$1"; }
setvar() {
  python3 - "$VARS" "$1" "$2" <<'PY'
import json, sys
path, key, value = sys.argv[1:]
data = json.load(open(path)); data[key] = value
json.dump(data, open(path, "w"), indent=2)
PY
}
out() {
  python3 -c 'import json,sys; v=json.load(open(sys.argv[1]))["outputs"][sys.argv[2]]["value"]; print(v[sys.argv[3]] if len(sys.argv) > 3 else v)' \
    "$STATE" "$@"
}

# Oracle throttles bursts of API calls (429 TooManyRequests), Resource Manager jobs above all.
# oci_retry waits and tries again on 429 only; any other error is returned as is.
oci_retry() {
  local i rc err out
  err="$(mktemp)"
  for i in 1 2 3 4 5 6; do
    out="$(oci "$@" 2>"$err")"; rc=$?
    if [ "$rc" = 0 ]; then rm -f "$err"; printf '%s' "$out"; return 0; fi
    if grep -qE 'TooManyRequests|"status": 429' "$err"; then
      echo "  Oracle asked to slow down (429); waiting $((i * 30)) s" >&2
      sleep $((i * 30)); continue
    fi
    break
  done
  cat "$err" >&2; rm -f "$err"; return "$rc"
}

COMP="$(var compartment_ocid)"
REGION="$(var region)"; REGION="${REGION:-sa-valparaiso-1}"
STACK="$(oci resource-manager stack list --compartment-id "$COMP" --display-name proto \
  --query 'data[0].id' --raw-output 2>/dev/null)"
[ -n "$STACK" ] || die "stack 'proto' not found in the compartment"
ALPACA="${PROTO_ALPACA:-0}"
ALPACA_SYMBOLS="$(printf '%s' "${PROTO_ALPACA_SYMBOLS:-SPY,QQQ}" | tr 'a-z' 'A-Z' | tr -cd 'A-Z0-9,.')"
[ "$ALPACA" = 1 ] && [ "$(var enable_alpaca)" != true ] && setvar enable_alpaca true

# --- 1. Stack ----------------------------------------------------------------------------------
job_state() { oci_retry resource-manager job get --job-id "$1" --query 'data."lifecycle-state"' --raw-output; }
stack_apply() {
  (cd "$REPO/infra/oci" && rm -f "$HOME/proto-oci.zip" \
    && zip -qr "$HOME/proto-oci.zip" . -x '.terraform/*' '*.tfstate*' 'terraform.tfvars') || return 1
  oci_retry resource-manager stack update --stack-id "$STACK" --config-source "$HOME/proto-oci.zip" \
    --variables "file://$VARS" --force >/dev/null || return 1
  local plan log summary apply
  plan="$(oci_retry resource-manager job create-plan-job --stack-id "$STACK" --display-name setup-plan \
    --wait-for-state SUCCEEDED --wait-for-state FAILED --query data.id --raw-output)" || return 1
  log="$(oci_retry resource-manager job get-job-logs-content --job-id "$plan" --query data --raw-output)"
  if [ "$(job_state "$plan")" != SUCCEEDED ]; then
    grep -iE 'error' <<<"$log" | tail -5; return 1
  fi
  if grep -q 'No changes' <<<"$log"; then
    note "stack already matches the repository"
  else
    summary="$(grep -oE 'Plan: [0-9]+ to add, [0-9]+ to change, [0-9]+ to destroy' <<<"$log" | tail -1)"
    grep -E 'will be|must be replaced' <<<"$log" | sed 's/^.*# /    # /'
    note "$summary"
    grep -q ' 0 to destroy' <<<"$summary" || { note "the plan destroys or replaces resources: nothing applied"; return 2; }
    apply="$(oci_retry resource-manager job create-apply-job --stack-id "$STACK" --display-name setup-apply \
      --execution-plan-strategy FROM_PLAN_JOB_ID --execution-plan-job-id "$plan" \
      --wait-for-state SUCCEEDED --wait-for-state FAILED --query data.id --raw-output)" || return 1
    [ "$(job_state "$apply")" = SUCCEEDED ] || { note "apply failed (Resource Manager > job setup-apply)"; return 1; }
    note "applied"
  fi
  oci_retry resource-manager stack get-stack-tf-state --stack-id "$STACK" --file "$STATE" >/dev/null
}

step "1/5 Stack (Resource Manager)"
stack_apply || die "the stack could not be synced"
BASTION="$(out bastion_id)"; VAULT="$(out vault_id)"
EXECUTOR_IP="$(out private_ips executor)"; API_IP="$(out private_ips api)"
DOMAIN="$(var domain)"
# No domain of your own: sslip.io resolves <a-b-c-d>.sslip.io to a.b.c.d, so Caddy can still get a
# real certificate for the API's public IP and serve the dashboard over HTTPS on 443.
if [ -z "$DOMAIN" ]; then
  API_PUBLIC="$(out api_public_ip)"
  [[ "$API_PUBLIC" =~ ^[0-9]+(\.[0-9]+){3}$ ]] && DOMAIN="${API_PUBLIC//./-}.sslip.io"
fi

# --- 2. Secrets in the Vault -------------------------------------------------------------------
step "2/5 Secrets in the Vault (created once, values never shown)"
# No prompt here: on a tablet, switching tabs to copy a key drops the Cloud Shell connection and
# kills the script. The Binance testnet key and secret go in through the Vault console instead.
PROTO_NO_PROMPT=1 PROTO_VARS="$VARS" PROTO_TFSTATE="$STATE" bash "$REPO/infra/oci/scripts/create-secrets.sh" \
  || die "the secrets could not be created"
BINANCE_MISSING=false
for name in proto-binance-testnet-key proto-binance-testnet-secret; do
  id="$(oci_retry vault secret list --compartment-id "$COMP" --vault-id "$VAULT" --name "$name" \
    --lifecycle-state ACTIVE --query 'data[0].id' --raw-output 2>/dev/null)"
  [[ "$id" == ocid1.* ]] || BINANCE_MISSING=true
done
$BINANCE_MISSING && note "Binance testnet key/secret not in the Vault yet: the executor waits for them (docs/runbooks/oracle-setup.md, 'Binance testnet key')"
# Optional: the Alpaca PAPER key pair. Missing only keeps the Alpaca executor off (it is fetched as
# its own role, executor-alpaca); the Binance executor, the API and the watchdog do not wait for it.
ALPACA_MISSING=false
for name in proto-alpaca-paper-key proto-alpaca-paper-secret; do
  id="$(oci_retry vault secret list --compartment-id "$COMP" --vault-id "$VAULT" --name "$name" \
    --lifecycle-state ACTIVE --query 'data[0].id' --raw-output 2>/dev/null)"
  [[ "$id" == ocid1.* ]] || { ALPACA_MISSING=true; note "$name not in the Vault: Alpaca (optional) stays off (docs/venues/alpaca-paper.md)"; break; }
done

# --- Bastion -----------------------------------------------------------------------------------
SSH_OPTS=(-i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=20
  -o ServerAliveInterval=15 -o LogLevel=ERROR)
on() {  # on <role> <command>: one SSH command through the role's current Bastion session
  local role="$1"; shift
  ssh "${SSH_OPTS[@]}" -o ProxyCommand="ssh ${SSH_OPTS[*]} -W %h:%p -p 22 ${SESSION[$role]}@host.bastion.$REGION.oci.oraclecloud.com" \
    "opc@$(out private_ips "$role")" "$@"
}
open_session() {  # a new session reads ACTIVE before the Bastion accepts its key; wait for that
  local role="$1" i
  SESSION[$role]="$(oci_retry bastion session create-port-forwarding --bastion-id "$BASTION" \
    --display-name "proto-setup-$role" --target-private-ip "$(out private_ips "$role")" --target-port 22 \
    --ssh-public-key-file "$KEY.pub" --session-ttl 10800 --wait-for-state SUCCEEDED --wait-for-state FAILED \
    --wait-interval-seconds 5 --query 'data.resources[0].identifier' --raw-output)" || return 1
  for i in $(seq 16); do
    on "$role" true </dev/null 2>/dev/null && return 0
    sleep 15
  done
  SESSION[$role]=""
  return 1
}
run() {  # run <role> <command>: reopens the session once if SSH itself fails (exit 255)
  local role="$1" rc; shift
  [ -n "${SESSION[$role]:-}" ] || open_session "$role" || return 255
  on "$role" "$@"; rc=$?
  if [ "$rc" = 255 ]; then open_session "$role" || return 255; on "$role" "$@"; rc=$?; fi
  return "$rc"
}

# --- 3. Bootstrap ------------------------------------------------------------------------------
step "3/5 Bootstrap (podman, Vault reader, deploy timer)"
for role in "${ROLES[@]}"; do
  ok=false
  for attempt in 1 2 3; do
    # The session opens here, in this shell: a pipeline runs in a subshell and would lose it.
    [ -n "${SESSION[$role]:-}" ] || open_session "$role" || { note "$role: Bastion session refused"; continue; }
    if tar -C "$REPO/infra/oci" -cz deploy scripts | on "$role" "d=\$(mktemp -d) && tar -xz -C \$d && \
        sudo bash \$d/deploy/bootstrap.sh $role $VAULT $EXECUTOR_IP $API_IP '$DOMAIN' >/tmp/proto-bootstrap.log 2>&1; \
        rc=\$?; rm -rf \$d; exit \$rc"; then ok=true; break; fi
    note "$role: attempt $attempt failed, retrying with a new Bastion session"
    SESSION[$role]=""
  done
  RESULT[$role.bootstrap]=$($ok && echo ok || echo FAILED)
  note "$role: bootstrap ${RESULT[$role.bootstrap]}"
done

# --- 4. Secrets --------------------------------------------------------------------------------
can_read() {  # prints nothing of the secret: only its content type
  run "$1" "oci secrets secret-bundle get-secret-bundle-by-name --auth instance_principal \
    --vault-id $VAULT --secret-name $2 --query 'data.\"secret-bundle-content\".\"content-type\"' \
    --raw-output 2>/dev/null" </dev/null | grep -q BASE64
}
secrets_ok() { run "$1" "sudo systemctl restart proto-secrets >/dev/null 2>&1 && sudo test -s /run/proto/$1.env" </dev/null; }
check_secrets() {
  local role all=true
  for role in "${ROLES[@]}"; do
    [ "${RESULT[$role.bootstrap]}" = ok ] || continue
    if [ "$role" = executor ] && $BINANCE_MISSING; then
      RESULT[$role.secrets]="needs Binance keys"; note "executor: waiting for the Binance testnet key and secret"
      continue
    fi
    if secrets_ok "$role"; then RESULT[$role.secrets]=ok; else RESULT[$role.secrets]=FAILED; all=false; fi
    note "$role: secrets ${RESULT[$role.secrets]}"
  done
  $all
}
policy_statement() {  # same forms as iam.tf, for one role and one secret
  python3 - "$@" <<'PY'
import json, sys
mode, dg, comp, vault, name, sid, inst = sys.argv[1:]
forms = {
    "dg_name": f"Allow dynamic-group {dg} to read secret-bundles in compartment id {comp} where target.secret.name = '{name}'",
    "dg_id": f"Allow dynamic-group {dg} to read secret-bundles in compartment id {comp} where target.secret.id = '{sid}'",
    "principal_name": f"Allow any-user to read secret-bundles in compartment id {comp} where all {{request.principal.id = '{inst}', target.secret.name = '{name}'}}",
    "principal_id": f"Allow any-user to read secret-bundles in compartment id {comp} where all {{request.principal.id = '{inst}', target.secret.id = '{sid}'}}",
}
print(json.dumps([forms[mode]]))
PY
}
find_policy_mode() {  # find_policy_mode <role>: prints the first form under which the role reads
  local role="$1" name sid inst pid mode found=""
  name="${FIRST_SECRET[$role]}"; inst="$(var "${role}_instance_id")"
  sid="$(oci vault secret list --compartment-id "$COMP" --vault-id "$VAULT" --name "$name" \
    --lifecycle-state ACTIVE --query 'data[0].id' --raw-output)"
  for mode in dg_name dg_id principal_name principal_id; do
    pid="$(oci_retry iam policy create --compartment-id "$COMP" --name proto-setup-test \
      --description "temporary: setup.sh policy test" \
      --statements "$(policy_statement "$mode" "proto-$role" "$COMP" "$VAULT" "$name" "$sid" "$inst")" \
      --query data.id --raw-output 2>/dev/null)" || continue
    sleep 90
    can_read "$role" "$name" && found="$mode"
    oci iam policy delete --policy-id "$pid" --force >/dev/null 2>&1
    echo "  test $mode: $([ -n "$found" ] && echo reads || echo refused)" >&2
    [ -n "$found" ] && break
  done
  echo "$found"
}

step "4/5 Secrets read by each instance"
old="$(oci iam policy list --compartment-id "$COMP" --name proto-setup-test --query 'data[0].id' --raw-output 2>/dev/null)"
[ -n "$old" ] && oci iam policy delete --policy-id "$old" --force >/dev/null 2>&1
# A policy changed by the stack apply above can take a minute or two to reach the instances.
if ! check_secrets && { note "waiting 2 min for IAM to propagate, then checking again"; sleep 120; ! check_secrets; }; then
  for role in "${ROLES[@]}"; do [ "${RESULT[$role.secrets]:-}" = FAILED ] && break; done
  note "testing policy forms with $role (about 90 s each)..."
  mode="$(find_policy_mode "$role")"
  if [ -z "$mode" ]; then
    note "no form reads: check that the dynamic group proto-$role matches the instance"
  elif [ "$mode" = "$(var secret_policy_mode)" ]; then
    note "form $mode already applied; waiting for IAM to propagate"; sleep 120; check_secrets
  else
    note "using secret_policy_mode = $mode"
    setvar secret_policy_mode "$mode"
    stack_apply && { sleep 120; check_secrets; }
  fi
fi

# --- Alpaca (opt-in) ---------------------------------------------------------------------------
# Writes the non-secret /etc/proto/alpaca.conf on each instance. The executor starts the Alpaca
# container on its next deploy; the API and the watchdog need a forced redeploy to pick up its URL.
declare -A DEPLOY_CMD
if [ "$ALPACA" = 1 ]; then
  step "Alpaca paper (opt-in, US agents stay off)"
  if $ALPACA_MISSING; then
    note "Alpaca paper key/secret not in the Vault: nothing written"
  elif [ -z "$ALPACA_SYMBOLS" ]; then
    note "PROTO_ALPACA_SYMBOLS is empty: nothing written"
  else
    # The stack apply above may have just granted the Alpaca secrets: give IAM time to propagate.
    note "waiting 2 min for IAM to propagate"; sleep 120
    for role in "${ROLES[@]}"; do
      [ "${RESULT[$role.secrets]:-}" = ok ] || continue
      if run "$role" "printf 'ALPACA_SYMBOLS=%s\nALPACA_EXECUTOR_PORT=8101\nAGENT_US_ENABLED=false\n' '$ALPACA_SYMBOLS' \
          | sudo install -D -m 0644 -o root -g root /dev/stdin /etc/proto/alpaca.conf" </dev/null; then
        note "$role: alpaca.conf written (symbols $ALPACA_SYMBOLS)"
        [ "$role" = executor ] || DEPLOY_CMD[$role]="sudo /usr/local/sbin/proto-deploy --force"
      else
        note "$role: alpaca.conf could not be written"
      fi
    done
  fi
fi

# --- 5. Deploy ---------------------------------------------------------------------------------
step "5/5 Deploy (pull the image, start, health check)"
for role in "${ROLES[@]}"; do
  if [ "${RESULT[$role.secrets]:-}" != ok ]; then RESULT[$role.deploy]="skipped"; continue; fi
  report="$(run "$role" "${DEPLOY_CMD[$role]:-sudo systemctl start proto-deploy.service}"'; echo "rc=$?";
    sudo podman ps --format "{{.Names}} {{.Status}}"; sudo journalctl -u proto-deploy -t proto-deploy -t systemd -n 40 --no-pager -o cat' </dev/null)"
  sed 's/^/    /' <<<"$report"
  if grep -q 'rc=0' <<<"$report"; then RESULT[$role.deploy]=running
  elif grep -qiE 'unauthorized|denied|manifest unknown' <<<"$report"; then
    RESULT[$role.deploy]="image not pullable (make the GHCR package public)"
  else RESULT[$role.deploy]=FAILED; fi
done

# --- Summary -----------------------------------------------------------------------------------
for role in "${ROLES[@]}"; do
  [ -n "${SESSION[$role]:-}" ] && oci bastion session delete --session-id "${SESSION[$role]}" --force >/dev/null 2>&1
done
step "Summary"
printf '  %-9s %-10s %-10s %s\n' role bootstrap secrets deploy
for role in "${ROLES[@]}"; do
  printf '  %-9s %-10s %-10s %s\n' "$role" "${RESULT[$role.bootstrap]:--}" "${RESULT[$role.secrets]:--}" "${RESULT[$role.deploy]:--}"
done
[ -n "$DOMAIN" ] && note "dashboard: https://$DOMAIN (the certificate can take a minute after the first deploy)"
note "full log: $LOG"
