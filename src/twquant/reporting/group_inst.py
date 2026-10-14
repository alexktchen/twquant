"""族群法人（D-177 修訂三）：每天每個族群的三大法人買賣超（金額），與族群頁象限圖的 x、y、軌跡。

- 每天：族群成員（全市場四碼普通股、在族群表上）當天的三大法人買賣超股數 × 當天收盤，加總成金額；
  外資、投信、自營商分開記。上市與上櫃的法人資料由呼叫端合併後傳進來。
  法人資料沒有列出的成員＝那天法人沒有買賣，記 0（證交所與櫃買都只列有法人交易的股票）。
  法人只買賣零股或鉅額、當天沒有一般交易（行情表沒有收盤）的股票，用最近一個收盤換算（2026-10-14 實測：
  5906、1423、3085 各幾十到一千股）；成員數只算當天有收盤的。
- x＝近 window 個交易日的合計；y（加速）＝（近 window 日合計 − 再前 window 日合計）÷ window，
  單位是「每天」：正的＝這一段平均每天比前一段多買（或少賣）。涵蓋的日子有一天沒算到 → 那個值是 None，不當成 0。
- 象限（網站畫）：x>0、y>0 持續加碼；x>0、y<0 買氣降溫；x<0、y>0 賣壓收斂；x<0、y<0 持續減碼。

純函數：資料由呼叫端讀好傳進來。族群用的是現在的分類（不是當時的），只供顯示與觀察，不進因子。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import polars as pl

from twquant.data.stock_groups import GroupInstConfig
from twquant.errors import DataError

_TABLE = "group_inst_daily"
_NETS = ("foreign_net", "trust_net", "dealer_net")


@dataclass(frozen=True)
class InstDay:
    """一個族群某一天的法人買賣超（TWD）。members＝當天有收盤的成員數。"""

    date: dt.date
    group: str
    members: int
    foreign: float
    trust: float
    dealer: float

    @property
    def net(self) -> float:
        """三大法人合計。"""
        return self.foreign + self.trust + self.dealer


@dataclass(frozen=True)
class GroupInst:
    """象限圖上一個族群某一天的點：當天的買賣超，加上近 window 日合計（x）、加速（y）與最近幾天的軌跡。"""

    day: InstDay
    net5: float | None
    accel: float | None
    foreign5: float | None
    trust5: float | None
    dealer5: float | None
    trail: tuple[
        tuple[dt.date, float, float], ...
    ]  # (日期, x, y)，舊到新，只含 x、y 都算得出來的日子


def group_inst_day(
    inst: pl.DataFrame,
    closes: pl.DataFrame,
    groups: Mapping[str, str],
    day: dt.date,
    universe: str,
) -> list[InstDay]:
    """`day` 這天每個族群的法人買賣超（金額），依族群名排序。

    inst：stock_id、foreign_net、trust_net、dealer_net（股，上市＋上櫃）；
    closes：stock_id、close、date（每檔 `day` 當天或之前最近一個收盤與它的日期，呼叫端限定往前找多久）。
    當天一檔收盤都沒有、同一檔出現兩次、或有法人買賣卻找不到任何收盤（換不成金額）即 raise。
    """
    if closes.filter(pl.col("date") == day).is_empty():
        raise DataError(_TABLE, day, f"{day} 沒有收盤價")
    if closes.filter(pl.col("date") > day).height:
        raise DataError(_TABLE, day, f"收盤價有 {day} 之後的日期（前視）")
    dup = inst.filter(pl.col("stock_id").is_duplicated())
    if dup.height:
        raise DataError(
            _TABLE, day, f"法人資料同一檔出現兩次（上市櫃重複？）：{dup.head(3).to_dicts()}"
        )
    member_map = pl.DataFrame(
        {"stock_id": list(groups), "group": list(groups.values())},
        schema={"stock_id": pl.String, "group": pl.String},
    ).filter(pl.col("stock_id").str.contains(universe))
    px = closes.select("stock_id", "close", "date").join(member_map, on="stock_id", how="inner")
    bad_px = px.filter(pl.col("close").is_null() | (pl.col("close") <= 0))
    if bad_px.height:
        raise DataError(_TABLE, day, f"收盤是空的或不是正數：{bad_px.head(3).to_dicts()}")
    flows = inst.select("stock_id", *_NETS).join(member_map, on="stock_id", how="inner")
    orphan = flows.join(px.select("stock_id"), on="stock_id", how="anti").filter(
        pl.any_horizontal(*(pl.col(c) != 0 for c in _NETS))
    )
    if orphan.height:
        raise DataError(
            _TABLE, day, f"{orphan.height} 檔有法人買賣卻沒有收盤，例：{orphan.head(3).to_dicts()}"
        )
    amounts = (
        flows.join(px.select("stock_id", "close"), on="stock_id", how="inner")
        .group_by("group")
        .agg(*(pl.col(c).mul(pl.col("close")).sum().alias(c) for c in _NETS))
    )
    counts = px.group_by("group").agg((pl.col("date") == day).sum().alias("members"))
    table = (
        counts.join(amounts, on="group", how="left")
        .filter(
            (pl.col("members") > 0)
            | pl.any_horizontal(*(pl.col(c).fill_null(0.0) != 0 for c in _NETS))
        )
        .sort("group")
    )
    return [
        InstDay(
            date=day,
            group=str(r["group"]),
            members=int(r["members"]),
            foreign=float(r["foreign_net"] or 0.0),  # 沒有任何成員被法人買賣＝0，不是缺值
            trust=float(r["trust_net"] or 0.0),
            dealer=float(r["dealer_net"] or 0.0),
        )
        for r in table.to_dicts()
    ]


def inst_series(
    days: Sequence[InstDay], sessions: Sequence[dt.date], cfg: GroupInstConfig
) -> list[GroupInst]:
    """`sessions`（連續交易日，舊到新）每一天、每個那天有算的族群的 x、y 與軌跡。

    某個交易日一個族群都沒有＝那天沒算到（不是法人沒買賣）：涵蓋它的 x、y 是 None。
    同一天裡沒有某族群（那天沒有成員有收盤）＝那個族群那天 0。
    """
    order = sorted(set(sessions))
    if list(sessions) != order:
        raise ValueError("sessions 要由舊到新、不重複")
    by_day: dict[dt.date, dict[str, InstDay]] = {s: {} for s in order}
    for d in days:
        if d.date not in by_day:
            raise ValueError(f"{d.date} 不在 sessions 裡")
        if d.group in by_day[d.date]:
            raise ValueError(f"{d.date} 族群「{d.group}」重複")
        by_day[d.date][d.group] = d
    done = [bool(by_day[s]) for s in order]
    w = cfg.window

    def total(i: int, group: str) -> tuple[float, float, float] | None:
        """第 i 天（含）往前 w 天的外資、投信、自營商合計；有一天沒算到就 None。"""
        if i - w + 1 < 0 or not all(done[i - w + 1 : i + 1]):
            return None
        rows = [by_day[order[j]].get(group) for j in range(i - w + 1, i + 1)]
        return (
            sum(r.foreign for r in rows if r),
            sum(r.trust for r in rows if r),
            sum(r.dealer for r in rows if r),
        )

    def point(i: int, group: str) -> tuple[float, float] | None:
        now, before = total(i, group), total(i - w, group)
        if now is None or before is None:
            return None
        return sum(now), (sum(now) - sum(before)) / w

    out: list[GroupInst] = []
    for i, s in enumerate(order):
        for group in sorted(by_day[s]):
            sums = total(i, group)
            xy = point(i, group)
            trail = tuple(
                (order[j], p[0], p[1])
                for j in range(max(0, i - cfg.trail + 1), i + 1)
                if (p := point(j, group)) is not None
            )
            out.append(
                GroupInst(
                    day=by_day[s][group],
                    net5=sum(sums) if sums else None,
                    accel=xy[1] if xy else None,
                    foreign5=sums[0] if sums else None,
                    trust5=sums[1] if sums else None,
                    dealer5=sums[2] if sums else None,
                    trail=trail,
                )
            )
    return out
