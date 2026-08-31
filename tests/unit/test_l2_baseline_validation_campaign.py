from __future__ import annotations

from pathlib import Path

import pytest

from services.market_data.l2_corpus_replay import PublicL2CorpusReplay
from services.market_data.l2_corpus_storage import PublicL2CorpusWriter
from services.market_data.public_l2 import PublicL2Frame, parse_public_l2_message
from services.validation.l2_baselines import (
    L2BaselineCampaignConfig,
    available_l2_baselines,
    build_l2_market_returns,
    run_l2_baseline,
    run_l2_baseline_campaign,
)


def _snapshot_frame(sequence: int, timestamp: str, mid: float) -> PublicL2Frame:
    frame = parse_public_l2_message(
        {
            "channel": "l2_data",
            "timestamp": timestamp,
            "sequence_num": sequence,
            "events": [
                {
                    "type": "snapshot",
                    "product_id": "BTC-USD",
                    "updates": [
                        {
                            "side": "bid",
                            "event_time": timestamp,
                            "price_level": f"{mid - 0.25:.2f}",
                            "new_quantity": "1.0",
                        },
                        {
                            "side": "offer",
                            "event_time": timestamp,
                            "price_level": f"{mid + 0.25:.2f}",
                            "new_quantity": "1.0",
                        },
                    ],
                }
            ],
        }
    )
    assert frame is not None
    return frame


def _build_corpus(path: Path) -> PublicL2CorpusReplay:
    writer = PublicL2CorpusWriter(
        path,
        dataset_name="btc-l2-baseline-campaign",
        flush_every_records=1,
    )
    generation_one = [100.0, 101.0, 100.5, 102.0, 101.0, 103.0, 102.5]
    generation_two = [200.0, 198.0, 199.0, 197.0, 198.5, 196.0, 197.0]

    for generation, prices, sequence_start, second_offset in (
        (1, generation_one, 10, 0),
        (2, generation_two, 100, 20),
    ):
        for index, mid in enumerate(prices):
            writer.append(
                _snapshot_frame(
                    sequence_start + index,
                    f"2026-08-31T21:00:{second_offset + index:02d}Z",
                    mid,
                ),
                connection_generation=generation,
            )
    writer.finalize()
    return PublicL2CorpusReplay(path)


def test_market_returns_do_not_cross_reconnect_boundaries(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")

    samples = build_l2_market_returns(replay, asset="BTC")

    assert len(samples) == 12
    assert [sample.connection_generation for sample in samples] == [1] * 6 + [2] * 6
    assert samples[0].previous_market_return is None
    assert samples[6].previous_market_return is None
    assert samples[0].market_return == pytest.approx(0.01)
    assert samples[6].market_return == pytest.approx(-0.01)


def test_momentum_signal_uses_only_previous_return(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")

    run = run_l2_baseline(
        replay,
        asset="BTC",
        strategy_name="momentum_1",
        cost_bps=0.0,
    )

    assert run.samples[0].position == 0.0
    assert run.samples[1].position == 1.0
    assert run.samples[1].market_return < 0.0
    assert run.samples[2].position == -1.0
    assert run.samples[6].position == 0.0
    assert run.samples[7].position == -1.0
    assert run.samples[7].market_return > 0.0


def test_buy_hold_charges_new_entry_cost_after_generation_reset(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")

    run = run_l2_baseline(
        replay,
        asset="BTC",
        strategy_name="buy_hold_mid",
        cost_bps=5.0,
    )

    assert run.samples[0].turnover == 1.0
    assert run.samples[0].transaction_cost == pytest.approx(0.0005)
    assert run.samples[1].turnover == 0.0
    assert run.samples[6].turnover == 1.0
    assert run.samples[6].transaction_cost == pytest.approx(0.0005)


def test_campaign_runs_validation_negative_controls_and_pbo_deterministically(
    tmp_path: Path,
) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")
    config = L2BaselineCampaignConfig(
        cost_bps=5.0,
        train_size=6,
        test_size=3,
        trials=3,
        monte_carlo_simulations=20,
        monte_carlo_block_size=2,
        monte_carlo_seed=13,
        delay_periods=1,
        shuffle_seed=17,
        pbo_segments=4,
    )

    first = run_l2_baseline_campaign(replay, asset="BTC", config=config)
    second = run_l2_baseline_campaign(replay, asset="BTC", config=config)

    assert first == second
    assert first.market_sample_count == 12
    assert [item.strategy.name for item in first.validations] == [
        "buy_hold_mid",
        "momentum_1",
        "mean_reversion_1",
    ]
    assert first.pbo_status == "COMPUTED"
    assert first.pbo is not None
    assert 0.0 <= first.pbo <= 1.0
    assert len(first.campaign_fingerprint) == 64
    assert len(first.replay_fingerprint) == 64
    assert first.dataset["data_level"] == "L2"
    assert first.financial_connectivity is False
    assert first.real_money_execution is False
    for validation in first.validations:
        assert validation.report.metrics.sample_count == 12
        assert 0.0 <= validation.deflated_sharpe_ratio <= 1.0
        assert validation.delay_control_metrics.sample_count == 12
        assert validation.shuffle_control_metrics.sample_count == 12


def test_campaign_reports_pbo_insufficient_sample_without_fabricating_value(
    tmp_path: Path,
) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")
    config = L2BaselineCampaignConfig(
        train_size=6,
        test_size=3,
        monte_carlo_simulations=10,
        pbo_segments=8,
    )

    result = run_l2_baseline_campaign(replay, asset="BTC", config=config)

    assert result.pbo is None
    assert result.pbo_status == "INSUFFICIENT_SAMPLE"


def test_registry_is_explicit_and_missing_asset_fails_closed(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")

    assert [item.name for item in available_l2_baselines()] == [
        "buy_hold_mid",
        "momentum_1",
        "mean_reversion_1",
    ]
    with pytest.raises(ValueError, match="no snapshots for ETH"):
        build_l2_market_returns(replay, asset="ETH")
