import asyncio
from playwright.async_api import async_playwright


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto("http://127.0.0.1:8000", wait_until="domcontentloaded")
        await page.get_by_role("button", name="Agents", exact=True).click()
        await page.locator("#agent-selection option").nth(1).wait_for(state="attached")
        value = await page.locator("#agent-selection option").nth(1).get_attribute("value")
        await page.locator("#agent-selection").select_option(value)
        await page.get_by_role("button", name="New explanation conversation").click()
        await page.get_by_text("Unavailable: Local ChatGPT conversation is disabled").wait_for()
        await page.screenshot(path="/tmp/proto-agents-desktop.png", full_page=True)
        await page.set_viewport_size({"width": 390, "height": 844})
        await page.screenshot(path="/tmp/proto-agents-mobile.png", full_page=True)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors, errors
        print(
            "PASS: real dashboard/API, actual roster selection, disabled auth, desktop/mobile, no JS errors. No OAuth or inference performed."
        )
        await browser.close()


asyncio.run(main())
