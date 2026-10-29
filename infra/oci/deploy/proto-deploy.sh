#!/usr/bin/env bash
# Run this instance's PROTO service from the image its role follows, and move to a new image
# when one is published. Installed as /usr/local/sbin/proto-deploy and run by proto-deploy.timer.
#
#   proto-deploy            pull the role's tag; restart only if the image changed or nothing runs
#   proto-deploy --force    restart even if the image is the same (for example after new secrets)
#
# Pull-based on purpose: GitHub publishes images and never reaches this instance
# (.github/workflows/deploy.yml). The role's tag decides what runs here:
#   api, watchdog -> :stable     executor -> :executor (moved only on an operator's approval)
# A new image that fails its health check is replaced by the previous one, so a bad release
# leaves the last good one running.
set -euo pipefail

role="$(cat /etc/proto/role)"
# shellcheck source=/dev/null
. /etc/proto/deploy.env  # IMAGE, EXECUTOR_IP, API_IP, DOMAIN (non-secret)
secrets="/run/proto/${role}.env"
state=/var/lib/proto
mkdir -p "$state"
force=false
[ "${1:-}" = "--force" ] && force=true

log() { echo "proto-deploy[$role]: $*"; }

case "$role" in
  executor) tag=executor ;;
  api | watchdog) tag=stable ;;
  *) log "unknown role"; exit 2 ;;
esac

# Secrets come from the Vault into tmpfs; a missing one stops the deploy before anything restarts.
if [ ! -s "$secrets" ] || $force; then
  systemctl restart proto-secrets.service
fi

podman pull -q "${IMAGE}:${tag}" >/dev/null
new="$(podman image inspect --format '{{.Id}}' "${IMAGE}:${tag}")"
current="$(podman container inspect --format '{{.Image}}' "proto-${role}" 2>/dev/null || true)"
running="$(podman container inspect --format '{{.State.Running}}' "proto-${role}" 2>/dev/null || echo false)"
revision() { podman image inspect --format '{{index .Labels "org.opencontainers.image.revision"}}' "$1"; }

# The database password lives only inside the URL secret; Postgres gets it from a tmpfs file.
ensure_db() {
  local var="$1" url user password db dbenv="/run/proto/${role}-db.env"
  url="$(grep "^${var}=" "$secrets" | cut -d= -f2-)"
  [[ "$url" =~ ^postgresql\+asyncpg://([^:]+):([^@]+)@127\.0\.0\.1:5432/(.+)$ ]] \
    || { log "$var is not a local postgresql+asyncpg URL"; exit 1; }
  user="${BASH_REMATCH[1]}" password="${BASH_REMATCH[2]}" db="${BASH_REMATCH[3]}"
  (umask 077 && printf 'POSTGRES_USER=%s\nPOSTGRES_PASSWORD=%s\nPOSTGRES_DB=%s\n' \
    "$user" "$password" "$db" >"$dbenv")
  # Postgres shares the host network and listens on loopback only. A published port (-p) depends
  # on podman's NAT rules, which every firewall-cmd --reload (bootstrap.sh) wipes: the app then got
  # "connection refused" on 127.0.0.1:5432 while the database itself was up.
  local name="proto-${role}-db"
  if [ "$(podman container inspect --format '{{.State.Running}}' "$name" 2>/dev/null)" != true ] \
    || [ "$(podman container inspect --format '{{.HostConfig.NetworkMode}}' "$name" 2>/dev/null)" != host ]; then
    podman rm -f "$name" >/dev/null 2>&1 || true
    podman run -d --name "$name" --restart=always --network host --env-file "$dbenv" \
      -v "${name}:/var/lib/postgresql/data" \
      docker.io/library/postgres:16 -c listen_addresses=127.0.0.1 -c port=5432 >/dev/null
  fi
  for _ in $(seq 30); do
    podman exec "$name" pg_isready -q -h 127.0.0.1 -p 5432 -U "$user" && return 0
    sleep 2
  done
  log "the database did not accept connections on 127.0.0.1:5432"
  exit 1
}

# Optional XP (B3) node: a root-only file written by deploy/set-xp-node.sh. On the API instance it
# holds XP_EXECUTOR_URL and XP_EXECUTOR_API_TOKEN; on the watchdog instance WATCHDOG_XP_EXECUTOR_URL
# and WATCHDOG_XP_EXECUTOR_TOKEN (the node's halt-only token). Absent file = XP off, exactly as
# before. A malformed file is ignored with a warning instead of passed on: both services refuse to
# start on an invalid XP setting, and a bad optional file must never take one down.
xp_env_file="${PROTO_XP_ENV:-/etc/proto/xp.env}"
xp_env_args() {
  [ -s "$xp_env_file" ] || return 0
  local url token url_key=XP_EXECUTOR_URL token_key=XP_EXECUTOR_API_TOKEN
  if [ "${role:-api}" = watchdog ]; then
    url_key=WATCHDOG_XP_EXECUTOR_URL token_key=WATCHDOG_XP_EXECUTOR_TOKEN
  fi
  url="$(sed -n "s/^${url_key}=//p" "$xp_env_file" | head -n1)"
  token="$(sed -n "s/^${token_key}=//p" "$xp_env_file" | head -n1)"
  case "$url" in http://* | https://*) ;; *) log "ignoring $xp_env_file: $url_key missing" >&2; return 0 ;; esac
  if [ "${#token}" -lt 32 ]; then
    log "ignoring $xp_env_file: $token_key shorter than 32 characters" >&2
    return 0
  fi
  printf '%s\n' --env-file "$xp_env_file"
}

# Optional Alpaca (US equities, PAPER account only) executor. Opt-in by a root-owned, NON-secret
# file /etc/proto/alpaca.conf (docs/venues/alpaca-paper.md), on each instance:
#   ALPACA_SYMBOLS=SPY,QQQ,AAPL      ALPACA_EXECUTOR_PORT=8101      AGENT_US_ENABLED=false
# Its secrets are fetched as role <role>-alpaca, apart from this role's own: a missing one (the
# operator has not created proto-alpaca-paper-key yet) leaves Alpaca off with a log line and
# changes nothing else. Absent file = Alpaca off, exactly as before.
alpaca_conf="${PROTO_ALPACA_CONF:-/etc/proto/alpaca.conf}"
alpaca_value() {  # alpaca_value <key> <default>: only [A-Za-z0-9,._-] survives
  local value
  value="$(sed -n "s/^$1=//p" "$alpaca_conf" 2>/dev/null | head -n1 | tr -cd 'A-Za-z0-9,._-')"
  printf '%s' "${value:-$2}"
}
alpaca_port() {
  local port
  port="$(alpaca_value ALPACA_EXECUTOR_PORT 8101)"
  [[ "$port" =~ ^[0-9]{4,5}$ ]] && [ "$port" != 8100 ] && printf '%s' "$port" || printf 8101
}
alpaca_ready() {  # true once alpaca.conf exists and this role's Alpaca secrets are in tmpfs
  [ -s "$alpaca_conf" ] || return 1
  local vault
  vault="$(sed -n 's/^ExecStart=.*proto-fetch-secrets [a-z]* //p' \
    /etc/systemd/system/proto-secrets.service 2>/dev/null | head -n1)"
  if [ -z "$vault" ] || ! /usr/local/sbin/proto-fetch-secrets "${role}-alpaca" "$vault" >/dev/null 2>&1; then
    log "alpaca: secrets for ${role}-alpaca are not readable yet; Alpaca stays off" >&2
    return 1
  fi
}
alpaca_answers() {  # alpaca_answers <port>: its public /health answers from this host
  curl -fsS --max-time 5 "http://${EXECUTOR_IP}:$1/health" >/dev/null 2>&1
}
alpaca_args() {  # extra podman args for the API and the watchdog, one per line
  alpaca_ready || return 0
  local url="http://${EXECUTOR_IP}:$(alpaca_port)"
  printf '%s\n' --env-file "/run/proto/${role}-alpaca.env"
  case "$role" in
    api)
      printf '%s\n' -e "ALPACA_EXECUTOR_URL=${url}"
      [ "$(alpaca_value AGENT_US_ENABLED false)" = true ] && printf '%s\n' -e AGENT_US_ENABLED=true
      ;;
    watchdog) printf '%s\n' -e "WATCHDOG_ALPACA_EXECUTOR_URL=${url}" ;;
  esac
  return 0
}

# The Alpaca executor: a second container on the executor instance, same approved :executor
# image, its own sqlite ledger in a volume, its own port. Outside the main health/rollback: a
# failing Alpaca executor never takes the Binance executor down.
ensure_alpaca_executor() {  # ensure_alpaca_executor <image id>
  [ "$role" = executor ] || return 0
  local image="$1" name=proto-executor-alpaca port
  if [ ! -s "$alpaca_conf" ]; then
    podman rm -f "$name" >/dev/null 2>&1 || true
    return 0
  fi
  # Opened on every run, before any "already running" return, and permanently too: bootstrap.sh's
  # firewall-cmd --reload drops a live-only rule, and the running container then sat behind a closed
  # port, "unreachable" from the API and the watchdog (2026-10-29).
  port="$(alpaca_port)"
  firewall-cmd -q --permanent --add-port="${port}/tcp" >/dev/null 2>&1 || true
  firewall-cmd -q --add-port="${port}/tcp" >/dev/null 2>&1 || true
  # Fetch first: a key fixed in the Vault (new secret version) must reach the container. The
  # container keeps the env it was created with, so it is recreated when that env changes.
  if ! alpaca_ready; then
    # A failed fetch (Vault hiccup) keeps a healthy container on this image; only a container
    # that is not running, or runs an older image, is removed.
    if [ "$(podman container inspect --format '{{.Image}}' "$name" 2>/dev/null)" = "$image" ] \
      && [ "$(podman container inspect --format '{{.State.Running}}' "$name" 2>/dev/null)" = true ]; then
      return 0
    fi
    podman rm -f "$name" >/dev/null 2>&1 || true
    return 0
  fi
  local envsum
  envsum="$(cat /run/proto/executor-alpaca.env "$alpaca_conf" | sha256sum | cut -c1-16)"
  if [ "$(podman container inspect --format '{{.Image}}' "$name" 2>/dev/null)" = "$image" ] \
    && [ "$(podman container inspect --format '{{.State.Running}}' "$name" 2>/dev/null)" = true ] \
    && [ "$(podman container inspect --format '{{index .Config.Labels "proto.envsum"}}' "$name" 2>/dev/null)" = "$envsum" ] \
    && ! $force; then
    # Running is not enough: a container that no longer answers looked healthy to every deploy
    # while the API and the watchdog saw it "unreachable" (2026-10-29). It is recreated, with
    # its last lines in the journal (and in setup.sh) first.
    alpaca_answers "$port" && return 0
    log "alpaca executor runs but does not answer on port $port; its last lines:"
    masked_logs "$name" /run/proto/executor-alpaca.env
    log "recreating the alpaca executor"
  fi
  podman rm -f "$name" >/dev/null 2>&1 || true
  # on-failure:5, not always: a refused key (401) stops after five tries instead of hammering
  # Alpaca; the next deploy tick (every 5 min) recreates it and tries again.
  podman run -d --name "$name" --restart=on-failure:5 --network host --label "proto.envsum=$envsum" \
    --env-file /run/proto/executor-alpaca.env -v "${name}:/data:U" \
    -e EXECUTOR_VENUE=ALPACA_US -e EXECUTOR_ENVIRONMENT=TESTNET -e EXECUTOR_ACCOUNT=main \
    -e "EXECUTOR_SYMBOLS=$(alpaca_value ALPACA_SYMBOLS SPY,QQQ)" \
    -e EXECUTOR_DATABASE_URL=sqlite+aiosqlite:////data/executor-alpaca.db \
    "$image" uvicorn apps.executor.main:app --host "$EXECUTOR_IP" --port "$port" >/dev/null \
    || { log "alpaca executor did not start"; return 0; }
  log "alpaca executor started on port $port (paper account)"
  for _ in $(seq 30); do
    sleep 2
    alpaca_answers "$port" && { log "alpaca executor answers on port $port"; return 0; }
  done
  log "alpaca executor does not answer on port $port after 60 s; its last lines:"
  masked_logs "$name" /run/proto/executor-alpaca.env
}

start() {  # start <image id>
  local image="$1"
  podman rm -f "proto-${role}" >/dev/null 2>&1 || true
  case "$role" in
    api)
      mapfile -t xp_args < <(xp_env_args)
      mapfile -t alpaca_extra < <(alpaca_args)
      podman run -d --name proto-api --restart=always --network host --env-file "$secrets" \
        ${xp_args[@]+"${xp_args[@]}"} ${alpaca_extra[@]+"${alpaca_extra[@]}"} \
        -e APP_ENV=production -e OMS_EXECUTION_BACKEND=executor \
        -e "EXECUTOR_URL=http://${EXECUTOR_IP}:8100" \
        ${DOMAIN:+-e "PUBLIC_ORIGIN=https://${DOMAIN}"} \
        "$image" uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 >/dev/null ;;
    executor)
      podman run -d --name proto-executor --restart=always --network host --env-file "$secrets" \
        -e EXECUTOR_ENVIRONMENT=TESTNET -e EXECUTOR_ACCOUNT=main \
        "$image" uvicorn apps.executor.main:app --host "$EXECUTOR_IP" --port 8100 >/dev/null ;;
    watchdog)
      mapfile -t xp_args < <(xp_env_args)
      mapfile -t alpaca_extra < <(alpaca_args)
      podman run -d --name proto-watchdog --restart=always --network host --env-file "$secrets" \
        ${xp_args[@]+"${xp_args[@]}"} ${alpaca_extra[@]+"${alpaca_extra[@]}"} \
        -e WATCHDOG_ID=oci-watchdog-1 -e "WATCHDOG_API_URL=http://${API_IP}:8000" \
        -e "WATCHDOG_EXECUTOR_URL=http://${EXECUTOR_IP}:8100" \
        "$image" python -m apps.watchdog >/dev/null ;;
  esac
}

# A container's last lines, with every secret value in the given env files replaced: a failed start
# shows its cause in the journal (and in setup.sh) without printing a credential.
masked_logs() {  # masked_logs <container> <env file>...
  local name="$1"
  shift
  podman logs --tail 30 "$name" 2>&1 | python3 -c '
import os, sys
values = [l.split("=", 1)[1].strip() for f in sys.argv[1:] if os.path.exists(f)
          for l in open(f) if "=" in l]
for line in sys.stdin:
    for v in values:
        if len(v) >= 16:  # tokens, keys and passwords; not the database user or name
            line = line.replace(v, "***")
    sys.stdout.write("  | " + line)
' "$@" || true
}
show_logs() {
  masked_logs "proto-${role}" "$secrets" "/run/proto/${role}-db.env"
}

healthy() {
  local url
  case "$role" in
    api) url="http://127.0.0.1:8000/health" ;;
    executor) url="http://${EXECUTOR_IP}:8100/health" ;;
    watchdog) url="" ;;
  esac
  for _ in $(seq 45); do
    sleep 2
    [ "$(podman container inspect --format '{{.State.Running}}' "proto-${role}" 2>/dev/null)" = true ] \
      || continue
    if [ -z "$url" ]; then
      # No HTTP surface: alive for 20 s without a restart counts as healthy.
      sleep 20
      [ "$(podman container inspect --format '{{.RestartCount}}' "proto-${role}")" = 0 ] && return 0
      return 1
    fi
    curl -fsS --max-time 3 "$url" >/dev/null 2>&1 && return 0
  done
  return 1
}

# TLS for the dashboard: Caddy on 443 in front of the API, only once a domain is configured.
ensure_caddy() {
  [ "$role" = api ] && [ -n "${DOMAIN:-}" ] || return 0
  [ "$(podman container inspect --format '{{.State.Running}}' proto-caddy 2>/dev/null)" = true ] \
    && return 0
  podman rm -f proto-caddy >/dev/null 2>&1 || true
  podman run -d --name proto-caddy --restart=always --network host -v proto-caddy:/data \
    docker.io/library/caddy:2 caddy reverse-proxy --from "$DOMAIN" --to 127.0.0.1:8000 >/dev/null
}

case "$role" in
  api) ensure_db DATABASE_URL ;;
  executor) ensure_db EXECUTOR_DATABASE_URL ;;
esac

if [ "$new" = "$current" ] && [ "$running" = true ] && ! $force; then
  ensure_caddy
  ensure_alpaca_executor "$new"
  exit 0
fi

log "starting revision $(revision "$new")"
start "$new"
if healthy; then
  echo "$new" >"$state/good-image"
  log "healthy on revision $(revision "$new")"
  ensure_caddy
  ensure_alpaca_executor "$new"
  exit 0
fi

log "revision $(revision "$new") is not healthy; its last lines:"
show_logs
previous="$(cat "$state/good-image" 2>/dev/null || true)"
if [ -n "$previous" ] && [ "$previous" != "$new" ] && podman image exists "$previous"; then
  log "revision $(revision "$new") failed its health check; back to $(revision "$previous")"
  start "$previous"
  healthy || log "the previous revision is unhealthy too"
else
  log "revision $(revision "$new") failed its health check and there is no previous good image"
fi
exit 1
