"""T-317／D-162：事後檢驗的選股力計算——只算 20 日窗走得完的訊號日、隔日開盤進場。"""

from __future__ import annotations

import datetime as dt

import polars as pl
from scripts.d162_score_window import hit_rates


def _px(closes: dict[str, list[float]]) -> pl.DataFrame:
    rows = []
    for sid, cs in closes.items():
        for i, c in enumerate(cs):
            rows.append(
                {
                    "date": dt.date(2026, 3, 2) + dt.timedelta(days=i),
                    "stock_id": sid,
                    "open": c,
                    "close": c,
                    "adj": 1.0,
                }
            )
    return pl.DataFrame(rows)


def test_ac1_hit_is_any_close_within_h_at_least_20pct_above_next_open() -> None:
    px = _px({"A": [10, 10, 12.5, 11, 11, 11], "B": [10, 10, 11, 11, 11, 11]})
    scores = pl.DataFrame(
        {"date": [dt.date(2026, 3, 2)] * 2, "stock_id": ["A", "B"], "score": [0.9, 0.1]}
    )
    hr = hit_rates(px, scores, top_n=1, h=2)
    assert hr.to_dicts() == [{"date": dt.date(2026, 3, 2), "top": 1.0, "pool": 0.5}]


def test_ac1_signal_days_whose_window_overruns_the_data_are_dropped() -> None:
    px = _px({"A": [10, 10, 10, 10]})
    scores = pl.DataFrame(
        {
            "date": [dt.date(2026, 3, 2), dt.date(2026, 3, 3), dt.date(2026, 3, 4)],
            "stock_id": ["A", "A", "A"],
            "score": [0.5, 0.5, 0.5],
        }
    )
    hr = hit_rates(px, scores, top_n=1, h=2)
    # 4 天資料、h=2：03-03 訊號 → 03-04 開盤進場、03-04／03-05 收盤都在；03-04 訊號的窗口越界
    assert sorted(hr["date"].to_list()) == [dt.date(2026, 3, 2), dt.date(2026, 3, 3)]
