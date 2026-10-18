"""每檔標的的權證摘要（T-385／D-191 方向 4）：認購／認售成交值、權證成交值對標的成交值、價平隱含波動。

純函數：吃當天行情、每檔權證最新一版條款、標的當天成交金額，回一張每檔標的一列的表；落地由 CLI 負責（黃金規則 10）。
隱含波動用 Black-Scholes（無股利）反解，參數在 configs/system.yaml `warrants`。這是給人看的參考值，不進因子、不進模型。
"""

from __future__ import annotations

import datetime as dt
import math

import polars as pl

from twquant.config import WarrantConfig
from twquant.conventions import TZ

SUMMARY_TABLE = "warrant_summary_daily"
SUMMARY_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date(),
    "underlying_id": pl.Utf8(),
    "n_call": pl.Int64(),  # 當天有行情的認購檔數
    "n_put": pl.Int64(),
    "call_value": pl.Float64(),  # 認購成交金額（元）
    "put_value": pl.Float64(),
    "underlying_amount": pl.Float64(),  # 標的當天成交金額（元）；沒有 → null
    "value_ratio": pl.Float64(),  # （認購＋認售成交金額）÷ 標的成交金額
    "iv_call": pl.Float64(),  # 價平認購隱含波動（年化，小數）；算不出來 null
    "iv_put": pl.Float64(),
    "iv_call_days": pl.Int64(),  # 用來算的那檔距到期天數
    "iv_put_days": pl.Int64(),
    "available_at": pl.Datetime("us", "Asia/Taipei"),
}

_SIGMA_LO, _SIGMA_HI, _ITERS = 0.01, 5.0, 100


def _ncdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(kind: str, s: float, k: float, t: float, r: float, sigma: float) -> float:
    """Black-Scholes 歐式價格（無股利）。t 年、r 與 sigma 年化。"""
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        raise ValueError(f"bs_price 參數不合法：s={s} k={k} t={t} sigma={sigma}")
    d1 = (math.log(s / k) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    if kind == "call":
        return s * _ncdf(d1) - k * math.exp(-r * t) * _ncdf(d2)
    if kind == "put":
        return k * math.exp(-r * t) * _ncdf(-d2) - s * _ncdf(-d1)
    raise ValueError(f"kind 必須是 call／put：{kind!r}")


def implied_vol(kind: str, price: float, s: float, k: float, t: float, r: float) -> float | None:
    """二分法反解年化隱含波動；價格低於無套利下界或高於上界、或超出 [1%, 500%] → None。"""
    if t <= 0 or s <= 0 or k <= 0 or price <= 0:
        return None
    lo_px = bs_price(kind, s, k, t, r, _SIGMA_LO)
    hi_px = bs_price(kind, s, k, t, r, _SIGMA_HI)
    if price <= lo_px or price >= hi_px:
        return None
    lo, hi = _SIGMA_LO, _SIGMA_HI
    for _ in range(_ITERS):
        mid = 0.5 * (lo + hi)
        if bs_price(kind, s, k, t, r, mid) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def _atm_iv(
    rows: list[dict[str, object]], kind: str, day: dt.date, cfg: WarrantConfig
) -> tuple[float | None, int | None]:
    """同一標的、同方向的候選裡挑最價平（並列取最近到期）的一檔算 IV。rows 已含條款欄位。"""
    best: tuple[float, int, dict[str, object]] | None = None
    for r in rows:
        if r["kind"] != kind or r.get("category") != "一般型":
            continue
        expiry = r.get("expiry_date")
        strike = r.get("strike")
        ratio = r.get("ratio")
        s = r.get("underlying_close")
        if (
            not isinstance(expiry, dt.date)
            or not isinstance(strike, float)
            or not isinstance(ratio, float)
        ):
            continue
        if not isinstance(s, float) or s <= 0 or strike <= 0 or ratio <= 0:
            continue
        days = (expiry - day).days
        if days < cfg.iv_min_days or days > cfg.iv_max_days:
            continue
        money = abs(strike / s - 1.0)
        if money > cfg.iv_max_moneyness:
            continue
        key = (money, days)
        if best is None or key < (best[0], best[1]):
            best = (money, days, r)
    if best is None:
        return None, None
    r = best[2]
    bid, ask, close = r.get("last_bid"), r.get("last_ask"), r.get("close")
    px: float | None
    if isinstance(bid, float) and isinstance(ask, float) and bid > 0 and ask > 0:
        px = 0.5 * (bid + ask)
    elif isinstance(close, float) and close > 0:
        px = close
    else:
        return None, None
    ratio = r["ratio"]
    strike = r["strike"]
    s = r["underlying_close"]
    assert isinstance(ratio, float) and isinstance(strike, float) and isinstance(s, float)
    iv = implied_vol(kind, px / ratio, s, strike, best[1] / 365.0, cfg.risk_free_rate)
    return iv, best[1]


def summarize(
    daily: pl.DataFrame,
    terms_latest: pl.DataFrame,
    amounts: pl.DataFrame,
    day: dt.date,
    cfg: WarrantConfig,
    now: dt.datetime,
) -> pl.DataFrame:
    """每檔四碼標的一列。daily＝當天行情（DAILY_SCHEMA）；terms_latest＝每檔權證最新一版條款；amounts＝(stock_id, amount)。"""
    if now.tzinfo is None:
        raise ValueError("now 必須帶時區")
    at = now.astimezone(TZ)
    if daily.is_empty():
        return pl.DataFrame(schema=SUMMARY_SCHEMA)
    d = daily.filter(pl.col("underlying_id").str.contains(cfg.stock_pattern))
    t = terms_latest.select("warrant_id", "category", "expiry_date", "strike", "ratio")
    j = d.join(t, on="warrant_id", how="left")
    agg = (
        j.group_by("underlying_id")
        .agg(
            n_call=(pl.col("kind") == "call").sum().cast(pl.Int64),
            n_put=(pl.col("kind") == "put").sum().cast(pl.Int64),
            call_value=pl.col("trade_value").filter(pl.col("kind") == "call").sum(),
            put_value=pl.col("trade_value").filter(pl.col("kind") == "put").sum(),
        )
        .join(amounts.rename({"stock_id": "underlying_id"}), on="underlying_id", how="left")
        .rename({"amount": "underlying_amount"})
        .with_columns(
            value_ratio=pl.when(pl.col("underlying_amount") > 0)
            .then((pl.col("call_value") + pl.col("put_value")) / pl.col("underlying_amount"))
            .otherwise(None)
        )
        .sort("underlying_id")
    )
    ivs: dict[str, tuple[float | None, int | None, float | None, int | None]] = {}
    for uid, grp in j.group_by("underlying_id"):
        rows = grp.to_dicts()
        c_iv, c_days = _atm_iv(rows, "call", day, cfg)
        p_iv, p_days = _atm_iv(rows, "put", day, cfg)
        ivs[str(uid[0])] = (c_iv, c_days, p_iv, p_days)
    out = agg.with_columns(
        date=pl.lit(day),
        iv_call=pl.col("underlying_id").map_elements(lambda u: ivs[u][0], return_dtype=pl.Float64),
        iv_call_days=pl.col("underlying_id").map_elements(
            lambda u: ivs[u][1], return_dtype=pl.Int64
        ),
        iv_put=pl.col("underlying_id").map_elements(lambda u: ivs[u][2], return_dtype=pl.Float64),
        iv_put_days=pl.col("underlying_id").map_elements(
            lambda u: ivs[u][3], return_dtype=pl.Int64
        ),
        available_at=pl.lit(at).cast(pl.Datetime("us", "Asia/Taipei")),
    )
    return out.select(list(SUMMARY_SCHEMA)).cast(SUMMARY_SCHEMA)  # type: ignore[arg-type]
