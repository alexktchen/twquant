"""T-042：技術型態事件因子——樞紐點基元與 price.double_bottom_120d（STRATEGY §4b）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors import compute as fc
from twquant.factors.patterns import Pivot, confirmed_pivots
from twquant.factors.registry import get, list_factors
from twquant.factors.testing import assert_no_lookahead

_BASE = dt.date(2026, 5, 1)


def _d(i: int) -> dt.date:
    return _BASE + dt.timedelta(days=i)


def _wh(tmp_path: Path, closes: list[float], stock_id: str = "2330") -> Warehouse:
    rows = []
    for i, c in enumerate(closes):
        d = _d(i)
        rows.append(
            {
                "date": d,
                "stock_id": stock_id,
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
    df = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df, source="twse")
    return wh


# W 底基準序列（40 根，預設參數可成立）：
# low1@5=90、頸線樞紐@12=97.5（17 確認）、low2@20=90.5（25 確認；差 0.56%、間隔 15）、
# 突破@28：close 98.5 > 97.5×1.005=97.9875 → 成立日 idx 28，value=(97.5−90.5)/97.5
_W = [
    101.0,
    100.0,
    99.0,
    98.0,
    97.0,  # 0-4 下跌
    90.0,  # 5 低點一
    91.5,
    93.0,
    94.0,
    95.0,
    96.0,
    96.8,  # 6-11 反彈
    97.5,  # 12 頸線樞紐高點
    96.5,
    95.0,
    94.0,
    93.0,
    92.0,
    91.8,
    91.5,  # 13-19 回落
    90.5,  # 20 低點二
    91.5,
    92.5,
    93.5,
    94.5,
    95.5,
    96.5,
    97.0,  # 21-27 反彈未破頸線閾值
    98.5,  # 28 突破 → 成立
    99.0,
    99.5,
    100.0,
    100.5,
    101.0,
    101.5,
    102.0,
    102.5,
    103.0,
    103.5,
    104.0,  # 29-39
]
_W_VALUE = (97.5 - 90.5) / 97.5


def test_registered_metadata() -> None:
    assert "price.double_bottom_120d" in list_factors(category="price")
    fdef = get("price.double_bottom_120d")
    assert fdef.direction == 1
    assert fdef.tables == ("prices_daily",)
    assert get("price.hs_top_120d").direction == -1
    assert get("price.box_breakout_60d").direction == 1


def test_pivot_confirmation_and_tiebreak() -> None:
    # 同值雙低：idx2 與 idx3 皆 3.0 → 只有較早的 idx2 是樞紐，確認日 = idx+k
    xs = [5.0, 4.0, 3.0, 3.0, 4.0, 5.0, 6.0]
    pivots = confirmed_pivots(xs, xs, k=2)
    assert pivots == [Pivot(idx=2, price=3.0, kind="low", confirmed_idx=4)]
    # 樞紐高點：嚴格高於前 k、不低於後 k
    ys = [1.0, 2.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    pivots = confirmed_pivots(ys, ys, k=2)
    assert Pivot(idx=2, price=5.0, kind="high", confirmed_idx=4) in pivots
    assert all(p.kind == "high" for p in pivots)


def test_double_bottom_event_on_breakout_day(tmp_path: Path) -> None:
    wh = _wh(tmp_path, _W)
    out = fc.compute(["price.double_bottom_120d"], wh.view(_d(39))).sort("date")
    # 成立日 = 突破日 idx 28；active_days=10 → 連續 10 列同值；之前無任何列
    assert out.height == 10
    assert out["date"].to_list() == [_d(i) for i in range(28, 38)]
    assert all(v == pytest.approx(_W_VALUE) for v in out["value"].to_list())


def test_double_bottom_truncated_view_partial_emission(tmp_path: Path) -> None:
    wh = _wh(tmp_path, _W)
    out = fc.compute(["price.double_bottom_120d"], wh.view(_d(30)))
    # as_of 停在 idx 30 → 只輸出 28/29/30 三列
    assert out["date"].to_list() == [_d(28), _d(29), _d(30)]
    # 突破前一日（idx 27）完全無列
    assert fc.compute(["price.double_bottom_120d"], wh.view(_d(27))).height == 0


def test_low_tol_rejected(tmp_path: Path) -> None:
    # 低點二改 94.0 → 與 90.0 差 4.4% > low_tol 3%，其餘條件皆備 → 不成立
    closes = [
        101.0,
        100.0,
        99.0,
        98.0,
        97.0,
        90.0,
        91.5,
        93.0,
        94.0,
        95.0,
        96.0,
        96.8,
        97.5,
        96.5,
        96.0,
        95.6,
        95.2,
        94.8,
        94.6,
        94.4,
        94.0,
        94.7,
        95.2,
        95.8,
        96.4,
        97.0,
        97.3,
        97.6,
        98.5,
        99.0,
        99.5,
        100.0,
        100.5,
        101.0,
    ]
    wh = _wh(tmp_path, closes)
    out = fc.compute(["price.double_bottom_120d"], wh.view(_d(len(closes) - 1)))
    assert out.height == 0


def test_min_gap_rejected(tmp_path: Path) -> None:
    # 兩低點間隔 10 根 < min_gap_bars 15，其餘條件皆備 → 不成立
    closes = [
        101.0,
        100.0,
        99.0,
        98.0,
        97.0,
        90.0,
        91.5,
        93.0,
        94.5,
        97.5,
        96.5,
        95.5,
        94.0,
        92.0,
        91.0,
        90.3,
        91.5,
        92.5,
        94.0,
        95.5,
        96.5,
        97.0,
        98.5,
        99.0,
        99.5,
        100.0,
        100.5,
    ]
    wh = _wh(tmp_path, closes)
    out = fc.compute(["price.double_bottom_120d"], wh.view(_d(len(closes) - 1)))
    assert out.height == 0


def test_flat_series_no_rows(tmp_path: Path) -> None:
    wh = _wh(tmp_path, [100.0] * 40)
    out = fc.compute(["price.double_bottom_120d"], wh.view(_d(39)))
    assert out.height == 0
    assert out.schema["value"] == pl.Float64


def test_zero_price_rows_dropped(tmp_path: Path) -> None:
    # 停牌/無成交列（價格 0）不得造成除零；比照 price.py 的 > 0 防衛剔除
    closes = [100.0] * 10 + [0.0] + [100.0] * 10
    wh = _wh(tmp_path, closes)
    for name in ("price.double_bottom_120d", "price.hs_top_120d", "price.box_breakout_60d"):
        assert fc.compute([name], wh.view(_d(len(closes) - 1))).height == 0


def test_no_lookahead_double_bottom(tmp_path: Path) -> None:
    wh = _wh(tmp_path, _W)
    # 切點涵蓋：低點二未確認、已確認未突破、成立日、active 期間、期後
    cuts = [_d(22), _d(26), _d(28), _d(33), _d(39)]
    assert_no_lookahead("price.double_bottom_120d", wh, cuts)


# 頭肩頂基準序列（48 根）：H1@5=100（10 確認）、L1@10=90、H2@16=108、L2@22=90.5、
# H3@28=101（33 確認）；頸線 = (90+90.5)/2 = 90.25，跌破閾值 90.25×0.995 = 89.79875
# → idx 37 close 89.5 首破 → 成立，value = (108−90.25)/90.25
_HS = [
    90.0,
    92.0,
    94.0,
    96.0,
    98.0,
    100.0,  # 0-5 升至左肩
    97.0,
    95.0,
    93.0,
    91.0,
    90.0,  # 6-10 回落至左谷
    93.0,
    96.0,
    99.0,
    102.0,
    105.0,
    108.0,  # 11-16 升至頭
    104.0,
    100.0,
    96.0,
    93.0,
    91.0,
    90.5,  # 17-22 回落至右谷
    93.0,
    95.0,
    97.0,
    99.0,
    100.5,
    101.0,  # 23-28 升至右肩
    99.0,
    97.0,
    95.0,
    93.0,
    91.5,  # 29-33 回落
    91.0,
    90.5,
    90.3,  # 34-36 逼近頸線未破
    89.5,  # 37 跌破 → 成立
    89.0,
    88.5,
    88.0,
    87.5,
    87.0,
    86.5,
    86.0,
    85.5,
    85.0,
    84.5,  # 38-47
]
_HS_VALUE = (108.0 - 90.25) / 90.25


def test_hs_top_event_on_break_day(tmp_path: Path) -> None:
    wh = _wh(tmp_path, _HS)
    out = fc.compute(["price.hs_top_120d"], wh.view(_d(47))).sort("date")
    assert out.height == 10
    assert out["date"].to_list() == [_d(i) for i in range(37, 47)]
    assert all(v == pytest.approx(_HS_VALUE) for v in out["value"].to_list())


def test_hs_top_shoulder_margin_rejected(tmp_path: Path) -> None:
    # 頭改 101.5：對兩肩 100/101 皆不足 +2% → 不成立（其餘結構同 _HS）
    closes = list(_HS)
    closes[11:17] = [93.0, 95.0, 97.0, 99.0, 100.7, 101.5]
    closes[17:22] = [100.0, 98.0, 95.0, 93.0, 91.0]
    wh = _wh(tmp_path, closes)
    out = fc.compute(["price.hs_top_120d"], wh.view(_d(len(closes) - 1)))
    assert out.height == 0


def test_no_lookahead_hs_top(tmp_path: Path) -> None:
    wh = _wh(tmp_path, _HS)
    # 切點涵蓋：右肩未確認、已確認未跌破、成立日、active 期間、期後
    cuts = [_d(30), _d(35), _d(37), _d(42), _d(47)]
    assert_no_lookahead("price.hs_top_120d", wh, cuts)


def _box_series(base: float) -> list[float]:
    # 0-64 箱體震盪 base..base+5；65 跳空突破 106；66-79 持平（箱含 106 後不再觸發）
    xs = [base + (i % 6) for i in range(65)]
    xs.append(106.0)
    xs.extend([106.0] * 14)
    return xs


def test_box_breakout_event_and_value(tmp_path: Path) -> None:
    # 箱體 98..103（寬 5.1% ≤ 12%）；65 日 close 106 > 103×1.005 → 成立，值=突破幅度
    wh = _wh(tmp_path, _box_series(98.0))
    out = fc.compute(["price.box_breakout_60d"], wh.view(_d(79))).sort("date")
    assert out.height == 10
    assert out["date"].to_list() == [_d(i) for i in range(65, 75)]
    assert all(v == pytest.approx(106.0 / 103.0 - 1.0) for v in out["value"].to_list())


def test_box_too_wide_rejected(tmp_path: Path) -> None:
    # 箱體 90..103（寬 14.4% > 12%）→ 同樣的突破不成立
    xs = [90.0 + (i % 14) for i in range(65)]
    xs.append(106.0)
    xs.extend([106.0] * 14)
    wh = _wh(tmp_path, xs)
    out = fc.compute(["price.box_breakout_60d"], wh.view(_d(79)))
    assert out.height == 0


def test_no_lookahead_box_breakout(tmp_path: Path) -> None:
    wh = _wh(tmp_path, _box_series(98.0))
    cuts = [_d(64), _d(65), _d(70), _d(79)]
    assert_no_lookahead("price.box_breakout_60d", wh, cuts)
