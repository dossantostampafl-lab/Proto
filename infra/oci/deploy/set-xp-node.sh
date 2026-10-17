#!/usr/bin/env bash
# Point this API instance at the XP (B3) node: the operator's PC, reached over Tailscale.
# Run as root on the API instance (OCI Bastion SSH session), once, and again if the PC's address
# or the node's token changes:
#
#   sudo set-xp-node.sh <tailscale-ip-of-the-pc> [port]      # token is read from stdin, hidden
#   sudo set-xp-node.sh --remove                              # XP routing off again
#
# It writes /etc/proto/xp.env (root only) with XP_EXECUTOR_URL and XP_EXECUTOR_API_TOKEN and
# recreates the API container so the file is read. proto-deploy.sh ignores a malformed file, so a
# typo here can never stop the API from starting. Nothing is written to the Vault or to Terraform.
set -euo pipefail

etc="${PROTO_ETC:-/etc/proto}"
file="${etc}/xp.env"

if [ "${1:-}" = "--remove" ]; then
  rm -f "$file"
  echo "XP routing removed: the API starts without it from now on."
else
  ip="${1:?usage: set-xp-node.sh <tailscale-ip> [port]  |  --remove}"
  port="${2:-8101}"
  # A Tailscale address (100.64.0.0/10) or a private one. Never a public address: the node's token
  # would cross the open internet.
  tailscale='^100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.[0-9]{1,3}\.[0-9]{1,3}$'
  private='^(10\.[0-9]{1,3}|192\.168|172\.(1[6-9]|2[0-9]|3[01]))\.[0-9]{1,3}\.[0-9]{1,3}$'
  if ! [[ "$ip" =~ $tailscale ]] && ! [[ "$ip" =~ $private ]]; then
    echo "refusing $ip: not a Tailscale (100.64.0.0/10) or private address" >&2
    exit 2
  fi
  [[ "$port" =~ ^[0-9]{2,5}$ ]] || { echo "bad port" >&2; exit 2; }
  read -rsp "XP node token (hidden): " token || true
  echo
  if [ "${#token}" -lt 32 ] || [[ "$token" =~ [[:space:]] ]]; then
    echo "refusing: the token must be 32+ characters without spaces" >&2
    exit 2
  fi
  umask 077
  mkdir -p "$etc"
  tmp="$(mktemp "${etc}/.xp.env.XXXXXX")"
  printf 'XP_EXECUTOR_URL=http://%s:%s\nXP_EXECUTOR_API_TOKEN=%s\n' "$ip" "$port" "$token" >"$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$file"
  echo "wrote $file for http://$ip:$port"
fi

if [ -z "${PROTO_SKIP_RESTART:-}" ]; then
  proto-deploy --force
fi
