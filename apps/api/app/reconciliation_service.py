from __future__ import annotations

from services.events.reconciliation import reconcile

from .app_state import persistent_journal, portfolio, runtime
from .metrics_state import metrics


def positions_from_fills(entries: list[dict[str, object]]) -> dict[str, float]:
    positions: dict[str, float] = {}
    for entry in entries:
        asset = str(entry["asset"])
        quantity = float(entry["filled_quantity"])
        signed_quantity = quantity if str(entry["side"]) == "BUY" else -quantity
        positions[asset] = positions.get(asset, 0.0) + signed_quantity
    return positions


async def reconciliation_status() -> dict[str, object]:
    memory_entries = portfolio.journal(1_000)
    authoritative_entries = (
        await persistent_journal.list(1_000)
        if persistent_journal is not None
        else memory_entries
    )
    actual_positions = {
        str(position["asset"]): float(position["quantity"])
        for position in portfolio.snapshot()["positions"]
    }
    result = reconcile(
        order_ids={str(entry["order_id"]) for entry in memory_entries},
        fill_order_ids={str(entry["order_id"]) for entry in authoritative_entries},
        expected_positions=positions_from_fills(authoritative_entries),
        actual_positions=actual_positions,
        journal_event_count=len(memory_entries),
        persisted_event_count=len(authoritative_entries),
    )
    metrics.increment("reconciliation_checks")
    if not result.consistent:
        metrics.increment("reconciliation_failures")
    return {
        "mode": runtime.mode,
        "consistent": result.consistent,
        "issues": [issue.value for issue in result.issues],
        "journal_fill_count": len(memory_entries),
        "authoritative_fill_count": len(authoritative_entries),
    }


__all__ = ["positions_from_fills", "reconciliation_status"]
