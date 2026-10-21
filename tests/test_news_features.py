"""T-414：新聞因子特徵（data/news_features.py 的純函式）。AC：截點（含等於）、缺值不補 0、可得時點只能變少。"""

from __future__ import annotations

import datetime as dt
import math

import numpy as np
import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import news_features as nf
from twquant.errors import DataError

_TS = pl.Datetime("us", "Asia/Taipei")
D = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(10)]


def _ts(d: dt.date, h: int, m: int = 0, us: int = 0) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, h, m, 0, us, tzinfo=TZ)


def _mentions(rows: list[tuple[str, str, dt.datetime, bool]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "item_id": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "available_at": [r[2] for r in rows],
            "is_mops": [r[3] for r in rows],
        },
        schema={
            "item_id": pl.String,
            "stock_id": pl.String,
            "available_at": _TS,
            "is_mops": pl.Boolean,
        },
    )


def _days(df: pl.DataFrame, cutoff: int) -> list[dt.date]:
    return df.select(nf.news_day(pl.col("available_at"), cutoff).alias("d"))["d"].to_list()


def test_ac_news_day_cutoff_is_inclusive() -> None:
    df = _mentions(
        [
            ("a", "1", _ts(D[1], 18, 59), False),
            ("b", "1", _ts(D[1], 19, 0), False),
            ("c", "1", _ts(D[1], 19, 0, 1), False),
            ("d", "1", _ts(D[1], 0, 30), False),
        ]
    )
    assert _days(df, 19) == [D[1], D[1], D[2], D[1]]
    assert _days(df, 18) == [D[2], D[2], D[2], D[1]]


def test_daily_counts_dedups_item_stock_and_splits_mops() -> None:
    df = _mentions(
        [
            ("a", "1", _ts(D[1], 9), False),
            ("a", "1", _ts(D[1], 9), False),
            ("b", "1", _ts(D[1], 10), True),
            ("c", "2", _ts(D[1], 20), False),
        ]
    )
    out = nf.daily_counts(df, 19).sort(["day", "stock_id"])
    assert out.rows() == [(D[1], "1", 1, 1), (D[2], "2", 1, 0)]


def test_count_features_log_and_attention_with_partial_base() -> None:
    grid = nf.dense_grid(["1"], D[0], D[2])
    counts = pl.DataFrame(
        {"day": [D[0], D[2]], "stock_id": ["1", "1"], "n": [2, 1], "n_mops": [0, 3]},
        schema={"day": pl.Date, "stock_id": pl.String, "n": pl.Int64, "n_mops": pl.Int64},
    )
    out = nf.count_features(grid, counts, short=2, mid=3, base=3, mops=2).sort("day")
    assert out["count_1d"].to_list() == pytest.approx([math.log1p(2), 0.0, math.log1p(1)])
    # 第 3 天：近 2 日 1 則、近 3 日 3 則／3 天＝日均 1 → log((1+1)/(1+1)) = 0
    assert out["attention_5d"][2] == pytest.approx(0.0)
    # 第 1 天：只有 1 天歷史，分母用已有天數（2/1+1）而不是 base
    assert out["attention_5d"][0] == pytest.approx(math.log((2 + 1) / (2 / 1 + 1)))
    assert out["mops_count_5d"].to_list() == [0.0, 0.0, 3.0]


def test_ac_counts_can_only_shrink_when_as_of_moves_earlier() -> None:
    """可得時點往前移（只看更早可得的新聞），每一天每一檔的則數只能變少或不變。"""
    rng = np.random.default_rng(3)
    rows = []
    for i in range(300):
        d = D[int(rng.integers(0, 8))]
        rows.append(
            (
                f"i{i}",
                str(rng.integers(1, 4)),
                _ts(d, int(rng.integers(0, 24)), int(rng.integers(0, 60))),
                False,
            )
        )
    df = _mentions(rows)
    cuts = [_ts(D[3], 12), _ts(D[5], 19), _ts(D[7], 3), _ts(D[9], 23)]
    prev: dict[tuple[dt.date, str], int] = {}
    for c in cuts:
        cur = {
            (r[0], r[1]): r[2]
            for r in nf.daily_counts(df.filter(pl.col("available_at") <= c), 19).rows()
        }
        for k, v in prev.items():
            assert cur.get(k, 0) >= v
        prev = cur


def test_sentiment_features_null_without_news_and_shift() -> None:
    grid = nf.dense_grid(["1"], D[0], D[3])
    scores = pl.DataFrame(
        {
            "item_id": ["a", "b", "c"],
            "stock_id": ["1", "1", "1"],
            "available_at": [_ts(D[0], 9), _ts(D[1], 9), _ts(D[1], 10)],
            "sent": [1.0, -1.0, 0.0],
        },
        schema={
            "item_id": pl.String,
            "stock_id": pl.String,
            "available_at": _TS,
            "sent": pl.Float64,
        },
    )
    out = nf.sentiment_features(grid, scores, cutoff_hour=19, short=1, mid=2, shift_base=1).sort(
        "day"
    )
    assert out["sent_1d"].to_list() == [1.0, -0.5, None, None]
    assert out["sent_5d"].to_list() == [1.0, 0.0, -0.5, None]
    # 第 2 天：近 2 日平均 0，再往前 1 日沒有 → shift null；第 3 天：近 2 日 −0.5，前 1 日（第 1 天）1 → −1.5
    assert out["sent_shift"].to_list()[1] is None
    assert out["sent_shift"].to_list()[2] == pytest.approx(-1.5)


def test_sector_heat_z_score_needs_history_and_spread() -> None:
    grid = nf.dense_grid(["1", "2"], D[0], D[9])
    counts = pl.DataFrame(
        {
            "day": [D[2], D[9], D[9]],
            "stock_id": ["1", "1", "2"],
            "n": [1, 5, 5],
            "n_mops": [0, 0, 0],
        },
        schema={"day": pl.Date, "stock_id": pl.String, "n": pl.Int64, "n_mops": pl.Int64},
    )
    gc = nf.count_features(grid, counts, short=1, mid=1, base=1, mops=1).select(
        "day", "stock_id", "n"
    )
    groups = pl.DataFrame({"stock_id": ["1", "2"], "grp": ["g", "g"]})
    out = nf.sector_heat(gc, groups, short=1, base=5, min_base=3).sort(["day", "stock_id"])
    by = {(r[0], r[1]): r[2] for r in out.rows()}
    assert by[(D[0], "1")] is None and by[(D[1], "1")] is None  # 歷史不足 3 天
    assert (
        by[(D[9], "1")] == by[(D[9], "2")] and by[(D[9], "1")] > 1.0
    )  # 族群同一個 z；最後一天爆量
    assert by[(D[8], "1")] is None  # 近 5 天全 0 → 標準差 0 → null


def test_novelty_and_mops_novelty_from_item_sims() -> None:
    grid = nf.dense_grid(["1"], D[0], D[5])
    sims = pl.DataFrame(
        {
            "item_id": ["a", "b", "c"],
            "stock_id": ["1", "1", "1"],
            "day": [D[1], D[1], D[2]],
            "max_sim": [0.2, None, 0.9],
        },
        schema={"item_id": pl.String, "stock_id": pl.String, "day": pl.Date, "max_sim": pl.Float64},
    )
    nov = nf.novelty_features(grid, sims, recent=2).sort("day")["novelty"].to_list()
    assert nov[0] is None and nov[1] == pytest.approx(0.8) and nov[2] == pytest.approx(0.1)
    assert nov[3] == pytest.approx(0.1) and nov[4] is None
    mn = nf.mops_novelty_features(grid, sims, max_age=2).sort("day")["mops_novelty"].to_list()
    assert mn[1] == pytest.approx(0.8) and mn[2] == pytest.approx(0.1)
    assert mn[3] == pytest.approx(0.1) and mn[4] == pytest.approx(0.1) and mn[5] is None


def test_item_max_sims_windows_and_need_mask() -> None:
    v = np.array([[1, 0], [0, 1], [1, 0], [0.6, 0.8]], dtype=np.float32)
    days = np.array([1, 1, 3, 10])
    out = nf.item_max_sims(days, v, np.array([True, True, True, True]), base_days=5)
    assert np.isnan(out[0]) and np.isnan(out[1])  # 同一天的不算舊聞
    assert out[2] == pytest.approx(1.0)  # 第 3 天 vs 第 1 天的 [1,0]
    assert np.isnan(out[3])  # 第 10 天：[5, 9] 沒有新聞
    only = nf.item_max_sims(days, v, np.array([False, False, True, False]), base_days=5)
    assert np.isnan(only[0]) and only[2] == pytest.approx(1.0)
    with pytest.raises(ValueError, match="升冪"):
        nf.item_max_sims(np.array([3, 1]), v[:2], np.array([True, True]), base_days=5)


def test_topic_features_uses_mean_vector_of_window() -> None:
    v = np.array([[1, 0], [0, 1]], dtype=np.float32)
    days = np.array([D[0].toordinal(), D[1].toordinal()])
    centers = np.array([[1, 0], [0, 1]], dtype=np.float32)
    out = nf.topic_features(days, v, centers, recent=2, stock_id="1").sort("day")
    assert out["day"].to_list() == [D[0], D[1], D[2]]
    assert out["topic_01"].to_list() == pytest.approx([1.0, math.sqrt(0.5), 0.0])
    assert out["topic_02"].to_list() == pytest.approx([0.0, math.sqrt(0.5), 1.0])
    assert (
        nf.topic_features(np.array([]), np.zeros((0, 2)), centers, recent=2, stock_id="1").height
        == 0
    )


def test_fit_topics_refuses_tiny_corpus() -> None:
    with pytest.raises(DataError, match="不夠"):
        nf.fit_topics(np.zeros((5, 3), dtype=np.float32), k=2, seed=1)


def test_assemble_only_trading_days_with_cutoff_available_at() -> None:
    grid = nf.dense_grid(["1"], D[0], D[3])
    counts = pl.DataFrame(
        {"day": [D[1]], "stock_id": ["1"], "n": [1], "n_mops": [0]},
        schema={"day": pl.Date, "stock_id": pl.String, "n": pl.Int64, "n_mops": pl.Int64},
    )
    cf = nf.count_features(grid, counts, short=1, mid=1, base=1, mops=1)
    nov = pl.DataFrame(
        {"day": [D[1]], "stock_id": ["1"], "novelty": [0.5]},
        schema={"day": pl.Date, "stock_id": pl.String, "novelty": pl.Float64},
    )
    out = nf.assemble(
        [D[1], D[3]], [cf, nov, pl.DataFrame()], cutoff_hour=19, topic_k=2, topic_version="v"
    )
    assert out["date"].to_list() == [D[1], D[3]]
    assert out["available_at"].to_list() == [_ts(D[1], 19), _ts(D[3], 19)]
    assert out["novelty"].to_list() == [0.5, None] and out["count_1d"].to_list()[1] == 0.0
    assert (
        out["sent_1d"].to_list() == [None, None]
        and "topic_02" in out.columns
        and "topic_03" not in out.columns
    )
    assert out["topic_version"].to_list() == ["v", "v"]


def test_cutoff_passed() -> None:
    assert nf.cutoff_passed(D[1], 19, _ts(D[1], 19))
    assert not nf.cutoff_passed(D[1], 19, _ts(D[1], 18, 59))
