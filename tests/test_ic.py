"""T-013 / SPEC-002 §4：IC 檢驗與 research_log。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.factors.ic import compute_ic_report, forward_returns, rank_ic_series
from twquant.reporting.research_log import (
    append_research_log,
    read_research_log,
    trials_count,
)


def _prices(days: int, series: dict[str, list[float]]) -> pl.DataFrame:
    base = dt.date(2026, 6, 1)
    rows = []
    for i in range(days):
        d = base + dt.timedelta(days=i)
        for sid, vals in series.items():
            rows.append({"date": d, "stock_id": sid, "close": vals[i]})
    return pl.DataFrame(rows)


def test_forward_returns() -> None:
    prices = _prices(3, {"a": [100.0, 110.0, 121.0]})
    fwd = forward_returns(prices, 1).sort("date")
    # t0: 110/100-1=0.1 ; t1: 121/110-1=0.1
    assert fwd["fwd"].to_list() == pytest.approx([0.1, 0.1])


def test_rank_ic_perfect_positive() -> None:
    # 因子值與前向報酬完全同序 → RankIC = 1
    d = dt.date(2026, 6, 30)
    factor = pl.DataFrame({"date": [d] * 5, "stock_id": list("abcde"), "value": [1.0, 2, 3, 4, 5]})
    fwd = pl.DataFrame(
        {"date": [d] * 5, "stock_id": list("abcde"), "fwd": [0.01, 0.02, 0.03, 0.04, 0.05]}
    )
    ic = rank_ic_series(factor, fwd)
    assert ic["rank_ic"][0] == 1.0


def test_rank_ic_perfect_negative() -> None:
    d = dt.date(2026, 6, 30)
    factor = pl.DataFrame({"date": [d] * 5, "stock_id": list("abcde"), "value": [1.0, 2, 3, 4, 5]})
    fwd = pl.DataFrame(
        {"date": [d] * 5, "stock_id": list("abcde"), "fwd": [0.05, 0.04, 0.03, 0.02, 0.01]}
    )
    ic = rank_ic_series(factor, fwd)
    assert ic["rank_ic"][0] == -1.0


def test_compute_ic_report_predictive_factor() -> None:
    # 造一個「因子值高 → 下一期報酬高」的資料：價格按 stock 固定成長率遞增，
    # 因子 = 成長率排名。5 檔、20 日，horizon=1。
    days = 20
    growth = {"a": 1.00, "b": 1.01, "c": 1.02, "d": 1.03, "e": 1.05}
    series = {sid: [100.0 * g**i for i in range(days)] for sid, g in growth.items()}
    prices = _prices(days, series)
    # 因子 = 每日各股「昨日報酬」（高成長股每天報酬高，且與明日報酬同序）
    factor = (
        prices.sort(["stock_id", "date"])
        .with_columns(
            (
                pl.col("close") / pl.col("close").shift(1).over("stock_id", order_by="date") - 1
            ).alias("value")
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
    )
    report = compute_ic_report("price.x", factor, prices, [1])
    h = report.horizons[0]
    assert h.ic_mean > 0.9  # 幾乎完美正相關
    assert h.ls_spread > 0  # 高分位報酬 > 低分位


def test_research_log_append_read_trials(tmp_path: Path) -> None:
    e1 = [
        {
            "entry_type": "ic_test",
            "strategy": "",
            "factor": "price.mom_20d",
            "params_hash": "aaa",
            "date": "2026-06-30",
            "metric_name": "ic_mean_20d",
            "metric_value": 0.03,
            "trial_scope": "ic:price.mom_20d",
        }
    ]
    e2 = [dict(e1[0], params_hash="bbb")]
    append_research_log(tmp_path, e1)
    append_research_log(tmp_path, e2)
    log = read_research_log(tmp_path)
    assert log.height == 2
    # 同 scope 下 2 組 distinct params_hash → trials = 2
    assert trials_count(tmp_path, "ic:price.mom_20d") == 2
