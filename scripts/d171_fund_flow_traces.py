"""T-339：投信／ETF 資金流八條軌跡（SPEC-016 §4、§5；D-171 §一、§一之一）。只報不判。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d171_fund_flow_traces.py \
        --data-dir D:/project/twquant/data --report-dir D:/project/twquant/.claude/worktrees/<wt>/docs/reports

- 所有輸入經 PitView（as_of＝traces.last_date，2026-08-31）；因子在那個截點一次算完整段歷史
  （反前視性質：f(資料[:t]) ＝ f(資料)[:t]，T-338 測試守著）。行情讀到的最晚日期 > last_date → raise（D-109）。
- 事件、配對、彙總都在 `twquant.reporting.fund_flow_traces`（T-339-t 測試已凍結）。
- 產出：`<data-dir>/reports/fund_flow_traces/<日>.json`（原始數字）、`<report-dir>/fund-flow-traces-<日>.md`、
  研究登記簿一批（trial_scope＝flow:traces）。**報告不含任何判讀字句**；判讀依 D-171 §二（使用者填）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import polars as pl
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import twquant.factors.chip  # noqa: E402,F401  # 註冊 chip.margin_velocity_5d
import twquant.factors.fund_flow as ffac  # noqa: E402  # 註冊資金流因子（不在 builtins）
from twquant.backtest.costs import CostConfig  # noqa: E402
from twquant.config import (  # noqa: E402
    FundFlowConfig,
    MlConfig,
    ServingConfig,
    config_hash,
    load_config,
)
from twquant.conventions import now_tw  # noqa: E402
from twquant.data.serving.pg_view import PgTableSource  # noqa: E402
from twquant.factors.registry import get  # noqa: E402
from twquant.ml.dataset import attach_adjustment, liquid_pool, state_pool  # noqa: E402
from twquant.reporting import fund_flow_traces as ft  # noqa: E402
from twquant.reporting.research_log import append_research_log  # noqa: E402
from twquant.strategy.config import StrategyConfig  # noqa: E402

COMMON = r"^[1-9][0-9]{3}$"
CUTOFF_HOUR = 20  # D-008 訊號截點
SPLIT_DATE = dt.date(2017, 4, 28)  # 當沖降稅（REVIEW.md 第 5 條）：前後分段
DECOMPOSED_MIN = 0.5  # D-171 §一之一 第 7 點
STRATA = ("全市場", "大盤精選池", "潛伏飆股池")


def log(msg: str) -> None:
    print(f"[{now_tw():%H:%M:%S}] {msg}", flush=True)


def factor_frame(view: Any, name: str, **params: object) -> pl.DataFrame:
    fdef = get(name)
    assert fdef.fn is not None
    t0 = time.monotonic()
    out = fdef.fn(view, {**fdef.params, **params})
    df = out.collect() if isinstance(out, pl.LazyFrame) else out
    log(f"  {name}：{df.height:,} 列（{time.monotonic() - t0:.0f} 秒）")
    return df


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data-dir", required=True, help="正本 data/ 的絕對路徑（CLAUDE.md §0.2）")
    ap.add_argument("--report-dir", required=True, help="docs/reports 的絕對路徑")
    args = ap.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    for p in (args.data_dir, args.report_dir):
        if not Path(p).is_absolute():
            raise SystemExit(f"請給絕對路徑：{p}")

    cfg = load_config(ROOT / "configs" / "fund_flow.yaml", FundFlowConfig)
    t, ev_cfg, fcfg = cfg.traces, cfg.events, cfg.factors
    costs = load_config(ROOT / "configs" / "costs.yaml", CostConfig)
    cost = (
        2 * costs.commission_rate * costs.commission_discount
        + costs.tax_rate_sell
        + 2 * costs.slippage.half_spread_bps / 1e4
    )
    e0 = load_config(ROOT / "configs" / "strategies" / "lw_e0_ctrl.yaml", StrategyConfig)
    a20 = load_config(ROOT / "configs" / "ml_quiet_a20.yaml", MlConfig)
    if a20.sample_state is None:
        raise SystemExit("ml_quiet_a20.yaml 缺 sample_state")
    scfg = load_config(ROOT / "configs" / "serving.yaml", ServingConfig)
    run_date = now_tw().date().isoformat()
    log(f"last_date={t.last_date}；來回成本 {cost:.5f}")

    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        view = PgTableSource(conn, scfg.pg_schema, cache=True).view(t.last_date)
        px = (
            view.table("prices_daily")
            .select("date", "stock_id", "open", "close", "amount")
            .filter(pl.col("stock_id").str.contains(COMMON) & (pl.col("close") > 0))
            .collect()
        )
        ft.guard_last_date(px, t.last_date, "prices_daily")
        sessions = sorted(
            view.table("prices_daily").select("date").unique().collect()["date"].to_list()
        )
        events_tbl = (
            view.table("corporate_events").select("date", "stock_id", "adj_factor").collect()
        )
        log(f"行情 {px.height:,} 列、交易日 {len(sessions)}（{sessions[0]}～{sessions[-1]}）")

        log("算因子")
        pressure = factor_frame(
            view,
            "chip.etf_flow_pressure_5d",
            window=fcfg.pressure_window,
            adv_window=fcfg.adv_window,
        )
        disc_net = factor_frame(view, "chip.trust_disc_net_10d", window=fcfg.disc_window)
        disc = ffac.daily_disc(view, 10)
        log(f"  daily_disc：{disc.height:,} 列")
        sbl = ffac.sbl_sell_ratio(view, fcfg.pressure_window)
        margin = factor_frame(view, "chip.margin_velocity_5d", window=fcfg.pressure_window)
        coverage = (
            factor_frame(view, "market.etf_holdings_coverage")
            .group_by("date")
            .agg(pl.col("value").first())
            .sort("date")
        )
        rebal = (
            view.table("index_rebalance_events")
            .select("stock_id", "action", "available_at")
            .collect()
        )
        for name, df in (("pressure", pressure), ("disc", disc), ("sbl", sbl), ("margin", margin)):
            ft.guard_last_date(df, t.last_date, name)

    log("還原價、池、配對特徵")
    adj = attach_adjustment(px, events_tbl).with_columns(
        open_adj=pl.col("open") / pl.col("adj"), close_adj=pl.col("close") / pl.col("adj")
    )
    universe = px.select("date", "stock_id")
    liquid = liquid_pool(
        px,
        adv_window=fcfg.adv_window,
        min_adv_pctl=e0.universe.min_adv_pctl,
        min_price=e0.universe.min_price,
    )
    a20_liquid = liquid_pool(
        px,
        adv_window=fcfg.adv_window,
        min_adv_pctl=a20.sample_min_adv_pctl,
        min_price=a20.sample_min_price,
    )
    quiet = state_pool(
        adj.select("date", "stock_id", "close", "adj"), a20.sample_state, adjusted=True
    ).join(a20_liquid, on=["date", "stock_id"], how="semi")
    pools = {STRATA[0]: universe, STRATA[1]: liquid, STRATA[2]: quiet}
    pos = ft.with_pos(
        adj.select("date", "stock_id", "open_adj", "close_adj", "amount"), sessions
    ).sort("stock_id", "_pos")
    chars = (
        pos.with_columns(
            _r=(pl.col("close_adj") / pl.col("close_adj").shift(1).over("stock_id") - 1.0),
        )
        .with_columns(
            vol=pl.col("_r").rolling_std(20).over("stock_id"),
            mom=pl.col("close_adj") / pl.col("close_adj").shift(60).over("stock_id") - 1.0,
            adv=pl.col("amount").rolling_median(fcfg.adv_window).over("stock_id"),
        )
        .select("date", "stock_id", "vol", "mom", "adv")
    )
    rets = ft.paths(
        pos.select("date", "stock_id", "_pos", "open_adj", "close_adj"),
        list(t.horizons),
        t.pre_sessions,
    )

    log("偵測事件")
    events = ft.detect_events(
        sessions=sessions,
        rebal=rebal,
        pressure=pressure,
        disc=disc,
        disc_net=disc_net,
        sbl=sbl,
        margin=margin,
        universe=universe,
        cfg=ev_cfg,
        cutoff_hour=CUTOFF_HOUR,
    )
    lagged = []
    idx = {d: i for i, d in enumerate(sessions)}
    for lag in (x for x in t.flow_lags if x > 0):
        for name in ("E3", "E4"):
            sub = events.filter(pl.col("event") == name)
            moved = [
                (f"{name}@lag{lag}", sessions[idx[d] - lag], s)
                for _, d, s in sub.iter_rows()
                if idx[d] - lag >= 0
            ]
            lagged.append(
                pl.DataFrame(
                    moved,
                    schema={"event": pl.String, "date": pl.Date, "stock_id": pl.String},
                    orient="row",
                )
            )
    all_events = pl.concat([events, *lagged]) if lagged else events
    log(
        "事件數："
        + "、".join(f"{k} {v}" for k, v in sorted(all_events.group_by("event").len().iter_rows()))
    )

    results: dict[str, Any] = {}
    drops: dict[str, dict[str, int]] = {}
    for stratum in STRATA:
        pool = pools[stratum]
        ev_s = all_events.join(pool, on=["date", "stock_id"], how="semi")
        pb = ft.buckets(chars, pool, t.match_deciles)
        x, d = ft.matched_excess(ev_s, pb, rets, list(t.horizons), t.matched_min)
        drops[stratum] = d
        if x.is_empty():
            results[stratum] = {}
            continue
        results.update(
            ft.summarize(
                x,
                strata={stratum: pool},
                coverage=coverage,
                tcfg=t,
                cost=cost,
                split_date=SPLIT_DATE,
                decomposed_min=DECOMPOSED_MIN,
            )
        )
        log(f"{stratum}：{x.height:,} 筆有配對；丟棄 {d}")

    hz = list(t.horizons)
    report = {
        "meta": {
            "run_date": run_date,
            "first_date": str(sessions[0]),
            "last_date": str(t.last_date),
            "horizons": hz,
            "ref": max(h for h in hz if h <= 20),
            "cost": cost,
            "matched_min": t.matched_min,
            "drops": {
                "not_in_pool": sum(v["not_in_pool"] for v in drops.values()),
                "few_matches": sum(v["few_matches"] for v in drops.values()),
            },
            "drops_by_stratum": drops,
            "strata": list(STRATA),
            "config_hash": config_hash(cfg),
            "decomposed_min": DECOMPOSED_MIN,
            "split_date": str(SPLIT_DATE),
            "event_counts": dict(sorted(all_events.group_by("event").len().iter_rows())),
        },
        "results": results,
    }
    out_dir = Path(args.data_dir) / "reports" / "fund_flow_traces"
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / f"{run_date}.json"
    jpath.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    mpath = Path(args.report_dir) / f"fund-flow-traces-{run_date}.md"
    mpath.write_text(
        ft.render_markdown(json.loads(jpath.read_text(encoding="utf-8"))), encoding="utf-8"
    )
    append_research_log(args.data_dir, ft.research_entries(report, config_hash(cfg), run_date))
    log(
        f"[OK] {jpath}\n     {mpath}\n     research_log +{sum(len(v) for v in results.values())} 列（{ft.TRIAL_SCOPE}）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
