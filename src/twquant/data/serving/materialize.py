"""供應層因子物化：PitView → factor_values / factor_cross_stats / composite_daily（SPEC-009 §3）。

對每個 as_of，用 PitView 對因子宇宙（四碼普通股）算全部註冊因子 → 標準化（winsorize→z）→
signed_z + 綜合分位 → upsert PG。橫斷面量（z / pct / composite pct）一天算一次、落地供查表，
取代舊 scorecard 每請求全宇宙重算。逐日推進 as_of 維持 PIT（不以最新資料回填歷史值）。

因子計算的讀取源是 `TableSource`（`data.warehouse.TableSource`，鴨型別）——生產走
`data.serving.pg_view.PgTableSource`（D-026：全面停用 parquet，因子物化改讀 PG serving
鏡射表），測試/舊路徑可仍傳 `Warehouse`（parquet）。本模組不 import psycopg——PG 連線由
呼叫端經 `store`/`pg_view` 提供。橫斷面演算法與 monitoring.snapshot 的
stock_factor_scorecard 一致，差別只在此處寫全宇宙進 PG、後者回單股。
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

import polars as pl

from twquant.config import ServingConfig
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.composite import composite_from_signed
from twquant.data.serving.sync import upsert
from twquant.data.warehouse import TableSource
from twquant.errors import DataError

_log = logging.getLogger("twquant.serving")

_UNIVERSE = r"^[1-9][0-9]{3}$"  # 因子宇宙＝四碼普通股（PRD-001 §3；同 snapshot.UNIVERSE）


@dataclass(frozen=True)
class MaterializeResult:
    """單一 as_of 物化結果。"""

    as_of: dt.date
    factor_rows: int  # factor_values 寫入列數
    composite_rows: int  # composite_daily 寫入列數
    factors: int  # 成功物化的因子數


def _compute_universe(source: TableSource, as_of: dt.date) -> pl.DataFrame | None:
    """算全宇宙全因子並附 pct / z / signed_z；無可算資料回 None。"""
    import twquant.factors.builtins  # noqa: F401  # 觸發因子註冊
    from twquant.factors.compute import compute
    from twquant.factors.registry import get, list_factors
    from twquant.factors.standardize import standardize

    view = source.view(as_of)
    frames: list[pl.DataFrame] = []
    for name in list_factors():
        # 單一因子失敗不中斷全宇宙物化，但一律留日誌（黃金規則 5：禁止靜默）。
        # 預期資料缺口（DataError：缺表/史料不足）記 DEBUG；非預期錯誤記 WARNING 附堆疊，
        # 讓「某因子在某些交易日悄悄消失」這類 bug 從無聲變可見（D-020）。
        try:
            frames.append(compute([name], view, dates=[as_of]))
        except DataError as exc:
            _log.debug("因子 %s 於 %s 無可算資料，略過：%s", name, as_of, exc)
        except Exception as exc:  # noqa: BLE001  # 記錄後續算，不吞掉整天
            _log.warning("因子 %s 於 %s 計算失敗，略過：%s", name, as_of, exc, exc_info=exc)
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
    source: TableSource, conn: Any, cfg: ServingConfig, as_of: dt.date
) -> MaterializeResult:
    merged = _compute_universe(source, as_of)
    if merged is None:
        return MaterializeResult(as_of, 0, 0, 0)

    factor_values = merged.select("factor", "stock_id", "date", "value", "z", "pct")
    cross = merged.group_by(["date", "factor"]).agg(
        pl.col("value").mean().alias("mean"),
        pl.col("value").std().alias("std"),
        pl.len().cast(pl.Int32).alias("n_universe"),
    )
    # 綜合分：D-020 有效子集的 signed_z 均值 + 宇宙百分位（非全因子等權）。
    # factor_values/factor_cross_stats 仍含全部因子（體檢卡展示用），僅 composite 用子集。
    comp = composite_from_signed(
        merged.select("date", "stock_id", "factor", "signed_z"),
        list(cfg.composite_factors),
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
    source: TableSource, cfg: ServingConfig, as_of: dt.date | None = None
) -> MaterializeResult:
    """物化單一交易日（as_of 預設為最新行情日）。"""
    day = as_of or source.latest_date("prices_daily")
    if day is None:
        raise DataError("prices_daily", None, "無行情資料，無法物化因子")
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        return _materialize_on(source, conn, cfg, day)


def materialize_range(
    source: TableSource, cfg: ServingConfig, start: dt.date, end: dt.date
) -> list[MaterializeResult]:
    """逐日物化 [start, end]（僅有行情資料的交易日）；as_of 逐日推進，維持 PIT。"""
    days = source.dates_in_range("prices_daily", start, end)
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        return [_materialize_on(source, conn, cfg, d) for d in days]
