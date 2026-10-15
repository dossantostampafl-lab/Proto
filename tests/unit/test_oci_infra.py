"""Oracle Cloud layout (infra/oci): the boundary holds in the Terraform, and names line up."""

from __future__ import annotations

import re
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
    assert len(created) == 10 and "proto-permit-private-key" in created
    assert not any(name.startswith("proto-binance") for name in created)  # typed by the operator
    private = base64.b64decode((store / "proto-permit-private-key").read_text())
    assert len(private) == 32
    again = subprocess.run(
        ["setsid", "bash", script], env=env, stdin=subprocess.DEVNULL, capture_output=True
    )
    assert again.returncode == 0 and b"created" not in again.stdout
