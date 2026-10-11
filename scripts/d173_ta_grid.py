"""D-173／T-343：《技術分析精論》進出規則排列組合（逐筆）。**參數照 D-173 §一～§四 寫死，不得依結果改。**

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d173_ta_grid.py --data-dir D:/project/twquant/data \
        --report-dir D:/project/twquant/.claude/worktrees/<wt>/docs/reports

部位沿用 D-156／D-159：A20 樣本外分數的候選、訊號日收盤判斷、第 0 天開盤買、開盤漲停買不到、21 列內不重買、
最長第 21 天開盤賣、每筆扣來回 0.40%、還原價、第 21 天 ≤ 2026-04-30。
H0＝池內前 15 名；H35＝分數 ≥ 0.35。進場濾網 {T,B,V,M} 的 16 子集 × 出場 {X1,X2,X3,X4} 的 16 子集 × 2 ＝ 512 組。
只報不判：照 D-173 §四 的事前規則挑組，正式判讀在第二保留期。
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib
import io
import itertools
import json
import math
import os
import pathlib
import sys
from statistics import NormalDist
from typing import Any

import numpy as np
import polars as pl

# ── D-173 的參數（寫死）────────────────────────────────────────────────
TOP_N = 15
H35 = 0.35
NO_REENTRY = 21
EXIT_DAY = 21  # 最長第 21 天開盤賣
N_DEC = 20  # 第 0～19 天收盤判斷
ROUND_TRIP = 0.004
LIMIT_GAP = 0.095
ENTRY_FROM = dt.date(2017, 10, 2)
PATH_END = dt.date(2026, 4, 30)
MA_LONG, MA_LONG_LAG = 200, 20
HI_N, BRK_PCT = 60, 0.03
VOL_N, VOL_MULT = 20, 1.5
MACD_FAST, MACD_SLOW, MACD_SIG = 12, 26, 9
RSI_N, RSI_LO, RSI_HI = 14, 40.0, 80.0
MA_MID, MA_MID_LAG = 50, 5
DIV_LOW_N = 5
STOP = 0.85
HIT_UP, BIG_LOSS = 0.20, -0.15
MIN_TRADES = 150
TOP_K = 3
N_COMBOS = 512
FAMILY_ALPHA = 0.05
FILTERS = ("T", "B", "V", "M")
EXITS = ("X1", "X2", "X3", "X4")
TRIAL_SCOPE = "ta:grid"
_S = "stock_id"


# ── 純函數（tests/test_d173_ta_grid.py 守著）────────────────────────────


def rsi_expr(col: str, n: int) -> pl.Expr:
    """Wilder RSI（alpha＝1/n 的指數平均；前 n 列為 null）。呼叫端以 .over(股票) 包起來。"""
    d = pl.col(col).diff()
    up = d.clip(lower_bound=0.0).ewm_mean(alpha=1.0 / n, adjust=False, min_samples=n)
    dn = (-d).clip(lower_bound=0.0).ewm_mean(alpha=1.0 / n, adjust=False, min_samples=n)
    return (
        pl.when(dn == 0.0)
        .then(pl.when(up > 0.0).then(100.0))
        .otherwise(100.0 - 100.0 / (1.0 + up / dn))
    )


def add_indicators(px: pl.DataFrame) -> pl.DataFrame:
    """逐檔指標（列為單位，依股票、日期排序）。需要 date, stock_id, ac（還原收盤）, volume, taiex_ok。"""
    ac = pl.col("ac")
    macd = ac.ewm_mean(span=MACD_FAST, adjust=False) - ac.ewm_mean(span=MACD_SLOW, adjust=False)
    out = px.sort(_S, "date").with_columns(
        ma200=ac.rolling_mean(MA_LONG).over(_S),
        ma50=ac.rolling_mean(MA_MID).over(_S),
        hi60=ac.shift(1).rolling_max(HI_N).over(_S),
        vbar=pl.col("volume").shift(1).rolling_mean(VOL_N).over(_S),
        macd=macd.over(_S),
        rsi=rsi_expr("ac", RSI_N).over(_S),
        _n=pl.int_range(pl.len()).over(_S),
    )
    return out.with_columns(
        ma200_lag=pl.col("ma200").shift(MA_LONG_LAG).over(_S),
        ma50_lag=pl.col("ma50").shift(MA_MID_LAG).over(_S),
        macd_sig=pl.col("macd").ewm_mean(span=MACD_SIG, adjust=False).over(_S),
    ).with_columns(
        # MACD 需要慢線暖機：前 MACD_SLOW + MACD_SIG 列不算（歷史不足 → 不符合）
        macd=pl.when(pl.col("_n") >= MACD_SLOW + MACD_SIG).then(pl.col("macd")),
    )


def filter_masks(df: pl.DataFrame) -> dict[str, np.ndarray]:
    """四個進場濾網（訊號日收盤）；任何 null → False。"""
    c = pl.col
    exprs = {
        "T": (c("ac") > c("ma200")) & (c("ma200") > c("ma200_lag")) & c("taiex_ok"),
        "B": c("ac") >= c("hi60") * (1.0 + BRK_PCT),
        "V": c("volume") >= c("vbar") * VOL_MULT,
        "M": (c("macd") > c("macd_sig")) & (c("rsi") >= RSI_LO) & (c("rsi") <= RSI_HI),
    }
    got = df.select(**{k: v.fill_null(False) for k, v in exprs.items()})
    return {k: got[k].to_numpy() for k in exprs}


def exit_day(trigger: np.ndarray, cap: int) -> np.ndarray:
    """第 k 天收盤第一次觸發 → 第 k+1 天開盤；沒觸發 → cap。"""
    hit = trigger.any(axis=1)
    out: np.ndarray = np.where(hit, trigger.argmax(axis=1) + 1, cap)
    return out


def divergence_trigger(close: np.ndarray, rsi: np.ndarray, low_n: int) -> np.ndarray:
    """X3：持有中收盤創持有以來新高、RSI 卻低於持有以來最高 → 確認線＝最近 low_n 日最低收盤；之後收盤跌破即觸發。

    close／rsi 形狀 (n, D)，第 0 天起；NaN 不觸發、也不更新新高。新的背離會把確認線換成最新的。
    """
    n, d = close.shape
    trig = np.zeros((n, d), dtype=bool)
    line = np.full(n, np.nan)
    with np.errstate(invalid="ignore"):
        for k in range(1, d):
            past_c = np.nanmax(close[:, :k], axis=1) if k else np.full(n, np.nan)
            past_r = np.nanmax(rsi[:, :k], axis=1)
            trig[:, k] = close[:, k] < line
            div = (close[:, k] > past_c) & (rsi[:, k] < past_r)
            lo = np.nanmin(close[:, max(0, k - low_n + 1) : k + 1], axis=1)
            line = np.where(div, lo, line)
    return trig


def exit_triggers(p: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """四個出場觸發矩陣 (n, N_DEC)。p 需要 ac, ma50, ma50_lag, rsi（(n, N_DEC)）, entry, level, brk（(n,)）。"""
    with np.errstate(invalid="ignore"):
        x1 = p["brk"][:, None] & (p["ac"] < p["level"][:, None])
        x2 = (p["ac"] < p["ma50"]) & (p["ma50"] < p["ma50_lag"])
        x3 = divergence_trigger(p["ac"], p["rsi"], DIV_LOW_N)
        x4 = p["ac"] <= p["entry"][:, None] * STOP
    return {"X1": x1, "X2": x2, "X3": x3, "X4": x4}


def trade_outcomes(ao: np.ndarray, ac: np.ndarray, ex: np.ndarray) -> dict[str, np.ndarray]:
    """出場日 ex（第 ex 天開盤賣）→ 每筆淨報酬、持有天數、持有中收盤曾 ≥ +20%。ao 形狀 (n, EXIT_DAY+1)。"""
    rows = np.arange(len(ex))
    entry = ao[:, 0]
    net = ao[rows, ex] / entry - 1.0 - ROUND_TRIP
    held = np.array([np.nanmax(ac[i, : max(e, 1)]) for i, e in enumerate(ex)])
    return {"net": net, "days": ex.astype(float), "hit": held >= entry * (1.0 + HIT_UP)}


def month_block_t(month: np.ndarray, net: np.ndarray) -> tuple[float, int]:
    """依進場月分組的平均淨報酬 → t（平均 ÷ 標準誤）。少於 2 個月 → nan。"""
    df = pl.DataFrame({"m": month, "x": net}).group_by("m").agg(pl.col("x").mean())
    v = df["x"].to_numpy()
    if v.size < 2 or v.std(ddof=1) == 0:
        return float("nan"), int(v.size)
    return float(v.mean() / (v.std(ddof=1) / math.sqrt(v.size))), int(v.size)


def sidak_t(n_tests: int, alpha: float) -> float:
    """n 次雙尾檢定、家族錯誤率 alpha 的 Šidák 門檻（常態近似）。"""
    a1 = 1.0 - (1.0 - alpha) ** (1.0 / n_tests)
    return NormalDist().inv_cdf(1.0 - a1 / 2.0)


def summarize(
    net: np.ndarray, days: np.ndarray, hit: np.ndarray, month: np.ndarray
) -> dict[str, float]:
    """一組的逐筆統計（D-173 §四）。"""
    if net.size == 0:
        return {"n": 0}
    t, nm = month_block_t(month, net)
    return {
        "n": int(net.size),
        "mean": float(net.mean()),
        "median": float(np.median(net)),
        "per_day": float(net.sum() / days.sum()),
        "days": float(days.mean()),
        "hit": float(hit.mean()),
        "win": float(
            (net > 0).mean()
        ),  # 扣成本後有賺的比例（2026-10-11 使用者要「機率高」時補的描述欄）
        "loss15": float((net <= BIG_LOSS).mean()),
        "t": t,
        "months": nm,
    }


def select(rows: list[dict[str, Any]], t_bar: float) -> list[str]:
    """D-173 §四：筆數 ≥ MIN_TRADES 且 t ≥ t_bar 中，依每持有日淨報酬取前 TOP_K。"""
    ok = [r for r in rows if r.get("n", 0) >= MIN_TRADES and r.get("t", float("nan")) >= t_bar]
    ok.sort(key=lambda r: -r["per_day"])
    return [r["combo"] for r in ok[:TOP_K]]


def subsets(items: tuple[str, ...]) -> list[tuple[str, ...]]:
    """全部子集，依大小再依字母序（空集合在前）。"""
    return [c for k in range(len(items) + 1) for c in itertools.combinations(items, k)]


# ── IO ────────────────────────────────────────────────────────────────────


def _sibling(name: str) -> Any:
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    return importlib.import_module(name)


def build_positions(
    mk: pl.DataFrame, cand: pl.DataFrame, end: np.ndarray, gap: np.ndarray, ao: np.ndarray
) -> pl.DataFrame:
    """候選 (r, stock_id) → 部位 (stock_id, r)：21 列內不重買、開盤漲停跳過、路徑要完整（同 D-156）。"""
    kept: list[tuple[str, int]] = []
    last: dict[str, int] = {}
    for s, r in cand.sort(_S, "r").select(_S, "r").iter_rows():
        if r - last.get(s, -(10**9)) < NO_REENTRY:
            continue
        if r + 1 + EXIT_DAY > end[r]:
            continue
        g = gap[r]
        if g is None or not math.isfinite(g) or g >= LIMIT_GAP:
            continue
        if not (math.isfinite(ao[r + 1]) and ao[r + 1] > 0):
            continue
        last[s] = r
        kept.append((s, r))
    return pl.DataFrame(kept, schema={_S: pl.String, "r": pl.Int64}, orient="row")


def main() -> int:
    import psycopg

    from twquant.data.serving.ml_store import read_index_close
    from twquant.reporting.research_log import append_research_log

    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--report-dir", required=True)
    args = ap.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    d151 = _sibling("d151_report")
    d156 = _sibling("d156_exit_model")
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        mk = d151.load_market(conn)
        tx = read_index_close(conn, d151.SCHEMA, "TAIEX", d151.DATA_START, d151.DATA_END)
    tx = (
        pl.DataFrame(tx).rename({tx.columns[0]: "date", tx.columns[1]: "tclose"})
        if isinstance(tx, pl.DataFrame)
        else pl.DataFrame(tx, schema=["date", "tclose"], orient="row")
    )
    tx = (
        tx.sort("date")
        .with_columns(taiex_ok=pl.col("tclose") > pl.col("tclose").rolling_mean(MA_LONG))
        .select("date", "taiex_ok")
    )
    mk = (
        mk.sort(_S, "date")
        .with_columns(ac=pl.col("close") / pl.col("adj"), ao=pl.col("open") / pl.col("adj"))
        .join(tx, on="date", how="left")
    )
    mk = add_indicators(mk).sort(_S, "date")
    sid = mk[_S].to_numpy()
    cut = np.flatnonzero(sid[1:] != sid[:-1]) + 1
    end = np.empty(len(sid), dtype=np.int64)
    for a, b in zip(np.r_[0, cut], np.r_[cut, len(sid)], strict=True):
        end[a:b] = b - 1
    dates = mk["date"].to_numpy()
    gap, ao_all, ac_all = mk["gap"].to_numpy(), mk["ao"].to_numpy(), mk["ac"].to_numpy()
    masks = filter_masks(mk)
    cols = {c: mk[c].to_numpy() for c in ("ma50", "ma50_lag", "rsi", "hi60")}
    rows_idx = mk.with_row_index("r").select(pl.col("r").cast(pl.Int64), "date", _S)

    scores = d156._a20_scores()
    hosts = {
        "H0": scores.filter(pl.col("score").rank("ordinal", descending=True).over("date") <= TOP_N),
        "H35": scores.filter(pl.col("score") >= H35),
    }
    last_entry = PATH_END  # 第 21 天 ≤ PATH_END 由路徑日期檢查
    results: list[dict[str, Any]] = []
    t_bar = sidak_t(N_COMBOS, FAMILY_ALPHA)
    print(f"運氣基準：{N_COMBOS} 次雙尾、家族 5% 的 Šidák 門檻 |t| ≥ {t_bar:.2f}", flush=True)
    for host, hs in hosts.items():
        cand_all = hs.join(rows_idx, on=["date", _S]).filter(pl.col("date") >= ENTRY_FROM)
        for fs in subsets(FILTERS):
            r = cand_all["r"].to_numpy()
            keep = np.ones(len(r), dtype=bool)
            for f in fs:
                keep &= masks[f][r]
            pos = build_positions(mk, cand_all.filter(pl.Series(keep)), end, gap, ao_all)
            rr = pos["r"].to_numpy()
            # 第 21 天（r+1+21）必須 ≤ PATH_END
            ok = np.array(
                [dates[x + 1 + EXIT_DAY] <= np.datetime64(last_entry) for x in rr], dtype=bool
            )
            rr = rr[ok]
            if rr.size == 0:
                for xs in subsets(EXITS):
                    results.append(
                        {"combo": f"{host}|{''.join(fs) or '-'}|{''.join(xs) or '-'}", "n": 0}
                    )
                continue
            day = rr[:, None] + 1 + np.arange(EXIT_DAY + 1)[None, :]
            dec = day[:, :N_DEC]
            path = {
                "ac": ac_all[dec],
                "ma50": cols["ma50"][dec],
                "ma50_lag": cols["ma50_lag"][dec],
                "rsi": cols["rsi"][dec],
                "entry": ao_all[rr + 1],
                "level": cols["hi60"][rr],
                "brk": ac_all[rr] >= cols["hi60"][rr],
            }
            trig = exit_triggers(path)
            ao_path, ac_path = ao_all[day], ac_all[day]
            month = np.array([str(dates[x])[:7] for x in rr])
            for xs in subsets(EXITS):
                t_any = np.zeros_like(trig["X1"])
                for x in xs:
                    t_any |= trig[x]
                ex = exit_day(t_any, EXIT_DAY)
                o = trade_outcomes(ao_path, ac_path, ex)
                row: dict[str, Any] = {"combo": f"{host}|{''.join(fs) or '-'}|{''.join(xs) or '-'}"}
                row.update(summarize(o["net"], o["days"], o["hit"], month))
                results.append(row)
            print(f"  {host} 濾網 {''.join(fs) or '無'}：{rr.size:,} 筆", flush=True)

    picked = select(results, t_bar)
    base = ["H0|-|-", "H35|-|-"]
    out_dir = pathlib.Path(args.data_dir) / "reports" / "d173"
    out_dir.mkdir(parents=True, exist_ok=True)
    run_date = dt.date.today().isoformat()
    payload = {
        "run_date": run_date,
        "t_bar": t_bar,
        "picked": picked,
        "baselines": base,
        "rows": results,
    }
    (out_dir / f"{run_date}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    md = render(payload)
    (pathlib.Path(args.report_dir) / "d173-ta-grid-dev.md").write_text(md, encoding="utf-8")
    phash = f"d173:{TOP_N}:{H35}:{BRK_PCT}:{VOL_MULT}:{STOP}"
    append_research_log(
        args.data_dir,
        [
            {
                "entry_type": "trace",
                "strategy": r["combo"],
                "factor": "ta_grid",
                "params_hash": f"{phash}|{r['combo']}",
                "date": run_date,
                "metric_name": "per_day_net",
                "metric_value": float(r.get("per_day", float("nan"))),
                "trial_scope": TRIAL_SCOPE,
            }
            for r in results
        ],
    )
    print(
        f"[OK] {len(results)} 組；入選 {picked}；報告 {args.report_dir}/d173-ta-grid-dev.md",
        flush=True,
    )
    return 0


def _pct(v: Any, digits: int = 2) -> str:
    return (
        "—" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:+.{digits}f}%"
    )


def render(p: dict[str, Any]) -> str:
    """報告（只有數字與定義，沒有判讀字句）。"""
    rows = {r["combo"]: r for r in p["rows"]}
    ranked = sorted(
        (r for r in p["rows"] if r.get("n", 0) >= MIN_TRADES), key=lambda r: -r["per_day"]
    )

    def line(r: dict[str, Any]) -> str:
        return (
            f"| {r['combo']} | {r['n']} | {_pct(r['mean'])} | {_pct(r['median'])} | {_pct(r['per_day'], 3)} | "
            f"{r['days']:.1f} | {_pct(r['hit'], 1).lstrip('+')} | {_pct(r['loss15'], 1).lstrip('+')} | {r['t']:+.2f} |"
        )

    head = "| 組合（候選｜濾網｜出場） | 筆數 | 每筆淨 | 中位數 | 每持有日 | 平均持有 | 曾 ≥ +20% | 虧 > 15% | 月區塊 t |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|"
    n_ok = sum(1 for r in p["rows"] if r.get("n", 0) >= MIN_TRADES)
    n_sig = sum(
        1 for r in p["rows"] if r.get("n", 0) >= MIN_TRADES and r.get("t", -9) >= p["t_bar"]
    )
    out = [
        f"# D-173 技術分析進出規則排列組合（逐筆，開發期描述，{p['run_date']}）",
        "",
        "規則見 DECISIONS.md D-173；部位沿用 D-156／D-159（A20 候選、第 0 天開盤買、最長第 21 天開盤賣、每筆扣 0.40%）。",
        "期間：訊號日 2017-10-02 起、第 21 天 ≤ 2026-04-30。**只報不判**；正式判讀在第二保留期。",
        "組合代碼：候選 H0＝前 15 名、H35＝分數 ≥ 0.35；濾網 T 大方向、B 突破、V 量能、M 動能；出場 X1 失敗突破、X2 趨勢反轉、X3 背離＋確認、X4 停損 15%；「-」＝無。",
        f"運氣基準：512 次雙尾、家族 5% 的 Šidák 門檻 |t| ≥ {p['t_bar']:.2f}。筆數 ≥ {MIN_TRADES} 的組合 {n_ok} 組，其中 t 達門檻 {n_sig} 組。",
        "",
        "## 基準（現行抱滿 20 日、無濾網）",
        "",
        head,
        *[line(rows[b]) for b in p["baselines"] if rows.get(b, {}).get("n")],
        "",
        f"## 照 D-173 §四 事前規則入選（筆數 ≥ {MIN_TRADES}、t ≥ {p['t_bar']:.2f}、依每持有日取前 {TOP_K}）",
        "",
    ]
    if p["picked"]:
        out += [head, *[line(rows[c]) for c in p["picked"]], ""]
    else:
        out += ["沒有任何組合同時達到筆數與 t 門檻，依事前規則一組都不取。", ""]
    out += [
        f"## 每持有日淨報酬前 20 名（筆數 ≥ {MIN_TRADES}；**不是入選名單**，看熱鬧用）",
        "",
        head,
        *[line(r) for r in ranked[:20]],
        "",
        "## 只換出場（無濾網）",
        "",
        head,
        *[
            line(rows[f"{h}|-|{''.join(x) or '-'}"])
            for h in ("H0", "H35")
            for x in subsets(EXITS)
            if rows.get(f"{h}|-|{''.join(x) or '-'}", {}).get("n")
        ],
        "",
        "## 只換濾網（抱滿）",
        "",
        head,
        *[
            line(rows[f"{h}|{''.join(f) or '-'}|-"])
            for h in ("H0", "H35")
            for f in subsets(FILTERS)
            if rows.get(f"{h}|{''.join(f) or '-'}|-", {}).get("n")
        ],
        "",
    ]
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
