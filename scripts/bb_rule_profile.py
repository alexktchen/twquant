"""使用者提議的布林規則（2026-09-28）——**事後描述統計，不是回測、不調參數、不是任何登記的試驗**。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/bb_rule_profile.py

使用者的規則：「打到布林通道底再買；再跌破通道底就停損；有往上開口就抱到離開布林上軌停利」。
部位與 D-156 完全相同（A20 池內前 15 名、20 列內不重買、開盤漲停不進；評估進場日 2019-10~2026-03，
最長到第 21 天開盤——與「抱滿 20 天」同一最長持有期）。布林通道：還原收盤 20 日均線 ± 2 倍 20 日標準差
（母體標準差，與常見看盤軟體一致；市面預設值，不是從資料挑的）。三件事分開量：

1. 買點：訊號後第 0~19 天，盤中低點碰到「前一天的下軌」的比例；最後有飆的（20 天內收盤曾 ≥ +20%）有幾成是先碰下軌。
   碰到的那天以 min(開盤, 下軌) 買（開盤就在下軌下面＝開盤買到），一樣第 21 天開盤賣。
2. 停損：進場後收盤跌破當天下軌 → 隔天開盤賣。
3. 停利：收盤曾站上上軌（開口往上）之後，收盤跌回上軌以下（離開上軌）→ 隔天開盤賣。
2＋3 合併；沒觸發就第 21 天開盤賣。每筆扣來回 0.40%，報每筆與每持有日；「安全」看虧損尾巴（虧 >10% 的比例、最差 5%）。
"""

from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import polars as pl
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from d151_report import load_market
from d156_exit_model import (
    _S,
    EVAL,
    EXIT_DAY,
    N_DECISIONS,
    PATH_END,
    ROUND_TRIP,
    _a20_scores,
    build_positions,
    decision_frame,
)

BB_N, BB_K = 20, 2.0  # 布林通道的市面預設（20 日、2 倍標準差）
WIN = 0.20  # 「有飆」＝20 天內收盤曾 ≥ +20%（A20 的標籤）
BIG_LOSS = -0.10


def _bands(mk: pl.DataFrame) -> pl.DataFrame:
    ac = pl.col("close") / pl.col("adj")
    ma = ac.rolling_mean(BB_N).over(_S)
    sd = ac.rolling_std(BB_N, ddof=0).over(_S)
    return mk.with_columns(
        ac=ac,
        ao=pl.col("open") / pl.col("adj"),
        al=pl.col("low") / pl.col("adj"),
        lower=ma - BB_K * sd,
        upper=ma + BB_K * sd,
    )


def _first(cond: np.ndarray) -> np.ndarray:
    """每列第一個 True 的位置；沒有 → -1。"""
    hit = cond.any(axis=1)
    return np.where(hit, cond.argmax(axis=1), -1)


def _line(tag: str, net: np.ndarray, days: np.ndarray) -> str:
    return (
        f"  {tag:<22} 每筆淨 平均 {net.mean():+.2%}／中位 {np.median(net):+.2%}　勝率 {(net > 0).mean():.1%}　"
        f"每持有日 {net.sum() / days.sum():+.3%}　平均持有 {days.mean():.1f} 日　"
        f"虧 >10% 的 {(net < BIG_LOSS).mean():.1%}　最差 5% {np.percentile(net, 5):+.1%}"
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        mk = _bands(load_market(conn).sort(_S, "date"))
    pos = build_positions(mk, _a20_scores())
    _, prices = decision_frame(mk, pos)
    keep = (
        pos.with_columns(ok=pl.Series(prices["ok"]))
        .with_row_index("i")
        .filter(
            pl.col("ok")
            & pl.col("entry_date").is_between(*EVAL)
            & (pl.col("exit_date") <= PATH_END)
        )["i"]
        .to_numpy()
    )
    r0 = pos["r0"].to_numpy()[keep]
    d = np.arange(N_DECISIONS)
    rows = r0[:, None] + d[None, :]  # 第 0~19 天
    opens = mk["ao"].to_numpy()[r0[:, None] + np.arange(EXIT_DAY + 1)[None, :]]  # 第 0~21 天開盤
    close = mk["ac"].to_numpy()[rows]
    low = mk["al"].to_numpy()[rows]
    lower = mk["lower"].to_numpy()[rows]
    upper = mk["upper"].to_numpy()[rows]
    lower_prev = mk["lower"].to_numpy()[rows - 1]
    ok = np.isfinite(lower_prev).all(axis=1) & np.isfinite(upper).all(axis=1)
    ok &= np.isfinite(low).all(axis=1) & np.isfinite(close).all(axis=1)
    opens, close, low, lower, upper, lower_prev = (
        a[ok] for a in (opens, close, low, lower, upper, lower_prev)
    )
    n = len(opens)
    ix = np.arange(n)
    entry = opens[:, 0]
    winner_day = _first(close / entry[:, None] - 1.0 >= WIN)
    winner = winner_day >= 0
    print("**使用者的布林規則：事後描述統計（不是回測、不調參數）。**")
    print(
        f"A20 部位 {n:,} 筆（D-156 同一批；評估進場 {EVAL[0]} ~ {EVAL[1]}）；"
        f"20 天內收盤曾 ≥ +{WIN:.0%}（有飆）的 {winner.mean():.1%}（{int(winner.sum()):,} 筆）"
    )

    # 1. 買點：碰前一天的下軌
    touch_day = _first(low <= lower_prev)
    touched = touch_day >= 0
    print("\n── 1. 買點：訊號後 20 天內盤中碰到下軌")
    print(
        f"  碰到的 {touched.mean():.1%}（5 天內 {((touch_day >= 0) & (touch_day < 5)).mean():.1%}、"
        f"10 天內 {((touch_day >= 0) & (touch_day < 10)).mean():.1%}）；中位第 {np.median(touch_day[touched]):.0f} 天"
    )
    before = winner & touched & (touch_day < np.where(winner_day >= 0, winner_day, 99))
    print(
        f"  有飆的 {int(winner.sum()):,} 筆裡，先碰下軌才飆的 {before.sum() / winner.sum():.1%}"
        f"——其餘 {1 - before.sum() / winner.sum():.1%} 沒回到下軌就漲上去（等下軌的人買不到）"
    )
    s0_net = opens[:, EXIT_DAY] / entry - 1.0 - ROUND_TRIP
    tj = touch_day[touched]
    band_px = np.minimum(opens[touched, tj], lower_prev[touched, tj])
    band_net = opens[touched, EXIT_DAY] / band_px - 1.0 - ROUND_TRIP
    band_days = (EXIT_DAY - tj).astype(float)
    print(_line("隔天開盤就買（原本）", s0_net, np.full(n, float(EXIT_DAY))))
    print(_line("等碰下軌才買", band_net, band_days))
    print(
        f"  等碰下軌才買：只買得到 {touched.mean():.1%} 的部位；沒碰到下軌（買不到）的那些，"
        f"原本每筆淨 {s0_net[~touched].mean():+.2%}、有飆的 {winner[~touched].mean():.1%}"
    )

    # 2＋3. 停損／停利（隔天開盤就買）
    stop = _first(close < lower)
    armed = _first(close > upper)
    later = (d[None, :] > armed[:, None]) & (armed[:, None] >= 0) & (close < upper)
    tp = _first(later)
    ex_stop = np.where(stop >= 0, stop + 1, EXIT_DAY)
    ex_tp = np.where(tp >= 0, tp + 1, EXIT_DAY)
    ex_both = np.minimum(ex_stop, ex_tp)
    print("\n── 2＋3. 隔天開盤買，停損／停利（沒觸發就第 21 天開盤賣）")
    s0_days = np.full(n, float(EXIT_DAY))
    print(_line("抱滿（S0）", s0_net, s0_days))
    for tag, ex in (
        ("只停損：收盤破下軌", ex_stop),
        ("只停利：離開上軌", ex_tp),
        ("停損＋停利（你的規則）", ex_both),
    ):
        net = opens[ix, ex] / entry - 1.0 - ROUND_TRIP
        print(_line(tag, net, ex.astype(float)))
    stopped_winner = winner & (stop >= 0) & (stop < winner_day)
    print(
        f"  觸發：停損 {(stop >= 0).mean():.1%}、站上上軌 {(armed >= 0).mean():.1%}、停利 {(tp >= 0).mean():.1%}；"
        f"有飆的裡面在飆之前就被停損洗出去的 {stopped_winner.sum() / winner.sum():.1%}"
    )


if __name__ == "__main__":
    main()
