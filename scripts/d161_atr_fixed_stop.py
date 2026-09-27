"""D-161（逐筆）：T-ATR 拿掉布林下軌、改固定 −15% 停損。**參數照 D-161 §一 寫死，不得依結果改。**

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d161_atr_fixed_stop.py

部位、還原價、成本、出場時點、最長持有全部沿用 `scripts/d159_trailing_exits.py`（D-159 §一）。並列：

| 臂 | 出場 |
|---|---|
| S0 | 第 21 天開盤（無停損） |
| T-ATR | 下軌停損；ATR 停利線（D-159，必須重現 D-159 §七 的數字） |
| T-ATR-F15 | 收盤 ≤ 買進價 × 0.85 停損；ATR 停利線 |
"""

from __future__ import annotations

import importlib
import io
import os
import sys
from typing import Any

import numpy as np
import polars as pl

# 研究腳本不是套件；同 d159 的 `_sibling`，執行期才載入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
d159: Any = importlib.import_module("d159_trailing_exits")

FIXED_STOP = 0.15  # D-161 §一：收盤較買進價跌 ≥ 15%
_S: str = d159._S


def fixed_stop_trigger(close: np.ndarray, entry: np.ndarray, pct: float) -> np.ndarray:
    """收盤 ≤ 買進價 × (1 − pct)；NaN 一律不觸發。"""
    with np.errstate(invalid="ignore"):
        out: np.ndarray = close <= entry[:, None] * (1.0 - pct)
        return out


def main() -> None:
    import psycopg

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    d156 = d159._sibling("d156_exit_model")
    cap = d159.MAX_EXIT

    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        mk = d159._market(conn)
        pos = d156.build_positions(mk, d156._a20_scores())
    cal = np.sort(mk["date"].unique().to_numpy())
    entry_ix = np.searchsorted(cal, pos["entry_date"].to_numpy())
    horizon_ok = entry_ix + cap < len(cal)
    horizon_ok[horizon_ok] = cal[entry_ix[horizon_ok] + cap] <= np.datetime64(d159.PATH_END)
    pos = pos.filter(pl.Series(horizon_ok) & (pl.col("entry_date") >= d159.EVAL_FROM))

    sid = mk[_S].to_numpy()
    cut = np.flatnonzero(sid[1:] != sid[:-1]) + 1
    stock_end = np.empty(len(sid), dtype=np.int64)
    for a, b in zip(np.r_[0, cut], np.r_[cut, len(sid)], strict=True):
        stock_end[a:b] = b - 1
    r0 = pos["r0"].to_numpy()
    last = np.minimum(stock_end[r0] - r0, cap)
    k = np.arange(cap)
    rows = np.minimum(r0[:, None] + k[None, :], stock_end[r0][:, None])
    valid = k[None, :] <= (stock_end[r0] - r0)[:, None]

    def grab(col: str) -> np.ndarray:
        out: np.ndarray = np.where(valid, mk[col].to_numpy().astype(float)[rows], np.nan)
        return out

    close, lower, atr = grab("ac"), grab("lower"), grab("atr")
    orow = np.minimum(r0[:, None] + np.arange(cap + 1)[None, :], stock_end[r0][:, None])
    opens = mk["ao"].to_numpy().astype(float)[orow]
    entry = opens[:, 0]
    ix = np.arange(len(r0))
    with np.errstate(invalid="ignore"):
        band = close < lower
    fixed = fixed_stop_trigger(close, entry, FIXED_STOP)
    lock = d159.lock_trigger(close, d159.atr_line(close, atr, d159.ATR_K), entry)

    arms: dict[str, tuple[np.ndarray, np.ndarray]] = {
        "S0": (np.full(len(r0), d159.S0_EXIT), np.zeros_like(band)),
        "T-ATR": (d159.exit_day(band | lock, last, cap), band),
        "T-ATR-F15": (d159.exit_day(fixed | lock, last, cap), fixed),
    }

    def net_of(ex: np.ndarray) -> np.ndarray:
        out: np.ndarray = opens[ix, ex] / entry - 1.0 - d159.ROUND_TRIP
        return out

    month = pos["entry_date"].dt.strftime("%Y-%m").to_numpy()
    print("**D-161（逐筆）：T-ATR 拿掉布林下軌、改固定 −15% 停損；事前登記，參數寫死。**")
    print(f"部位 {len(r0):,} 筆（進場 {pos['entry_date'].min()} ~ {pos['entry_date'].max()}）")
    base = (net_of(arms["S0"][0]), arms["S0"][0].astype(float))
    atr_ref = (net_of(arms["T-ATR"][0]), arms["T-ATR"][0].astype(float))
    for tag, (ex, stop) in arms.items():
        net = net_of(ex)
        print(d159.summarize(tag, net, ex.astype(float)))
        if tag == "S0":
            continue
        kk = np.clip(ex - 1, 0, cap - 1)
        reason = np.where(
            stop[ix, kk],
            "停損",
            np.where(
                lock[ix, kk],
                "停利線",
                np.where((ex == last) & (last < cap), "沒行情", "時間到"),
            ),
        )
        vals, cnt = np.unique(reason, return_counts=True)
        print(
            "         出場："
            + "、".join(f"{v} {c / len(ex):.1%}" for v, c in zip(vals, cnt, strict=True))
        )
        print("         對 S0 " + d159._month_t(month, (net, ex.astype(float)), base))
        if tag == "T-ATR-F15":
            print("         對 T-ATR " + d159._month_t(month, (net, ex.astype(float)), atr_ref))
    print(
        f"\n（最長持有 {cap} 天；判定窗止於 {d159.PATH_END}；還原價；來回 {d159.ROUND_TRIP:.2%}）"
    )


if __name__ == "__main__":
    main()
