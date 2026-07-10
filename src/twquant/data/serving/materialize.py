"""供應層因子物化：PitView → factor_values / factor_cross_stats / composite_daily（SPEC-009 §3）。

對每個 as_of，用 PitView 對因子宇宙（四碼普通股）算全部註冊因子 → 標準化（winsorize→z）→
signed_z + 綜合分位 → upsert PG。橫斷面量（z / pct / composite pct）一天算一次、落地供查表，
取代舊 scorecard 每請求全宇宙重算。逐日推進 as_of 維持 PIT（不以最新資料回填歷史值）。

本模組不 import psycopg——連線由 store 提供。橫斷面演算法與 monitoring.snapshot 的
stock_factor_scorecard 一致，差別只在此處寫全宇宙進 PG、後者回單股。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import polars as pl

from twquant.config import ServingConfig
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import partition_dates, upsert
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError

_UNIVERSE = r"^[1-9][0-9]{3}$"  # 因子宇宙＝四碼普通股（PRD-001 §3；同 snapshot.UNIVERSE）


@dataclass(frozen=True)
class MaterializeResult:
    """單一 as_of 物化結果。"""

    as_of: dt.date
    factor_rows: int  # factor_values 寫入列數
    composite_rows: int  # composite_daily 寫入列數
    factors: int  # 成功物化的因子數


def _compute_universe(wh: Warehouse, as_of: dt.date) -> pl.DataFrame | None:
    """算全宇宙全因子並附 pct / z / signed_z；無可算資料回 None。"""
    import twquant.factors.builtins  # noqa: F401  # 觸發因子註冊
    from twquant.factors.compute import compute
    from twquant.factors.registry import get, list_factors
    from twquant.factors.standardize import standardize

    view = wh.view(as_of)
    frames: list[pl.DataFrame] = []
    for name in list_factors():
        try:
            frames.append(compute([name], view, dates=[as_of]))
        except Exception:  # noqa: BLE001  # 該日史料不足/缺表的因子略過（同 scorecard）
            continue
    if not frames:
        return None
    fdf = (
        pl.concat(frames, how="vertical")
        .filter(pl.col("stock_id").str.contains(_UNIVERSE))
        .drop_nulls("value")
    )
    if fdf.is_empty():
        return None

    directions = {n: get(n).direction for n in fdf["factor"].unique().to_list()}
    n_over = pl.col("value").count().over(["date", "factor"])
    ranked = fdf.with_columns(
        (
            (pl.col("value").rank().over(["date", "factor"]) - 1)
            / pl.max_horizontal(n_over - 1, pl.lit(1))
        ).alias("pct")
    )
    zdf = standardize(fdf).rename({"value": "z"}).select("date", "stock_id", "factor", "z")
    merged = ranked.join(zdf, on=["date", "stock_id", "factor"], how="inner").filter(
        pl.col("z").is_finite()
    )
    if merged.is_empty():
        return None
    dir_expr = pl.col("factor").replace_strict(directions, return_dtype=pl.Int64)
    return merged.with_columns((pl.col("z") * dir_expr).alias("signed_z"))


def _materialize_on(
    wh: Warehouse, conn: Any, cfg: ServingConfig, as_of: dt.date
) -> MaterializeResult:
    merged = _compute_universe(wh, as_of)
    if merged is None:
        return MaterializeResult(as_of, 0, 0, 0)

    factor_values = merged.select("factor", "stock_id", "date", "value", "z", "pct")
    cross = merged.group_by(["date", "factor"]).agg(
        pl.col("value").mean().alias("mean"),
        pl.col("value").std().alias("std"),
        pl.len().cast(pl.Int32).alias("n_universe"),
    )
    comp = merged.group_by(["date", "stock_id"]).agg(
        pl.col("signed_z").mean().alias("signed_z_mean")
    )
    cnt = pl.col("signed_z_mean").count().over("date")
    comp = comp.with_columns(
        (
            (pl.col("signed_z_mean").rank().over("date") - 1)
            / pl.max_horizontal(cnt - 1, pl.lit(1))
        ).alias("pct")
    )

    with conn.cursor() as cur:
        upsert(cur, cfg.pg_schema, sch.spec_of("factor_values"), factor_values)
        upsert(cur, cfg.pg_schema, sch.spec_of("factor_cross_stats"), cross)
        upsert(cur, cfg.pg_schema, sch.spec_of("composite_daily"), comp)
    conn.commit()
    return MaterializeResult(
        as_of, factor_values.height, comp.height, int(factor_values["factor"].n_unique())
    )


def materialize_day(
    wh: Warehouse, cfg: ServingConfig, as_of: dt.date | None = None
) -> MaterializeResult:
    """物化單一交易日（as_of 預設為最新行情日）。"""
    day = as_of or wh.latest_date("prices_daily")
    if day is None:
        raise DataError("prices_daily", None, "無行情資料，無法物化因子")
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        return _materialize_on(wh, conn, cfg, day)


def materialize_range(
    wh: Warehouse, cfg: ServingConfig, start: dt.date, end: dt.date
) -> list[MaterializeResult]:
    """逐日物化 [start, end]（僅有行情分區的交易日）；as_of 逐日推進，維持 PIT。"""
    days = [d for d in partition_dates(wh, "prices_daily") if start <= d <= end]
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        return [_materialize_on(wh, conn, cfg, d) for d in days]
