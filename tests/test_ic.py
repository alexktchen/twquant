"""T-013 / SPEC-002 §4：IC 檢驗與 research_log；T-043 事件研究。"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import polars as pl
import pytest

from twquant.factors.ic import (
    compute_event_study,
    compute_ic_report,
    forward_returns,
    rank_ic_series,
)
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


def test_rank_ic_constant_day_excluded() -> None:
    # 當日因子值全同 → pl.corr 回 NaN（非 null）→ 該日剔除、不毒化 IC 均值（T-045 發現）
    d1, d2 = dt.date(2026, 6, 29), dt.date(2026, 6, 30)
    factor = pl.DataFrame(
        {
            "date": [d1] * 5 + [d2] * 5,
            "stock_id": list("abcde") * 2,
            "value": [1.0] * 5 + [1.0, 2.0, 3.0, 4.0, 5.0],
        }
    )
    fwd = pl.DataFrame(
        {
            "date": [d1] * 5 + [d2] * 5,
            "stock_id": list("abcde") * 2,
            "fwd": [0.01, 0.02, 0.03, 0.04, 0.05] * 2,
        }
    )
    ic = rank_ic_series(factor, fwd)
    assert ic.height == 1
    assert ic["rank_ic"][0] == 1.0


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


def test_event_study_basic() -> None:
    # 三檔 12 日：1101 日漲 1 元、2330 持平、3008 日跌 1 元 → 每日宇宙中位數 fwd = 0。
    # 事件：1101 於 d2 成立（持續 d2-d4）、d8 再成立（前向不足）；3008 於 d3 成立。
    days = 12
    base = dt.date(2026, 6, 1)
    series = {
        "1101": [100.0 + i for i in range(days)],
        "2330": [50.0] * days,
        "3008": [100.0 - i for i in range(days)],
    }
    rows = []
    for i in range(days):
        d = base + dt.timedelta(days=i)
        for sid, vals in series.items():
            # 三個事件日皆有漲停旗標資料；僅 1101@d2 收在漲停價（102 ≥ 102）
            limit_up = {("1101", 2): 102.0, ("1101", 8): 200.0, ("3008", 3): 150.0}.get((sid, i))
            rows.append({"date": d, "stock_id": sid, "close": vals[i], "limit_up_price": limit_up})
    prices = pl.DataFrame(rows).with_columns(pl.col("limit_up_price").cast(pl.Float64))
    f_rows = [
        {"date": base + dt.timedelta(days=i), "stock_id": "1101", "value": 0.1}
        for i in (2, 3, 4, 8)
    ] + [{"date": base + dt.timedelta(days=3), "stock_id": "3008", "value": 0.2}]
    rpt = compute_event_study("price.x", pl.DataFrame(f_rows), prices, [5, 20])

    assert rpt.n_events == 3  # d2 連續段首日、d8 新段、3008 d3
    assert rpt.limit_up_share == pytest.approx(1 / 3)
    h5 = rpt.horizons[0]
    assert h5.n_events == 2  # 1101@d8 前向資料不足 → 排除
    assert h5.hit_rate == pytest.approx(0.5)
    # 1101@d2 超額 = 107/102−1；3008@d3 超額 = 92/97−1（宇宙中位數皆 0）
    assert h5.mean_excess == pytest.approx((5 / 102 - 5 / 97) / 2)
    h20 = rpt.horizons[1]
    assert h20.n_events == 0
    assert math.isnan(h20.mean_excess)
    # 旗標欄全缺（drop）→ 占比 NaN 而非 0
    rpt2 = compute_event_study("price.x", pl.DataFrame(f_rows), prices.drop("limit_up_price"), [5])
    assert math.isnan(rpt2.limit_up_share)


def test_event_study_empty_factor() -> None:
    prices = _prices(5, {"1101": [1.0, 1.0, 1.0, 1.0, 1.0]})
    empty = pl.DataFrame(schema={"date": pl.Date, "stock_id": pl.String, "value": pl.Float64})
    rpt = compute_event_study("price.x", empty, prices, [5])
    assert rpt.n_events == 0
    assert math.isnan(rpt.limit_up_share)
    assert rpt.horizons[0].n_events == 0


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
