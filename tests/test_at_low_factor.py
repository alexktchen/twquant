"""T-366／D-184：price.at_low_20——收盤是不是近 20 個有收盤的交易日最低（還原除權息、同價也算）。"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from pathlib import Path

import polars as pl

import twquant.factors.builtins  # noqa: F401
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors.compute import compute
from twquant.factors.testing import assert_no_lookahead

BASE = dt.date(2026, 1, 5)


def _day(i: int) -> dt.date:
    return BASE + dt.timedelta(days=i)


def _wh(
    tmp_path: Path,
    closes: Mapping[str, Sequence[float | None]],
    events: tuple[tuple[str, int, float], ...] = (),
) -> Warehouse:
    rows = []
    for sid, cs in closes.items():
        for i, c in enumerate(cs):
            if c is None:  # 停牌：那天沒有列
                continue
            d = _day(i)
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": c * 1000,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
                }
            )
    wh = Warehouse(tmp_path)
    wh.write(
        "clean",
        "prices_daily",
        pl.DataFrame(rows).with_columns(
            pl.col("available_at").cast(schemas.TW_DT),
            pl.col("limit_up_price").cast(pl.Float64),
            pl.col("limit_down_price").cast(pl.Float64),
        ),
        source="twse",
    )
    # 一筆遠在窗口之前、別檔的除息：讓事件表永遠存在（空表與「窗口內沒事件」是兩回事）
    ev = [("9999", -400, 0.9), *events]
    wh.write(
        "clean",
        "corporate_events",
        pl.DataFrame(
            [
                {
                    "date": _day(i),
                    "stock_id": sid,
                    "event_type": "cash_dividend",
                    "adj_factor": f,
                    "ref_price": None,
                    "available_at": dt.datetime(
                        _day(i).year, _day(i).month, _day(i).day, 8, tzinfo=TZ
                    ),
                }
                for sid, i, f in ev
            ]
        ).with_columns(
            pl.col("available_at").cast(schemas.TW_DT), pl.col("ref_price").cast(pl.Float64)
        ),
    )
    return wh


def _val(wh: Warehouse, day: int, sid: str) -> float | None:
    d = _day(day)
    out = compute(["price.at_low_20"], wh.view(d), dates=[d]).filter(pl.col("stock_id") == sid)
    return None if out.is_empty() else float(out["value"][0])


def test_ac1_new_low_tie_and_not_low(tmp_path: Path) -> None:
    wh = _wh(
        tmp_path,
        {
            "1101": [60.0] * 20 + [58.0, 57.0, 56.0, 55.0],  # 一路破底
            "2330": [60.0] * 10 + [50.0] + [60.0] * 10 + [55.0, 50.0],  # 同價也算
            "2454": [60.0] * 10 + [40.0] + [60.0] * 10 + [55.0, 50.0],  # 窗口內有更低的
            "2603": [40.0] + [60.0] * 19 + [58.0, 57.0, 56.0, 55.0],  # 20 天以前更低不算
        },
    )
    assert _val(wh, 23, "1101") == 1.0
    assert _val(wh, 22, "2330") == 1.0
    assert _val(wh, 22, "2454") == 0.0
    assert _val(wh, 23, "2603") == 1.0
    assert _val(wh, 19, "1101") == 1.0  # 前 20 天都 60：當天收盤等於最低
    assert _val(wh, 21, "2330") == 0.0


def test_ac2_needs_twenty_closes_and_skips_missing_days(tmp_path: Path) -> None:
    wh = _wh(
        tmp_path,
        {
            "1101": [60.0, 59.0, 58.0, 57.0, 56.0],  # 只有 5 天：不給值
            "2330": [*([60.0] * 20), None, None, 58.0],  # 停牌兩天：比的是有收盤的 20 天
        },
    )
    assert _val(wh, 4, "1101") is None
    assert _val(wh, 22, "2330") == 1.0


def test_ac3_ex_dividend_gap_is_not_a_new_low(tmp_path: Path) -> None:
    # 除息 5%：參考價 95，當天收 95.5——還原後是小漲
    closes = {"1101": [100.0] * 22 + [95.5]}
    raw = _wh(tmp_path / "raw", closes)
    assert _val(raw, 22, "1101") == 1.0, "沒有事件時，跳空就是破底"
    adj = _wh(tmp_path / "adj", closes, (("1101", 22, 0.95),))
    assert _val(adj, 22, "1101") == 0.0
    # 還原後真的跌破才算：除息後收 94（還原 98.9 < 100）
    deeper = _wh(tmp_path / "deep", {"1101": [100.0] * 22 + [94.0]}, (("1101", 22, 0.95),))
    assert _val(deeper, 22, "1101") == 1.0


def test_ac4_no_lookahead_and_trim_equivalence(tmp_path: Path) -> None:
    import math

    closes = {
        sid: [100.0 + 10.0 * math.sin(i / (3.0 + k)) - 0.1 * i for i in range(90)]
        for k, sid in enumerate(("1101", "2330", "2454"))
    }
    wh = _wh(tmp_path, closes, (("2330", 40, 0.97), ("2454", 70, 0.9)))
    assert_no_lookahead("price.at_low_20", wh, [_day(i) for i in (30, 41, 55, 71, 89)])
    for i in (40, 70, 89):
        d = _day(i)
        full = compute(["price.at_low_20"], wh.view(d), dates=[d]).sort("stock_id")
        trimmed = compute(["price.at_low_20"], wh.view(d), dates=[d], trim_lookback=True).sort(
            "stock_id"
        )
        assert full.equals(trimmed), d
    again = compute(["price.at_low_20"], wh.view(_day(89)), dates=[_day(89)]).sort("stock_id")
    assert again.equals(
        compute(["price.at_low_20"], wh.view(_day(89)), dates=[_day(89)]).sort("stock_id")
    )
