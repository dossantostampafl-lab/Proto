from __future__ import annotations

from collections.abc import Mapping
from math import isfinite


def _metric_bool(value: object) -> int:
    return int(value is True)


def _prometheus_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _append_optional_gauge(
    lines: list[str],
    *,
    metric: str,
    value: object,
    labels: str = "",
) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    if isinstance(value, float) and not isfinite(value):
        return
    lines.append(f"{metric}{labels} {value}")


def render_live_prometheus(status: Mapping[str, object]) -> str:
    feed_health = status.get("feed_health")
    health: Mapping[str, object] = feed_health if isinstance(feed_health, Mapping) else {}
    persistence_raw = status.get("persistence")
    persistence: Mapping[str, object] = (
        persistence_raw if isinstance(persistence_raw, Mapping) else {}
    )
    journal_raw = persistence.get("journal")
    journal: Mapping[str, object] = journal_raw if isinstance(journal_raw, Mapping) else {}
    lines = [
        "# HELP proto_live_running Whether the read-only live monitor task is running.",
        "# TYPE proto_live_running gauge",
        f"proto_live_running {_metric_bool(status.get('running'))}",
        "# HELP proto_live_connected Whether the public market-data source is connected.",
        "# TYPE proto_live_connected gauge",
        f"proto_live_connected {_metric_bool(health.get('connected'))}",
        "# HELP proto_live_message_fresh Whether the public source has delivered a recent message.",
        "# TYPE proto_live_message_fresh gauge",
        f"proto_live_message_fresh {_metric_bool(health.get('message_fresh'))}",
        "# HELP proto_live_all_symbols_fresh Whether BTC ETH and SOL source timestamps are fresh.",
        "# TYPE proto_live_all_symbols_fresh gauge",
        f"proto_live_all_symbols_fresh {_metric_bool(status.get('all_symbols_fresh'))}",
        (
            "# HELP proto_live_all_symbols_receipts_fresh "
            "Whether BTC ETH and SOL were recently received by Proto."
        ),
        "# TYPE proto_live_all_symbols_receipts_fresh gauge",
        "proto_live_all_symbols_receipts_fresh "
        f"{_metric_bool(status.get('all_symbols_receipts_fresh'))}",
        (
            "# HELP proto_live_all_symbols_current_connection "
            "Whether all symbols are from the current socket generation."
        ),
        "# TYPE proto_live_all_symbols_current_connection gauge",
        "proto_live_all_symbols_current_connection "
        f"{_metric_bool(status.get('all_symbols_current_connection'))}",
        (
            "# HELP proto_live_sequence_rejections_current_connection "
            "Rejected duplicate or regressing sequences in the current connection generation."
        ),
        "# TYPE proto_live_sequence_rejections_current_connection gauge",
        "# HELP proto_live_persistence_required Whether durable storage is required before fanout.",
        "# TYPE proto_live_persistence_required gauge",
        f"proto_live_persistence_required {_metric_bool(persistence.get('required'))}",
        "# HELP proto_live_persistence_configured Whether a durable live journal is configured.",
        "# TYPE proto_live_persistence_configured gauge",
        f"proto_live_persistence_configured {_metric_bool(persistence.get('configured'))}",
        "# HELP proto_live_persistence_healthy Whether durable writes are currently healthy.",
        "# TYPE proto_live_persistence_healthy gauge",
        f"proto_live_persistence_healthy {_metric_bool(persistence.get('healthy'))}",
        "# HELP proto_live_persistence_read_healthy Whether persisted history reads are healthy.",
        "# TYPE proto_live_persistence_read_healthy gauge",
        f"proto_live_persistence_read_healthy {_metric_bool(persistence.get('read_healthy'))}",
        (
            "# HELP proto_live_financial_connectivity "
            "Financial account connectivity capability; invariant zero."
        ),
        "# TYPE proto_live_financial_connectivity gauge",
        "proto_live_financial_connectivity 0",
        "# HELP proto_live_real_money_execution Real-money execution capability; invariant zero.",
        "# TYPE proto_live_real_money_execution gauge",
        "proto_live_real_money_execution 0",
    ]
    _append_optional_gauge(
        lines,
        metric="proto_live_sequence_rejections_current_connection",
        value=status.get("sequence_rejections_current_connection"),
    )
    for metric, key in (
        ("proto_live_connection_generation", "connection_generation"),
        ("proto_live_connection_attempts_total", "connection_attempts"),
        ("proto_live_reconnects_total", "reconnect_count"),
        ("proto_live_frames_received_total", "frames_received"),
        ("proto_live_ticks_emitted_total", "ticks_emitted"),
        ("proto_live_parse_errors_total", "parse_error_count"),
        ("proto_live_message_timeouts_total", "message_timeout_count"),
        ("proto_live_consecutive_parse_errors", "consecutive_parse_errors"),
        ("proto_live_last_message_age_seconds", "last_message_age_seconds"),
        ("proto_live_last_tick_age_seconds", "last_tick_age_seconds"),
        ("proto_live_last_receipt_age_seconds", "last_receipt_age_seconds"),
    ):
        source = status if key == "last_receipt_age_seconds" else health
        _append_optional_gauge(lines, metric=metric, value=source.get(key))

    for metric, key in (
        ("proto_live_persisted_current_connection", "persisted_current_connection"),
        (
            "proto_live_persistence_idempotent_hits_current_connection",
            "idempotent_hits_current_connection",
        ),
        (
            "proto_live_persistence_write_failures_current_connection",
            "write_failures_current_connection",
        ),
        ("proto_live_persistence_read_failures", "read_failures"),
    ):
        _append_optional_gauge(lines, metric=metric, value=persistence.get(key))

    for metric, key in (
        ("proto_live_journal_writes_attempted", "writes_attempted"),
        ("proto_live_journal_writes_inserted", "writes_inserted"),
        ("proto_live_journal_idempotent_hits", "idempotent_hits"),
        ("proto_live_journal_write_failures", "write_failures"),
        ("proto_live_journal_read_failures", "read_failures"),
        ("proto_live_journal_maintenance_failures", "maintenance_failures"),
        ("proto_live_journal_pruned_rows", "pruned_rows"),
        ("proto_live_journal_retention_seconds", "retention_seconds"),
    ):
        _append_optional_gauge(lines, metric=metric, value=journal.get(key))

    symbol_health = status.get("symbol_health")
    expected_symbols = status.get("expected_symbols")
    last_sequences = status.get("last_sequence_by_symbol")
    sequence_map: Mapping[str, object] = (
        last_sequences if isinstance(last_sequences, Mapping) else {}
    )
    sequence_rejections = status.get("sequence_rejections_by_symbol")
    rejection_map: Mapping[str, object] = (
        sequence_rejections if isinstance(sequence_rejections, Mapping) else {}
    )
    if isinstance(symbol_health, Mapping) and isinstance(expected_symbols, list):
        for symbol in expected_symbols:
            if not isinstance(symbol, str):
                continue
            item = symbol_health.get(symbol)
            if not isinstance(item, Mapping):
                continue
            labels = f'{{symbol="{_prometheus_label(symbol)}"}}'
            lines.append(
                f"proto_live_symbol_fresh{labels} {_metric_bool(item.get('fresh'))}"
            )
            lines.append(
                "proto_live_symbol_receipt_fresh"
                f"{labels} {_metric_bool(item.get('receipt_fresh'))}"
            )
            lines.append(
                "proto_live_symbol_current_connection"
                f"{labels} {_metric_bool(item.get('current_connection'))}"
            )
            _append_optional_gauge(
                lines,
                metric="proto_live_symbol_last_sequence",
                value=sequence_map.get(symbol),
                labels=labels,
            )
            symbol_rejections = rejection_map.get(symbol)
            if isinstance(symbol_rejections, Mapping):
                _append_optional_gauge(
                    lines,
                    metric="proto_live_symbol_sequence_duplicate_rejections_current_connection",
                    value=symbol_rejections.get("duplicate"),
                    labels=labels,
                )
                _append_optional_gauge(
                    lines,
                    metric="proto_live_symbol_sequence_regression_rejections_current_connection",
                    value=symbol_rejections.get("regression"),
                    labels=labels,
                )
            _append_optional_gauge(
                lines,
                metric="proto_live_symbol_source_age_seconds",
                value=item.get("age_seconds"),
                labels=labels,
            )
            _append_optional_gauge(
                lines,
                metric="proto_live_symbol_receipt_age_seconds",
                value=item.get("receipt_age_seconds"),
                labels=labels,
            )
    return "\n".join(lines) + "\n"
