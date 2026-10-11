"""D-171 §一之四（右尾）：命中率、配對命中率、倍率、差與登記簿（先凍結，再跑資料）。"""

from __future__ import annotations

import datetime as dt
from typing import Any

import polars as pl
import pytest

from twquant.config import FundFlowTracesConfig
from twquant.reporting import fund_flow_traces as ft

DAY = dt.date(2026, 3, 2)
TC = FundFlowTracesConfig(bootstrap_n=200)


def _pool(n: int) -> tuple[pl.DataFrame, list[str]]:
    ids = [f"{1000 + i}" for i in range(n)]
    pool_b = pl.DataFrame(
        {
            "date": [DAY] * n,
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
    return pool_b, ids


def _hits(ids: list[str], hits: list[float | None]) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": [DAY] * len(ids), "stock_id": ids, "r20": hits, "pre": [None] * len(ids)},
        schema={"date": pl.Date, "stock_id": pl.String, "r20": pl.Float64, "pre": pl.Float64},
    )


def _matched(event_ids: list[str], hits: list[float | None], n: int) -> pl.DataFrame:
    pool_b, ids = _pool(n)
    ev = pl.DataFrame(
        {"event": ["E3"] * len(event_ids), "date": [DAY] * len(event_ids), "stock_id": event_ids},
        schema={"event": pl.String, "date": pl.Date, "stock_id": pl.String},
    )
    x, _ = ft.matched_excess(ev, pool_b, _hits(ids, hits), [20], matched_min=5)
    return x


def test_tail_lift_hit_minus_control_mean() -> None:
    # 10 檔：事件 1000、1001 都命中；其餘 8 檔有 2 檔命中 → 配對命中率 2/8＝25%
    hits: list[float | None] = [1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    x = _matched(["1000", "1001"], hits, 10)
    node = ft.tail_summary(x, stratum="全市場", tcfg=TC)["全市場"]["E3"]
    assert node["n"] == 2 and node["stocks"] == 2
    assert node["hit"] == pytest.approx(1.0)
    assert node["ctrl"] == pytest.approx(0.25)
    assert node["lift"] == pytest.approx(4.0)
    assert node["diff"] == pytest.approx(0.75)
    assert set(node["by_year"]) == {"2026"}


def test_tail_null_label_is_not_zero() -> None:
    # 事件股標籤未成熟（null）→ 不計入，不是「沒飆」
    hits: list[float | None] = [None, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    x = _matched(["1000", "1001"], hits, 10)
    node = ft.tail_summary(x, stratum="全市場", tcfg=TC)["全市場"]["E3"]
    assert node["n"] == 1 and node["hit"] == pytest.approx(1.0)


def test_tail_zero_control_rate_has_no_lift() -> None:
    hits: list[float | None] = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    x = _matched(["1000"], hits, 10)
    node = ft.tail_summary(x, stratum="全市場", tcfg=TC)["全市場"]["E3"]
    assert node["ctrl"] == 0.0 and node["lift"] is None


def _report() -> dict[str, Any]:
    hits: list[float | None] = [1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    x = _matched(["1000", "1001"], hits, 10)
    return {
        "meta": {
            "run_date": "2026-10-11",
            "first_date": "2015-06-01",
            "last_date": "2026-08-31",
            "match_bins": 3,
            "matched_min": 5,
            "strata": ["全市場"],
        },
        "results": ft.tail_summary(x, stratum="全市場", tcfg=TC),
    }


def test_tail_markdown_and_entries() -> None:
    rep = _report()
    md = ft.render_tail_markdown(rep)
    assert "倍率" in md and "E3 ETF 申購潮" in md
    for word in ("通過", "不通過", "失敗", "成功", "有效", "無效"):
        assert word not in md
    rows = ft.tail_research_entries(rep, "h1", "2026-10-11")
    assert rows and rows[0]["trial_scope"] == "flow:tails"
    assert rows[0]["metric_name"] == "tail_lift_20d_20pct"
