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
            assert "oci_vault_secret" not in text, path  # values would land in the state
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
