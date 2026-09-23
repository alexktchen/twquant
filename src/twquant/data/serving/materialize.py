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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import polars as pl

from twquant.config import ServingConfig
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.composite import composite_from_signed
from twquant.data.serving.sync import upsert
from twquant.data.warehouse import TableSource
from twquant.errors import DataError, TwquantError

_log = logging.getLogger("twquant.serving")

_UNIVERSE = r"^[1-9][0-9]{3}$"  # 因子宇宙＝四碼普通股（PRD-001 §3；同 snapshot.UNIVERSE）


@dataclass(frozen=True)
class MaterializeResult:
    """單一 as_of 物化結果。"""

    as_of: dt.date
    factor_rows: int  # factor_values 寫入列數
    composite_rows: int  # composite_daily 寫入列數
    factors: int  # 成功物化的因子數


def read_since(as_of: dt.date, names: Sequence[str]) -> dt.date:
    """讀 PG 時的安全時間下界：涵蓋 `names` 裡最長的宣告回看窗（T-278）。

    **沒有下界時 `PgTableSource` 是整張表全掃**——`ml_scores` 有 3,283 萬列，
    每個 ml 因子讀一次就是一次全掃，實測單日 pipeline 卡在
    `SELECT * FROM serving.ml_scores`、吃掉 7.5 GB（2026-09-21／22 整天沒有名單即此）。

    跨度沿用 `factors.compute` 的 `lookback_days × _TRIM_SAFETY + _TRIM_BUFFER_DAYS`，
    不另立一套——兩套常數會漂移，而漂移的後果是因子少讀了歷史卻不會報錯。
    """
    from twquant.factors.compute import _TRIM_BUFFER_DAYS, _TRIM_SAFETY
    from twquant.factors.registry import get

    longest = max((get(n).lookback_days for n in names), default=0)
    return as_of - dt.timedelta(days=longest * _TRIM_SAFETY + _TRIM_BUFFER_DAYS)


# 只有這些表允許按表縮窗：它們的讀取者**全都**在因子註冊時宣告過 `tables`，而且有靜態測試
# 守著「沒有別的程式讀它」（tests/test_serving_materialize_since.py）。其餘表沿用全體最長回看。
# ml_scores 是唯一的一張：只有 `factors/ml.py` 的薄因子讀它、回看 0 天，卻被 `fundamental.sue`
# 的 850 天拖著整批載入——2026-09-23 每日 pipeline 因此在 15.3 GB 被 OOM 砍掉。
_NARROWABLE_TABLES: tuple[str, ...] = ("ml_scores",)


def read_since_by_table(as_of: dt.date, names: Sequence[str]) -> dict[str, dt.date]:
    """按表的讀取下界：只看**宣告讀這張表的因子**的回看，而不是全體最長回看。

    跨度公式與 `read_since` 相同。只對 `_NARROWABLE_TABLES` 生效；沒有任何讀取者時不給值
    （`PgTableSource` 就退回全域的 `since`）。
    """
    from twquant.factors.compute import _TRIM_BUFFER_DAYS, _TRIM_SAFETY
    from twquant.factors.registry import get

    out: dict[str, dt.date] = {}
    for table in _NARROWABLE_TABLES:
        spans = [get(n).lookback_days for n in names if table in get(n).tables]
        if spans:
            out[table] = as_of - dt.timedelta(days=max(spans) * _TRIM_SAFETY + _TRIM_BUFFER_DAYS)
    return out


def _compute_universe(
    source: TableSource,
    as_of: dt.date,
    factors: Sequence[str] | None = None,
    *,
    trim_lookback: bool = False,
    exclude: Sequence[str] = (),
) -> pl.DataFrame | None:
    """算全宇宙全因子並附 pct / z / signed_z；無可算資料回 None。

    factors（T-094）：只算這個子集（籌碼回補後重算 7 個籌碼因子，不重跑全表 7 小時）。

    trim_lookback（T-083）：依各因子宣告的 `lookback_days` 裁切歷史下界。物化是逐日
    重跑上千次的路徑，不裁切時每個因子每天都重讀六年全史（實測全期約 45 小時）。
    只動下界、不動 PIT 上界，等價性由 `tests/test_lookback_trim.py` 保證。
    子集內任一名字未註冊即 raise——打錯名字若被下面的 except 吞掉，就會「成功地什麼都沒算」。
    """
    import twquant.factors.builtins  # noqa: F401  # 觸發因子註冊
    from twquant.factors.compute import compute
    from twquant.factors.registry import get, list_factors
    from twquant.factors.standardize import standardize

    names = list(factors) if factors is not None else list_factors()
    # 明確指定 factors 時不套排除清單：那是呼叫端刻意點名的，靜默拿掉會很難察覺
    if factors is None and exclude:
        skipped = sorted(set(names) & set(exclude))
        names = [n for n in names if n not in set(exclude)]
        if skipped:
            _log.info("%s：依 serving.exclude_factors 略過 %s", as_of, ", ".join(skipped))
    for name in names:
        get(name)  # 未註冊 → TwquantError（在 try 之外，不得被吞）
    view = source.view(as_of)
    frames: list[pl.DataFrame] = []
    for name in names:
        # 單一因子失敗不中斷全宇宙物化，但一律留日誌（黃金規則 5：禁止靜默）。
        # 預期資料缺口（DataError：缺表/史料不足）記 DEBUG；非預期錯誤記 WARNING 附堆疊，
        # 讓「某因子在某些交易日悄悄消失」這類 bug 從無聲變可見（D-020）。
        try:
            frames.append(compute([name], view, dates=[as_of], trim_lookback=trim_lookback))
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
    source: TableSource,
    conn: Any,
    cfg: ServingConfig,
    as_of: dt.date,
    factors: Sequence[str] | None = None,
    *,
    trim_lookback: bool = False,
) -> MaterializeResult:
    # D-057：子集 ⊇ composite 集合時照常重算 composite_daily（與全因子模式同結果）；
    # 部分重疊則兩害皆有——重算會得到不同的綜合分，不重算則兩張表對同一因子有兩個版本。
    comp_set = set(cfg.composite_factors)
    recompute_composite = factors is None or comp_set <= set(factors)
    if factors is not None and not recompute_composite:
        clash = sorted(comp_set & set(factors))
        if clash:
            raise TwquantError(
                f"因子子集與 composite 集合部分重疊：{', '.join(clash)}"
                f"——缺少 {', '.join(sorted(comp_set - set(factors)))}。"
                "請把 composite 因子補齊（子集完整包含即可重算 composite_daily），"
                "或改用全因子物化。"
            )
    merged = _compute_universe(
        source, as_of, factors, trim_lookback=trim_lookback, exclude=cfg.exclude_factors
    )
    if merged is None:
        return MaterializeResult(as_of, 0, 0, 0)

    factor_values = merged.select("factor", "stock_id", "date", "value", "z", "pct")
    cross = merged.group_by(["date", "factor"]).agg(
        pl.col("value").mean().alias("mean"),
        pl.col("value").std().alias("std"),
        pl.len().cast(pl.Int32).alias("n_universe"),
    )
    if not recompute_composite:
        # 子集模式且與 composite 集合無交集：只寫因子表，composite_daily 維持既有值
        with conn.cursor() as cur:
            upsert(cur, cfg.pg_schema, sch.spec_of("factor_values"), factor_values)
            upsert(cur, cfg.pg_schema, sch.spec_of("factor_cross_stats"), cross)
        conn.commit()
        return MaterializeResult(
            as_of, factor_values.height, 0, int(factor_values["factor"].n_unique())
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
    source: TableSource,
    cfg: ServingConfig,
    as_of: dt.date | None = None,
    factors: Sequence[str] | None = None,
    *,
    trim_lookback: bool = False,
) -> MaterializeResult:
    """物化單一交易日（as_of 預設為最新行情日）。factors 見 `_compute_universe`（T-094）。

    `trim_lookback=True` 依各因子宣告的 `lookback_days` 裁切歷史（T-083）——
    只算幾支短視窗因子時差別極大：全歷史模式要掃 `broker_trades` 的 5,770 萬列。
    """
    day = as_of or source.latest_date("prices_daily")
    if day is None:
        raise DataError("prices_daily", None, "無行情資料，無法物化因子")
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        return _materialize_on(source, conn, cfg, day, factors, trim_lookback=trim_lookback)


def materialize_range(
    source: TableSource,
    cfg: ServingConfig,
    start: dt.date,
    end: dt.date,
    factors: Sequence[str] | None = None,
    *,
    trim_lookback: bool = False,
) -> list[MaterializeResult]:
    """逐日物化 [start, end]（僅有行情資料的交易日）；as_of 逐日推進，維持 PIT。"""
    days = source.dates_in_range("prices_daily", start, end)
    with store.connect(cfg) as conn:
        sch.ensure_schema(conn, cfg.pg_schema)
        return [
            _materialize_on(source, conn, cfg, d, factors, trim_lookback=trim_lookback)
            for d in days
        ]
