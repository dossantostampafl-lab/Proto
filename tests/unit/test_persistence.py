from sqlalchemy import inspect

from apps.api.app.models import Asset, Fill, Side, SimulationOrder
from apps.api.app.persistence import AsyncSqlFillJournal, build_engine, init_database
from apps.api.app.schema_registry import CANONICAL_TABLE_NAMES


async def _paper_order_and_fill(market_id: str = "btc-usd-paper"):
    order = SimulationOrder(
        market_id=market_id,
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
    return order, fill


async def test_async_fill_journal_persists_and_deduplicates_order() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    journal = AsyncSqlFillJournal(engine)

    order, fill = await _paper_order_and_fill()

    assert await journal.append(order, fill) is True
    assert await journal.append(order, fill) is False
    records = await journal.list(limit=10)

    assert len(records) == 1
    assert records[0]["order_id"] == str(order.id)
    assert records[0]["session_id"]
    assert records[0]["market_id"] == "btc-usd-paper"
    assert records[0]["asset"] == "BTC"
    assert records[0]["side"] == "BUY"

    await engine.dispose()


async def test_new_simulation_session_hides_prior_session_from_active_recovery() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    journal = AsyncSqlFillJournal(engine)

    first_order, first_fill = await _paper_order_and_fill("first-session")
    assert await journal.append(first_order, first_fill) is True
    first_records = await journal.list(limit=10)
    assert len(first_records) == 1
    first_session = first_records[0]["session_id"]

    second_session = await journal.start_new_session()
    assert second_session != first_session
    assert await journal.list(limit=10) == []

    second_order, second_fill = await _paper_order_and_fill("second-session")
    assert await journal.append(second_order, second_fill) is True
    second_records = await journal.list(limit=10)
    assert len(second_records) == 1
    assert second_records[0]["session_id"] == second_session
    assert second_records[0]["market_id"] == "second-session"

    await engine.dispose()


async def test_same_order_id_can_be_replayed_in_a_new_simulation_session() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)
    journal = AsyncSqlFillJournal(engine)

    order, fill = await _paper_order_and_fill("deterministic-replay")
    assert await journal.append(order, fill) is True
    first_session = (await journal.list(limit=10))[0]["session_id"]

    second_session = await journal.start_new_session()
    assert second_session != first_session
    assert await journal.append(order, fill) is True
    assert await journal.append(order, fill) is False

    records = await journal.list(limit=10)
    assert len(records) == 1
    assert records[0]["session_id"] == second_session
    assert records[0]["order_id"] == str(order.id)

    await engine.dispose()


async def test_init_database_creates_canonical_research_tables() -> None:
    engine = build_engine("sqlite+aiosqlite:///:memory:")
    await init_database(engine)

    async with engine.connect() as connection:
        table_names = await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )
        unique_constraints = await connection.run_sync(
            lambda sync_connection: inspect(sync_connection).get_unique_constraints(
                "simulation_fills"
            )
        )

    assert set(CANONICAL_TABLE_NAMES).issubset(table_names)
    assert "simulation_fills" in table_names
    assert "simulation_sessions" in table_names
    assert any(
        constraint["name"] == "uq_simulation_fills_session_order"
        and constraint["column_names"] == ["session_id", "order_id"]
        for constraint in unique_constraints
    )

    await engine.dispose()
