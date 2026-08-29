from sqlalchemy import inspect

from apps.api.app.models import Asset, Fill, Side, SimulationOrder
from apps.api.app.persistence import AsyncSqlFillJournal, build_engine, init_database
from apps.api.app.schema_registry import CANONICAL_TABLE_NAMES


async def test_async_fill_journal_persists_and_deduplicates_order() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    journal = AsyncSqlFillJournal(engine)

    order = SimulationOrder(
        market_id="btc-usd-paper",
        asset=Asset.BTC,
        side=Side.BUY,
        quantity=0.01,
        limit_price=61_000,
    )
    fill = Fill(
        order_id=order.id,
        market_id=order.market_id,
        asset=order.asset,
        side=order.side,
        filled_quantity=0.01,
        fill_price=60_020,
        fee=0.12,
        slippage_bps=3.1,
    )

    await journal.append(order, fill)
    await journal.append(order, fill)
    records = await journal.list(limit=10)

    assert len(records) == 1
    assert records[0]["order_id"] == str(order.id)
    assert records[0]["market_id"] == "btc-usd-paper"
    assert records[0]["asset"] == "BTC"
    assert records[0]["side"] == "BUY"

    await engine.dispose()


async def test_init_database_creates_canonical_research_tables() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)

    async with engine.connect() as connection:
        table_names = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )

    assert set(CANONICAL_TABLE_NAMES).issubset(table_names)
    assert "simulation_fills" in table_names

    await engine.dispose()
