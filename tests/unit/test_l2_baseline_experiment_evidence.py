from __future__ import annotations

from pathlib import Path

import pytest

from apps.api.app.research_persistence import persist_l2_baseline_evidence
from services.market_data.l2_corpus_replay import PublicL2CorpusReplay
from services.market_data.l2_corpus_storage import PublicL2CorpusWriter
from services.market_data.public_l2 import PublicL2Frame, parse_public_l2_message
from services.validation import (
    L2BaselineCampaignConfig,
    build_l2_baseline_experiment_evidence,
    evidence_manifest_fingerprint,
    evidence_payload_fingerprint,
)

GIT_SHA = "776E0F1A68EBC289911770577AC75BAB16C19E43"


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
        dataset_name="btc-l2-experiment-evidence",
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


def _config(*, cost_bps: float = 5.0, pbo_segments: int = 4) -> L2BaselineCampaignConfig:
    return L2BaselineCampaignConfig(
        cost_bps=cost_bps,
        train_size=6,
        test_size=3,
        trials=3,
        monte_carlo_simulations=20,
        monte_carlo_block_size=2,
        monte_carlo_seed=13,
        delay_periods=1,
        shuffle_seed=17,
        pbo_segments=pbo_segments,
    )


def test_evidence_is_deterministic_and_control_only(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")

    first = build_l2_baseline_experiment_evidence(
        replay,
        asset="BTC",
        git_sha=GIT_SHA,
        config=_config(),
    )
    second = build_l2_baseline_experiment_evidence(
        replay,
        asset="BTC",
        git_sha=GIT_SHA,
        config=_config(),
    )

    assert first == second
    assert [item.strategy_name for item in first] == [
        "buy_hold_mid",
        "momentum_1",
        "mean_reversion_1",
    ]
    assert len({item.experiment_id for item in first}) == 3
    assert len({item.returns_fingerprint for item in first}) == 3
    for item in first:
        assert item.research_decision == "CONTROL_ONLY"
        assert item.payload["research_decision"] == {
            "status": "CONTROL_ONLY",
            "promotion_eligible": False,
            "reason": (
                "Baseline benchmark evidence is a research control and cannot "
                "be promoted as alpha."
            ),
        }
        assert item.financial_connectivity is False
        assert item.real_money_execution is False
        assert item.payload["financial_connectivity"] is False
        assert item.payload["real_money_execution"] is False
        assert len(item.experiment_id) == 64
        assert len(item.dataset_fingerprint) == 64
        assert len(item.returns_fingerprint) == 64
        assert len(item.evidence_fingerprint) == 64
        assert evidence_manifest_fingerprint(item) == item.experiment_id
        assert len(evidence_payload_fingerprint(item)) == 64


def test_evidence_manifest_matches_experiment_registry_contract(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")

    evidence = build_l2_baseline_experiment_evidence(
        replay,
        asset="BTC",
        git_sha=GIT_SHA,
        config=_config(),
    )

    buy_hold = evidence[0]
    momentum = evidence[1]
    mean_reversion = evidence[2]
    manifest = buy_hold.manifest
    dataset = manifest["dataset"]

    assert manifest["research_mode"] == "HISTORICAL_REPLAY"
    assert manifest["feature_version"] == "public-l2-mid-return-v1"
    assert manifest["model_version"] == "none"
    assert manifest["git_sha"] == GIT_SHA.lower()
    assert manifest["seed"] == 13
    assert manifest["replay_fingerprint"] == replay.replay_fingerprint(seed=13)
    assert manifest["windows"] == [
        {
            "role": "TEST",
            "start_at": dataset["start_at"],
            "end_at": dataset["end_at"],
        }
    ]
    assert manifest["execution_assumptions"]["transaction_cost_model"] == (
        "TURNOVER_BPS"
    )
    assert manifest["execution_assumptions"]["queue_model"] == (
        "NOT_APPLICABLE_CONTROL"
    )
    assert buy_hold.manifest["parameters"]["signal_lag_periods"] == 0
    for item in (momentum, mean_reversion):
        assert item.manifest["parameters"]["signal_source"] == (
            "PREVIOUS_COMPLETED_MID_RETURN"
        )
        assert item.manifest["parameters"]["signal_lag_periods"] == 1
        assert item.manifest["parameters"]["reconnect_position_reset"] is True


def test_evidence_contains_validation_and_negative_controls(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")

    evidence = build_l2_baseline_experiment_evidence(
        replay,
        asset="BTC",
        git_sha=GIT_SHA,
        config=_config(),
    )

    for item in evidence:
        result = item.validation_result
        assert result["performance"]["sample_count"] == 12
        assert result["pbo_status"] == "COMPUTED"
        assert result["probability_of_backtest_overfitting"] is not None
        assert 0.0 <= result["deflated_sharpe_ratio"] <= 1.0
        assert result["monte_carlo"]["simulations"] == 20
        assert result["negative_controls"]["delay"]["sample_count"] == 12
        assert result["negative_controls"]["timestamp_shuffle"][
            "sample_count"
        ] == 12
        assert item.validation_plan["method"] == "PURGED_WALK_FORWARD"
        assert item.validation_plan["negative_controls"] == {
            "delay_periods": 1,
            "shuffle_seed": 17,
        }


def test_insufficient_pbo_is_recorded_without_fabricated_value(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")

    evidence = build_l2_baseline_experiment_evidence(
        replay,
        asset="BTC",
        git_sha=GIT_SHA,
        config=_config(pbo_segments=8),
    )

    for item in evidence:
        assert item.validation_result["pbo_status"] == "INSUFFICIENT_SAMPLE"
        assert item.validation_result["probability_of_backtest_overfitting"] is None


def test_manifest_inputs_change_experiment_identity(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")

    baseline = build_l2_baseline_experiment_evidence(
        replay,
        asset="BTC",
        git_sha=GIT_SHA,
        config=_config(cost_bps=5.0),
    )
    changed_cost = build_l2_baseline_experiment_evidence(
        replay,
        asset="BTC",
        git_sha=GIT_SHA,
        config=_config(cost_bps=7.5),
    )
    changed_code = build_l2_baseline_experiment_evidence(
        replay,
        asset="BTC",
        git_sha="abcdef0123456789",
        config=_config(cost_bps=5.0),
    )

    assert baseline[0].experiment_id != changed_cost[0].experiment_id
    assert baseline[0].experiment_id != changed_code[0].experiment_id


def test_invalid_git_sha_fails_closed(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")

    with pytest.raises(ValueError, match="git_sha"):
        build_l2_baseline_experiment_evidence(
            replay,
            asset="BTC",
            git_sha="not-a-sha",
            config=_config(),
        )


@pytest.mark.asyncio
async def test_persistence_is_disabled_cleanly_without_engine(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")
    evidence = build_l2_baseline_experiment_evidence(
        replay,
        asset="BTC",
        git_sha=GIT_SHA,
        config=_config(),
    )

    assert await persist_l2_baseline_evidence(None, evidence) == 0


@pytest.mark.asyncio
async def test_persistence_rejects_duplicate_experiment_ids(tmp_path: Path) -> None:
    replay = _build_corpus(tmp_path / "btc-l2.jsonl")
    evidence = build_l2_baseline_experiment_evidence(
        replay,
        asset="BTC",
        git_sha=GIT_SHA,
        config=_config(),
    )

    with pytest.raises(ValueError, match="duplicate experiment identities"):
        await persist_l2_baseline_evidence(None, (evidence[0], evidence[0]))
