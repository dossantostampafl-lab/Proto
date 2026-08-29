from proto_api.models import Asset, Fill, Side, SimulationOrder
from proto_api.persistence import SqlFillJournal, build_engine, init_database


def test_sql_fill_journal_persists_and_deduplicates_order() -> None:
    engine = build_engine("sqlite+pysqlite:///:memory:")
    init_database(engine)
    journal = SqlFillJournal(engine)

    order = SimulationOrder(
        market_id="btc-usd-paper",
        asset=Asset.BTC,
        side=Side.BUY,
        quantity=0.01,
        limit_price=61_000,
    )
    fill = Fill(
        order_id=order.id,
        filled_quantity=0.01,
        fill_price=60_020,
        fee=0.12,
        slippage_bps=3.1,
    )

    journal.append(order, fill)
    journal.append(order, fill)
    records = journal.list(limit=10)

    assert len(records) == 1
    assert records[0]["order_id"] == str(order.id)
    assert records[0]["market_id"] == "btc-usd-paper"
    assert records[0]["asset"] == "BTC"
    assert records[0]["side"] == "BUY"
    assert records[0]["filled_quantity"] == 0.01
