from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "railway-deploy-fallback.yml"
DOCKERFILE = ROOT / "Dockerfile"


def test_railway_fallback_uses_checksum_verified_immutable_cli_release() -> None:
    content = WORKFLOW.read_text(encoding="utf-8")

    assert "ghcr.io/railwayapp/cli:latest" not in content
    assert "RAILWAY_CLI_VERSION: 5.30.1" in content
    assert (
        "RAILWAY_CLI_AMD64_SHA256: "
        "295d9657801a234bb197ba15ab3ea2ef76bb073d30f1703ca080b0f46ad20449"
        in content
    )
    assert "sha256sum --check --strict" in content
    assert "persist-credentials: false" in content


def test_railway_application_image_drops_root_before_runtime() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "useradd --create-home --uid 10001 proto" in content
    assert "USER proto" in content
    assert content.index("USER proto") < content.index("CMD [")
