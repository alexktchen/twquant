"""D-156 附錄：A20 選股「買進後第 N 天開盤賣」的報酬剖面——**事後描述統計，不是測試**（使用者提問 2026-09-25）。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d156_holding_profile.py

部位與 `d156_exit_model.py` 完全相同（A20 池內前 15 名、20 列內不重買、開盤漲停不進；評估進場日 2019-10~2026-03）。
對照：同一批訊號日、A20 池內**全部**股票（隔日開盤進場、同樣的路徑條件）。第 N 天＝進場日後第 N 個交易日開盤賣。
**看過 D-156 §六 之後才算的**：不得從這張表挑持有天數再宣稱有效——要用必須另立登記。
"""

from __future__ import annotations

import os
import pathlib
import sys

import numpy as np
import polars as pl
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from d151_report import LIMIT_GAP, load_market
from d156_exit_model import (
    _S,
    EVAL,
    EXIT_DAY,
    PATH_END,
    ROUND_TRIP,
    _a20_scores,
    build_positions,
    decision_frame,
)

DAYS = (1, 2, 3, 5, 7, 10, 15, 20, 21)


def _pool_paths(mk: pl.DataFrame, scores: pl.DataFrame) -> np.ndarray:
    """A20 池內全部股票：訊號日隔天開盤進場，第 0~21 天還原開盤（路徑完整、非開盤漲停、訊號日在評估窗）。"""
    ao = (mk["open"] / mk["adj"]).to_numpy()
    sid = mk[_S].to_numpy()
    rows = mk.with_row_index("r").select(pl.col("r").cast(pl.Int64), "date", _S, "gap")
    pool = scores.join(rows, on=["date", _S]).filter(pl.col("date").is_between(*EVAL))
    r0 = pool["r"].to_numpy() + 1
    gap = pool["gap"].to_numpy()
    ok = (r0 + EXIT_DAY < len(sid)) & np.isfinite(gap) & (gap < LIMIT_GAP)
    r0 = r0[ok]
    same = sid[np.minimum(r0 + EXIT_DAY, len(sid) - 1)] == sid[r0 - 1]
    r0 = r0[same]
    opens = ao[r0[:, None] + np.arange(EXIT_DAY + 1)[None, :]]
    good = np.isfinite(opens).all(axis=1) & (opens > 0).all(axis=1)
    dates = mk["date"].to_numpy()[r0 + EXIT_DAY]
    good &= dates <= np.datetime64(PATH_END)
    return opens[good]


def _row(tag: str, opens: np.ndarray, n: int) -> str:
    g = opens[:, n] / opens[:, 0] - 1.0
    net = g - ROUND_TRIP
    return (
        f"{tag}　毛 平均 {g.mean():+.2%}　中位 {np.median(g):+.2%}　勝率(淨) {(net > 0).mean():.1%}　"
        f"淨 {net.mean():+.2%}　每持有日淨 {net.mean() / n:+.3%}"
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        mk = load_market(conn).sort(_S, "date")
    scores = _a20_scores()
    pos = build_positions(mk, scores)
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
    picks = prices["opens"][keep]
    pool = _pool_paths(mk, scores)
    print("**D-156 附錄：事後描述統計（不是測試）。** 第 N 天開盤賣；每筆扣來回 0.40%。")
    print(f"A20 前 15 名新進部位 {len(picks):,} 筆；對照＝同訊號日 A20 池內全部 {len(pool):,} 筆\n")
    for n in DAYS:
        print(f"第 {n:2d} 天")
        print("  " + _row("A20 前 15", picks, n))
        print("  " + _row("池內全部 ", pool, n))
        print(
            f"  前 15 − 池內（毛）：{(picks[:, n] / picks[:, 0] - 1).mean() - (pool[:, n] / pool[:, 0] - 1).mean():+.2%}"
        )


if __name__ == "__main__":
    main()
