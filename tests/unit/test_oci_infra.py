"""Oracle Cloud layout (infra/oci): the boundary holds in the Terraform, and names line up."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

OCI = Path("infra/oci")


def tf(name: str) -> str:
    return (OCI / name).read_text()


def block(text: str, kind: str, name: str) -> str:
    start = text.index(f'resource "{kind}" "{name}"')
    following = text.find('\nresource "', start + 1)
    return text[start : following if following != -1 else None]


def test_only_the_api_has_a_public_ip() -> None:
    compute = tf("compute.tf")
    assert "assign_public_ip = true" in block(compute, "oci_core_instance", "api")
    for name in ("executor", "watchdog", "mt5"):
        body = block(compute, "oci_core_instance", name)
        assert "assign_public_ip = false" in body, name
        assert "oci_core_subnet.private.id" in body, name
    private = block(tf("network.tf"), "oci_core_subnet", "private")
    assert "prohibit_public_ip_on_vnic = true" in private


def test_inbound_is_allow_listed_by_role() -> None:
    nsg = tf("nsg.tf")
    internet = re.findall(r'source\s+= "0\.0\.0\.0/0"', nsg)
    assert len(internet) == 1  # 443 to the API, nothing else from the internet
    assert "min = 443" in block(nsg, "oci_core_network_security_group_security_rule", "api_https")
    executor = block(nsg, "oci_core_network_security_group_security_rule", "executor_from")
    assert "NETWORK_SECURITY_GROUP" in executor and "watchdog" in executor
    bridge = block(nsg, "oci_core_network_security_group_security_rule", "mt5_bridge")
    assert "oci_core_network_security_group.executor.id" in bridge


def test_binance_egress_ip_is_reserved_and_kept() -> None:
    network = tf("network.tf")
    nat_ip = block(network, "oci_core_public_ip", "nat")
    assert 'lifetime       = "RESERVED"' in nat_ip and "prevent_destroy = true" in nat_ip
    assert "public_ip_id   = oci_core_public_ip.nat.id" in network


def test_no_secret_value_is_in_terraform_or_committed() -> None:
    for path in OCI.rglob("*"):
        if path.is_file() and ".terraform" not in path.parts:
            text = path.read_text(errors="ignore")
            assert not re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", text), path
            # A secret resource or a bundle read would put the value in the state. Listing the
            # secrets' metadata (data "oci_vault_secrets", ids only) does not.
            assert 'resource "oci_vault_secret"' not in text, path
            assert "oci_secrets_secretbundle" not in text, path
    ignored = (OCI / ".gitignore").read_text()
    assert "terraform.tfvars" in ignored and "*.tfstate" in ignored


def test_fetched_secrets_match_the_iam_policy_and_the_settings() -> None:
    from apps.api.app.settings import Settings
    from apps.executor.settings import ExecutorSettings
    from apps.watchdog.settings import WatchdogSettings

    vault = tf("vault.tf")
    script = (OCI / "scripts/fetch-secrets.sh").read_text()
    allowed = {
        role: set(re.findall(r'"(proto-[a-z0-9-]+)"', body))
        for role, body in re.findall(r"(\w+) = \[(.*?)\]", vault, re.S)
    }
    fields = {
        "api": {f.upper() for f in Settings.model_fields},
        "executor": {f"EXECUTOR_{f.upper()}" for f in ExecutorSettings.model_fields},
        "watchdog": {f"WATCHDOG_{f.upper()}" for f in WatchdogSettings.model_fields},
    }
    for role in ("api", "executor", "watchdog"):
        section = re.search(rf"\n  {role}\)\n(.*?)\" ;;", script, re.S)
        assert section, role
        pairs = re.findall(r"([A-Z0-9_]+)=(proto-[a-z0-9-]+)", section.group(1))
        assert pairs, role
        for var, secret in pairs:
            assert secret in allowed[role], f"{role} fetches {secret} but IAM does not allow it"
            if not var.startswith("PROTO_CRED_"):
                assert var in fields[role], f"{role}: {var} is not a setting"


def test_create_secrets_covers_every_name_and_never_puts_a_value_in_argv() -> None:
    vault = tf("vault.tf")
    names = set(re.findall(r'"(proto-[a-z0-9-]+)"', vault.split("secrets = {", 1)[1]))
    script = (OCI / "scripts/create-secrets.sh").read_text()
    for name in names:
        assert name in script, f"create-secrets.sh never creates {name}"
    # The value travels in a 0600 --from-json file, never as a command-line flag.
    assert "--from-json" in script and "--secret-content-content" not in script


def test_permit_pair_from_cloud_shell_matches_rfc8032_and_the_api() -> None:
    import base64
    import importlib.util

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from services.risk.permit_keys import decode_key

    spec = importlib.util.spec_from_file_location("ed25519_pair", OCI / "scripts/ed25519_pair.py")
    assert spec and spec.loader
    pair = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pair)

    seed = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    assert pair.public_key(seed).hex() == (
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
    )  # RFC 8032, 7.1, test 1
    for i in range(8):
        seed = bytes([i]) * 32
        public = pair.public_key(seed)
        assert public == Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes_raw()
        assert decode_key(base64.b64encode(public).decode()) == public


def test_deploy_workflow_publishes_images_and_never_reaches_oracle() -> None:
    import yaml

    workflow = Path(".github/workflows/deploy.yml").read_text()
    spec = yaml.safe_load(workflow)
    # The only secret it may use is the job's own GITHUB_TOKEN, for GHCR.
    assert set(re.findall(r"secrets\.(\w+)", workflow)) == {"GITHUB_TOKEN"}
    assert not re.search(r"\b(oci|ssh|bastion)\b", workflow.split("jobs:", 1)[1], re.I)
    triggers = spec.get("on", spec.get(True))
    assert triggers["workflow_run"] == {
        "workflows": ["CI"],
        "types": ["completed"],
        "branches": ["main"],
    }
    assert "conclusion == 'success'" in spec["jobs"]["image"]["if"]
    # The executor's tag moves only through the approval-gated environment.
    executor = spec["jobs"]["executor"]
    assert executor["environment"] == "executor" and executor["needs"] == "image"
    assert ":executor" not in yaml.safe_dump(spec["jobs"]["image"])


def test_instances_follow_their_tag_and_the_executor_stays_on_the_testnet() -> None:
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    assert re.search(r"executor\) tag=executor ;;", deploy)
    assert re.search(r"api \| watchdog\) tag=stable ;;", deploy)
    assert "-e EXECUTOR_ENVIRONMENT=TESTNET" in deploy
    assert "LIVE" not in deploy.replace("LIVE_", "")
    bootstrap = (OCI / "deploy/bootstrap.sh").read_text()
    assert "scripts/fetch-secrets.sh" in bootstrap
    for port in ("443 8000", "8100"):
        assert port in bootstrap


def test_the_bastion_can_reach_the_instances_and_nothing_enters_by_security_list() -> None:
    network = tf("network.tf")
    private = block(network, "oci_core_subnet", "private")
    assert "oci_core_security_list.bastion_egress.id" in private
    rules = block(network, "oci_core_security_list", "bastion_egress")
    assert "egress_security_rules" in rules and "ingress_security_rules" not in rules
    assert "ssh = 22, rdp = 3389" in rules and "destination      = var.vcn_cidr" in rules


def test_every_secret_policy_form_keeps_the_per_secret_restriction() -> None:
    iam = tf("iam.tf")
    forms = dict(re.findall(r'^\s+(\w+)\s+= "(Allow [^"]+)"', iam, re.M))
    assert set(forms) == {"dg_name", "dg_id", "principal_name", "principal_id"}
    for mode, statement in forms.items():
        # Oracle: GetSecretBundleByName needs SECRET_BUNDLE_READ only
        assert "read secret-bundles in compartment id {comp}" in statement, mode
        assert "target.vault.id" not in statement, mode  # vaults only: never matches a bundle
        assert "'{name}'" in statement or "'{id}'" in statement, mode  # never all secrets
        assert "any {" not in statement, mode  # the nested form returned 404 on every read
        if mode.startswith("principal"):
            assert "request.principal.id = '{instance}'" in statement, mode
    assert "for s in local.secrets[each.key]" in block(iam, "oci_identity_policy", "secrets")
    setup = (OCI / "deploy/setup.sh").read_text()
    for mode in forms:
        assert f'"{mode}"' in setup, f"setup.sh does not know {mode}"


def test_setup_never_reads_a_secret_value_and_never_applies_a_destroy() -> None:
    setup = (OCI / "deploy/setup.sh").read_text()
    assert '\\"content-type\\"' in setup and '\\"content\\"' not in setup
    assert "0 to destroy" in setup and "nothing applied" in setup
    assert "oci iam policy delete" in setup  # the temporary test policy never stays


def test_create_secrets_creates_what_an_empty_list_reports_as_missing(tmp_path: Path) -> None:
    """The OCI CLI prints nothing for an empty list; a count check read that as 'exists'."""
    import base64
    import json
    import os
    import subprocess

    store, fake = tmp_path / "store", tmp_path / "bin"
    store.mkdir()
    fake.mkdir()
    (fake / "oci").write_text(
        "#!/usr/bin/env python3\n"
        "import base64, json, os, sys\n"
        "store = os.environ['FAKE_STORE']; a = sys.argv[1:]\n"
        "if a[:3] == ['vault', 'secret', 'list']:\n"
        "    if os.path.exists(os.path.join(store, a[a.index('--name') + 1])):\n"
        "        print('ocid1.vaultsecret.oc1..x')\n"
        "    sys.exit(0)  # empty list: no output at all, like the real CLI\n"
        "req = json.load(open(a[a.index('--from-json') + 1][7:]))\n"
        "path = os.path.join(store, req['secretName'])\n"
        "open(path, 'w').write(base64.b64decode(req['secretContentContent']).decode())\n"
    )
    (fake / "oci").chmod(0o755)
    (tmp_path / "vars.json").write_text(json.dumps({"compartment_ocid": "ocid1.compartment.x"}))
    (tmp_path / "state.json").write_text(
        json.dumps({"outputs": {"vault_id": {"value": "v"}, "secrets_key_id": {"value": "k"}}})
    )
    env = {
        **os.environ,
        "PATH": f"{fake}:{os.environ['PATH']}",
        "FAKE_STORE": str(store),
        "PROTO_VARS": str(tmp_path / "vars.json"),
        "PROTO_TFSTATE": str(tmp_path / "state.json"),
    }
    script = str(OCI / "scripts/create-secrets.sh")
    run = subprocess.run(
        ["setsid", "bash", script], env=env, stdin=subprocess.DEVNULL, capture_output=True
    )
    assert run.returncode == 0, run.stderr
    created = {p.name for p in store.iterdir()}
    assert len(created) == 12 and "proto-permit-private-key" in created
    assert {"proto-alpaca-executor-token", "proto-watchdog-alpaca-token"} <= created
    assert not any(name.startswith("proto-binance") for name in created)  # typed by the operator
    assert not any(name.startswith("proto-alpaca-paper") for name in created)  # typed too
    private = base64.b64decode((store / "proto-permit-private-key").read_text())
    assert len(private) == 32
    again = subprocess.run(
        ["setsid", "bash", script], env=env, stdin=subprocess.DEVNULL, capture_output=True
    )
    assert again.returncode == 0 and b"created" not in again.stdout
    # setup.sh never prompts: the Binance halves are reported missing, not asked for.
    quiet = subprocess.run(
        ["bash", script], env={**env, "PROTO_NO_PROMPT": "1"}, capture_output=True
    )
    assert quiet.returncode == 0 and b"missing proto-binance-testnet-key" in quiet.stdout
    assert b"missing proto-alpaca-paper-key" in quiet.stdout


def test_the_deploy_unit_leaves_the_containers_running_and_failures_show_their_cause() -> None:
    bootstrap = (OCI / "deploy/bootstrap.sh").read_text()
    unit = bootstrap.split("proto-deploy.service <<'EOF'", 1)[1].split("EOF", 1)[0]
    # The default KillMode killed conmon (and the container) when the oneshot ended.
    assert "KillMode=process" in unit
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    failure = deploy.split("if healthy; then", 1)[1]
    assert "show_logs" in failure
    show = deploy.split("show_logs() {", 1)[1].split("\n}\n", 1)[0]
    assert '"$secrets"' in show and "-db.env" in show
    assert '"***"' in deploy.split("masked_logs() {", 1)[1].split("\n}\n", 1)[0]
    # setup.sh shows only the script's and systemd's lines, never the raw container output.
    setup = (OCI / "deploy/setup.sh").read_text()
    assert "journalctl -u proto-deploy -t proto-deploy -t systemd" in setup


def test_the_database_does_not_depend_on_nat_rules_a_firewall_reload_wipes() -> None:
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    db = deploy.split("ensure_db() {", 1)[1].split("\n}\n", 1)[0]
    assert "-p 127.0.0.1:5432" not in db  # podman's NAT rules vanish on firewall-cmd --reload
    assert "--network host" in db and "listen_addresses=127.0.0.1" in db
    assert "pg_isready -q -h 127.0.0.1 -p 5432" in db
    # An existing database on the old published port is recreated (its volume is kept).
    assert "{{.HostConfig.NetworkMode}}" in db and '-v "${name}:/var/lib/postgresql/data"' in db


def test_without_a_domain_the_dashboard_still_gets_https_on_the_api_public_ip() -> None:
    setup = (OCI / "deploy/setup.sh").read_text()
    assert 'API_PUBLIC="$(out api_public_ip)"' in setup
    assert 'DOMAIN="${API_PUBLIC//./-}.sslip.io"' in setup
    # Only a dotted IPv4 becomes a name; a missing output (None) leaves DOMAIN empty.
    assert "^[0-9]+(\\.[0-9]+){3}$" in setup
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    assert "len(v) >= 16" in deploy  # the database user ("executor") is not a secret


def test_a_pending_executor_approval_never_blocks_the_next_stable_image() -> None:
    import yaml

    spec = yaml.safe_load(Path(".github/workflows/deploy.yml").read_text())
    # A workflow-level lock held the next run behind an approval that was still waiting.
    assert "concurrency" not in spec
    assert spec["jobs"]["image"]["concurrency"] == {
        "group": "deploy-image",
        "cancel-in-progress": False,
    }
    executor = spec["jobs"]["executor"]
    assert executor["concurrency"] == {"group": "deploy-executor", "cancel-in-progress": True}
    assert executor["environment"] == "executor"


def test_alpaca_secrets_are_their_own_fetch_roles_and_opt_in() -> None:
    fetch = (OCI / "scripts/fetch-secrets.sh").read_text()
    roles = dict(re.findall(r"\n  ([a-z-]+)\)\n    pairs=\"(.*?)\" ;;", fetch, re.S))
    # The Binance executor, the API and the watchdog never wait for an Alpaca secret.
    for role in ("api", "executor", "watchdog"):
        assert "alpaca" not in roles[role], role
    assert (
        "PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_KEY=proto-alpaca-paper-key"
        in roles["executor-alpaca"]
    )
    assert (
        "PROTO_CRED_ALPACA_US_TESTNET_MAIN_API_SECRET=proto-alpaca-paper-secret"
        in roles["executor-alpaca"]
    )
    assert "paper" not in roles["api-alpaca"] and "paper" not in roles["watchdog-alpaca"]
    vault = tf("vault.tf")
    for role in ("api", "watchdog"):
        listed = vault.split(f"    {role} = [", 1)[1].split("]", 1)[0]
        assert "proto-alpaca-paper" not in listed, role  # the paper key: executor only
    executor = vault.split("    executor = [", 1)[1].split("]", 1)[0]
    assert '"proto-alpaca-paper-key"' in executor and '"proto-alpaca-paper-secret"' in executor
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    assert 'alpaca_conf="${PROTO_ALPACA_CONF:-/etc/proto/alpaca.conf}"' in deploy
    assert "EXECUTOR_VENUE=ALPACA_US -e EXECUTOR_ENVIRONMENT=TESTNET" in deploy
    assert "api.alpaca.markets" not in deploy
    nsg = block(
        tf("nsg.tf"), "oci_core_network_security_group_security_rule", "executor_alpaca_from"
    )
    assert "var.enable_alpaca ?" in nsg


def test_setup_turns_alpaca_on_only_when_asked_and_never_the_us_agents() -> None:
    setup = (OCI / "deploy/setup.sh").read_text()
    # Opt-in: without PROTO_ALPACA=1 nothing about Alpaca changes.
    assert 'ALPACA="${PROTO_ALPACA:-0}"' in setup
    assert (
        '[ "$ALPACA" = 1 ] && [ "$(var enable_alpaca)" != true ] && setvar enable_alpaca true'
        in setup
    )
    # Symbols are sanitized before they reach a remote shell.
    assert "tr -cd 'A-Z0-9,.'" in setup
    # It writes only the non-secret conf, with the US agents off, and never when the key is missing.
    assert "AGENT_US_ENABLED=false" in setup and "AGENT_US_ENABLED=true" not in setup
    assert "if $ALPACA_MISSING; then" in setup
    # Only the API and the watchdog are forced; the Binance executor is not restarted for it.
    assert (
        '[ "$role" = executor ] || DEPLOY_CMD[$role]="sudo /usr/local/sbin/proto-deploy --force"'
        in setup
    )
    assert "api.alpaca.markets" not in setup


def test_alpaca_container_follows_a_fixed_key_and_never_hammers_alpaca() -> None:
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    body = deploy.split("ensure_alpaca_executor() {", 1)[1].split("\n}\n", 1)[0]
    # The secrets are fetched before the "already running" shortcut, and a changed env recreates it.
    assert body.index("if ! alpaca_ready") < body.index('index .Config.Labels "proto.envsum"')
    assert 'index .Config.Labels "proto.envsum"' in body
    assert '--label "proto.envsum=$envsum"' in body
    # A refused key stops after a few tries instead of a tight restart loop.
    assert "--restart=on-failure:5" in body and "--restart=always" not in body


def test_a_failed_alpaca_secret_fetch_keeps_a_healthy_container() -> None:
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    body = deploy.split("ensure_alpaca_executor() {", 1)[1].split("\n}\n", 1)[0]
    failed = body.split("if ! alpaca_ready; then", 1)[1].split("\n  fi\n", 1)[0]
    # A Vault hiccup must not remove a running container on the current image.
    assert failed.index("{{.State.Running}}") < failed.index("podman rm -f")
    assert "return 0" in failed.split("podman rm -f", 1)[0]


def test_the_alpaca_port_survives_a_firewall_reload() -> None:
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    body = deploy.split("ensure_alpaca_executor() {", 1)[1].split("\n}\n", 1)[0]
    opened = body.index('firewall-cmd -q --permanent --add-port="${port}/tcp"')
    # Before the shortcuts that leave a running container alone: bootstrap reloads the firewall.
    assert opened < body.index("if ! alpaca_ready")
    assert opened < body.index('index .Config.Labels "proto.envsum"')
    assert 'firewall-cmd -q --add-port="${port}/tcp"' in body
    assert "firewall-cmd -q --reload" in (OCI / "deploy/bootstrap.sh").read_text()


_ALPACA_SIM = r"""
set -euo pipefail
src="$1"
fn() { sed -n "/^$1() {/,/^}/p" "$src"; }
for f in alpaca_value alpaca_port alpaca_answers masked_logs ensure_alpaca_executor; do
  eval "$(fn $f)"
done
log() { echo "LOG $*"; }
role=executor force=false EXECUTOR_IP=10.0.0.5
alpaca_conf="$2"
alpaca_ready() { return 0; }
sleep() { :; }
firewall-cmd() { :; }
cat() {
  if [ "$1" = /run/proto/executor-alpaca.env ]; then shift; printf 'K=v\n'; fi
  command cat "$@"
}
envsum="$(printf 'K=v\n' | command cat - "$alpaca_conf" | sha256sum | cut -c1-16)"
podman() {
  case "$1 $2" in
    "container inspect")
      case "$4" in *Image*) echo IMG ;; *Running*) echo true ;; *envsum*) echo "$envsum" ;; esac ;;
    "logs --tail") echo "last line" ;;
    "rm -f") echo RM >>"$CALLS" ;;
    "run -d") echo RUN >>"$CALLS" ;;
  esac
}
curl() { [ "$ANSWERS" = yes ]; }
ensure_alpaca_executor IMG
"""


def test_an_alpaca_executor_that_stops_answering_is_recreated_and_its_lines_logged(
    tmp_path: Path,
) -> None:
    conf = tmp_path / "alpaca.conf"
    conf.write_text("ALPACA_EXECUTOR_PORT=8101\n")
    script = tmp_path / "sim.sh"
    script.write_text(_ALPACA_SIM)
    deploy = str(OCI / "deploy/proto-deploy.sh")

    def sim(answers: str) -> tuple[str, str]:
        calls = tmp_path / f"calls-{answers}"
        calls.write_text("")
        done = subprocess.run(
            ["bash", str(script), deploy, str(conf)],
            capture_output=True,
            text=True,
            env={**os.environ, "ANSWERS": answers, "CALLS": str(calls)},
            check=True,
        )
        return done.stdout, calls.read_text()

    out, calls = sim("yes")
    assert calls == "" and "LOG" not in out
    out, calls = sim("no")
    assert "does not answer on port 8101; its last lines:" in out
    assert "  | last line" in out
    assert calls.split() == ["RM", "RUN"]
    assert "after 60 s" in out
    setup = (OCI / "deploy/setup.sh").read_text()
    assert 'note "alpaca executor (paper): $alpaca"' in setup


# ---------- a Binance pair that cannot sign never reaches the executor (2026-10-28) ----------

GOOD_KEY, GOOD_SECRET = "K" * 64, "s3cret" * 10 + "abcd"
ALPACA_SECRET = "A" * 44


def _fake_binance():
    """A local testnet stand-in: 200 for the good pair's HMAC, -1022 for a bad signature."""
    import hashlib
    import hmac
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            query, _, signature = self.path.split("?", 1)[1].rpartition("&signature=")
            expected = hmac.new(GOOD_SECRET.encode(), query.encode(), hashlib.sha256).hexdigest()
            if self.headers.get("X-MBX-APIKEY") != GOOD_KEY:
                status, body = 401, {"code": -2015, "msg": "Invalid API-key"}
            elif not hmac.compare_digest(signature, expected):
                status, body = 400, {"code": -1022, "msg": "Signature is not valid."}
            else:
                status, body = 200, {"balances": []}
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def _env_file(path: Path, key: str, secret: str) -> Path:
    path.write_text(
        f"EXECUTOR_API_TOKEN=t\nPROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_KEY={key}\n"
        f"PROTO_CRED_BINANCE_SPOT_TESTNET_MAIN_API_SECRET={secret}\n"
    )
    return path


def test_the_binance_pair_check_refuses_what_cannot_sign_and_never_prints_a_value(
    tmp_path: Path,
) -> None:
    import os
    import subprocess
    import sys

    server, url = _fake_binance()
    checker = str(OCI / "scripts/check-binance-pair.py")
    env = {
        **os.environ,
        "PROTO_BINANCE_TESTNET_URL": url,
        "PROTO_ALPACA_SECRET": ALPACA_SECRET,
        "NO_PROXY": "127.0.0.1",
        "no_proxy": "127.0.0.1",
    }

    def check(key: str, secret: str, **extra: str) -> subprocess.CompletedProcess:
        envfile = _env_file(tmp_path / "executor.env", key, secret)
        return subprocess.run(
            [sys.executable, checker, str(envfile)],
            env={**env, **extra},
            capture_output=True,
            text=True,
        )

    try:
        assert check(GOOD_KEY, GOOD_SECRET).returncode == 0
        mixed = check(GOOD_KEY, ALPACA_SECRET)  # the 2026-10-28 mix-up
        assert mixed.returncode == 3 and "Alpaca paper secret" in mixed.stderr
        wrong = check(GOOD_KEY, "x" * 64)  # 64 characters, but not the pair's secret
        assert wrong.returncode == 3 and "code -1022" in wrong.stderr
        assert check("k" * 64, GOOD_SECRET).returncode == 3  # unknown key: -2015
        assert check(GOOD_KEY, GOOD_SECRET, PROTO_ALPACA_SECRET=GOOD_SECRET).returncode == 3
        for run in (mixed, wrong):
            assert GOOD_KEY not in run.stderr and ALPACA_SECRET not in run.stderr
    finally:
        server.shutdown()
    # Binance unreachable: the shape is right, so the pair is not refused for it.
    down = check(GOOD_KEY, GOOD_SECRET, PROTO_BINANCE_TESTNET_URL="http://127.0.0.1:9")
    assert down.returncode == 0 and "not verified" in down.stderr


def test_fetch_secrets_keeps_the_previous_file_when_the_new_binance_pair_cannot_sign(
    tmp_path: Path,
) -> None:
    import base64
    import os
    import subprocess

    server, url = _fake_binance()
    vault, fake, run_dir = tmp_path / "vault", tmp_path / "bin", tmp_path / "run"
    vault.mkdir()
    fake.mkdir()
    (fake / "oci").write_text(
        "#!/usr/bin/env python3\n"
        "import base64, os, sys\n"
        "a = sys.argv[1:]; name = a[a.index('--secret-name') + 1]\n"
        "path = os.path.join(os.environ['FAKE_VAULT'], name)\n"
        "if not os.path.exists(path): sys.exit(1)\n"
        "print(base64.b64encode(open(path).read().encode()).decode())\n"
    )
    (fake / "oci").chmod(0o755)
    for name in (
        "proto-executor-token",
        "proto-watchdog-executor-token",
        "proto-permit-public-key",
        "proto-executor-database-url",
    ):
        (vault / name).write_text("value-of-" + name)
    (vault / "proto-binance-testnet-key").write_text(GOOD_KEY)
    (vault / "proto-binance-testnet-secret").write_text(GOOD_SECRET)
    (vault / "proto-alpaca-paper-secret").write_text(ALPACA_SECRET)
    env = {
        **os.environ,
        "PATH": f"{fake}:{os.environ['PATH']}",
        "FAKE_VAULT": str(vault),
        "PROTO_RUN_DIR": str(run_dir),
        "PROTO_BINANCE_TESTNET_URL": url,
        "PROTO_CHECK_BINANCE": str((OCI / "scripts/check-binance-pair.py").resolve()),
        "NO_PROXY": "127.0.0.1",
        "no_proxy": "127.0.0.1",
    }
    script = str(OCI / "scripts/fetch-secrets.sh")
    try:
        first = subprocess.run(["bash", script, "executor", "v"], env=env, capture_output=True)
        assert first.returncode == 0, first.stderr
        written = (run_dir / "executor.env").read_text()
        assert f"_API_SECRET={GOOD_SECRET}" in written
        # A new Vault version holding the Alpaca secret: refused, the working file stays.
        (vault / "proto-binance-testnet-secret").write_text(ALPACA_SECRET)
        second = subprocess.run(["bash", script, "executor", "v"], env=env, capture_output=True)
        assert second.returncode == 3
        assert b"Alpaca paper secret" in second.stderr and b"binance-versions.sh" in second.stderr
        assert (run_dir / "executor.env").read_text() == written
        assert not [p for p in run_dir.iterdir() if p.name.startswith(".")]  # no temp left behind
        assert base64.b64encode(ALPACA_SECRET.encode()) not in second.stderr
    finally:
        server.shutdown()


def test_setup_reports_a_refused_binance_pair_without_probing_iam_and_skips_the_executor() -> None:
    setup = (OCI / "deploy/setup.sh").read_text()
    assert "ExecMainStatus --value proto-secrets" in setup and "exit 3" in setup
    refused = setup[setup.index('elif [ "$rc" = 3 ]') :]
    refused = refused[: refused.index("else RESULT")]
    assert "binance-versions.sh" in refused and "continue" in refused and "all=false" not in refused
    bootstrap = (OCI / "deploy/bootstrap.sh").read_text()
    assert 'check-binance-pair.py" /usr/local/sbin/proto-check-binance-pair' in bootstrap


def test_the_chatgpt_session_lives_only_on_the_api_and_signs_in_from_cloud_shell() -> None:
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    api = deploy.split("start() {", 1)[1].split("    api)\n", 1)[1].split(";;", 1)[0]
    assert '-v "${chatgpt_dir}:/home/proto/.config/proto/chatgpt:Z"' in api
    assert "ensure_chatgpt_dir" in api
    for other in ("    executor)\n", "    watchdog)\n"):
        body = deploy.split("start() {", 1)[1].split(other, 1)[1].split(";;", 1)[0]
        assert "chatgpt" not in body
    assert "install -d -m 0700 -o 10001 -g 10001" in deploy
    # An API container from before the mount is restarted once to gain it.
    assert "&& chatgpt_mounted; then" in deploy
    bastion = (OCI / "deploy/bastion-run.sh").read_text()
    login = bastion.split("  chatgpt-login)\n", 1)[1].split(";;", 1)[0]
    assert "podman exec -it proto-api" in login and "--paste" in login
    assert '[ "$role" = api ]' in login


def test_the_mcp_token_is_its_own_optional_fetch_role() -> None:
    from apps.api.app.settings import Settings

    script = (OCI / "scripts/fetch-secrets.sh").read_text()
    roles = dict(re.findall(r"\n  ([a-z-]+)\)\n(.*?)\" ;;", script, re.S))
    assert roles["api-mcp"].strip() == 'pairs="MCP_API_TOKEN=proto-mcp-api-token'
    assert "proto-mcp" not in roles["api"]  # a missing token must never stop the API
    api_allowed = re.search(r"api = \[(.*?)\]", tf("vault.tf"), re.S).group(1)
    assert '"proto-mcp-api-token"' in api_allowed
    assert "mcp_api_token" in Settings.model_fields
    for role in ("executor", "watchdog", "mt5"):
        listed = re.search(rf"{role} = \[(.*?)\]", tf("vault.tf"), re.S).group(1)
        assert "proto-mcp" not in listed, role
    image = Path("Dockerfile").read_text()
    assert "FINROBOTS_ENABLED=true" in image and "MCP_ENABLED=true" in image


_MCP_SIM = r"""
set -euo pipefail
src="$1"
fn() { sed -n "/^$1() {/,/^}/p" "$src"; }
eval "$(fn mcp_token_acceptable)"
eval "$(fn mcp_args)"
log() { echo "LOG $*"; }
# Every python3 call's arguments are recorded: no secret may appear in them.
python3() { printf '%s\n' "$@" >>"$ARGV_LOG"; command python3 "$@"; }
role="$2" secrets="$3" mcp_env="$4"
mcp_fetch() { [ -s "$mcp_env" ]; }
mcp_args
"""


def test_the_api_gets_the_mcp_token_only_when_the_api_would_accept_it(tmp_path: Path) -> None:
    script = tmp_path / "sim.sh"
    script.write_text(_MCP_SIM)
    secrets = tmp_path / "api.env"
    operator = "o" * 40
    secrets.write_text(f"OPERATOR_API_TOKEN={operator}\nEXECUTOR_API_TOKEN={'e' * 40}\n")
    env_file = tmp_path / "api-mcp.env"
    argv_log = tmp_path / "argv.log"
    argv_log.write_text("")

    def sim(token: str | None, role: str = "api") -> str:
        if token is None:
            env_file.unlink(missing_ok=True)
        else:
            env_file.write_text(f"MCP_API_TOKEN={token}\n")
        done = subprocess.run(
            [
                "bash",
                str(script),
                str(OCI / "deploy/proto-deploy.sh"),
                role,
                str(secrets),
                str(env_file),
            ],
            capture_output=True,
            text=True,
            check=True,
            env={**os.environ, "ARGV_LOG": str(argv_log)},
        )
        return done.stdout + done.stderr

    good = "m" * 40
    assert sim(good).splitlines() == ["--env-file", str(env_file)]
    # The API strips tokens (Settings.normalize_optional_secret): padding must not let through a
    # token that is short, or equal to another, once stripped; the API would refuse to start.
    padded_short = "short-token" + " " * 40
    padded_operator = f"  {operator}  "
    for bad in (None, "short-token", operator, padded_short, padded_operator):
        out = sim(bad)
        assert "--env-file" not in out and "LOG mcp:" in out, repr(bad)
        assert good not in out and operator not in out  # never echoes a value
    assert sim(good, role="executor") == ""
    logged = argv_log.read_text()
    assert logged  # the check ran through python3
    assert good not in logged and operator not in logged and "short-token" not in logged
    deploy = (OCI / "deploy/proto-deploy.sh").read_text()
    body = deploy.split("mcp_token_acceptable() {", 1)[1].split("\nmcp_args() {", 1)[0]
    body += deploy.split("\nmcp_args() {", 1)[1].split("\n}\n", 1)[0]
    assert "grep" not in body and "$token" not in body  # no secret in any argv


def test_create_secrets_never_generates_the_mcp_token() -> None:
    script = (OCI / "scripts/create-secrets.sh").read_text()
    generated = script.split("for name in proto-operator-token", 1)[1].split("done", 1)[0]
    assert "proto-mcp" not in generated
    typed = script.split("for name in proto-binance-testnet-key", 1)[1].split("done", 1)[0]
    assert "proto-mcp-api-token" in typed and '"${#value}" -lt 32' in typed
    # Stripped before the length check, as the API strips it.
    assert typed.index("[[:space:]]") < typed.index('"${#value}" -lt 32')
