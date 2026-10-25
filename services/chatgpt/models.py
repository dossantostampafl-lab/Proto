"""List the selected PROTO account's visible models without exposing credentials."""

from __future__ import annotations

import argparse
import asyncio

import httpx

from .chatgpt_connect import _profile_credentials_path
from .chatgpt_credentials import ChatGPTCredentialStore
from .chatgpt_provider import listed_model_slugs


async def models(profile: str) -> list[str]:
    credentials = ChatGPTCredentialStore(str(_profile_credentials_path(profile)))
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://api.openai.com/v1/models",
            headers={"Authorization": f"Bearer {await credentials.access_token()}"},
        )
    if response.status_code != 200:
        raise RuntimeError(f"ChatGPT model catalog unavailable (HTTP {response.status_code})")
    return listed_model_slugs(response.json())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="default")
    args = parser.parse_args()
    for model in asyncio.run(models(args.profile)):
        print(model)


if __name__ == "__main__":
    main()
