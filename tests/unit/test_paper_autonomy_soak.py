from apps.api.app.paper_autonomy_soak import build_soak_report


def test_soak_report_uses_counter_deltas_and_never_invents_pnl() -> None:
    samples = [
        {
            "observed_at": "2026-09-03T23:00:00Z",
            "live_ready": True,
            "autopilot": {
                "counters": {
                    "cycles": 10,
                    "signals": 2,
                    "submissions": 1,
                    "accepted": 1,
                    "rejected": 0,
                    "stop_loss_exits": 0,
                    "errors": 0,
                }
            },
            "bootstrap": {"watchdog": {"restarts": 0, "failures": 0}},
            "paper_pnl": None,
        },
        {
            "observed_at": "2026-09-03T23:01:00Z",
            "live_ready": False,
            "autopilot": {
                "counters": {
                    "cycles": 14,
                    "signals": 3,
                    "submissions": 2,
                    "accepted": 1,
                    "rejected": 1,
                    "stop_loss_exits": 0,
                    "errors": 0,
                }
            },
            "bootstrap": {"watchdog": {"restarts": 1, "failures": 0}},
            "paper_pnl": None,
        },
    ]

    report = build_soak_report(samples)

    assert report["sample_count"] == 2
    assert report["counter_deltas"] == {
        "cycles": 4,
        "signals": 1,
        "submissions": 1,
        "accepted": 0,
        "rejected": 1,
        "stop_loss_exits": 0,
        "errors": 0,
    }
    assert report["watchdog_deltas"] == {"restarts": 1, "failures": 0}
    assert report["stale_samples"] == 1
    assert report["paper_pnl"] is None
    assert report["profitability_asserted"] is False
    assert report["financial_connectivity"] is False
    assert report["real_money_execution"] is False


def test_soak_report_preserves_truthful_final_pnl_when_present() -> None:
    samples = [
        {
            "observed_at": "2026-09-03T23:00:00Z",
            "live_ready": True,
            "autopilot": {"counters": {"cycles": 1}},
            "bootstrap": {"watchdog": {}},
            "paper_pnl": {"total_pnl": -12.5, "source": "PAPER"},
        }
    ]

    report = build_soak_report(samples)

    assert report["paper_pnl"] == {"total_pnl": -12.5, "source": "PAPER"}
    assert report["profitability_asserted"] is False
