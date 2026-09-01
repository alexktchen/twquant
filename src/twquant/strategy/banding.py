"""buy/hold banding 選股（SPEC-012 §2、D-041）。

遲滯選股：**已持有**與**未持有**適用不同門檻——已持有者跌破 exit 才出場，
未持有者要越過 entry 才建倉。中間那段（exit ≤ rank_pct < entry）是遲滯區，
名次在此抖動不會產生交易，換手因此下降而毛訊號幾乎不損失
（Novy-Marx & Velikov, FAJ 2019）。

純函數：狀態（持股清單）由呼叫端（回測引擎）持有，本模組不做 IO、不碰倉儲。
"""

from __future__ import annotations

import polars as pl

_RANK_PCT = "_rank_pct"


def cross_rank_pct(score: pl.DataFrame, *, score_col: str = "score") -> pl.DataFrame:
    """同一訊號日的橫斷面百分位 (0, 1]；分數越高值越大。null 分數不參與排名。"""
    return score.with_columns(
        (
            pl.col(score_col).rank(method="average")
            / pl.col(score_col).drop_nulls().len().cast(pl.Float64)
        ).alias(_RANK_PCT)
    )


def banded_selection(
    score: pl.DataFrame,
    *,
    held: frozenset[str],
    top_n: int,
    entry_rank_pct: float,
    exit_rank_pct: float,
    score_col: str = "score",
) -> pl.DataFrame:
    """遲滯選股：續抱 rank_pct ≥ exit 的持股，再以 rank_pct ≥ entry 的新標的補足 top_n。

    續抱優先於新建倉——名額不足時寧可留著既有部位，這正是換手下降的來源。
    同分以 stock_id 決定性斷開（與 build._select 的 top_n 路徑一致）。
    """
    if top_n < 1:
        raise ValueError(f"top_n 須 ≥ 1，得到 {top_n}")
    if not 0.0 < exit_rank_pct < entry_rank_pct <= 1.0:
        raise ValueError(
            f"門檻須滿足 0 < exit < entry ≤ 1，得到 exit={exit_rank_pct}、entry={entry_rank_pct}"
        )

    ranked = cross_rank_pct(score, score_col=score_col).drop_nulls(_RANK_PCT)
    if ranked.is_empty():
        return score.clear()

    ordered = ranked.sort([_RANK_PCT, "stock_id"], descending=[True, False])
    is_held = pl.col("stock_id").is_in(list(held))

    keep = ordered.filter(is_held & (pl.col(_RANK_PCT) >= exit_rank_pct)).head(top_n)
    room = top_n - keep.height
    enter = (
        ordered.filter(~is_held & (pl.col(_RANK_PCT) >= entry_rank_pct)).head(room)
        if room > 0
        else ordered.clear()
    )
    return pl.concat([keep, enter]).drop(_RANK_PCT).sort("stock_id")
