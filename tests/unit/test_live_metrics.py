from apps.api.app.live_metrics import render_live_prometheus


def _status() -> dict[str, object]:
    return {
        "running": True,
        "all_symbols_fresh": True,
        "all_symbols_receipts_fresh": True,
        "all_symbols_current_connection": True,
        "last_receipt_age_seconds": 0.25,
        "last_sequence_by_symbol": {"BTC": 42},
        "sequence_rejections_current_connection": 3,
        "sequence_rejections_by_symbol": {
            "BTC": {"duplicate": 2, "regression": 1, "total": 3}
        },
        "persistence": {
            "configured": True,
            "required": True,
            "healthy": True,
            "write_healthy": True,
            "read_healthy": True,
            "persisted_current_connection": 12,
            "idempotent_hits_current_connection": 1,
            "write_failures_current_connection": 0,
            "read_failures": 0,
            "journal": {
                "writes_attempted": 13,
                "writes_inserted": 12,
                "idempotent_hits": 1,
                "write_failures": 0,
                "read_failures": 0,
                "maintenance_failures": 0,
                "pruned_rows": 4,
                "retention_seconds": 86400,
            },
        },
        "expected_symbols": ["BTC"],
        "feed_health": {
            "connected": True,
            "message_fresh": True,
            "connection_generation": 3,
            "connection_attempts": 4,
            "reconnect_count": 1,
            "frames_received": 10,
            "ticks_emitted": 8,
            "parse_error_count": 0,
            "message_timeout_count": 0,
            "consecutive_parse_errors": 0,
            "last_message_age_seconds": 0.1,
            "last_tick_age_seconds": 0.2,
        },
        "symbol_health": {
            "BTC": {
                "fresh": True,
                "receipt_fresh": True,
                "current_connection": True,
                "age_seconds": 0.3,
                "receipt_age_seconds": 0.2,
            }
        },
    }


def test_live_prometheus_renders_read_only_invariants_and_finite_values() -> None:
    body = render_live_prometheus(_status())

    assert "proto_live_financial_connectivity 0" in body
    assert "proto_live_real_money_execution 0" in body
    assert "proto_live_connection_generation 3" in body
    assert "proto_live_all_symbols_receipts_fresh 1" in body
    assert "proto_live_sequence_rejections_current_connection 3" in body
    assert "proto_live_persistence_required 1" in body
    assert "proto_live_persistence_configured 1" in body
    assert "proto_live_persistence_healthy 1" in body
    assert "proto_live_persisted_current_connection 12" in body
    assert "proto_live_journal_writes_inserted 12" in body
    assert "proto_live_journal_pruned_rows 4" in body
    assert 'proto_live_symbol_receipt_fresh{symbol="BTC"} 1' in body
    assert 'proto_live_symbol_last_sequence{symbol="BTC"} 42' in body
    assert (
        'proto_live_symbol_sequence_duplicate_rejections_current_connection{symbol="BTC"} 2'
        in body
    )
    assert (
        'proto_live_symbol_sequence_regression_rejections_current_connection{symbol="BTC"} 1'
        in body
    )
    assert 'proto_live_symbol_receipt_age_seconds{symbol="BTC"} 0.2' in body


def test_live_prometheus_marks_stale_receipts_without_financial_capabilities() -> None:
    status = _status()
    status["all_symbols_receipts_fresh"] = False
    symbol_health = status["symbol_health"]
    assert isinstance(symbol_health, dict)
    btc = symbol_health["BTC"]
    assert isinstance(btc, dict)
    btc["receipt_fresh"] = False

    body = render_live_prometheus(status)

    assert "proto_live_all_symbols_receipts_fresh 0" in body
    assert 'proto_live_symbol_receipt_fresh{symbol="BTC"} 0' in body
    assert "proto_live_financial_connectivity 0" in body
    assert "proto_live_real_money_execution 0" in body


def test_live_prometheus_omits_non_finite_numeric_telemetry() -> None:
    status = _status()
    status["last_receipt_age_seconds"] = float("nan")
    status["sequence_rejections_current_connection"] = float("nan")
    health = status["feed_health"]
    assert isinstance(health, dict)
    health["last_message_age_seconds"] = float("inf")
    persistence = status["persistence"]
    assert isinstance(persistence, dict)
    journal = persistence["journal"]
    assert isinstance(journal, dict)
    journal["pruned_rows"] = float("inf")
    symbol_health = status["symbol_health"]
    assert isinstance(symbol_health, dict)
    btc = symbol_health["BTC"]
    assert isinstance(btc, dict)
    btc["age_seconds"] = float("-inf")

    body = render_live_prometheus(status)

    assert "proto_live_last_receipt_age_seconds" not in body
    assert "proto_live_last_message_age_seconds" not in body
    assert "proto_live_symbol_source_age_seconds" not in body
    assert "proto_live_journal_pruned_rows" not in body
    assert "proto_live_sequence_rejections_current_connection nan" not in body.lower()
    assert " nan" not in body.lower()
    assert " inf" not in body.lower()
