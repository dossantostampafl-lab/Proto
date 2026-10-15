#!/usr/bin/env bash
# One-time setup of a PROTO instance (Oracle Linux 9, as root). Idempotent: run it again to
# update the units or the configuration. bastion-run.sh copies this directory over the Bastion.
#
#   bootstrap.sh <api|executor|watchdog> <vault-ocid> <executor-ip> <api-ip> [domain]
#
# Installs podman, the OCI CLI (Vault reads as the instance principal), the firewall rules of
# the role, the secret-fetch unit and the pull-based deploy timer. No secret is written here.
set -euo pipefail

role="${1:?role}" vault="${2:?vault ocid}" executor_ip="${3:?executor ip}" api_ip="${4:?api ip}"
domain="${5:-}"
here="$(cd "$(dirname "$0")" && pwd)"
case "$role" in
  api) ports="443 8000" ;;
  executor) ports="8100" ;;
  watchdog) ports="" ;;
  *) echo "unknown role: $role" >&2; exit 2 ;;
esac

dnf -y -q install oraclelinux-developer-release-el9
dnf -y -q install podman firewalld python39-oci-cli

systemctl enable --now firewalld
for port in $ports; do firewall-cmd -q --permanent --add-port="${port}/tcp"; done
firewall-cmd -q --reload

install -d -m 0755 /etc/proto /var/lib/proto
install -m 0750 "$here/../scripts/fetch-secrets.sh" /usr/local/sbin/proto-fetch-secrets
install -m 0750 "$here/proto-deploy.sh" /usr/local/sbin/proto-deploy
echo "$role" >/etc/proto/role
cat >/etc/proto/deploy.env <<EOF
IMAGE=ghcr.io/dossantostampafl-lab/proto
EXECUTOR_IP=${executor_ip}
API_IP=${api_ip}
DOMAIN=${domain}
EOF

cat >/etc/systemd/system/proto-secrets.service <<EOF
[Unit]
Description=PROTO ${role}: secrets from OCI Vault into /run/proto (tmpfs)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/sbin/proto-fetch-secrets ${role} ${vault}

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/proto-deploy.service <<'EOF'
[Unit]
Description=PROTO: run the published image for this role (pull-based deploy)
Wants=network-online.target
After=network-online.target proto-secrets.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/proto-deploy
EOF

cat >/etc/systemd/system/proto-deploy.timer <<'EOF'
[Unit]
Description=PROTO: check for a new image every 5 minutes

[Timer]
OnBootSec=1min
OnUnitInactiveSec=5min
RandomizedDelaySec=30s

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable proto-secrets.service podman-restart.service proto-deploy.timer
if systemctl restart proto-secrets.service; then
  echo "secrets: ok"
else
  echo "secrets: not readable yet (create them in the Vault, then: systemctl restart proto-secrets)"
fi
systemctl start proto-deploy.timer
echo "bootstrap done: role=${role}; the first deploy runs within 5 minutes (journalctl -u proto-deploy)"
