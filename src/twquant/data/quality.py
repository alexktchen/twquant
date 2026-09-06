"""擷取層資料完整性檢查（T-079 AC-2、黃金規則 5）。

由 2026-09-01 的資料事故導出：`prices_daily` 有 43 個交易日、`institutional_daily`
有 2 個日期被寫入重複或陳舊的快照，存在至少三年未被發現——因為沒有任何一關在檢查。

`duplicate_snapshot_dates` 的判別力極高：實測汙染日的重複率為 54~58%，
而修復後全歷史的最大值為 1.64%、中位數 0.39%。差距近兩個數量級，
故本檢查幾乎不可能誤報。關鍵在於把**成交量**納入比對——兩個不同交易日的收盤價
可能巧合相同，但成交量精確到股數相同不可能。

純函數：不做 IO、不連 PG；由呼叫端（擷取／同步流程）餵入 DataFrame。
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

import polars as pl

from twquant.errors import QualityGateError

_DATE = "date"


@dataclass(frozen=True)
class DuplicateSnapshot:
    """一個可疑日期：`rate` 的分母是該日自身列數；`partner` 是重合最多的對象日期。"""

    date: dt.date
    rate: float
    partner: dt.date | None
    n_rows: int


def duplicate_snapshot_dates(
    frame: pl.DataFrame,
    *,
    key: Sequence[str],
    values: Sequence[str],
    max_rate: float,
    date_col: str = _DATE,
) -> list[DuplicateSnapshot]:
    """找出與其他日期共用快照的日期，依重複率由高至低排序。

    重複率＝該日有多少比例的列，其 (key..., values...) 組合在**別的日期**也出現。
    """
    if not 0.0 < max_rate < 1.0:
        raise ValueError(f"max_rate 須介於 0 與 1 之間，得到 {max_rate}")
    missing = [c for c in (date_col, *key, *values) if c not in frame.columns]
    if missing:
        raise ValueError(f"缺少欄位：{', '.join(missing)}")
    if frame.is_empty():
        return []

    tuple_cols = [*key, *values]
    marked = (
        frame.select(date_col, *tuple_cols)
        .with_columns(pl.len().over(tuple_cols).alias("_k"))
        .filter(pl.col("_k") > 1)
    )
    if marked.is_empty():
        return []

    per_day = marked.group_by(date_col).len().rename({"len": "_dup"})
    totals = frame.group_by(date_col).len().rename({"len": "_n"})
    rated = (
        per_day.join(totals, on=date_col)
        .with_columns((pl.col("_dup") / pl.col("_n")).alias("_rate"))
        .filter(pl.col("_rate") > max_rate)
        .sort("_rate", descending=True)
    )
    if rated.is_empty():
        return []

    # 對每個可疑日找出重合最多的對象日期，供人工判定哪一方才是複製來源
    pairs = marked.join(marked, on=tuple_cols, suffix="_r").filter(
        pl.col(date_col) != pl.col(f"{date_col}_r")
    )
    best = (
        pairs.group_by(date_col, f"{date_col}_r")
        .len()
        .sort([date_col, "len"], descending=[False, True])
        .group_by(date_col, maintain_order=True)
        .first()
    )
    partner = dict(zip(best[date_col].to_list(), best[f"{date_col}_r"].to_list(), strict=True))
    return [
        DuplicateSnapshot(
            date=r[date_col], rate=r["_rate"], partner=partner.get(r[date_col]), n_rows=r["_n"]
        )
        for r in rated.iter_rows(named=True)
    ]


def assert_no_duplicate_snapshots(
    frame: pl.DataFrame,
    *,
    table: str,
    key: Sequence[str],
    values: Sequence[str],
    max_rate: float,
    date_col: str = _DATE,
) -> None:
    """檢出重複快照即 raise（黃金規則 5：資料有問題就 raise，不靜默流下去）。"""
    hits = duplicate_snapshot_dates(
        frame, key=key, values=values, max_rate=max_rate, date_col=date_col
    )
    if not hits:
        return
    lines = [
        f"{h.date}（{h.rate * 100:.1f}%，{h.n_rows:,} 列"
        + (f"，最大重合對象 {h.partner}）" if h.partner else "）")
        for h in hits[:10]
    ]
    more = f"，另有 {len(hits) - 10} 天" if len(hits) > 10 else ""
    raise QualityGateError(
        f"{table} 重複快照",
        f"以下日期與其他日期共用快照，超過門檻 {max_rate * 100:.1f}%：{'；'.join(lines)}{more}。"
        f"比對鍵＝({', '.join(key)}) + ({', '.join(values)})。"
        "這通常代表擷取層回退到快取或陳舊回應——請重抓該日期，不要略過。",
    )


def phantom_dates(present: Sequence[dt.date], calendar: Sequence[dt.date]) -> list[dt.date]:
    """出現在資料表、但不在交易日曆上的日期（非交易日卻有資料＝擷取層寫錯）。"""
    known = set(calendar)
    return sorted({d for d in present if d not in known})


def assert_no_phantom_dates(
    present: Sequence[dt.date], calendar: Sequence[dt.date], *, table: str
) -> None:
    """資料表出現非交易日即 raise。"""
    ghosts = phantom_dates(present, calendar)
    if not ghosts:
        return
    shown = ", ".join(str(d) for d in ghosts[:10])
    more = f"，另有 {len(ghosts) - 10} 天" if len(ghosts) > 10 else ""
    raise QualityGateError(
        f"{table} 幽靈日期",
        f"下列日期不在交易日曆上卻有資料：{shown}{more}。"
        "非交易日有資料代表擷取層寫入了錯誤日期（實例：農曆年封關期間被寫入前一年的快照）。",
    )


@dataclass(frozen=True)
class SparseDate:
    """一個列數塌陷的日期：`expected` 是鄰近日的列數中位數。"""

    date: dt.date
    n_rows: int
    expected: float


def sparse_dates(
    frame: pl.DataFrame,
    *,
    min_ratio: float,
    window: int,
    date_col: str = _DATE,
) -> list[SparseDate]:
    """找出列數遠低於鄰近日的日期，依塌陷程度由重至輕排序。

    `duplicate_snapshot_dates` 抓的是「多出來的列」（同一份快照被寫兩次），
    抓不到「少掉的列」。2026-09-02 實際踩到的是後者：8 個交易日只有 TWSE 一條腿、
    整個 TPEX 缺席，擷取仍回報成功。

    基準取**鄰近 ±window 日（含當日）的列數中位數**而非全期中位數——股票宇宙會隨
    年份成長（2020 約 2,000 檔、2026 約 6,000 檔），用全期基準會把早期整段誤報。
    含當日是為了讓宇宙規模跳階處的中位數不被前後兩種水位對半拉扯。
    """
    if not 0.0 < min_ratio < 1.0:
        raise ValueError(f"min_ratio 須介於 0 與 1 之間，得到 {min_ratio}")
    if window < 1:
        raise ValueError(f"window 須 >= 1，得到 {window}")
    if date_col not in frame.columns:
        raise ValueError(f"缺少欄位：{date_col}")
    if frame.is_empty():
        return []

    counts = frame.group_by(date_col).len().sort(date_col)
    days: list[dt.date] = counts[date_col].to_list()
    rows: list[int] = counts["len"].to_list()
    # 鄰近日不足以構成基準時不判定——回補初期的頭幾天本來就孤立，誤報比漏報更糟
    if len(days) < window + 1:
        return []

    hits: list[SparseDate] = []
    for i, (day, n) in enumerate(zip(days, rows, strict=True)):
        lo, hi = max(0, i - window), min(len(rows), i + window + 1)
        expected = float(statistics.median(rows[lo:hi]))
        if expected > 0 and n < expected * min_ratio:
            hits.append(SparseDate(date=day, n_rows=n, expected=expected))
    hits.sort(key=lambda h: h.n_rows / h.expected)
    return hits


def assert_no_sparse_dates(
    frame: pl.DataFrame,
    *,
    table: str,
    min_ratio: float,
    window: int,
    date_col: str = _DATE,
) -> None:
    """檢出列數塌陷的日期即 raise（黃金規則 5）。"""
    hits = sparse_dates(frame, min_ratio=min_ratio, window=window, date_col=date_col)
    if not hits:
        return
    lines = [f"{h.date}（{h.n_rows:,} 列，鄰近日 {h.expected:,.0f} 列）" for h in hits[:10]]
    more = f"，另有 {len(hits) - 10} 天" if len(hits) > 10 else ""
    raise QualityGateError(
        f"{table} 列數塌陷",
        f"下列日期的列數低於鄰近日的 {min_ratio * 100:.0f}%：{'；'.join(lines)}{more}。"
        "這通常代表某一個來源整條腿缺席（實例：只抓到上市、整個上櫃沒進來），"
        "而該日仍被記為擷取成功——請補抓缺的來源，不要略過。",
    )


@dataclass(frozen=True)
class GroupShareAnomaly:
    """某日某個分組的列數佔比異常塌陷（T-136）。"""

    date: dt.date
    group: str
    n_rows: int
    share: float  # 當日該組佔比
    baseline: float  # 全期該組佔比


def group_share_anomalies(
    frame: pl.DataFrame,
    *,
    group_col: str,
    min_ratio: float,
    date_col: str = _DATE,
) -> list[GroupShareAnomaly]:
    """找出「某個分組在某日的佔比遠低於其全期佔比」的日期。

    為什麼需要它而不是 `sparse_dates`（T-136 的教訓）：後者以**鄰近日中位數**為基準，
    對長時間的停擺完全失效——`prices_daily` 曾連續 51 個交易日只有 TPEX 一條腿、
    TWSE 整腿缺席（2023-03-09~05-23，台積電 0 列），而缺口中間那些日子的鄰居**也全是壞的**，
    局部中位數因此跟著塌陷，`sparse_dates` 一天都抓不到。

    本函數的基準是**全期佔比**，不看鄰居，故不受停擺長度影響：
    TWSE 佔四碼普通股約 55%（975/1,767），缺腿日掉到 0.3%（5/797），差三個數量級。

    限制：若某組在**多數期間**都缺席，全期佔比本身就被拉低，本檢查會失靈。
    它防的是「本來有、後來整段消失」，不是「從來就沒有」。
    """
    if not 0.0 < min_ratio < 1.0:
        raise ValueError(f"min_ratio 須介於 0 與 1 之間，得到 {min_ratio}")
    for c in (date_col, group_col):
        if c not in frame.columns:
            raise ValueError(f"缺少欄位：{c}")
    if frame.is_empty():
        return []

    total = frame.height
    base = (
        frame.group_by(group_col)
        .len()
        .with_columns(_base=pl.col("len") / total)
        .select(group_col, "_base")
    )
    per_day = frame.group_by([date_col, group_col]).len()
    day_tot = frame.group_by(date_col).len().rename({"len": "_day"})
    joined = (
        per_day.join(day_tot, on=date_col)
        .join(base, on=group_col)
        .with_columns(_share=pl.col("len") / pl.col("_day"))
        .filter(pl.col("_share") < pl.col("_base") * min_ratio)
        .sort([date_col, group_col])
    )
    return [
        GroupShareAnomaly(
            date=r[date_col],
            group=str(r[group_col]),
            n_rows=int(r["len"]),
            share=float(r["_share"]),
            baseline=float(r["_base"]),
        )
        for r in joined.to_dicts()
    ]


def assert_no_group_share_anomalies(
    frame: pl.DataFrame,
    *,
    table: str,
    group_col: str,
    min_ratio: float,
    date_col: str = _DATE,
) -> None:
    """`group_share_anomalies` 的閘門版：有任何塌陷即 raise（黃金規則 5）。"""
    hits = group_share_anomalies(frame, group_col=group_col, min_ratio=min_ratio, date_col=date_col)
    if not hits:
        return
    head = "；".join(
        f"{h.date} {h.group} 佔比 {h.share:.1%}（全期 {h.baseline:.1%}、{h.n_rows} 列）"
        for h in hits[:5]
    )
    raise QualityGateError(
        f"{table}.group_share",
        f"{len(hits)} 個 (日期, {group_col}) 的列數佔比塌陷至全期水準的 "
        f"{min_ratio:.0%} 以下：{head}。這是「整條腿缺席」的特徵"
        "——`sparse_dates` 以鄰近日為基準，對長時間停擺會失效，故另設本檢查。",
    )
