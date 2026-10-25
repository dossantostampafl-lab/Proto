"""Browser regression of the API-backed PROTO Control Room, desktop and mobile.

Requires the built dashboard and development Playwright browser dependencies.
Does not seed synthetic positions or replace the API with fabricated trading data.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

SCRIPT_PATH = str(Path(__file__).resolve())


def serve(port: int) -> None:
    import uvicorn

    with tempfile.TemporaryDirectory(prefix="proto-floor-control-") as directory:
        os.environ.update(
            APP_ENV="development",
            DATABASE_URL=f"sqlite+aiosqlite:///{directory}/fixture.db",
            CONTROL_PERSISTENCE_ENABLED="true",
            AGENT_ROSTER_ENABLED="true",
            LIVE_MONITORING_AUTOSTART="false",
            OPERATOR_API_TOKEN="fixture-operator",
        )
        from apps.api.app.main import app
        uvicorn.run(app, host="127.0.0.1", port=port, proxy_headers=False, log_level="warning")


async def exercise(port: int, output: Path) -> None:
    import httpx
    from playwright.async_api import async_playwright, expect

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile() as log:
        server = await asyncio.create_subprocess_exec(
            sys.executable, SCRIPT_PATH, "--serve", str(port), stdout=log, stderr=log
        )
        try:
            url = f"http://127.0.0.1:{port}"
            async with httpx.AsyncClient(trust_env=False) as client:
                for _ in range(120):
                    try:
                        if (await client.get(url + "/health")).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if server.returncode is not None:
                        raise RuntimeError("Control Room fixture server exited")
                    await asyncio.sleep(0.25)
                else:
                    raise RuntimeError("Control Room fixture server did not become ready")

            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=True, args=["--no-sandbox"]
                )
                for width, height, name in (
                    (1440, 900, "desktop"),
                    (390, 844, "mobile"),
                ):
                    page = await browser.new_page(viewport={"width": width, "height": height})
                    errors: list[str] = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    await page.add_init_script("""
                        const original = HTMLCanvasElement.prototype.getContext;
                        HTMLCanvasElement.prototype.getContext = function(type, ...args) {
                          if (String(type).startsWith('webgl')) return null;
                          return original.call(this, type, ...args);
                        };
                    """)
                    await page.goto(url, wait_until="domcontentloaded")
                    await page.get_by_role("button", name="Controle", exact=True).click()
                    room = page.locator(".fc-scene")
                    await expect(room).to_be_visible(timeout=15000)
                    await expect(room.locator(".fc-room")).to_be_visible(timeout=15000)
                    await expect(room.locator(".fc-roster")).to_be_visible()
                    await expect(room.locator(".fc-queue")).to_be_visible()
                    # Agents displayed on the room are inspectable without granting
                    # order authority or replacing their real roster state.
                    await room.locator(".fc-roster-select").first.click()
                    await expect(room.locator(".fc-agent-detail")).to_be_visible()
                    await room.get_by_role("button", name="Fechar detalhes do agente").click()
                    await expect(room.locator(".fc-agent-detail")).to_have_count(0)
                    await expect(room.locator(".fc-head li").nth(1)).to_contain_text(
                        "SUMMARY", timeout=15000
                    )
                    assert await room.locator(".fc-room").evaluate(
                        "(el) => el.complete && el.naturalWidth > 0"
                    ), "rendered room image must load"
                    assert await page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    ), "unwanted horizontal scroll"
                    if width == 390:
                        await expect(room.locator(".fc-bot > i").first).to_be_visible(timeout=15000)
                        diameter = await room.locator(".fc-globe").evaluate(
                            "(el) => el.getBoundingClientRect().width"
                        )
                        assert diameter > 120, f"Mobile hologram is too small: {diameter:.1f}px"
                    await page.screenshot(
                        path=str(output / f"control-{name}.png"),
                        full_page=True,
                    )
                    assert not errors, errors
                    await page.close()
                await browser.close()
        finally:
            server.terminate()
            await asyncio.wait_for(server.wait(), timeout=15)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", type=int, default=0)
    parser.add_argument("--port", type=int, default=18121)
    parser.add_argument("--output", type=Path, default=Path("/tmp/proto-floor-screenshots"))
    arguments = parser.parse_args()
    if arguments.serve:
        serve(arguments.serve)
    else:
        asyncio.run(exercise(arguments.port, arguments.output))
        print("PASS: prerendered Control Room without WebGL, desktop and mobile")
