"""投信／ETF 資金流八條軌跡（SPEC-016 §4、§5，T-339）：純函數核心。IO 由 scripts/d171_fund_flow_traces.py 負責。

只報不判：本模組產出數字與表格，**不含任何通過／不通過字句**；判讀依使用者填的 D-171 §二。

事件（D-171 §一，寫於任何軌跡結果之前）：
- E1／E2：指數型 ETF 持股推導的 add／remove（T＝持股可用的第一個訊號日）；T−20…T 只描述。
- E3／E4：`chip.etf_flow_pressure_5d` 當日橫斷面 ≥ flow_hi_pctl 且 > 0／≤ flow_lo_pctl 且 < 0。
- E5／E6：每日投信裁量（disc）連續 streak_start 日 > 0／< 0，且之前 quiet_before 日都 ≤ 0／≥ 0（皆已知）。
- E7：季末最後 quarter_end_sessions 個交易日內，`chip.trust_disc_net_10d` 橫斷面 ≥ quarter_end_pctl。
- E8：E3 成立，且 `sbl_sell_ratio` 與 `chip.margin_velocity_5d` 都 ≥ absorb_pctl（橫斷面）。
同一股同一事件 dedup_sessions 個交易日內只取第一次。百分位都在「全市場」（四碼普通股）當日橫斷面算。
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
import polars as pl

from twquant.config import FundFlowEventsConfig, FundFlowTracesConfig

EVENT_TYPES = ("E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8")
EVENT_NAMES = {
    "E1": "指數納入",
    "E2": "指數剔除",
    "E3": "ETF 申購潮",
    "E4": "ETF 贖回潮",
    "E5": "投信裁量連買起點",
    "E6": "投信裁量連賣起點",
    "E7": "季底投信拉抬",
    "E8": "ETF 接貨（申購潮＋借券＋融資）",
}
_EV: dict[str, pl.DataType | type[pl.DataType]] = {
    "event": pl.String,
    "date": pl.Date,
    "stock_id": pl.String,
}


class LastDateError(ValueError):
    """讀到 last_date（D-109 第二保留期之前）以後的資料。"""


def guard_last_date(df: pl.DataFrame, last_date: dt.date, what: str) -> None:
    """AC-18：任何輸入的最晚日期 > last_date → raise（第二保留期不得被看到）。"""
    if not df.height:
        return
    mx = df["date"].max()
    if not isinstance(mx, dt.date):
        raise LastDateError(f"{what} 的 date 欄不是日期：{mx!r}")
    if mx > last_date:
        raise LastDateError(
            f"{what} 最晚到 {mx}，超過 last_date {last_date}（D-109 第二保留期不碰）"
        )


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema=_EV)


def with_pos(df: pl.DataFrame, sessions: Sequence[dt.date]) -> pl.DataFrame:
    """掛上交易日位置 `_pos`（不在日曆上的日期 → raise）。"""
    idx = pl.DataFrame(
        {"date": list(sessions), "_pos": list(range(len(sessions)))},
        schema={"date": pl.Date, "_pos": pl.Int64},
    )
    out = df.join(idx, on="date", how="left")
    if out["_pos"].null_count():
        bad = out.filter(pl.col("_pos").is_null())["date"].head(3).to_list()
        raise ValueError(f"日期不在交易日曆上：{bad}")
    return out


def dedup(events: pl.DataFrame, sessions: Sequence[dt.date], n: int) -> pl.DataFrame:
    """同一股同一事件：前一次（被保留的）之後 n 個交易日內再出現 → 丟掉。"""
    if events.is_empty():
        return events
    e = with_pos(events, sessions).sort("event", "stock_id", "_pos")
    keep: list[bool] = []
    last: dict[tuple[str, str], int] = {}
    for ev, sid, pos in e.select("event", "stock_id", "_pos").iter_rows():
        k = (ev, sid)
        ok = k not in last or pos - last[k] > n
        if ok:
            last[k] = pos
        keep.append(ok)
    return e.filter(pl.Series(keep)).drop("_pos").sort("event", "date", "stock_id")


def _cut(col: str, q: float) -> pl.Expr:
    """當日橫斷面第 q 分位數（線性內插）：「≥ 第 95 百分位」＝ 值 ≥ 這個數。"""
    return pl.col(col).quantile(q, "linear").over("date")


def tail_events(
    panel: pl.DataFrame, col: str, *, hi: float | None, lo: float | None, name: str
) -> pl.DataFrame:
    """panel[date, stock_id, col] 的橫斷面尾端事件：≥ hi 且 > 0，或 ≤ lo 且 < 0。"""
    p = panel.drop_nulls(col)
    cond = (
        (pl.col(col) >= _cut(col, hi)) & (pl.col(col) > 0)
        if hi is not None
        else (pl.col(col) <= _cut(col, lo if lo is not None else 0.0)) & (pl.col(col) < 0)
    )
    return p.filter(cond).select(pl.lit(name).alias("event"), "date", "stock_id")


def streak_start_events(
    disc: pl.DataFrame, sessions: Sequence[dt.date], start: int, quiet: int, *, buy: bool, name: str
) -> pl.DataFrame:
    """每日 disc 連續 start 日同號（buy：> 0；否則 < 0），且之前 quiet 日都「不是該號」且皆已知 → 事件日＝第 start 日。

    窗口以交易日位置計：該股缺任何一天就不成立（不跨缺日）。
    """
    d = with_pos(disc.select("date", "stock_id", "disc"), sessions).sort("stock_id", "_pos")
    sign = (pl.col("disc") > 0) if buy else (pl.col("disc") < 0)
    d = d.with_columns(_hit=sign.cast(pl.Int8), _known=pl.col("disc").is_not_null().cast(pl.Int8))
    span = start + quiet
    shifted = [pl.col("_pos").shift(k).over("stock_id") for k in range(span)]
    hits = [pl.col("_hit").shift(k).over("stock_id") for k in range(span)]
    known = [pl.col("_known").shift(k).over("stock_id") for k in range(span)]
    contiguous = pl.all_horizontal([(shifted[k] == pl.col("_pos") - k) for k in range(span)])
    all_known = pl.all_horizontal([known[k] == 1 for k in range(span)])
    streak = pl.all_horizontal([hits[k] == 1 for k in range(start)])
    calm = pl.all_horizontal([hits[k] == 0 for k in range(start, span)])
    return d.filter(contiguous & all_known & streak & calm).select(
        pl.lit(name).alias("event"), "date", "stock_id"
    )


def quarter_end_days(sessions: Sequence[dt.date], n: int) -> set[dt.date]:
    """每季最後 n 個交易日。"""
    by_q: dict[tuple[int, int], list[dt.date]] = {}
    for d in sessions:
        by_q.setdefault((d.year, (d.month - 1) // 3), []).append(d)
    return {d for ds in by_q.values() for d in sorted(ds)[-n:]}


def signal_date(
    available_at: dt.datetime, sessions: Sequence[dt.date], cutoff_hour: int
) -> dt.date | None:
    """資料 available_at 之後第一個「截點（cutoff_hour）≥ available_at」的交易日。"""
    for d in sessions:
        if dt.datetime.combine(d, dt.time(cutoff_hour), available_at.tzinfo) >= available_at:
            return d
    return None


def rebalance_events(
    rebal: pl.DataFrame, sessions: Sequence[dt.date], cutoff_hour: int
) -> pl.DataFrame:
    """index_rebalance_events → E1（add）／E2（remove）；T＝持股可用的第一個訊號日。"""
    rows = []
    for sid, action, at in rebal.select("stock_id", "action", "available_at").iter_rows():
        t = signal_date(at, sessions, cutoff_hour)
        if t is not None:
            rows.append(("E1" if action == "add" else "E2", t, sid))
    return pl.DataFrame(rows, schema=_EV, orient="row").unique()


def detect_events(
    *,
    sessions: Sequence[dt.date],
    rebal: pl.DataFrame,
    pressure: pl.DataFrame,
    disc: pl.DataFrame,
    disc_net: pl.DataFrame,
    sbl: pl.DataFrame,
    margin: pl.DataFrame,
    universe: pl.DataFrame,
    cfg: FundFlowEventsConfig,
    cutoff_hour: int,
) -> pl.DataFrame:
    """八種事件（去重後）。panel 類輸入都是 (date, stock_id, value)；universe＝全市場 (date, stock_id)。"""

    def _in(df: pl.DataFrame, col: str = "value") -> pl.DataFrame:
        return (
            df.join(universe, on=["date", "stock_id"], how="semi").rename({"value": col})
            if "value" in df.columns
            else df.join(universe, on=["date", "stock_id"], how="semi")
        )

    pr = _in(pressure, "pressure")
    e3 = tail_events(pr, "pressure", hi=cfg.flow_hi_pctl, lo=None, name="E3")
    e4 = tail_events(pr, "pressure", hi=None, lo=cfg.flow_lo_pctl, name="E4")
    dd = _in(disc)
    e5 = streak_start_events(dd, sessions, cfg.streak_start, cfg.quiet_before, buy=True, name="E5")
    e6 = streak_start_events(dd, sessions, cfg.streak_start, cfg.quiet_before, buy=False, name="E6")
    qe = quarter_end_days(sessions, cfg.quarter_end_sessions)
    dn = _in(disc_net, "disc_net").filter(pl.col("date").is_in(sorted(qe)))
    e7 = tail_events(dn, "disc_net", hi=cfg.quarter_end_pctl, lo=None, name="E7")
    s = _in(sbl, "sbl").drop_nulls("sbl")
    m = _in(margin, "margin").drop_nulls("margin")
    e8 = (
        e3.join(
            s.filter(pl.col("sbl") >= _cut("sbl", cfg.absorb_pctl)),
            on=["date", "stock_id"],
            how="semi",
        )
        .join(
            m.filter(pl.col("margin") >= _cut("margin", cfg.absorb_pctl)),
            on=["date", "stock_id"],
            how="semi",
        )
        .with_columns(event=pl.lit("E8"))
    )
    e12 = rebalance_events(rebal, sessions, cutoff_hour).join(
        universe, on=["date", "stock_id"], how="semi"
    )
    allv = pl.concat(
        [x.select(list(_EV)).cast(pl.Schema(_EV)) for x in (e12, e3, e4, e5, e6, e7, e8)]
    )
    return dedup(allv, sessions, cfg.dedup_sessions)


# ── 路徑與配對 ─────────────────────────────────────────────────────────────────


def paths(px: pl.DataFrame, horizons: Sequence[int], pre: int) -> pl.DataFrame:
    """(date, stock_id, open_adj, close_adj) → 每列的前向與事前報酬。

    r_k＝open(T+1+k)/open(T+1) − 1（T+1 開盤進場、T+1+k 開盤出場）；pre＝close(T)/close(T−pre) − 1。
    以該股自己的列位移，但兩端相距的**全市場交易日數**必須剛好相符（`_pos`），否則 null（不跨缺日）。
    """
    p = px.sort("stock_id", "_pos")
    cols = []
    entry = pl.col("open_adj").shift(-1).over("stock_id")
    epos = pl.col("_pos").shift(-1).over("stock_id")
    for k in horizons:
        ex = pl.col("open_adj").shift(-(1 + k)).over("stock_id")
        xpos = pl.col("_pos").shift(-(1 + k)).over("stock_id")
        ok = (
            (epos == pl.col("_pos") + 1) & (xpos == pl.col("_pos") + 1 + k) & (entry > 0) & (ex > 0)
        )
        cols.append(pl.when(ok).then(ex / entry - 1.0).otherwise(None).alias(f"r{k}"))
    back = pl.col("close_adj").shift(pre).over("stock_id")
    bpos = pl.col("_pos").shift(pre).over("stock_id")
    cols.append(
        pl.when((bpos == pl.col("_pos") - pre) & (back > 0))
        .then(pl.col("close_adj") / back - 1.0)
        .otherwise(None)
        .alias("pre")
    )
    return p.with_columns(cols).select("date", "stock_id", *[f"r{k}" for k in horizons], "pre")


def buckets(chars: pl.DataFrame, pool: pl.DataFrame, n: int) -> pl.DataFrame:
    """池內每天的 (vol, mom, adv) 各自切 n 等分（0..n−1）；任何一項 null → 不入配對。"""
    c = chars.join(pool, on=["date", "stock_id"], how="semi").drop_nulls(["vol", "mom", "adv"])
    out = c
    for col in ("vol", "mom", "adv"):
        out = out.with_columns(
            ((pl.col(col).rank("ordinal").over("date") - 1) * n // pl.col(col).count().over("date"))
            .cast(pl.Int32)
            .alias(f"_{col}_b")
        )
    return out.select("date", "stock_id", "_vol_b", "_mom_b", "_adv_b")


def matched_excess(
    events: pl.DataFrame,
    pool_buckets: pl.DataFrame,
    rets: pl.DataFrame,
    horizons: Sequence[int],
    matched_min: int,
) -> tuple[pl.DataFrame, dict[str, int]]:
    """事件股的報酬減「同日、同三個十分位、非同型事件」的配對組中位數。

    回傳 (逐事件超額, 丟棄計數)。配對股數 < matched_min → 整筆丟棄並計數（AC-17）；事件股不在池內 → 計數。
    """
    keys = ["date", "_vol_b", "_mom_b", "_adv_b"]
    rcols = [f"r{k}" for k in horizons] + ["pre"]
    ev = events.join(pool_buckets, on=["date", "stock_id"], how="left")
    not_in_pool = ev.filter(pl.col("_vol_b").is_null())
    ev = ev.drop_nulls("_vol_b")
    out = []
    drops = {"not_in_pool": not_in_pool.height, "few_matches": 0}
    base = pool_buckets.join(rets, on=["date", "stock_id"], how="left")
    for (name,), g in ev.group_by(["event"], maintain_order=True):
        excluded = g.select("date", "stock_id")
        ctrl = (
            base.join(excluded, on=["date", "stock_id"], how="anti")
            .group_by(keys)
            .agg(pl.len().alias("_n"), *[pl.col(c).median().alias(f"_m_{c}") for c in rcols])
        )
        gg = g.join(ctrl, on=keys, how="left").with_columns(pl.col("_n").fill_null(0))
        few = gg.filter(pl.col("_n") < matched_min)
        drops["few_matches"] += few.height
        kept = gg.filter(pl.col("_n") >= matched_min).join(
            rets, on=["date", "stock_id"], how="left"
        )
        out.append(
            kept.select(
                pl.lit(name).alias("event"),
                "date",
                "stock_id",
                pl.col("_n").alias("n_matched"),
                *[(pl.col(c) - pl.col(f"_m_{c}")).alias(f"x_{c}") for c in rcols],
                *[pl.col(c).alias(f"raw_{c}") for c in rcols],
            )
        )
    if not out:
        return pl.DataFrame(), drops
    return pl.concat(out).sort("event", "date", "stock_id"), drops


# ── 彙總 ──────────────────────────────────────────────────────────────────────


def block_bootstrap(values: pl.DataFrame, col: str, n: int, seed: int) -> dict[str, float | None]:
    """以「月」為區塊重抽事件的平均值：回傳 mean、95% CI、t（mean ÷ 重抽標準差）。少於 2 個月 → None。"""
    v = values.drop_nulls(col).with_columns(_m=pl.col("date").dt.strftime("%Y-%m"))
    months = v.group_by("_m").agg(pl.col(col).sum().alias("_s"), pl.len().alias("_c")).sort("_m")
    if months.height < 2:
        return {"mean": None, "ci_lo": None, "ci_hi": None, "t": None}
    s, c = months["_s"].to_numpy(), months["_c"].to_numpy()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(s), size=(n, len(s)))
    boot = s[idx].sum(axis=1) / c[idx].sum(axis=1)
    mean = float(s.sum() / c.sum())
    sd = float(boot.std(ddof=1))
    return {
        "mean": mean,
        "ci_lo": float(np.quantile(boot, 0.025)),
        "ci_hi": float(np.quantile(boot, 0.975)),
        "t": mean / sd if sd > 0 else None,
    }


def _stats(
    x: pl.DataFrame, horizons: Sequence[int], cost: float, tcfg: FundFlowTracesConfig
) -> dict[str, Any]:
    res: dict[str, Any] = {"n": x.height, "stocks": x["stock_id"].n_unique() if x.height else 0}
    pre = x["x_pre"].drop_nulls()
    res["pre_mean"] = float(pre.mean()) if pre.len() else None  # type: ignore[arg-type]
    hz: dict[str, Any] = {}
    for k in horizons:
        col = x[f"x_r{k}"].drop_nulls()
        if not col.len():
            hz[str(k)] = {"n": 0}
            continue
        mean = float(col.mean())  # type: ignore[arg-type]
        hz[str(k)] = {
            "n": col.len(),
            "mean": mean,
            "median": float(col.median()),  # type: ignore[arg-type]
            "hit": float((col > 0).mean()),  # type: ignore[arg-type]
            "per_day": mean / k,
            "net_mean": mean - cost,
            "net_per_day": (mean - cost) / k,
            "boot": block_bootstrap(x, f"x_r{k}", tcfg.bootstrap_n, tcfg.seed),
        }
    res["horizons"] = hz
    return res


def summarize(
    excess: pl.DataFrame,
    *,
    strata: Mapping[str, pl.DataFrame],
    coverage: pl.DataFrame,
    tcfg: FundFlowTracesConfig,
    cost: float,
    split_date: dt.date,
    decomposed_min: float,
) -> dict[str, Any]:
    """逐事件超額 → 報告用的巢狀 dict（JSON 可重算 markdown，AC-19）。"""
    hz = list(tcfg.horizons)
    ref = max(h for h in hz if h <= 20) if any(h <= 20 for h in hz) else hz[0]
    x = excess.join(coverage.rename({"value": "_cov"}), on="date", how="left")
    out: dict[str, Any] = {}
    for stratum, members in strata.items():
        sx = x.join(members, on=["date", "stock_id"], how="semi")
        for ev in sorted(sx["event"].unique().to_list()):
            e = sx.filter(pl.col("event") == ev)
            node = _stats(e, hz, cost, tcfg)
            node["by_year"] = {
                str(y): {"n": g.height, "mean_ref": _mean(g[f"x_r{ref}"])}
                for (y,), g in e.with_columns(_y=pl.col("date").dt.year()).group_by(
                    ["_y"], maintain_order=True
                )
            }
            node["by_year"] = dict(sorted(node["by_year"].items()))
            node["segments"] = {
                "decomposed": _stats(e.filter(pl.col("_cov") >= decomposed_min), hz, cost, tcfg),
                "not_decomposed": _stats(
                    e.filter(pl.col("_cov").fill_null(0.0) < decomposed_min), hz, cost, tcfg
                ),
                "before_split": _stats(e.filter(pl.col("date") < split_date), hz, cost, tcfg),
                "after_split": _stats(e.filter(pl.col("date") >= split_date), hz, cost, tcfg),
            }
            out.setdefault(stratum, {})[ev] = node
    return out


def _mean(s: pl.Series) -> float | None:
    s = s.drop_nulls()
    return float(s.mean()) if s.len() else None  # type: ignore[arg-type]


def _f(v: float | None, pct: bool = True) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "—"
    return f"{v * 100:+.2f}%" if pct else f"{v:+.2f}"


def render_markdown(report: Mapping[str, Any]) -> str:
    """由 JSON 報告畫 markdown（只有數字與定義，沒有判讀字句）。"""
    meta = report["meta"]
    hz = [str(h) for h in meta["horizons"]]
    lines = [
        f"# 投信／ETF 資金流軌跡（T-339，{meta['run_date']}）",
        "",
        "SPEC-016 §5、D-171 §一 的量測結果。**只報不判**：採用門檻由使用者填 D-171 §二。",
        f"期間 {meta['first_date']}～{meta['last_date']}（第二保留期不碰）；來回成本 {meta['cost'] * 100:.3f}%。",
        f"配對：同日、同 20 日波動×60 日漲幅×ADV 十分位、非同型事件，至少 {meta['matched_min']} 檔。",
        f"丟棄：不在池內 {meta['drops']['not_in_pool']}、配對不足 {meta['drops']['few_matches']}。",
        "",
    ]
    for stratum, evs in report["results"].items():
        lines += [
            f"## {stratum}",
            "",
            "| 事件 | 筆數 | 檔數 | 事前 | "
            + " | ".join(f"+{h} 超額" for h in hz)
            + f" | +{meta['ref']} 每日 | +{meta['ref']} 扣成本每日 | +{meta['ref']} 命中 | +{meta['ref']} t |",
            "|" + "---|" * (len(hz) + 8),
        ]
        for ev in EVENT_TYPES:
            for key in sorted(k for k in evs if k == ev or k.startswith(ev + "@")):
                n = evs[key]
                h = n["horizons"]
                r = h.get(str(meta["ref"]), {})
                cells = [_f(h.get(k, {}).get("mean")) for k in hz]
                lines.append(
                    f"| {key} {EVENT_NAMES[ev]} | {n['n']} | {n['stocks']} | {_f(n['pre_mean'])} | "
                    + " | ".join(cells)
                    + f" | {_f(r.get('per_day'))} | {_f(r.get('net_per_day'))} | {_f(r.get('hit'))} | {_f((r.get('boot') or {}).get('t'), pct=False)} |"
                )
        lines.append("")
    lines += ["## 逐年筆數與 +{} 超額（全市場）".format(meta["ref"]), ""]
    full = report["results"].get(meta["strata"][0], {})
    for ev in sorted(full):
        yr = full[ev]["by_year"]
        lines.append(
            f"- {ev}：" + "、".join(f"{y} {v['n']} 筆 {_f(v['mean_ref'])}" for y, v in yr.items())
        )
    lines.append("")
    return "\n".join(lines)


TRIAL_SCOPE = "flow:traces"


def research_entries(
    report: Mapping[str, Any], params_hash: str, run_date: str
) -> list[dict[str, object]]:
    """研究登記簿列（AC-20）：每個 (分層, 事件) 一列參考期超額，trial_scope＝flow:traces。"""
    ref = str(report["meta"]["ref"])
    rows: list[dict[str, object]] = []
    for stratum, evs in report["results"].items():
        for ev, node in evs.items():
            mean = node["horizons"].get(ref, {}).get("mean")
            rows.append(
                {
                    "entry_type": "trace",
                    "strategy": stratum,
                    "factor": ev,
                    "params_hash": params_hash,
                    "date": run_date,
                    "metric_name": f"excess_r{ref}",
                    "metric_value": float("nan") if mean is None else float(mean),
                    "trial_scope": TRIAL_SCOPE,
                }
            )
    return rows
