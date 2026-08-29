import pytest

from services.security.live_data_policy import (
    LiveDataPolicy,
    LiveDataPolicyViolation,
    validate_no_private_credentials,
)


def test_policy_accepts_only_exact_allowlisted_tls_endpoints() -> None:
    policy = LiveDataPolicy(
        frozenset({"api.market.example", "stream.market.example"}),
        allowed_ports=frozenset({443, 9443}),
    )

    assert policy.validate_endpoint("https://api.market.example/v1/book").hostname == (
        "api.market.example"
    )
    assert policy.validate_endpoint("wss://stream.market.example/ws").scheme == "wss"
    assert policy.validate_endpoint("wss://stream.market.example:9443/ws").port == 9443

    with pytest.raises(LiveDataPolicyViolation, match="not allowlisted"):
        policy.validate_endpoint("https://api.market.example.attacker.invalid/v1/book")


@pytest.mark.parametrize(
    "endpoint, message",
    [
        ("http://api.market.example/book", "https or wss"),
        ("https://user:pass@api.market.example/book", "credentials"),
        ("https://api.market.example:8443/book", "port is not allowlisted"),
        ("https://api.market.example/book?api_key=secret", "query strings"),
    ],
)
def test_policy_rejects_unsafe_endpoint_configuration(
    endpoint: str, message: str
) -> None:
    policy = LiveDataPolicy(frozenset({"api.market.example"}))

    with pytest.raises(LiveDataPolicyViolation, match=message):
        policy.validate_endpoint(endpoint)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_policy_rejects_outbound_write_methods(method: str) -> None:
    policy = LiveDataPolicy(frozenset({"api.market.example"}))

    with pytest.raises(LiveDataPolicyViolation, match="not read-only"):
        policy.validate_http_method(method)


def test_private_trading_credentials_fail_closed_without_matching_ci_secrets() -> None:
    validate_no_private_credentials(
        {"DATABASE_PASSWORD": "local-only", "GITHUB_TOKEN": "ci-token"}
    )

    with pytest.raises(LiveDataPolicyViolation, match="EXCHANGE_API_SECRET"):
        validate_no_private_credentials(
            {"EXCHANGE_API_SECRET": "must-not-be-configured", "BROKER_API_KEY": ""}
        )
