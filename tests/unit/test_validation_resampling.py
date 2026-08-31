from random import Random

from services.validation import block_bootstrap_path, monte_carlo_block_bootstrap


def test_block_bootstrap_is_deterministic_for_seeded_rng() -> None:
    returns = (0.01, -0.02, 0.03, 0.01, -0.01, 0.02)
    first = block_bootstrap_path(returns, path_length=10, block_size=2, rng=Random(11))
    second = block_bootstrap_path(returns, path_length=10, block_size=2, rng=Random(11))
    assert first == second
    assert len(first) == 10


def test_monte_carlo_summary_is_reproducible() -> None:
    returns = (0.01, -0.005, 0.012, 0.003, -0.004, 0.009, 0.006, -0.002)
    first = monte_carlo_block_bootstrap(returns, simulations=200, block_size=2, seed=19)
    second = monte_carlo_block_bootstrap(returns, simulations=200, block_size=2, seed=19)
    assert first == second
    assert 0.0 <= first.probability_of_loss <= 1.0
    assert first.p05_terminal_return <= first.median_terminal_return <= first.p95_terminal_return
    assert first.median_max_drawdown <= first.p95_max_drawdown


def test_bootstrap_rejects_invalid_block_size() -> None:
    try:
        monte_carlo_block_bootstrap((0.01, 0.02), simulations=10, block_size=3)
    except ValueError as exc:
        assert "block_size" in str(exc)
    else:
        raise AssertionError("expected invalid block size to be rejected")
