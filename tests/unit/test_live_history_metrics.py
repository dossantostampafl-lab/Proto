from apps.api.app.live_history_metrics import LiveHistoryReadMetrics


def test_live_history_read_metrics_separate_client_rejections_from_backend_failures() -> None:
    metrics = LiveHistoryReadMetrics()

    metrics.record_request()
    metrics.record_cursor_rejection()
    metrics.record_request()
    metrics.record_backend_failure()
    metrics.record_request()
    metrics.record_success(rows_returned=3, has_more=True)

    snapshot = metrics.snapshot()
    assert snapshot["requests_total"] == 3
    assert snapshot["successes_total"] == 1
    assert snapshot["rows_returned_total"] == 3
    assert snapshot["pages_with_more_total"] == 1
    assert snapshot["cursor_rejections_total"] == 1
    assert snapshot["backend_failures_total"] == 1
    assert snapshot["disabled_total"] == 0
    assert snapshot["last_success_at"] is not None
    assert snapshot["financial_connectivity"] is False
    assert snapshot["real_money_execution"] is False


def test_live_history_read_metrics_reset_and_validate_row_count() -> None:
    metrics = LiveHistoryReadMetrics()
    metrics.record_request()
    metrics.record_disabled()
    metrics.record_success(rows_returned=0, has_more=False)

    metrics.reset()
    snapshot = metrics.snapshot()
    assert snapshot["requests_total"] == 0
    assert snapshot["successes_total"] == 0
    assert snapshot["rows_returned_total"] == 0
    assert snapshot["pages_with_more_total"] == 0
    assert snapshot["cursor_rejections_total"] == 0
    assert snapshot["backend_failures_total"] == 0
    assert snapshot["disabled_total"] == 0
    assert snapshot["last_success_at"] is None

    try:
        metrics.record_success(rows_returned=-1, has_more=False)
    except ValueError as error:
        assert "non-negative" in str(error)
    else:
        raise AssertionError("negative row counts must be rejected")
