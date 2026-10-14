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
