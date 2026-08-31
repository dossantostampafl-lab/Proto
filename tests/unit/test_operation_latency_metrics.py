from apps.api.app.observability import OperationLatencyTimer, RuntimeMetrics


def test_named_operation_latency_is_recorded_and_reset() -> None:
    metrics = RuntimeMetrics()

    metrics.observe_operation_latency_ms("calibration", 4.0)
    metrics.observe_operation_latency_ms("calibration", 6.0)

    snapshot = metrics.snapshot()
    assert snapshot["operation_latency"]["calibration"] == {
        "average_ms": 5.0,
        "samples": 2,
    }

    metrics.reset()
    assert metrics.snapshot()["operation_latency"] == {}


def test_operation_latency_timer_records_sample() -> None:
    metrics = RuntimeMetrics()

    with OperationLatencyTimer(metrics, "calibration"):
        pass

    latency = metrics.snapshot()["operation_latency"]["calibration"]
    assert latency["samples"] == 1
    assert latency["average_ms"] >= 0.0


def test_operation_latency_requires_non_empty_name() -> None:
    metrics = RuntimeMetrics()

    try:
        metrics.observe_operation_latency_ms("   ", 1.0)
    except ValueError as error:
        assert str(error) == "operation name must not be empty"
    else:
        raise AssertionError("expected ValueError")
