"""族群資金（D-177 修訂）：每天每個族群拿到多少資金，看資金是不是往同一個族群去。

- 成交比重＝族群成員當天成交金額合計 ÷ 全市場（四碼普通股）當天成交金額。
- 資金倍數＝當天成交比重 ÷ 前 `avg_sessions` 個交易日的平均成交比重（不含當天）。
  看比重不看金額：大盤整體量放大時，每個族群的金額都會變大，比重才看得出資金在族群之間的移動。
- 漲跌家數、平均漲幅：成員當天收盤對前一個交易日收盤（等權平均；前一天沒有收盤的新股不算）。
  用的是原始收盤，除權息當天會略低估。
- 資金流入（hot）：資金倍數 ≥ hot_ratio、上漲家數比例 ≥ hot_breadth、算得出漲跌的成員 ≥ hot_min_members。

純函數：行情與族群由呼叫端讀好傳進來。族群用的是現在的分類（不是當時的），只供顯示與觀察，不進因子。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass

import polars as pl

from twquant.data.stock_groups import GroupFlowConfig
from twquant.errors import DataError

_TABLE = "group_flow_daily"
_COLUMNS = ("date", "stock_id", "close", "amount")


@dataclass(frozen=True)
class FlowMember:
    """族群排行列出的成員：成交金額大的在前。chg＝當天漲跌（前一天沒有收盤為 None）。"""

    sid: str
    name: str | None
    chg: float | None
    amount: float


@dataclass(frozen=True)
class GroupFlow:
    """一個族群某一天的資金與漲跌。share_avg／ratio：前面不足 avg_sessions 個交易日、或前面比重都是 0 時為 None。"""

    date: dt.date
    group: str
    n: int  # 當天有成交的成員
    up: int
    down: int
    ret: float | None  # 算得出漲跌的成員，當天漲跌的等權平均
    amount: float
    share: float
    share_avg: float | None
    ratio: float | None
    hot: bool
    members: tuple[FlowMember, ...]


def group_flow(
    prices: pl.DataFrame,
    groups: Mapping[str, str],
    names: Mapping[str, str],
    day: dt.date,
    cfg: GroupFlowConfig,
    universe: str,
) -> list[GroupFlow]:
    """`day` 這天每個族群的資金與漲跌，依族群名排序。

    prices 要有 date、stock_id、close、amount，涵蓋 `day` 與之前至少 avg_sessions 個交易日；
    universe 是全市場的代號規則（regex，四碼普通股）。`day` 沒有行情、或收盤／成交金額有空值即 raise。
    """
    missing = [c for c in _COLUMNS if c not in prices.columns]
    if missing:
        raise DataError(_TABLE, day, f"行情少了欄位 {missing}")
    px = prices.select(_COLUMNS).filter(pl.col("stock_id").str.contains(universe))
    bad = px.filter(pl.col("close").is_null() | pl.col("amount").is_null())
    if bad.height:
        raise DataError(
            _TABLE, day, f"行情有 {bad.height} 列收盤或成交金額是空的，例：{bad.row(0)}"
        )
    sessions = sorted(d for d in px.get_column("date").unique().to_list() if d <= day)
    if not sessions or sessions[-1] != day:
        raise DataError(
            _TABLE, day, f"{day} 沒有行情（最近一天 {sessions[-1] if sessions else '無'}）"
        )
    prior = sessions[-1 - cfg.avg_sessions : -1]
    window = [*prior, day]
    px = px.filter(pl.col("date").is_in(window))
    total = px.group_by("date").agg(pl.col("amount").sum().alias("total"))
    member_map = pl.DataFrame(
        {"stock_id": list(groups), "group": list(groups.values())},
        schema={"stock_id": pl.String, "group": pl.String},
    )
    tagged = px.join(member_map, on="stock_id", how="inner")
    shares = (
        tagged.group_by("date", "group")
        .agg(pl.col("amount").sum().alias("amount"))
        .join(total, on="date")
        .with_columns((pl.col("amount") / pl.col("total")).alias("share"))
    )
    # 前面某天族群裡沒有任何一檔成交，那天的比重就是 0（不是缺值）
    past = (
        shares.filter(pl.col("date").is_in(prior))
        .group_by("group")
        .agg(pl.col("share").sum().alias("past_sum"))
    )
    prev_close = (
        px.filter(pl.col("date") == prior[-1]).select("stock_id", pl.col("close").alias("prev"))
        if prior
        else pl.DataFrame(schema={"stock_id": pl.String, "prev": pl.Float64})
    )
    today = (
        tagged.filter(pl.col("date") == day)
        .join(prev_close, on="stock_id", how="left")
        .with_columns(
            pl.when(pl.col("prev") > 0)
            .then(pl.col("close") / pl.col("prev") - 1.0)
            .otherwise(None)
            .alias("chg")
        )
    )
    today_share = {
        r["group"]: (r["amount"], r["share"])
        for r in shares.filter(pl.col("date") == day).to_dicts()
    }
    past_sum = {r["group"]: r["past_sum"] for r in past.to_dicts()}
    full = len(prior) == cfg.avg_sessions
    out: list[GroupFlow] = []
    for (group,), rows in sorted(today.group_by("group"), key=lambda kv: str(kv[0][0])):
        chg = [c for c in rows.get_column("chg").to_list() if c is not None]
        up = sum(1 for c in chg if c > 0)
        amount, share = today_share[str(group)]
        avg = past_sum.get(str(group), 0.0) / cfg.avg_sessions if full else None
        ratio = share / avg if avg else None
        hot = (
            ratio is not None
            and ratio >= cfg.hot_ratio
            and len(chg) >= cfg.hot_min_members
            and up / len(chg) >= cfg.hot_breadth
        )
        top = rows.sort(["amount", "stock_id"], descending=[True, False]).head(cfg.max_members)
        out.append(
            GroupFlow(
                date=day,
                group=str(group),
                n=rows.height,
                up=up,
                down=sum(1 for c in chg if c < 0),
                ret=sum(chg) / len(chg) if chg else None,
                amount=float(amount),
                share=float(share),
                share_avg=avg,
                ratio=ratio,
                hot=hot,
                members=tuple(
                    FlowMember(
                        r["stock_id"], names.get(r["stock_id"]), r["chg"], float(r["amount"])
                    )
                    for r in top.to_dicts()
                ),
            )
        )
    return out
