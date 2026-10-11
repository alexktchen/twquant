"""`etf_meta`／`etf_units_daily`／`etf_holdings_daily` 的清洗（SPEC-016 §2，T-335／T-336）：純函數，不碰網路與 PG。

- `units_delta`＝同一 ETF 在**相鄰兩個交易日**的單位數相減（T-334：自算）。
  前一交易日沒有列 → null；分割／反分割窗內 → null。**不得**跨缺日相減、不得補 0。
- 分割窗：corporate_events 的事件日，加上它之前 `split_null_sessions_before` 個交易日
  （0050 2025-06-18、0052 2025-11-26 的單位數跳動都落在事件日的前一個交易日）。
- 完整性：每日模式下，某交易日只要有一檔「該日已上市、未下市」的 ETF 沒列 → raise，整天不寫。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence

import polars as pl

from twquant.config import EtfMetaConfig
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.sources.etf_pcf import PcfSnapshot
from twquant.errors import DataError

TABLE = "etf_units_daily"
_META_HOUR = 18
_META_SCHEMA: dict[str, pl.DataType | type[pl.DataType]] = {
    "etf_id": pl.String,
    "name": pl.String,
    "issuer": pl.String,
    "fund_type": pl.String,
    "is_active": pl.Boolean,
    "index_name": pl.String,
    "listed_date": pl.Date,
    "delisted_date": pl.Date,
    "units_tracked": pl.Boolean,
    "available_at": schemas.TW_DT,
}


def _at(day: dt.date, hour: int) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour), tzinfo=TZ)


def etf_meta_frame(meta: EtfMetaConfig) -> pl.DataFrame:
    """configs/etf_meta.yaml → `etf_meta` 表（available_at＝上市日 18:00；pandera 驗證）。"""
    rows = [
        {**e.model_dump(exclude={"note"}), "available_at": _at(e.listed_date, _META_HOUR)}
        for e in meta.etfs
    ]
    df = pl.DataFrame(rows, schema=_META_SCHEMA).select(list(schemas.ETF_META.columns))
    return schemas.validate("etf_meta", df)


def split_null_days(
    events: Iterable[Mapping[str, object]],
    sessions: Sequence[dt.date],
    before: int,
) -> dict[str, set[dt.date]]:
    """{etf_id: 該設 null 的交易日}。events 每列需有 stock_id、date（已由呼叫端篩過事件型別）。

    事件日不是交易日（停牌期間）時，取它之後第一個交易日當錨點。
    """
    cal = sorted(sessions)
    out: dict[str, set[dt.date]] = {}
    for ev in events:
        sid, day = ev["stock_id"], ev["date"]
        assert isinstance(sid, str) and isinstance(day, dt.date)
        i = next((k for k, d in enumerate(cal) if d >= day), None)
        if i is None:
            continue  # 事件在交易日曆之後：還沒有單位數可以受影響
        out.setdefault(sid, set()).update(cal[max(0, i - before) : i + 1])
    return out


def units_frame(
    raw: pl.DataFrame,
    sessions: Sequence[dt.date],
    targets: Sequence[dt.date],
    null_days: Mapping[str, set[dt.date]],
    available_hour: int,
) -> pl.DataFrame:
    """[date, etf_id, units]（含 targets 的前一交易日，好算第一天的差）→ `etf_units_daily`（只回 targets）。

    raw 裡不在交易日曆上的日期 → raise（日曆對不上，差分會跨錯日子）。
    """
    cal = sorted(set(sessions))
    pos = {d: i for i, d in enumerate(cal)}
    off = sorted(set(raw["date"].to_list()) - set(pos))
    if off:
        raise DataError(TABLE, off[0], f"{len(off)} 個日期不在交易日曆上（例 {off[:3]}）")
    dup = raw.group_by(["date", "etf_id"]).len().filter(pl.col("len") > 1)
    if dup.height:
        raise DataError(TABLE, dup["date"][0], f"同一天同一檔重複：{dup.head(3).rows()}")
    idx = pl.DataFrame(
        {"date": cal, "_pos": list(range(len(cal)))}, schema={"date": pl.Date, "_pos": pl.Int64}
    )
    nulls = pl.DataFrame(
        [(sid, d) for sid, ds in null_days.items() for d in ds],
        schema={"etf_id": pl.String, "date": pl.Date},
        orient="row",
    ).with_columns(_split=pl.lit(True))
    want = set(targets)
    df = (
        raw.select("date", "etf_id", pl.col("units").cast(pl.Int64))
        .join(idx, on="date", how="left")
        .sort(["etf_id", "_pos"])
        .with_columns(
            _prev_units=pl.col("units").shift(1).over("etf_id"),
            _prev_pos=pl.col("_pos").shift(1).over("etf_id"),
        )
        .join(nulls, on=["etf_id", "date"], how="left")
        .with_columns(
            units_delta=pl.when(
                (pl.col("_prev_pos") == pl.col("_pos") - 1) & pl.col("_split").is_null()
            )
            .then(pl.col("units") - pl.col("_prev_units"))
            .otherwise(None)
            .cast(pl.Int64),
            nav=pl.lit(None, dtype=pl.Float64),
            available_at=pl.col("date").map_elements(
                lambda d: _at(d, available_hour), return_dtype=schemas.TW_DT
            ),
        )
        .filter(pl.col("date").is_in(list(want)))
        .select(list(schemas.ETF_UNITS_DAILY.columns))
        .sort(["date", "etf_id"])
    )
    return schemas.validate(TABLE, df)


def missing_etfs(
    df: pl.DataFrame, meta: EtfMetaConfig, days: Sequence[dt.date]
) -> dict[dt.date, list[str]]:
    """每個交易日「追蹤中、該日已上市（上市日之後）、未下市」卻沒有列的 ETF。上市當天不要求。"""
    have: dict[dt.date, set[str]] = {}
    for d, sid in df.select("date", "etf_id").iter_rows():
        have.setdefault(d, set()).add(sid)
    out: dict[dt.date, list[str]] = {}
    for d in days:
        live = [
            e.etf_id
            for e in meta.etfs
            if e.units_tracked
            and e.listed_date < d
            and (e.delisted_date is None or d < e.delisted_date)
        ]
        lack = sorted(set(live) - have.get(d, set()))
        if lack:
            out[d] = lack
    return out


def require_complete(df: pl.DataFrame, meta: EtfMetaConfig, days: Sequence[dt.date]) -> None:
    """每日模式的完整性閘門（SPEC-016 AC-6）：任一天缺任一檔 → raise。"""
    lack = missing_etfs(df, meta, days)
    if lack:
        day = min(lack)
        raise DataError(
            TABLE,
            day,
            f"{len(lack)} 個交易日缺 ETF 單位數（{day} 缺 {lack[day][:8]}…）；整批不寫——"
            "缺列不得靜默寫成部分資料。新上市或下市請改 configs/etf_meta.yaml。",
        )


def units_jumps(df: pl.DataFrame, ratio: float) -> pl.DataFrame:
    """只報不修：units_delta 非 null 且單日 units 比值落在 [1/ratio, ratio] 之外的列。"""
    prev = pl.col("units") - pl.col("units_delta")
    return df.filter(
        pl.col("units_delta").is_not_null()
        & (prev > 0)
        & ((pl.col("units") / prev > ratio) | (pl.col("units") / prev < 1.0 / ratio))
    )


# ── 每日持股（T-336）──────────────────────────────────────────────────────────

HOLDINGS = "etf_holdings_daily"


def holdings_frame(
    snaps: Sequence[PcfSnapshot], available_hour: int, weight_sum_max: float
) -> pl.DataFrame:
    """PcfSnapshot → `etf_holdings_daily`。股票權重和不在 (0, weight_sum_max] → raise（AC-8）。

    available_at＝max(基準日 available_hour, 來源公布時點)：來源晚於該時點才公布（或事後修訂）就照實記晚。
    同一 (ETF, 基準日) 出現兩份 → raise。
    """
    seen: set[tuple[str, dt.date]] = set()
    rows: list[tuple[object, ...]] = []
    for s in snaps:
        key = (s.etf_id, s.base_date)
        if key in seen:
            raise DataError(HOLDINGS, s.base_date, f"{s.etf_id} 同一基準日兩份清單")
        seen.add(key)
        total = sum(w for _, _, w in s.rows)
        if not 0.0 < total <= weight_sum_max:
            raise DataError(
                HOLDINGS,
                s.base_date,
                f"{s.etf_id}：股票權重和 {total:.4f} 不在 (0, {weight_sum_max}]",
            )
        at = max(_at(s.base_date, available_hour), s.published_at)
        rows += [(s.base_date, s.etf_id, sid, sh, w, at) for sid, sh, w in s.rows]
    df = pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "etf_id": pl.String,
            "stock_id": pl.String,
            "shares": pl.Int64,
            "weight": pl.Float64,
            "available_at": schemas.TW_DT,
        },
        orient="row",
    ).sort(["date", "etf_id", "stock_id"])
    return schemas.validate(HOLDINGS, df)


# ── 指數換股事件（T-337）：由指數型 ETF 的持股增刪推導 ───────────────────────────

REBALANCE = "index_rebalance_events"
REBALANCE_SOURCE = "holdings_diff"


def rebalance_events(holdings: pl.DataFrame, index_etfs: set[str]) -> pl.DataFrame:
    """[date, etf_id, stock_id, available_at] → `index_rebalance_events`。

    每檔指數型 ETF 依基準日排序，相鄰兩份清單比成分：後一份多出的 → add、少掉的 → remove，
    effective_date＝後一份的基準日、prev_base_date＝前一份。主動式 ETF 天天換股，不在 index_etfs 就不產生事件。
    只看成分、不看股數與權重：分割、配股、申贖造成的股數變化不是換股。
    """
    rows: list[tuple[object, ...]] = []
    h = holdings.filter(pl.col("etf_id").is_in(sorted(index_etfs)))
    for (etf_id,), g in h.group_by(["etf_id"], maintain_order=True):
        snaps = (
            g.group_by("date")
            .agg(pl.col("stock_id").sort(), pl.col("available_at").max())
            .sort("date")
        )
        prev: tuple[dt.date, set[str]] | None = None
        for day, ids, at in snaps.iter_rows():
            cur = set(ids)
            if prev is not None:
                pday, pids = prev
                rows += [
                    (etf_id, s, "add", day, pday, None, REBALANCE_SOURCE, at)
                    for s in sorted(cur - pids)
                ]
                rows += [
                    (etf_id, s, "remove", day, pday, None, REBALANCE_SOURCE, at)
                    for s in sorted(pids - cur)
                ]
            prev = (day, cur)
    df = pl.DataFrame(
        rows,
        schema={
            "etf_id": pl.String,
            "stock_id": pl.String,
            "action": pl.String,
            "effective_date": pl.Date,
            "prev_base_date": pl.Date,
            "announce_date": pl.Date,
            "source": pl.String,
            "available_at": schemas.TW_DT,
        },
        orient="row",
    ).sort(["etf_id", "effective_date", "action", "stock_id"])
    return schemas.validate(REBALANCE, df)


def net_changes(
    events: pl.DataFrame, etf_id: str, after: dt.date | None, upto: dt.date
) -> tuple[set[str], set[str]]:
    """某 ETF 在 (after, upto] 期間的淨增、淨刪成分（先加後刪的暫時持股互相抵銷）。"""
    e = events.filter(
        (pl.col("etf_id") == etf_id)
        & (pl.col("effective_date") <= upto)
        & ((pl.col("effective_date") > after) if after is not None else pl.lit(True))
    ).sort("effective_date")
    added: set[str] = set()
    removed: set[str] = set()
    for sid, action in e.select("stock_id", "action").iter_rows():
        if action == "add":
            if sid in removed:
                removed.discard(sid)
            else:
                added.add(sid)
        elif sid in added:
            added.discard(sid)
        else:
            removed.add(sid)
    return added, removed
