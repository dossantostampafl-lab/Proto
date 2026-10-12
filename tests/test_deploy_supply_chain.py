from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"


def test_application_image_drops_root_before_runtime() -> None:
    content = DOCKERFILE.read_text(encoding="utf-8")

    assert "useradd --create-home --uid 10001 proto" in content
    assert "USER proto" in content
    assert content.index("USER proto") < content.index("CMD [")
