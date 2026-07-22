"""監控快照建構（SPEC-007）。

從 clean 資料湖組出前端儀表板所需的全部資料：等權市場指數、個股 K 線、因子健檢
（RankIC）、策略回測（權益曲線＋指標）、法人流向、月營收、容量、擷取狀態。

同一組純函數同時供：
  * 即時 API（`monitoring.api`，React 前端逐次 fetch）；
  * 離線快照（`frontend/scripts/export_snapshot.py` 寫 data.json，供自包含單檔 fallback）。

因子 IC 一律經 `Warehouse.view(as_of)`（PitView，黃金規則 1 反前視）；其餘為監控用
唯讀顯示查詢（與退役前的 Streamlit dashboard 同路徑）。
"""

from __future__ import annotations

import datetime as dt
import math
import re
from pathlib import Path
from typing import TYPE_CHECKING, cast

import polars as pl

from twquant.config import ServingConfig, load_config
from twquant.data.serving.composite import composite_from_signed
from twquant.errors import ConfigError, DataError

if TYPE_CHECKING:
    from twquant.data.calendar import TradingCalendar
    from twquant.data.warehouse import TableSource, Warehouse

# ── 快照參數（named constants，非邏輯內魔術數字）─────────────────────────
UNIVERSE = r"^[1-9][0-9]{3}$"  # 四碼普通股，排除權證/ETF 尾碼
WINDOW_START = dt.date(2025, 8, 1)  # 連續期起點，避開 TWSE 2026-01 後缺口
WINDOW_END = dt.date(2025, 12, 31)
SNAPSHOT_TOP_N = 8  # 離線快照內嵌的個股數（data.json 大小取捨）
LIQUID_TOP_N = 150  # 即時個股選單涵蓋的成交值前 N 檔
IC_HORIZONS = (1, 5, 10, 20)  # 因子前向報酬視窗（交易日）；1 供短線因子（如 price.mom_1d）

_REPO = Path(__file__).resolve().parents[3]
CONFIGS_DIR = _REPO / "configs"
STRATEGY_PATH = CONFIGS_DIR / "strategies" / "sample_revenue_trust.yaml"
COSTS_PATH = CONFIGS_DIR / "costs.yaml"
SERVING_PATH = CONFIGS_DIR / "serving.yaml"


def _composite_factors() -> list[str]:
    """綜合分有效子集（D-020）：讀 serving.yaml；缺檔/驗證失敗 → ServingConfig 預設子集。

    live 現算（本模組）與 PG 物化（materialize.py）共用同一子集，維持綜合分定義單一真相源。
    """
    try:
        cfg = load_config(SERVING_PATH, ServingConfig)
    except ConfigError:  # 缺檔或驗證失敗 → 用內建預設子集（非靜默吞資料錯，僅 config 後備）
        cfg = ServingConfig()
    return list(cfg.composite_factors)


_SID_RE = re.compile(r"[0-9A-Z]{2,6}")  # 個股代號白名單（API 路徑參數防 SQL 注入）

# ── 擷取後台（熱力圖 / 漏抓清單）───────────────────────────────────────
# 每日來源軌（table, source, 中文標籤）——後台熱力圖與缺口清單共用的唯一真相源。
INGEST_TRACKS: tuple[tuple[str, str, str], ...] = (
    ("prices_daily", "twse", "上市行情"),
    ("prices_daily", "tpex", "上櫃行情"),
    ("institutional_daily", "twse", "三大法人"),
    ("margin_daily", "twse", "融資融券"),
    ("index_daily", "twse", "加權指數"),
    ("index_daily", "tpex", "櫃買指數"),
)
MATRIX_WINDOW = 30  # 熱力圖預設回看的交易日數
TWSE_BACKFILL_WARN = 20  # 超過此天數的 TWSE 免費回補易觸發 ban（D-015）→ 清單標紅、改建議 FinMind


# ── 市場等權指數 K 線 ─────────────────────────────────────────────────
def market_index(wh: Warehouse) -> list[dict[str, object]]:
    """等權價格指數（基期 1000、四碼普通股）的日 OHLC + 成交金額（億）。"""
    df = wh.sql(
        "WITH base AS (SELECT stock_id, arg_min(close,date) b FROM prices_daily "
        f"WHERE regexp_matches(stock_id,'{UNIVERSE}') AND close>0 GROUP BY stock_id) "
        "SELECT p.date::VARCHAR d, round(1000*avg(p.open/b.b),2) o, round(1000*avg(p.high/b.b),2) h, "
        "round(1000*avg(p.low/b.b),2) l, round(1000*avg(p.close/b.b),2) c, "
        "round(sum(p.amount)/1e8,0) amt "
        f"FROM prices_daily p JOIN base b USING(stock_id) WHERE regexp_matches(p.stock_id,'{UNIVERSE}') "
        "GROUP BY p.date ORDER BY p.date"
    )
    return df.to_dicts()


# ── 個股 K 線 ─────────────────────────────────────────────────────────
def liquid_stocks(wh: Warehouse, limit: int = LIQUID_TOP_N) -> list[str]:
    """成交金額前 limit 檔四碼普通股代號（即時選單用）。"""
    df = wh.sql(
        f"SELECT stock_id FROM prices_daily WHERE regexp_matches(stock_id,'{UNIVERSE}') "
        f"GROUP BY stock_id ORDER BY avg(amount) DESC LIMIT {int(limit)}"
    )
    return [str(x) for x in df["stock_id"].to_list()]


def market_indices(wh: Warehouse) -> dict[str, object]:
    """官方指數序列：加權指數 TAIEX（日 OHLC）與櫃買指數 TPEX（日收盤）。

    資料湖尚無 index_daily（未 backfill-index）時回空序列，不報錯（前端顯示提示）。
    """
    if not (wh.root / "clean" / "index_daily").exists():
        return {"taiex": [], "tpex": []}
    taiex = wh.sql(
        "SELECT date::VARCHAR d, open o, high h, low l, close c FROM index_daily "
        "WHERE index_id='TAIEX' ORDER BY date"
    ).to_dicts()
    tpex = wh.sql(
        "SELECT date::VARCHAR d, close c FROM index_daily WHERE index_id='TPEX' ORDER BY date"
    ).to_dicts()
    return {"taiex": taiex, "tpex": tpex}


def stock_meta_map(wh: Warehouse) -> dict[str, dict[str, object]]:
    """代號 → {name, industry, market}（中文名 / 族群顯示用）。無 stock_meta 時回空 dict。"""
    if not (wh.root / "clean" / "stock_meta").exists():
        return {}
    rows = wh.sql("SELECT stock_id, name, industry, market FROM stock_meta").to_dicts()
    return {
        str(r["stock_id"]): {"name": r["name"], "industry": r["industry"], "market": r["market"]}
        for r in rows
    }


# 因子中文名（後端 API 直接可讀；與前端 FACTOR_META 對照一致）
FACTOR_ZH: dict[str, str] = {
    "fundamental.revenue_yoy": "月營收 YoY",
    "fundamental.revenue_accel": "月營收 YoY 加速",
    "chip.trust_net_10d": "投信10日買超占量",
    "chip.foreign_net_10d": "外資10日買超占量",
    "chip.trust_streak": "投信連買天數",
    "chip.margin_velocity_5d": "5日融資增速",
    "price.mom_20d": "20日動能",
    "price.mom_60d": "60日動能",
    "price.high_52w": "52週高接近度",
    "price.reversal_5d": "5日短期反轉",
    "price.vol_20d": "20日波動率",
    "price.rs_market_20d": "相對大盤強弱",
    "volume.vol_ratio_5d": "5日量比",
    "volume.turnover_z_20d": "20日量能Z",
    "chip.foreign_streak": "外資連買天數",
    "chip.inst_net_10d": "三大法人10日買超占量",
    "chip.short_margin_ratio": "券資比",
    "price.overnight_ret_20d": "20日隔夜累報",
    "price.intraday_ret_20d": "20日盤中累報",
    "price.residual_reversal_5d": "5日產業殘差反轉",
    "price.mom_1d": "1日動能",
}


def stock_factor_scorecard(
    wh: Warehouse, sid: str, as_of: dt.date | None = None
) -> dict[str, object]:
    """個股因子體檢卡：每因子的值、橫斷面 z / 百分位、方向、傾向 + 綜合宇宙排名 + 中文名/族群。

    描述性定位（該股在各因子的橫斷面相對位置），非對該股的預測力（RankIC 是宇宙屬性，
    見因子健檢頁）。sid 經白名單驗證（防 SQL 注入）。
    """
    if not _SID_RE.fullmatch(sid):
        raise DataError("stock_meta", None, f"非法股票代號：{sid!r}")
    import twquant.factors.builtins  # noqa: F401  # 觸發因子註冊
    from twquant.factors.compute import compute
    from twquant.factors.registry import get, list_factors
    from twquant.factors.standardize import standardize

    day = as_of or wh.latest_date("prices_daily")
    meta = stock_meta_map(wh).get(sid, {})
    base: dict[str, object] = {
        "sid": sid,
        "name": meta.get("name"),
        "industry": meta.get("industry"),
        "market": meta.get("market"),
        "as_of": day.isoformat() if day else None,
        "factors": [],
        "composite": None,
    }
    if day is None:
        return base

    view = wh.view(day)
    frames: list[pl.DataFrame] = []
    for name in list_factors():
        try:
            frames.append(compute([name], view, dates=[day]))
        except Exception:  # noqa: BLE001  # 略過此日無法計算的因子（如 52 週高史料不足）
            continue
    if not frames:
        return base
    fdf = (
        pl.concat(frames, how="vertical")
        .filter(pl.col("stock_id").str.contains(UNIVERSE))
        .drop_nulls("value")
    )
    if fdf.is_empty():
        return base

    directions = {n: get(n).direction for n in fdf["factor"].unique().to_list()}
    n_over = pl.col("value").count().over(["date", "factor"])
    ranked = fdf.with_columns(
        (
            (pl.col("value").rank().over(["date", "factor"]) - 1)
            / pl.max_horizontal(n_over - 1, pl.lit(1))
        ).alias("pct")
    )
    zdf = standardize(fdf).rename({"value": "z"}).select("date", "stock_id", "factor", "z")
    # 濾掉 z 為 NaN/Inf 的因子（如某因子橫斷面標準差為 0 → z=NaN）：否則會污染綜合
    # 平均（NaN 傳染）並得靠 _sanitize 事後補救。drop_nulls 只除 NULL 不除 NaN，故需 is_finite。
    merged = ranked.join(zdf, on=["date", "stock_id", "factor"], how="inner").filter(
        pl.col("z").is_finite()
    )
    # 綜合分 = D-020 有效子集的 direction 調整後 z（signed_z）均值，再取宇宙百分位排名
    # （非全因子等權——等權在台股實證無預測力，見 D-020）；體檢卡下方仍列全部因子。
    dir_expr = pl.col("factor").replace_strict(directions, return_dtype=pl.Int64)
    merged = merged.with_columns((pl.col("z") * dir_expr).alias("signed_z"))
    comp = composite_from_signed(
        merged.select("date", "stock_id", "factor", "signed_z"), _composite_factors()
    )

    factors_out: list[dict[str, object]] = []
    for r in merged.filter(pl.col("stock_id") == sid).sort("factor").to_dicts():
        d = int(directions[r["factor"]])
        z = float(r["z"])
        factors_out.append(
            {
                "factor": r["factor"],
                "zh": FACTOR_ZH.get(r["factor"], r["factor"]),
                "category": r["factor"].split(".")[0],
                "value": round(float(r["value"]), 4),
                "z": round(z, 2),
                "pct": round(float(r["pct"]), 3),
                "direction": d,
                "signed_z": round(z * d, 2),  # >0 站多方、<0 站空方
            }
        )
    base["factors"] = factors_out
    crows = comp.filter(pl.col("stock_id") == sid).to_dicts()
    if crows:
        base["composite"] = {
            "signed_z_mean": round(float(crows[0]["signed_z_mean"]), 2),
            "pct": round(float(crows[0]["pct"]), 3),
            "n_universe": int(comp.height),
        }
    return cast("dict[str, object]", _sanitize(base))


def stock_ohlc(wh: Warehouse, sid: str) -> list[dict[str, object]]:
    """單一個股的日 OHLC + 成交量。sid 經白名單驗證（防 SQL 注入）。"""
    if not _SID_RE.fullmatch(sid):
        raise DataError("prices_daily", None, f"非法股票代號：{sid!r}")
    df = wh.sql(
        "SELECT date::VARCHAR d, open o, high h, low l, close c, volume v FROM prices_daily "
        f"WHERE stock_id='{sid}' ORDER BY date"
    )
    return df.to_dicts()


# ── 因子健檢（RankIC）─────────────────────────────────────────────────
def factor_reports(
    wh: Warehouse,
    start: dt.date = WINDOW_START,
    end: dt.date = WINDOW_END,
    horizons: tuple[int, ...] = IC_HORIZONS,
) -> list[dict[str, object]]:
    """全註冊因子在因子日期 [start, end] 上的多視窗 RankIC（經 PitView，反前視）。

    因子日期限「連續期」[start, end]（避開 2026-01 後 TWSE 缺口污染 IC，與回測同窗）；
    前向報酬所需未來價自然延伸至 end 之後（正常）。前端以此區間標示。無有效 10 日 t 值
    的因子（如 52 週高需 252 日）會被略過。
    """
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.factors.compute import compute
    from twquant.factors.ic import compute_ic_report
    from twquant.factors.registry import get, list_factors

    as_of = wh.latest_date("prices_daily")
    if as_of is None:
        return []
    view = wh.view(as_of)
    prices = (
        view.table("prices_daily")
        .select("date", "stock_id", "close")
        .filter(pl.col("stock_id").str.contains(UNIVERSE))
        .collect()
    )
    out: list[dict[str, object]] = []
    for name in list_factors():
        try:
            fdf = (
                compute([name], view)
                .filter(pl.col("stock_id").str.contains(UNIVERSE))
                .filter(pl.col("date") >= start)
                .filter(pl.col("date") <= end)  # 限連續期，避開 2026-01 後 TWSE 缺口
            )
            rep = compute_ic_report(name, fdf, prices, list(horizons))
        except Exception:  # noqa: BLE001  # 略過此窗無法檢驗的因子（如 52 週高需 252 日）
            continue
        out.append(
            {
                "name": name,
                "direction": get(name).direction,
                "horizons": [
                    {
                        "h": h.horizon,
                        "ic": round(h.ic_mean, 4),
                        "t": round(h.t_stat, 2),
                        "ir": round(h.ic_ir, 3),
                        "ls": round(h.ls_spread, 4),
                        "n": h.n_days,
                    }
                    for h in rep.horizons
                ],
            }
        )
    # 丟掉在此窗無有效 10 日 t 的因子。NaN/Inf 也算無效——52 週高需 252 日，連續期
    # 樣本不足會得 NaN；此處在 _sanitize（NaN→None）之前，故須以 isfinite 判定而非
    # `is not None`（NaN is not None 為 True，會漏放行後被 sanitize 轉 null 讓前端爆）。
    return [f for f in out if _has_valid_ten_day_t(f)]


def _has_valid_ten_day_t(factor: dict[str, object]) -> bool:
    horizons = factor["horizons"]
    if not isinstance(horizons, list):
        return False
    t = next((h["t"] for h in horizons if h["h"] == 10), None)
    return isinstance(t, int | float) and not isinstance(t, bool) and math.isfinite(t)


# ── 策略回測 ─────────────────────────────────────────────────────────
def backtest_result(
    wh: TableSource,
    start: dt.date = WINDOW_START,
    end: dt.date = WINDOW_END,
    strat_path: Path = STRATEGY_PATH,
    cost_path: Path = COSTS_PATH,
) -> dict[str, object]:
    """回測權益曲線 + 成本後績效指標（純量化，不需 LLM）。"""
    from twquant.backtest.costs import CostConfig
    from twquant.backtest.engine import run_backtest
    from twquant.config import load_config
    from twquant.strategy.config import load_strategy

    strat = load_strategy(strat_path)
    cost = load_config(cost_path, CostConfig)
    res = run_backtest(strat, cost, start, end, wh)
    m = res.metrics
    return {
        "name": strat.name,
        "window": f"{start}~{end}",
        "equity": [{"d": e.date.isoformat(), "e": round(e.equity, 0)} for e in res.equity],
        "metrics": {
            "sharpe": round(m.sharpe, 2),
            "cagr": round(m.cagr, 4),
            "mdd": round(m.mdd, 4),
            "sortino": round(m.sortino, 2),
            "calmar": round(m.calmar, 2),
            "win_rate": round(m.win_rate, 3),
            "turnover": round(m.turnover, 2),
            "total_ret": round(res.final_equity / res.initial_capital - 1, 4),
            "n_trades": len(res.ledger),
        },
    }


# ── 法人流向 / 月營收 ─────────────────────────────────────────────────
def institutional(wh: Warehouse) -> list[dict[str, object]]:
    """最新交易日投信買超前 15（含外資淨額與收盤）。"""
    inst_date = wh.latest_date("institutional_daily")
    if inst_date is None:
        return []
    return wh.sql(
        "SELECT i.stock_id sid, i.trust_net trust, i.foreign_net frgn, p.close px "
        "FROM institutional_daily i LEFT JOIN prices_daily p USING(date,stock_id) "
        f"WHERE i.date=DATE '{inst_date}' ORDER BY i.trust_net DESC LIMIT 15"
    ).to_dicts()


def revenue(wh: Warehouse) -> tuple[str | None, list[dict[str, object]]]:
    """最新月營收 YoY 成長前 15（營收 > 5 億）；回傳 (年月, 明細)。

    尚未擷取 monthly_revenue（只抓了行情的資料湖）→ 回 (None, [])，不讓 /api/meta 因單一
    可選表缺席而 500（比照 market_indices/stock_meta_map/sector_flows 的存在性守衛）。
    """
    if not (wh.root / "clean" / "monthly_revenue").exists():
        return None, []
    ym = wh.sql("SELECT max(year_month) ym FROM monthly_revenue")["ym"][0]
    if ym is None:
        return None, []
    rows = wh.sql(
        "SELECT stock_id sid, round(revenue/1e8,1) rev, round(yoy*100,1) yoy FROM monthly_revenue "
        f"WHERE year_month='{ym}' AND revenue>5e8 AND yoy IS NOT NULL ORDER BY yoy DESC LIMIT 15"
    ).to_dicts()
    return str(ym), rows


# ── 容量 ─────────────────────────────────────────────────────────────
def capacity(
    wh: Warehouse, as_of: dt.date | None = None, strat_path: Path = STRATEGY_PATH
) -> dict[str, object]:
    """策略資金容量上限（目標權重 × 20 日 ADV × 參與率上限）。"""
    from twquant.backtest.capacity import estimate_capacity
    from twquant.strategy.build import build_targets
    from twquant.strategy.config import load_strategy

    day = as_of or wh.latest_date("prices_daily")
    if day is None:
        return {}
    strat = load_strategy(strat_path)
    tdf = build_targets(strat, wh, day)
    weights = {r["stock_id"]: float(r["target_weight"]) for r in tdf.to_dicts()}
    if not weights:
        return {}
    adv = {
        r["stock_id"]: float(r["adv"])
        for r in wh.sql(
            "SELECT stock_id, median(amount) adv FROM prices_daily WHERE date IN "
            f"(SELECT DISTINCT date FROM prices_daily WHERE date<=DATE '{day}' "
            "ORDER BY date DESC LIMIT 20) GROUP BY stock_id"
        ).to_dicts()
    }
    cap = estimate_capacity(weights, adv)
    return {
        "twd": round(cap.capacity_twd, 0),
        "binding": cap.binding_stock,
        "n_positions": len(weights),
    }


# ── 擷取狀態（涵蓋 / 缺口 / 最近失敗）─────────────────────────────────
def coverage(wh: Warehouse) -> list[dict[str, object]]:
    """各來源在其涵蓋區間的交易日數與跨度（供總覽卡片）。"""
    plan = [
        ("prices_daily", "twse", "上市"),
        ("prices_daily", "tpex", "上櫃"),
        ("institutional_daily", "twse", "三大法人"),
        ("margin_daily", "twse", "融資券"),
    ]
    out: list[dict[str, object]] = []
    for table, source, label in plan:
        dates = wh.source_dates(table, source)
        out.append(
            {
                "label": label,
                "days": len(dates),
                "span": f"{dates[0]}~{dates[-1]}" if dates else "—",
            }
        )
    return out


def ingest_status(wh: Warehouse) -> dict[str, object]:
    """擷取涵蓋、以交易日曆計缺口、最後執行時點與最近失敗（Streamlit 擷取狀態即時版）。"""
    from twquant.data import calendar as cal
    from twquant.reporting.ingest_log import last_run, read_ingest_log, recent_failures

    calendar = cal.load(wh)  # 交易日曆：缺口以開市日為準（假日不算缺口）
    plan = [
        ("prices_daily", "twse", "上市行情"),
        ("prices_daily", "tpex", "上櫃行情"),
        ("institutional_daily", "twse", "三大法人"),
        ("margin_daily", "twse", "融資融券"),
    ]
    rows: list[dict[str, object]] = []
    for table, source, label in plan:
        dates = wh.source_dates(table, source)
        if not dates:
            rows.append({"label": label, "span": "—", "days": 0, "gaps": 0})
            continue
        gaps = calendar.missing(dates, dates[0], dates[-1])
        rows.append(
            {
                "label": label,
                "span": f"{dates[0]} ~ {dates[-1]}",
                "days": len(dates),
                "gaps": len(gaps),
            }
        )
    data_dir = wh.root
    log = read_ingest_log(data_dir)
    n_fail = 0 if log.is_empty() else log.filter(pl.col("status") == "failed").height
    fails = recent_failures(data_dir, limit=8)
    fail_rows = (
        []
        if fails.is_empty()
        else fails.select("attempt_ts", "source", "table", "date", "detail").to_dicts()
    )
    return {
        "sources": rows,
        "last_run": last_run(data_dir),
        "records": 0 if log.is_empty() else log.height,
        "failed": n_fail,
        "recent_failures": fail_rows,
    }


# ── 擷取後台：涵蓋矩陣（熱力圖）與漏抓清單 ────────────────────────────
def _latest_status_by_date(
    log: pl.DataFrame, source: str, table: str
) -> dict[str, tuple[str, str]]:
    """該 (source, table) 每個日期「最近一次」擷取嘗試的 (status, detail)。

    同日多次嘗試（先失敗後成功）取最新一筆——故補上後不再顯示為失敗。
    """
    if log.is_empty():
        return {}
    sub = log.filter((pl.col("source") == source) & (pl.col("table") == table))
    if sub.is_empty():
        return {}
    agg = (
        sub.sort("attempt_ts")
        .group_by("date", maintain_order=True)
        .agg(pl.last("status").alias("status"), pl.last("detail").alias("detail"))
    )
    return {r["date"]: (r["status"], r["detail"] or "") for r in agg.iter_rows(named=True)}


def _failed_dates(latest: dict[str, tuple[str, str]], present: set[dt.date]) -> dict[str, str]:
    """最近一次為 failed 且該分區仍不存在的日期 → 失敗細節（已補上的不算）。"""
    out: dict[str, str] = {}
    for iso, (status, detail) in latest.items():
        if status != "failed":
            continue
        try:
            day = dt.date.fromisoformat(iso)
        except ValueError:
            continue  # 月營收等非日粒度（YYYY-MM）在此略過
        if day not in present:
            out[iso] = detail
    return out


def _classify_cell(
    day: dt.date,
    iso: str,
    present: set[dt.date],
    failed: dict[str, str],
    first: dt.date | None,
    last: dt.date | None,
) -> str:
    """單格狀態：present（有）/ failed（最近嘗試失敗）/ missing（涵蓋區間內漏抓）/ none（尚未涵蓋）。"""
    if day in present:
        return "present"
    if iso in failed:
        return "failed"
    if first is not None and last is not None and first <= day <= last:
        return "missing"
    return "none"


def ingest_matrix(
    wh: Warehouse,
    start: dt.date | None = None,
    end: dt.date | None = None,
    window: int = MATRIX_WINDOW,
) -> dict[str, object]:
    """dataset × 交易日 的擷取涵蓋矩陣（present/missing/failed/none），供後台熱力圖。

    交易日以資料湖交易日曆為準（假日不算漏抓）；未帶區間時取最近 window 個交易日。
    """
    from twquant.data import calendar as cal
    from twquant.reporting.ingest_log import last_run, read_ingest_log

    calendar = cal.load(wh)
    sessions = calendar.sessions
    if not sessions:
        return {"datasets": [], "dates": [], "cells": {}, "last_run": None, "latest": None}
    hi = end or sessions[-1]
    if start is not None:
        lo = start
    else:
        upto = [d for d in sessions if d <= hi]
        lo = upto[-window] if len(upto) >= window else upto[0]
    dates = calendar.between(lo, hi)
    date_isos = [d.isoformat() for d in dates]
    log = read_ingest_log(wh.root)

    datasets: list[dict[str, object]] = []
    cells: dict[str, dict[str, str]] = {}
    for table, source, label in INGEST_TRACKS:
        key = f"{table}:{source}"
        present_dates = wh.source_dates(table, source)
        present = set(present_dates)
        failed = _failed_dates(_latest_status_by_date(log, source, table), present)
        first = present_dates[0] if present_dates else None
        last = present_dates[-1] if present_dates else None
        row: dict[str, str] = {}
        n_missing = 0
        for day, iso in zip(dates, date_isos, strict=True):
            state = _classify_cell(day, iso, present, failed, first, last)
            row[iso] = state
            if state == "missing":
                n_missing += 1
        cells[key] = row
        datasets.append(
            {
                "key": key,
                "table": table,
                "source": source,
                "label": label,
                "span": f"{first} ~ {last}" if first else "—",
                "days": len(present),
                "window_missing": n_missing,
            }
        )
    latest_px = wh.latest_date("prices_daily")
    return {
        "datasets": datasets,
        "dates": date_isos,
        "cells": cells,
        "last_run": last_run(wh.root),
        "latest": latest_px.isoformat() if latest_px else None,
    }


def _collapse_sessions(dates: list[dt.date], calendar: TradingCalendar) -> list[list[dt.date]]:
    """把升冪日期依交易日曆相鄰性歸併成連續區段（中間無其他開市日才算同段）。"""
    runs: list[list[dt.date]] = []
    for day in dates:
        if runs and calendar.next_session(runs[-1][-1]) == day:
            runs[-1].append(day)
        else:
            runs.append([day])
    return runs


def _daily_run_entry(
    table: str, source: str, label: str, run: list[dt.date], kind: str, failed: dict[str, str]
) -> dict[str, object]:
    """單一連續缺口區段 → 清單項（含建議補抓指令與 D-015 ban 警示）。"""
    a, b = run[0], run[-1]
    if table == "index_daily":  # 指數為月批擷取 → 補抓以月為單位
        fm, tm = a.isoformat()[:7], b.isoformat()[:7]
        cmd = (
            f"twquant ingest-index --month {fm}"
            if fm == tm
            else f"twquant backfill-index --from {fm} --to {tm}"
        )
    elif len(run) == 1:
        cmd = f"twquant ingest --date {a.isoformat()} --tables {table}"
    else:
        cmd = f"twquant backfill --from {a.isoformat()} --to {b.isoformat()}"
    warn = source == "twse" and table == "prices_daily" and len(run) > TWSE_BACKFILL_WARN
    return {
        "label": label,
        "table": table,
        "source": source,
        "kind": kind,
        "from": a.isoformat(),
        "to": b.isoformat(),
        "days": len(run),
        "command": cmd,
        "warn_ban": warn,
        "detail": failed.get(a.isoformat(), ""),
    }


def _month_iter(start: str, end: str) -> list[str]:
    """[start, end]（含端點）的所有 YYYY-MM。"""
    ys, ms = (int(x) for x in start.split("-"))
    ye, me = (int(x) for x in end.split("-"))
    out: list[str] = []
    y, m = ys, ms
    while (y, m) <= (ye, me):
        out.append(f"{y}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _month_next(ym: str) -> str:
    y, m = (int(x) for x in ym.split("-"))
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def _collapse_months(months: list[str]) -> list[list[str]]:
    """把升冪 YYYY-MM 歸併成連續月份區段。"""
    runs: list[list[str]] = []
    for ym in months:
        if runs and _month_next(runs[-1][-1]) == ym:
            runs[-1].append(ym)
        else:
            runs.append([ym])
    return runs


def _revenue_gaps(wh: Warehouse) -> list[dict[str, object]]:
    """月營收缺月（以現有 year_month 的最小~最大為區間，找中間漏掉的月份）。"""
    if not (wh.root / "clean" / "monthly_revenue").exists():
        return []
    try:
        raw = wh.sql("SELECT DISTINCT year_month FROM monthly_revenue ORDER BY year_month")[
            "year_month"
        ].to_list()
    except Exception:  # noqa: BLE001  # 月營收表異常時不拖垮整份缺口清單
        return []
    months = [str(m) for m in raw if m is not None]
    if not months:
        return []
    have = set(months)
    missing = [m for m in _month_iter(months[0], months[-1]) if m not in have]
    out: list[dict[str, object]] = []
    for run in _collapse_months(missing):
        a, b = run[0], run[-1]
        cmd = (
            f"twquant ingest-revenue --month {a}"
            if len(run) == 1
            else f"twquant backfill-revenue --from {a} --to {b}"
        )
        out.append(
            {
                "label": "月營收",
                "table": "monthly_revenue",
                "source": "mops",
                "kind": "missing",
                "from": a,
                "to": b,
                "days": len(run),
                "command": cmd,
                "warn_ban": False,
                "detail": "",
            }
        )
    return out


def holiday_calendar(wh: Warehouse) -> dict[str, object]:
    """官方開休市日曆摘要（供資料後台「官方開休市日曆」面板）。

    未擷取（ingest-holidays）→ {"available": False}。否則回開市日數、涵蓋區間、休市清單
    （前端依當日過濾出近期休市）與官方 vs 觀測（prices_daily）核對結果。
    """
    from twquant.data import calendar as cal

    hol = cal.official_holidays(wh)
    if hol is None or hol.is_empty():
        return {"available": False}
    tc = cal.official_calendar(wh)
    closed = (
        hol.filter(pl.col("closed"))
        .sort("holiday_date")
        .select(pl.col("holiday_date").cast(pl.Utf8).alias("date"), "weekday", "name")
        .to_dicts()
    )
    chk = cal.cross_check_official(wh)
    rng = cast("tuple[dt.date, dt.date] | None", chk["range"])
    missing = cast("list[dt.date]", chk["missing"])
    unexpected = cast("list[dt.date]", chk["unexpected"])
    return {
        "available": True,
        "years": chk["years"],
        "sessions": len(tc),
        "first": tc.sessions[0].isoformat() if tc.sessions else None,
        "last": tc.sessions[-1].isoformat() if tc.sessions else None,
        "closed": closed,
        "cross_check": {
            "missing": [d.isoformat() for d in missing],
            "unexpected": [d.isoformat() for d in unexpected],
            "range": [rng[0].isoformat(), rng[1].isoformat()] if rng else None,
        },
    }


def ingest_gaps(wh: Warehouse) -> dict[str, object]:
    """所有漏抓/失敗缺口，歸併成連續區段並附建議補抓指令（供後台漏抓清單）。"""
    from twquant.data import calendar as cal
    from twquant.reporting.ingest_log import read_ingest_log

    calendar = cal.load(wh)
    log = read_ingest_log(wh.root)
    runs: list[dict[str, object]] = []
    for table, source, label in INGEST_TRACKS:
        present_dates = wh.source_dates(table, source)
        present = set(present_dates)
        failed = _failed_dates(_latest_status_by_date(log, source, table), present)
        by_kind: dict[dt.date, str] = {}
        if present_dates:
            for day in calendar.missing(present_dates, present_dates[0], present_dates[-1]):
                by_kind[day] = "missing"
        for iso in failed:
            by_kind.setdefault(dt.date.fromisoformat(iso), "failed")
        if not by_kind:
            continue
        for run in _collapse_sessions(sorted(by_kind), calendar):
            kind = "missing" if any(by_kind[d] == "missing" for d in run) else "failed"
            runs.append(_daily_run_entry(table, source, label, run, kind, failed))
    runs.extend(_revenue_gaps(wh))
    runs.sort(key=lambda r: str(r["from"]), reverse=True)
    total = sum(cast("int", r["days"]) for r in runs)
    return {"gaps": runs, "runs": len(runs), "total_missing": total}


# ── 資金移動板塊（各產業別法人資金流）─────────────────────────────────
SECTOR_WINDOW = 20  # 板塊資金流預設回看交易日數
SECTOR_HEATMAP_TOP = 16  # 熱力圖取 |淨流入| 前 N 大族群


def sector_flows(
    wh: Warehouse, window: int = SECTOR_WINDOW, as_of: dt.date | None = None
) -> dict[str, object]:
    """各產業別（族群）近 window 交易日的三大法人資金淨流入（億元）、報酬、成交值。

    淨流入 = Σ 個股（(外資+投信+自營淨買股數) × 收盤）於窗內；族群報酬 = 族群內個股窗
    報酬（末收/首收−1）等權均值。需 stock_meta（族群）+ institutional_daily + prices_daily，
    缺任一回空。sectors 依淨流入由大到小；daily 為族群×日的每日淨流入（供熱力圖）。
    """
    empty: dict[str, object] = {
        "as_of": None,
        "window": window,
        "sectors": [],
        "dates": [],
        "daily": {},
    }
    needed = ("stock_meta", "institutional_daily", "prices_daily")
    if not all((wh.root / "clean" / t).exists() for t in needed):
        return empty
    day = as_of or wh.latest_date("prices_daily")
    if day is None:
        return empty
    w = int(window)
    # stock_meta 為 _all 分區，DuckDB hive 會加一個 date 欄 → 以子查詢只取需要欄避免 date 衝突；
    # 各 join 用明確 ON（不用 USING(date)）避免歧義。
    base = (
        f"WITH win AS (SELECT DISTINCT date FROM prices_daily WHERE date <= DATE '{day}' "
        f"ORDER BY date DESC LIMIT {w}), "
        "base AS (SELECT p.date, p.stock_id, p.close, p.amount, m.industry, "
        "coalesce(i.foreign_net,0) fn, coalesce(i.trust_net,0) tn, coalesce(i.dealer_net,0) dn "
        "FROM prices_daily p "
        "JOIN (SELECT stock_id, industry FROM stock_meta) m ON m.stock_id = p.stock_id "
        "LEFT JOIN institutional_daily i ON i.date = p.date AND i.stock_id = p.stock_id "
        "WHERE p.date IN (SELECT date FROM win) AND m.industry IS NOT NULL AND p.close > 0 "
        r"AND regexp_matches(p.stock_id,'^[1-9][0-9]{3}$'))"
    )
    sectors = wh.sql(
        base + " , per_stock AS (SELECT stock_id, any_value(industry) industry, "
        "sum((fn+tn+dn)*close) net, sum(fn*close) frgn, sum(tn*close) trust, sum(amount) amt, "
        "arg_max(close,date)/arg_min(close,date)-1 r FROM base GROUP BY stock_id) "
        "SELECT industry, count(*) n, round(sum(net)/1e8,1) net_yi, round(sum(frgn)/1e8,1) frgn_yi, "
        "round(sum(trust)/1e8,1) trust_yi, round(sum(amt)/1e8,0) amt_yi, round(avg(r)*100,2) ret_pct "
        "FROM per_stock GROUP BY industry ORDER BY net_yi DESC"
    ).to_dicts()
    daily_rows = wh.sql(
        base + " , d AS (SELECT date, industry, sum((fn+tn+dn)*close)/1e8 net "
        "FROM base GROUP BY date, industry) "
        "SELECT industry, date::VARCHAR d, round(net,1) net FROM d ORDER BY date"
    ).to_dicts()
    dates = sorted({str(r["d"]) for r in daily_rows})
    daily: dict[str, dict[str, object]] = {}
    for r in daily_rows:
        daily.setdefault(str(r["industry"]), {})[str(r["d"])] = r["net"]
    return {
        "as_of": day.isoformat(),
        "window": w,
        "sectors": sectors,
        "dates": dates,
        "daily": daily,
    }


# ── 組裝整份快照 ─────────────────────────────────────────────────────
def build_snapshot(
    wh: Warehouse, start: dt.date = WINDOW_START, end: dt.date = WINDOW_END
) -> dict[str, object]:
    """組出前端 data.json 的完整結構（NaN/Inf 清成 null）。"""
    as_of = wh.latest_date("prices_daily")
    if as_of is None:
        raise DataError("prices_daily", None, "資料湖尚無行情，無法建構快照")

    top = liquid_stocks(wh, SNAPSHOT_TOP_N)
    stocks = {sid: stock_ohlc(wh, sid) for sid in top}
    rev_ym, rev_rows = revenue(wh)

    out: dict[str, object] = {
        "meta": {
            "as_of": as_of.isoformat(),
            "window": f"{start}~{end}",
            "rev_month": rev_ym,
            "top_stocks": top,
            "coverage": coverage(wh),
        },
        "market": market_index(wh),
        "stocks": stocks,
        "factors": factor_reports(wh, start),
        "backtest": backtest_result(wh, start, end),
        "institutional": institutional(wh),
        "revenue": rev_rows,
        "capacity": capacity(wh, as_of),
    }
    return cast("dict[str, object]", _sanitize(out))


def _sanitize(o: object) -> object:
    """把非有限 float（NaN/Inf）換成 None——否則 json.dumps(allow_nan=False) 會拒寫。"""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _sanitize(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_sanitize(v) for v in o]
    return o
