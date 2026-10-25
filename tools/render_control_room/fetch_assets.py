"""Download the CC0 models and HDRI the Control Room render uses from Poly Haven.

    python tools/render_control_room/fetch_assets.py

Everything lands in tools/render_control_room/assets/ (git-ignored); render.py uses each asset
when it is there and falls back to its own primitives when it is not. All files are CC0
(https://polyhaven.com/license); the list below is the record of what the render is built from.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
API = "https://api.polyhaven.com/files/"
MODELS = ["potted_plant_01", "modern_arm_chair_01", "sofa_02"]
HDRI = "blue_photo_studio"


def get(url: str, timeout: int) -> bytes:
    # Poly Haven refuses urllib's default user agent; say who is asking.
    request = urllib.request.Request(url, headers={"User-Agent": "proto-control-room-renderer/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (fixed https hosts)
        return response.read()


def fetch(url: str, dst: Path) -> None:
    if dst.exists() and dst.stat().st_size > 0:
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(get(url, 120))
    print("fetched", dst.relative_to(ASSETS))


def files(asset: str) -> dict:
    return json.loads(get(API + asset, 60))


def main() -> None:
    for asset in MODELS:
        blend = files(asset)["blend"]["1k"]["blend"]
        folder = ASSETS / asset
        fetch(blend["url"], folder / Path(blend["url"]).name)
        for rel, item in blend.get("include", {}).items():
            fetch(item["url"], folder / rel)
    hdri = files(HDRI)["hdri"]["1k"]["hdr"]
    fetch(hdri["url"], ASSETS / f"{HDRI}_1k.hdr")


if __name__ == "__main__":
    main()
