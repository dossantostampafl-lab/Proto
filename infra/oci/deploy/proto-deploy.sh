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

start() {  # start <image id>
  local image="$1"
  podman rm -f "proto-${role}" >/dev/null 2>&1 || true
  case "$role" in
    api)
      podman run -d --name proto-api --restart=always --network host --env-file "$secrets" \
        -e APP_ENV=production -e OMS_EXECUTION_BACKEND=executor \
        -e "EXECUTOR_URL=http://${EXECUTOR_IP}:8100" \
        ${DOMAIN:+-e "PUBLIC_ORIGIN=https://${DOMAIN}"} \
        "$image" uvicorn apps.api.app.main:app --host 0.0.0.0 --port 8000 >/dev/null ;;
    executor)
      podman run -d --name proto-executor --restart=always --network host --env-file "$secrets" \
        -e EXECUTOR_ENVIRONMENT=TESTNET -e EXECUTOR_ACCOUNT=main \
        "$image" uvicorn apps.executor.main:app --host "$EXECUTOR_IP" --port 8100 >/dev/null ;;
    watchdog)
      podman run -d --name proto-watchdog --restart=always --network host --env-file "$secrets" \
        -e WATCHDOG_ID=oci-watchdog-1 -e "WATCHDOG_API_URL=http://${API_IP}:8000" \
        -e "WATCHDOG_EXECUTOR_URL=http://${EXECUTOR_IP}:8100" \
        "$image" python -m apps.watchdog >/dev/null ;;
  esac
}

# The container's last lines, with every secret value of this role replaced: a failed start shows
# its cause in the journal (and in setup.sh) without printing a credential.
show_logs() {
  podman logs --tail 30 "proto-${role}" 2>&1 | python3 -c '
import os, sys
values = [l.split("=", 1)[1].strip() for f in sys.argv[1:] if os.path.exists(f)
          for l in open(f) if "=" in l]
for line in sys.stdin:
    for v in values:
        if len(v) >= 8:
            line = line.replace(v, "***")
    sys.stdout.write("  | " + line)
' "$secrets" "/run/proto/${role}-db.env" || true
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
  exit 0
fi

log "starting revision $(revision "$new")"
start "$new"
if healthy; then
  echo "$new" >"$state/good-image"
  log "healthy on revision $(revision "$new")"
  ensure_caddy
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
