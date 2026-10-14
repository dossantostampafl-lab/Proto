"""Read-only connectivity check for Binance Spot credentials (no orders).

    python -m services.venues.binance_smoke --environment TESTNET --account main

It checks:
- clock alignment;
- the order grids from `exchangeInfo`;
- that signed requests work, with a balance read and the query of a
  non-existent order;
- for LIVE only, that the key is trade-only and IP-restricted.

Orders are placed only by the OMS behind a Risk Permit, never by this tool.
"""

from __future__ import annotations

import argparse
import asyncio
import json

from services.execution import VenueId

from .binance import BinanceSpotAdapter, load_instruments
from .credentials import VenueEnvironment, load_credentials

DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BTCBRL", "ETHBRL", "SOLBRL")


async def run(environment: VenueEnvironment, account: str, symbols: list[str]) -> dict:
    credentials = load_credentials(VenueId.BINANCE_SPOT, environment, account)
    instruments = await load_instruments(symbols, environment=environment)
    adapter = BinanceSpotAdapter(credentials, instruments=instruments)
    try:
        balances = await adapter.balances()
        probe_id = next(iter(instruments), None)
        missing = (
            await adapter.query("PXsmoke-not-an-order", instrument_id=probe_id)
            if probe_id
            else None
        )
        result = {
            "environment": environment.value,
            "credentials": str(credentials),
            "clock_offset_ms": adapter._offset_ms,
            "instruments": {
                k: {"tick": str(v.tick_size), "lot": str(v.lot_size), "min": str(v.min_notional)}
                for k, v in instruments.items()
            },
            "assets_with_balance": sorted(balances),
            "signed_query_ok": missing is None,
            "real_money_execution": adapter.real_money_execution,
        }
        if environment is VenueEnvironment.LIVE:
            result["key_restrictions"] = await adapter.verify_trade_only()
        return result
    finally:
        await adapter.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--environment", default="TESTNET", choices=[e.value for e in VenueEnvironment]
    )
    parser.add_argument("--account", default="main")
    parser.add_argument("--symbols", nargs="*", default=list(DEFAULT_SYMBOLS))
    args = parser.parse_args()
    report = asyncio.run(run(VenueEnvironment(args.environment), args.account, args.symbols))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
