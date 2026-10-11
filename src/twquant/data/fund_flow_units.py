"""`etf_meta`／`etf_units_daily` 的清洗（SPEC-016 §2，T-335）：純函數，不碰網路與 PG。

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
