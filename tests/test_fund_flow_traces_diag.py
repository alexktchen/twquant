"""D-171 §一之三：「事件平均 − 配對組中位數」的右偏偏誤，與診斷欄「對配對組平均的超額」。

這些測試在第 2 次試驗結果出來之後才寫（不是 T-339-t 凍結測試的一部分），只驗證診斷欄的算法。
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import polars as pl
import pytest

from twquant.reporting import fund_flow_traces as ft


def _cell(n: int, seed: int) -> tuple[pl.DataFrame, pl.DataFrame]:
    rng = np.random.default_rng(seed)
    day = dt.date(2026, 3, 2)
    ids = [f"{1000 + i}" for i in range(n)]
    r = np.exp(rng.normal(0.0, 0.3, n)) - 1.0  # 對數常態：右偏，平均比中位數高約 4.6%
    pool_b = pl.DataFrame(
        {
            "date": [day] * n,
            "stock_id": ids,
            "_vol_b": [0] * n,
            "_mom_b": [0] * n,
            "_adv_b": [0] * n,
        },
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "_vol_b": pl.Int32,
            "_mom_b": pl.Int32,
            "_adv_b": pl.Int32,
        },
    )
    rets = pl.DataFrame({"date": [day] * n, "stock_id": ids, "r1": r, "pre": [0.0] * n})
    return pool_b, rets


def test_diag_random_events_show_skew_bias_but_not_vs_mean() -> None:
    pool_b, rets = _cell(10000, 7)
    ids = rets["stock_id"].to_list()
    events = pl.DataFrame(
        {"event": ["E3"] * 2000, "date": [dt.date(2026, 3, 2)] * 2000, "stock_id": ids[::5]},
        schema={"event": pl.String, "date": pl.Date, "stock_id": pl.String},
    )
    x, _ = ft.matched_excess(events, pool_b, rets, [1], matched_min=5)
    # 隨機挑的「事件」：減中位數的平均顯著為正（偏誤），減平均的接近 0
    assert float(x["x_r1"].mean()) > 0.025  # type: ignore[arg-type]
    assert abs(float(x["y_r1"].mean())) < 0.02  # type: ignore[arg-type]


def test_diag_vs_mean_is_ret_minus_control_mean() -> None:
    pool_b, rets = _cell(8, 1)
    ev = pl.DataFrame(
        {"event": ["E3"], "date": [dt.date(2026, 3, 2)], "stock_id": ["1000"]},
        schema={"event": pl.String, "date": pl.Date, "stock_id": pl.String},
    )
    x, _ = ft.matched_excess(ev, pool_b, rets, [1], matched_min=5)
    ctrl = rets.filter(pl.col("stock_id") != "1000")["r1"]
    own = rets.filter(pl.col("stock_id") == "1000")["r1"][0]
    assert x["y_r1"][0] == pytest.approx(own - float(ctrl.mean()))  # type: ignore[arg-type]
    assert x["x_r1"][0] == pytest.approx(own - float(ctrl.median()))  # type: ignore[arg-type]
