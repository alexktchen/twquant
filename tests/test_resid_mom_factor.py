"""D-020：price.resid_mom_20d（beta 調整殘差動能）取代橫斷面同構的 rs_market_20d。

驗證：(1) 舊 rs_market_20d 已退役、新因子註冊/方向；(2) 手算 β 與殘差值；(3) 與 mom_20d
標準化後**不同構**（對比：舊 rs 的常數平移版標準化後與 mom 同構）；(4) 市場零變異 β 守門
不洩 NaN；(5) 反前視。小 fixture、精確斷言，beta_window 用小值以短序列覆蓋。
"""

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
from twquant.factors.registry import get, list_factors
from twquant.factors.standardize import standardize
from twquant.factors.testing import assert_no_lookahead


def _wh(
    tmp_path: Path,
    closes: dict[str, list[float]],
    index_closes: list[float],
) -> Warehouse:
    """以 {stock_id: [close...]} + 大盤 [close...] 建 prices_daily + index_daily（逐日連續）。"""
    base = dt.date(2026, 5, 1)
    n = len(index_closes)
    rows = []
    for sid, cs in closes.items():
        assert len(cs) == n, sid
        for i, c in enumerate(cs):
            d = base + dt.timedelta(days=i)
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
    px = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    idx_rows = []
    for i, c in enumerate(index_closes):
        d = base + dt.timedelta(days=i)
        idx_rows.append(
            {
                "date": d,
                "index_id": "TAIEX",
                "open": None,
                "high": None,
                "low": None,
                "close": c,
                "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
            }
        )
    idx = pl.DataFrame(idx_rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("open").cast(pl.Float64),
        pl.col("high").cast(pl.Float64),
        pl.col("low").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", px, source="twse")
    wh.write("clean", "index_daily", idx, source="twse")
    return wh


def test_ac_rs_market_retired_resid_mom_registered() -> None:
    names = list_factors()
    assert "price.rs_market_20d" not in names  # D-020：橫斷面同構，已退役
    assert "price.resid_mom_20d" in names
    assert "price.resid_mom_20d" in list_factors(category="price")
    assert get("price.resid_mom_20d").direction == 1
    assert get("price.resid_mom_20d").tables == ("prices_daily", "index_daily")


def test_resid_mom_manual_beta_and_value() -> None:
    """手算：window=2、beta_window=3，單股單日對照 β=Cov/Var、殘差=s20−β·m20。"""
    from twquant.factors.price import resid_mom_20d

    # 6 日大盤與個股收盤；末日 (t=5) 手算
    idx = [100.0, 101.0, 103.0, 102.0, 104.0, 107.0]
    stk = [50.0, 50.5, 52.0, 51.5, 53.0, 55.0]
    wh = _wh(Path(_tmp()), {"2330": stk}, idx)
    out = resid_mom_20d(wh.view(dt.date(2026, 5, 6)), {"window": 2, "beta_window": 3})
    val = out.filter(pl.col("date") == dt.date(2026, 5, 6))["value"].item()

    # 手算日報酬
    r = [stk[i] / stk[i - 1] - 1.0 for i in range(1, 6)]  # r[k] 對應 t=k+1
    m = [idx[i] / idx[i - 1] - 1.0 for i in range(1, 6)]
    # β：末 3 日日報酬（t=3,4,5 → 索引 r[2],r[3],r[4]）Cov/Var（母體，ddof=0，同 rolling_mean 定義）
    rr, mm = r[2:5], m[2:5]
    mr = sum(a * b for a, b in zip(rr, mm, strict=True)) / 3
    er = sum(rr) / 3
    em = sum(mm) / 3
    emm = sum(b * b for b in mm) / 3
    beta = (mr - er * em) / (emm - em * em)
    s20 = stk[5] / stk[3] - 1.0
    m20 = idx[5] / idx[3] - 1.0
    assert val == pytest.approx(s20 - beta * m20, rel=1e-9, abs=1e-12)


def _affine_closes(
    betas: dict[str, tuple[float, float]], mkt_ret: list[float]
) -> tuple[dict[str, list[float]], list[float]]:
    """由市場日報酬與每股 (β, drift) 造收盤：r_i,d = β_i·m_d + c_i（殘差=0 → β 可被精確估回）。

    首日收盤設基準（無當日報酬），其後逐日以 r_i,d 複利。回傳 ({sid: closes}, index_closes)。
    """
    n = len(mkt_ret) + 1
    idx = [8000.0]
    for m in mkt_ret:
        idx.append(idx[-1] * (1.0 + m))
    closes: dict[str, list[float]] = {}
    for sid, (beta, drift) in betas.items():
        c = [100.0]
        for m in mkt_ret:
            c.append(c[-1] * (1.0 + beta * m + drift))
        assert len(c) == n
        closes[sid] = c
    return closes, idx


def test_resid_mom_not_isomorphic_to_mom() -> None:
    """核心：β 相異、raw-mom 與 β 去相關 + 大盤有走勢 → resid_mom 標準化後與 mom_20d **顯著
    不同**（重排序，非常數平移）；對比舊 rs（減同一大盤常數）標準化後與 mom **同構**。"""
    from twquant.factors.price import resid_mom_20d

    # 大盤走勢（正向、有變異 → Var>0），重複樣式湊足 beta_window+window+1 日
    pattern = [0.02, -0.005, 0.015, 0.0, 0.012, -0.008]
    mkt_ret = (pattern * 6)[:32]  # 33 日收盤
    # 三檔：高 β 小 drift、低 β 高 drift、中 β 負 drift → mom 與 β 排序不一致
    betas = {"1111": (2.2, 0.0), "2222": (0.2, 0.006), "3333": (1.1, -0.003)}
    closes, idx = _affine_closes(betas, mkt_ret)
    wh = _wh(Path(_tmp()), closes, idx)
    last = dt.date(2026, 5, 1) + dt.timedelta(days=len(idx) - 1)
    view = wh.view(last)

    resid = resid_mom_20d(view, {"window": 20, "beta_window": 20}).with_columns(
        pl.lit("price.resid_mom_20d").alias("factor")
    )
    mom = fc.compute(["price.mom_20d"], view)

    # 無 NaN / inf
    bad = resid.select(pl.col("value").is_nan().sum() + pl.col("value").is_infinite().sum()).item()
    assert bad == 0

    zr_df = standardize(resid.filter(pl.col("date") == last)).sort("stock_id")
    zm_df = standardize(mom.filter(pl.col("date") == last)).sort("stock_id")
    zr = zr_df["value"].to_list()
    zm = zm_df["value"].to_list()
    assert len(zr) == 3 and len(zm) == 3
    # resid 與 mom 標準化後顯著不同（獨立資訊，非常數平移）
    assert max(abs(x - y) for x, y in zip(zr, zm, strict=True)) > 1e-2
    # 且橫斷面排序被 β 調整改變（高 β 檔被下修）
    order_mom = zm_df.sort("value")["stock_id"].to_list()
    order_res = zr_df.sort("value")["stock_id"].to_list()
    assert order_mom != order_res

    # 對比：舊 rs = mom − 同一大盤 20d 報酬（常數平移）→ 標準化後與 mom 同構
    mret = idx[-1] / idx[-21] - 1.0
    rs = mom.filter(pl.col("date") == last).with_columns(value=pl.col("value") - mret)
    zrs = standardize(rs).sort("stock_id")["value"].to_list()
    assert max(abs(x - y) for x, y in zip(zrs, zm, strict=True)) < 1e-9


def test_resid_mom_market_zero_variance_guard() -> None:
    """大盤在 beta_window 內零變異（收盤全同）→ Var=0 → β 未定義 → 該列不產生（不洩 NaN）。"""
    from twquant.factors.price import resid_mom_20d

    n = 10
    idx = [100.0] * n  # 大盤完全不動 → 日報酬全 0 → Var=0
    stk = [50.0 + i for i in range(n)]
    wh = _wh(Path(_tmp()), {"2330": stk}, idx)
    out = resid_mom_20d(wh.view(dt.date(2026, 5, 10)), {"window": 2, "beta_window": 3})
    # β 全程未定義 → 無任何列；且不得含 NaN/inf
    assert out.is_empty()


def test_resid_mom_drops_nonfinite_from_zero_close() -> None:
    """退化價（close=0，schema 允許 _nonneg）產生 0/0=NaN 或 x/0=inf → 該股不產生值、
    絕不外洩非有限值污染整個橫斷面標準化（實測 2248 曾以單一 NaN 使 resid_mom 整日消失）。"""
    from twquant.factors.price import resid_mom_20d

    idx = [8000.0, 8010.0, 8020.0, 8030.0, 8040.0, 8050.0]
    good = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0]
    bad = [50.0, 51.0, 0.0, 0.0, 52.0, 53.0]  # 連兩日 0 → 0/0=NaN、x/0=inf
    wh = _wh(Path(_tmp()), {"1111": good, "9999": bad}, idx)
    last = dt.date(2026, 5, 6)
    out = resid_mom_20d(wh.view(last), {"window": 2, "beta_window": 3})
    # 核心：輸出無任何 NaN/inf
    assert out.select(pl.col("value").is_finite().all()).item()
    at_last = out.filter(pl.col("date") == last)["stock_id"].to_list()
    assert "1111" in at_last  # 正常股仍在
    assert "9999" not in at_last  # 退化股在該日不產生值（非有限）


def test_no_lookahead_resid_mom() -> None:
    n = 30
    idx = [100.0 + (i % 5) + 0.3 * i for i in range(n)]
    a = [50.0 + (i % 3) + 0.2 * i for i in range(n)]
    b = [80.0 + (i % 4) - 0.1 * i for i in range(n)]
    wh = _wh(Path(_tmp()), {"1111": a, "2222": b}, idx)
    cuts = [dt.date(2026, 5, 24), dt.date(2026, 5, 27), dt.date(2026, 5, 30)]
    assert_no_lookahead("price.resid_mom_20d", wh, cuts)


def _tmp() -> str:
    """每次呼叫給一個獨立暫存夾（避開 fixture 樣板、與其他測試檔一致的簡單做法）。"""
    import tempfile

    return tempfile.mkdtemp(prefix="twq_resid_")
