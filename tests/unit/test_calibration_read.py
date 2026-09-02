from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import create_async_engine

from apps.api.app.calibration_read import latest_calibration_metric
from apps.api.app.schema_registry import CANONICAL_TABLES, canonical_metadata


@pytest.mark.asyncio
async def test_latest_calibration_metric_returns_none_without_persistence() -> None:
    assert await latest_calibration_metric(None) is None


@pytest.mark.asyncio
async def test_latest_calibration_metric_reads_newest_matching_model() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    table = CANONICAL_TABLES["calibration_metrics"]
    base_time = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

    try:
        async with engine.begin() as connection:
            await connection.run_sync(canonical_metadata.create_all)
            await connection.execute(
                insert(table),
                [
                    {
                        "id": "baseline-old",
                        "created_at": base_time,
                        "correlation_id": "baseline-old",
                        "payload": {
                            "model_version": "baseline-logit-v0",
                            "feature_version": "microstructure-v0",
                            "observed_at": base_time.isoformat(),
                            "count": 20,
                            "brier_score": 0.21,
                            "log_loss": 0.61,
                            "expected_calibration_error": 0.08,
                            "maximum_calibration_error": 0.13,
                            "bins": [],
                        },
                    },
                    {
                        "id": "other-newer",
                        "created_at": base_time + timedelta(minutes=2),
                        "correlation_id": "other-newer",
                        "payload": {
                            "model_version": "candidate-v1",
                            "count": 99,
                            "brier_score": 0.01,
                            "bins": [],
                        },
                    },
                    {
                        "id": "baseline-new",
                        "created_at": base_time + timedelta(minutes=1),
                        "correlation_id": "baseline-new",
                        "payload": {
                            "model_version": "baseline-logit-v0",
                            "feature_version": "microstructure-v0",
                            "observed_at": (base_time + timedelta(seconds=30)).isoformat(),
                            "count": 40,
                            "brier_score": 0.18,
                            "log_loss": 0.55,
                            "expected_calibration_error": 0.05,
                            "maximum_calibration_error": 0.09,
                            "bins": [
                                {
                                    "lower": 0.4,
                                    "upper": 0.5,
                                    "count": 10,
                                    "mean_probability": 0.46,
                                    "observed_frequency": 0.5,
                                    "calibration_error": 0.04,
                                }
                            ],
                        },
                    },
                ],
            )

        metric = await latest_calibration_metric(
            engine,
            model_version="baseline-logit-v0",
        )

        assert metric is not None
        assert metric["model_version"] == "baseline-logit-v0"
        assert metric["count"] == 40
        assert metric["brier_score"] == 0.18
        assert metric["computed_at"] == (base_time + timedelta(minutes=1)).isoformat()
    finally:
        await engine.dispose()
