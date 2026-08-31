from apps.api.app.live_metrics import render_live_prometheus


def test_live_prometheus_exports_normalized_pipeline_snapshot() -> None:
    body = render_live_prometheus(
        {
            "data_pipeline": {
                "accepted": 12,
                "duplicates": 2,
                "quality_rejections": 3,
                "publish_failures": 1,
                "published": 11,
                "tracked_event_ids": 10,
                "dedupe_capacity": 100_000,
                "tracked_markets": 3,
            }
        }
    )

    assert "proto_live_pipeline_accepted_total 12" in body
    assert "proto_live_pipeline_duplicates_total 2" in body
    assert "proto_live_pipeline_quality_rejections_total 3" in body
    assert "proto_live_pipeline_publish_failures_total 1" in body
    assert "proto_live_pipeline_published_total 11" in body
    assert "proto_live_pipeline_tracked_event_ids 10" in body
    assert "proto_live_pipeline_dedupe_capacity 100000" in body
    assert "proto_live_pipeline_tracked_markets 3" in body
    assert "proto_live_financial_connectivity 0" in body
    assert "proto_live_real_money_execution 0" in body


def test_live_prometheus_omits_nonfinite_pipeline_metrics() -> None:
    body = render_live_prometheus(
        {
            "data_pipeline": {
                "accepted": float("nan"),
                "publish_failures": float("inf"),
            }
        }
    )

    assert "proto_live_pipeline_accepted_total" not in body
    assert "proto_live_pipeline_publish_failures_total" not in body
    assert " nan" not in body.lower()
    assert " inf" not in body.lower()
