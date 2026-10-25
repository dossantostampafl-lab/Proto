"""Browser checks against real PROTO HTTP/SQLite with explicitly SIMULATED OAuth/inference.

Run with the development Playwright extra installed; no real account is contacted.
The fixture runner lives outside the production image and uses a temporary database.
"""

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

SCRIPT_PATH = str(Path(__file__).resolve())


def serve(port: int, simulated: bool):
    import uvicorn

    with tempfile.TemporaryDirectory(prefix="proto-browser-") as directory:
        os.environ.update(
            APP_ENV="development",
            DATABASE_URL=f"sqlite+aiosqlite:///{directory}/fixture.db",
            CONTROL_PERSISTENCE_ENABLED="true",
            AGENT_ROSTER_ENABLED="true",
            LIVE_MONITORING_AUTOSTART="false",
            CHATGPT_LOCAL_ENABLED=str(simulated).lower(),
            OPERATOR_API_TOKEN="fixture-operator",
        )
        from apps.api.app import conversation_surface as surface
        from apps.api.app.main import app

        if simulated:
            surface.ChatGPTCredentialStore._read = lambda _: SimpleNamespace(
                subject="fixture-user", client_id="fixture-client"
            )

            async def models(profile):
                return ["fixture-model"]

            async def generate(self, request):
                return SimpleNamespace(
                    content="SIMULATED ChatGPT explanation of recorded evidence.",
                    model="fixture-model",
                )

            surface.models = models
            surface.ChatGPTPlanProvider.generate = generate
            surface.sign_out = lambda profile: False
        uvicorn.run(app, host="127.0.0.1", port=port, proxy_headers=False, log_level="warning")


async def scenario(simulated: bool, port: int):
    import httpx
    from playwright.async_api import async_playwright, expect

    with tempfile.TemporaryFile() as logs:
        args = [sys.executable, SCRIPT_PATH, "--serve", str(port)]
        if simulated:
            args.append("--simulated")
        server = await asyncio.create_subprocess_exec(*args, stdout=logs, stderr=logs)
        try:
            base = f"http://127.0.0.1:{port}"
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(120):
                    try:
                        if (await client.get(base + "/health")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if server.returncode is not None:
                        raise RuntimeError("Browser fixture server failed")
                    await asyncio.sleep(0.25)
                else:
                    raise RuntimeError("Browser fixture server did not become ready")
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(base, wait_until="domcontentloaded")
                await page.evaluate(
                    "sessionStorage.setItem('proto.operatorToken', 'fixture-operator')"
                )
                await page.get_by_role("button", name="Agents", exact=True).click()
                await page.locator(".agent-pick").first.click()
                section = page.get_by_role("region", name="ChatGPT explanation")
                await section.get_by_role("button", name="Verify OpenAI connection").click()
                if not simulated:
                    await expect(section.get_by_role("status")).to_contain_text(
                        "Local ChatGPT conversation is disabled"
                    )
                    await expect(
                        section.get_by_role("button", name="New explanation conversation")
                    ).to_be_disabled()
                else:
                    await expect(section.get_by_role("status")).to_contain_text(
                        "OpenAI account access verified"
                    )
                    await section.get_by_role("button", name="New explanation conversation").click()
                    await section.get_by_label("Question", exact=True).fill(
                        "Explain the available evidence"
                    )
                    await section.get_by_role("button", name="Ask about evidence").click()
                    await expect(section.get_by_role("status")).to_have_text(
                        "ChatGPT explanation saved"
                    )
                    await expect(
                        section.get_by_text("SIMULATED ChatGPT explanation of recorded evidence.")
                    ).to_be_visible()
                    identifier = await section.locator("p code").last.inner_text()
                    await section.get_by_label("Resume conversation ID").fill(identifier)
                    await section.get_by_role("button", name="Load history").click()
                    await expect(section.get_by_role("status")).to_have_text("History restored")
                    # A persisted conversation cannot be shown under a different agent identity.
                    await page.locator(".agent-pick").nth(1).click()
                    await section.get_by_label("Resume conversation ID").fill(identifier)
                    await section.get_by_role("button", name="Load history").click()
                    await expect(section.get_by_role("status")).to_contain_text(
                        "Select that agent first"
                    )
                    await expect(section.get_by_label("Question", exact=True)).to_have_count(0)
                    await section.get_by_role("button", name="Verify OpenAI connection").click()
                    await expect(section.get_by_role("status")).to_contain_text(
                        "OpenAI account access verified"
                    )
                    await section.get_by_role("button", name="Disconnect ChatGPT").click()
                    await expect(section.get_by_role("status")).to_contain_text(
                        "Remote revocation unconfirmed"
                    )
                await section.get_by_role("button", name="Connect ChatGPT · setup").click()
                label = "simulated" if simulated else "disabled"
                await page.screenshot(
                    path=f"/tmp/proto-chatgpt-{label}-desktop.png",
                    full_page=True,
                )
                await page.set_viewport_size({"width": 390, "height": 844})
                await page.screenshot(
                    path=f"/tmp/proto-chatgpt-{label}-mobile.png",
                    full_page=True,
                )
                assert await page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
                assert not errors, errors
                await browser.close()
        finally:
            server.terminate()
            await asyncio.wait_for(server.wait(), timeout=15)
    label = "SIMULATED account and inference" if simulated else "disabled authentication"
    print(f"PASS: real dashboard/HTTP/SQLite, {label}, desktop/mobile. No real OAuth.")


async def main():
    await scenario(False, 18111)
    await scenario(True, 18112)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int)
    parser.add_argument("--simulated", action="store_true")
    args = parser.parse_args()
    if args.serve:
        serve(args.serve, args.simulated)
    else:
        asyncio.run(main())
