"""SPEC-004 AC-11 / SPEC-010 AC-11：回測試驗身分與 DSR family。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from twquant.backtest.costs import CostConfig
from twquant.reporting.research_log import (
    append_research_log,
    backtest_log_entries,
    backtest_params_hash,
    backtest_trial_scope,
    trials_count,
)
from twquant.strategy.config import SignalConfig, StrategyConfig


def _strategy(*, model_id: str = "lgbm_h10_20260225_2f5a21a9") -> StrategyConfig:
    return StrategyConfig(
        name="ml_rank_variant",
        trial_scope="ml_rank_h10",
        signals=[
            SignalConfig(
                factor="ml.lgbm_rank_10d",
                weight=1.0,
                params={"model_id": model_id},
            )
        ],
    )


def _hash(
    strategy: StrategyConfig | None = None,
    cost: CostConfig | None = None,
    *,
    start: dt.date = dt.date(2022, 7, 1),
    capital: float = 10_000_000.0,
    participation: float = 0.05,
    source: str = "pg",
) -> str:
    return backtest_params_hash(
        strategy=strategy or _strategy(),
        cost=cost or CostConfig(),
        start=start,
        end=dt.date(2026, 2, 28),
        initial_capital=capital,
        max_participation=participation,
        source=source,
    )


def test_ac11_canonical_hash_is_deterministic() -> None:
    a = _strategy()
    b = StrategyConfig.model_validate(a.model_dump(mode="json"))
    assert _hash(a) == _hash(b)


def test_ac11_hash_changes_for_every_material_dimension() -> None:
    base = _hash()
    changed = {
        _hash(_strategy(model_id="lgbm_h10_20260115_2f5a21a9")),
        _hash(cost=CostConfig(cost_scale=0.0)),
        _hash(start=dt.date(2022, 7, 4)),
        _hash(capital=20_000_000.0),
        _hash(participation=0.03),
        _hash(source="lake"),
    }
    assert len(changed) == 6
    assert base not in changed


def test_ac11_variants_share_explicit_trial_scope() -> None:
    original = _strategy()
    variant = original.model_copy(update={"name": "ml_rank_ema5"})
    assert backtest_trial_scope(original) == "bt:ml_rank_h10"
    assert backtest_trial_scope(variant) == "bt:ml_rank_h10"
    # 只改顯示名稱不改經濟行為，不得製造假 trial。
    assert _hash(original) == _hash(variant)


def test_ac11_default_scope_falls_back_to_strategy_name() -> None:
    cfg = StrategyConfig(
        name="reversal_revenue",
        signals=[SignalConfig(factor="price.reversal_5d", weight=1.0)],
    )
    assert backtest_trial_scope(cfg) == "bt:reversal_revenue"


def test_ac11_backtest_entries_use_canonical_identity() -> None:
    strategy = _strategy()
    cost = CostConfig()
    entries = backtest_log_entries(
        strategy=strategy,
        cost=cost,
        start=dt.date(2022, 7, 1),
        end=dt.date(2026, 2, 28),
        initial_capital=10_000_000.0,
        max_participation=0.05,
        source="pg",
        metrics={"sharpe": -0.36, "cagr": -0.14, "mdd": -0.56},
    )
    expected_hash = _hash(strategy, cost)
    assert {e["params_hash"] for e in entries} == {expected_hash}
    assert {e["trial_scope"] for e in entries} == {"bt:ml_rank_h10"}
    assert {e["metric_name"] for e in entries} == {"sharpe", "cagr", "mdd"}


def test_ac10_trials_count_distinct_hashes_once_per_family(tmp_path: Path) -> None:
    def entries(strategy: StrategyConfig) -> list[dict[str, object]]:
        return backtest_log_entries(
            strategy=strategy,
            cost=CostConfig(),
            start=dt.date(2022, 7, 1),
            end=dt.date(2026, 2, 28),
            initial_capital=10_000_000.0,
            max_participation=0.05,
            source="pg",
            metrics={"sharpe": -0.36, "cagr": -0.14, "mdd": -0.56},
        )

    # 同一次 trial 的三個 metrics 只算一個；重複 append 同 hash 仍只算一個。
    first = entries(_strategy())
    append_research_log(tmp_path, first)
    append_research_log(tmp_path, first)
    assert trials_count(tmp_path, "bt:ml_rank_h10") == 1

    second = entries(_strategy(model_id="lgbm_h10_20260115_2f5a21a9"))
    append_research_log(tmp_path, second)
    assert trials_count(tmp_path, "bt:ml_rank_h10") == 2
