"""PAPERS-SHORTTERM P0 短線因子：隔夜/盤中分解、產業殘差反轉、1 日動能。"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.clean import clean_stock_meta
from twquant.data.sources import raw_frame
from twquant.data.warehouse import Warehouse
from twquant.factors import compute as fc
from twquant.factors.registry import get, list_factors
from twquant.factors.testing import assert_no_lookahead

_ISIN_FIELDS = ["code_name", "isin", "list_date", "market", "industry", "cfi", "note"]


def _wh(tmp_path: Path, bars: dict[str, list[tuple[float, float]]]) -> Warehouse:
    """以 {stock_id: [(open, close), ...]} 建 prices_daily（每股同一起始日、逐日連續）。"""
    base = dt.date(2026, 5, 1)
    rows = []
    for sid, ocs in bars.items():
        for i, (o, c) in enumerate(ocs):
            d = base + dt.timedelta(days=i)
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": o,
                    "high": max(o, c),
                    "low": min(o, c),
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


def _write_meta(wh: Warehouse, industries: dict[str, str]) -> None:
    """{stock_id: 產業別} → stock_meta（走真實 cleaner，與正式 ingest 同路徑）。"""
    rows = [
        [f"{sid}　測試{sid}", "x", "2000/01/01", "上市", ind, "y", ""]
        for sid, ind in industries.items()
    ]
    wh.write("clean", "stock_meta", clean_stock_meta(raw_frame(_ISIN_FIELDS, rows), "TWSE"))


def test_registered_and_directions() -> None:
    names = list_factors()
    for n in (
        "price.overnight_ret_20d",
        "price.intraday_ret_20d",
        "price.residual_reversal_5d",
        "price.mom_1d",
    ):
        assert n in names, n
    assert get("price.overnight_ret_20d").direction == -1
    assert get("price.intraday_ret_20d").direction == 1
    assert get("price.residual_reversal_5d").direction == -1
    assert get("price.mom_1d").direction == 1


def test_overnight_intraday_values(tmp_path: Path) -> None:
    # 開盤跳空 +5%、盤中再走 +5%/持平交錯，window=2 手算驗證
    from twquant.factors.price import intraday_ret_20d, overnight_ret_20d

    bars = {"2330": [(99.0, 100.0), (105.0, 110.0), (115.5, 121.0)]}
    wh = _wh(tmp_path, bars)
    view = wh.view(dt.date(2026, 5, 3))
    on = overnight_ret_20d(view, {"window": 2}).sort("date")
    # 第 3 日：ln(105/100) + ln(115.5/110) = 2·ln(1.05)
    assert on["value"].to_list()[-1] == pytest.approx(2 * math.log(1.05))
    intra = intraday_ret_20d(view, {"window": 2}).sort("date")
    # 第 3 日：ln(110/105) + ln(121/115.5)
    assert intra["value"].to_list()[-1] == pytest.approx(
        math.log(110 / 105) + math.log(121 / 115.5)
    )


def test_overnight_plus_intraday_is_total_return(tmp_path: Path) -> None:
    # 分解恆等式：Σ隔夜 + Σ盤中 = ln(close_t / close_{t-w})
    from twquant.factors.price import intraday_ret_20d, overnight_ret_20d

    bars = {"2330": [(100.0, 102.0), (101.0, 99.0), (100.5, 107.0), (109.0, 104.0)]}
    wh = _wh(tmp_path, bars)
    view = wh.view(dt.date(2026, 5, 4))
    w = 3
    on = overnight_ret_20d(view, {"window": w}).sort("date")["value"].to_list()[-1]
    intra = intraday_ret_20d(view, {"window": w}).sort("date")["value"].to_list()[-1]
    assert on + intra == pytest.approx(math.log(104.0 / 102.0))


def test_residual_reversal_industry_demean(tmp_path: Path) -> None:
    # 同產業 A +10%、B −10% → 殘差 ±0.1；單一成員產業 C → 殘差 0
    from twquant.factors.price import residual_reversal_5d

    bars = {
        "2330": [(100.0, 100.0), (100.0, 100.0), (110.0, 110.0)],
        "2317": [(100.0, 100.0), (100.0, 100.0), (90.0, 90.0)],
        "1101": [(100.0, 100.0), (100.0, 100.0), (105.0, 105.0)],
    }
    wh = _wh(tmp_path, bars)
    _write_meta(wh, {"2330": "半導體業", "2317": "半導體業", "1101": "水泥工業"})
    out = residual_reversal_5d(wh.view(dt.date(2026, 5, 3)), {"window": 2})
    last = {
        r["stock_id"]: r["value"]
        for r in out.filter(pl.col("date") == dt.date(2026, 5, 3)).to_dicts()
    }
    assert last["2330"] == pytest.approx(0.1)
    assert last["2317"] == pytest.approx(-0.1)
    assert last["1101"] == pytest.approx(0.0)


def test_residual_reversal_skips_null_industry(tmp_path: Path) -> None:
    # 無產業別（如 ETF）→ inner join 剔除，不產生殘差值
    from twquant.factors.price import residual_reversal_5d

    bars = {
        "2330": [(100.0, 100.0), (100.0, 100.0), (110.0, 110.0)],
        "0050": [(100.0, 100.0), (100.0, 100.0), (103.0, 103.0)],
    }
    wh = _wh(tmp_path, bars)
    _write_meta(wh, {"2330": "半導體業", "0050": ""})  # 空產業別 → cleaner 轉 null
    out = residual_reversal_5d(wh.view(dt.date(2026, 5, 3)), {"window": 2})
    assert set(out["stock_id"].to_list()) == {"2330"}


def test_mom_1d_value(tmp_path: Path) -> None:
    bars = {"2330": [(100.0, 100.0), (109.0, 110.0)]}
    wh = _wh(tmp_path, bars)
    out = fc.compute(["price.mom_1d"], wh.view(dt.date(2026, 5, 2)))
    row = out.filter(pl.col("date") == dt.date(2026, 5, 2)).to_dicts()[0]
    assert row["value"] == pytest.approx(0.1)


def _geom_bars(a: float, b: float, n: int, c0: float = 100.0) -> list[tuple[float, float]]:
    """固定隔夜比 a、盤中比 b 的 (open, close) 序列：open_t/close_{t-1}=a、close_t/open_t=b。"""
    bars = [(c0 / b, c0)]  # day0：close0=c0、open0=c0/b（盤中比仍為 b）
    close = c0
    for _ in range(1, n):
        o = close * a
        close = o * b
        bars.append((o, close))
    return bars


def test_registered_window_defaults(tmp_path: Path) -> None:
    """經 fc.compute()（走 fdef.params 註冊預設）驗值，守住 _20d/_5d/_1d 的 window。

    直呼因子並帶顯式 window 的測試繞過了註冊預設；若某 '_20d' 因子誤註冊成別的 window
    仍會綠燈。此測試把 4 因子全走 production 路徑（compute→fdef.params），釘死預設值。
    """
    a, b = 1.01, 1.005
    bars_a = _geom_bars(a, b, 25)
    bars_b = _geom_bars(0.995, 1.002, 25)
    wh = _wh(tmp_path, {"2330": bars_a, "2317": bars_b})
    _write_meta(wh, {"2330": "半導體業", "2317": "半導體業"})
    last = dt.date(2026, 5, 25)  # base(5/1)+24
    view = wh.view(last)

    def _val(name: str, sid: str) -> float:
        out = fc.compute([name], view)
        row = out.filter((pl.col("date") == last) & (pl.col("stock_id") == sid)).to_dicts()
        assert row, f"{name} 在 {last} 無 {sid} 值（window 預設可能被改大導致樣本不足）"
        return float(row[0]["value"])

    # 隔夜/盤中：window=20 → 末日累計 = 20·ln(比值)（若被改成 4 則為 4·ln，測試爆）
    assert _val("price.overnight_ret_20d", "2330") == pytest.approx(20 * math.log(a))
    assert _val("price.intraday_ret_20d", "2330") == pytest.approx(20 * math.log(b))
    # 1 日動能：window=1 → close_t/close_{t-1}-1 = a·b-1
    assert _val("price.mom_1d", "2330") == pytest.approx(a * b - 1.0)
    # 產業殘差反轉：window=5 → 個股 5 日報酬 − 同業等權；殘差 = (ret_A-ret_B)/2
    ret_a = (a * b) ** 5 - 1.0
    ret_b = (0.995 * 1.002) ** 5 - 1.0
    assert _val("price.residual_reversal_5d", "2330") == pytest.approx((ret_a - ret_b) / 2)


def test_no_lookahead_shortterm(tmp_path: Path) -> None:
    bars = {
        "2330": [(100.0 + (i % 3), 101.0 + (i % 4)) for i in range(30)],
        "2317": [(50.0 + (i % 4), 50.5 + (i % 3)) for i in range(30)],
    }
    wh = _wh(tmp_path, bars)
    _write_meta(wh, {"2330": "半導體業", "2317": "半導體業"})
    cuts = [dt.date(2026, 5, 24), dt.date(2026, 5, 27), dt.date(2026, 5, 30)]
    for name in (
        "price.overnight_ret_20d",
        "price.intraday_ret_20d",
        "price.residual_reversal_5d",
        "price.mom_1d",
    ):
        assert_no_lookahead(name, wh, cuts)
