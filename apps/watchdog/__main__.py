"""Run the watchdog: `python -m apps.watchdog`.

Deploy it on its own instance (or at least its own process and service account), so a failure of
the API or the executor does not take the watchdog down with it.
"""

from __future__ import annotations

import asyncio
import logging
import signal

import httpx

from services.watchdog import Limits, Watchdog

from .settings import WatchdogSettings


async def main() -> None:
    settings = WatchdogSettings()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with httpx.AsyncClient(base_url=settings.api_url) as api:
        executor = (
            httpx.AsyncClient(base_url=settings.executor_url) if settings.executor_url else None
        )
        xp = (
            httpx.AsyncClient(base_url=settings.xp_executor_url)
            if settings.xp_executor_url
            else None
        )
        alpaca = (
            httpx.AsyncClient(base_url=settings.alpaca_executor_url)
            if settings.alpaca_executor_url
            else None
        )
        try:
            watchdog = Watchdog(
                watchdog_id=settings.id,
                api=api,
                api_token=settings.api_token.get_secret_value(),
                executor=executor,
                executor_token=settings.executor_token.get_secret_value()
                if settings.executor_token
                else None,
                xp_executor=xp,
                xp_executor_token=settings.xp_executor_token.get_secret_value()
                if settings.xp_executor_token
                else None,
                alpaca_executor=alpaca,
                alpaca_executor_token=settings.alpaca_executor_token.get_secret_value()
                if settings.alpaca_executor_token
                else None,
                limits=Limits(
                    daily_loss_fraction=settings.daily_loss_fraction,
                    drawdown_fraction=settings.drawdown_fraction,
                    unreachable_cycles=settings.unreachable_cycles,
                ),
            )
            await watchdog.run(settings.interval_seconds, stop)
        finally:
            for client in (executor, xp, alpaca):
                if client is not None:
                    await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
