"""D-155：事件研究——安靜一段時間 → 突然拉升（5 日 ≥ +15%）→ 回測扣抵值附近再進場。**只報不判。**

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d155_pullback.py            # 開發期
    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d155_pullback.py --confirm  # 確認窗（開一次）

定義全部寫在 D-155 §一，本檔只是照做：
- 安靜：`configs/ml_quiet_a.yaml` 的 `sample_state`（D-151 §一），拉升前不要求流動性；
- 拉升日 s：安靜(s−5) 且 還原收盤(s)/還原收盤(s−5) − 1 ≥ SURGE；同檔 COOLDOWN 個交易日內不重算；
- 扣抵值：N 日線在 t 日的扣抵值＝N 個交易日前的收盤（還原）；
- 回測：s 之後 WINDOW 日內第一個 收盤 ≤ 扣抵值×BAND_HI 的日子 t；收盤 ≥ 扣抵值×BAND_LO → t+1 開盤進場，否則「跌破」；
- 進場日須在策略宇宙（成交額當日前 30%、股價 ≥ 10，同一份配置）；
- 報酬、配對基準、月區塊 t 全用 `d151_report` 的同一套函數（t+1 開盤進場、還原價、20 日）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import sys

import numpy as np
import polars as pl
import psycopg

from twquant.config import MlConfig, load_config
from twquant.data.serving.ml_store import read_index_close
from twquant.ml.context import market_state_features
from twquant.ml.dataset import liquid_pool, state_pool

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from d151_report import DATA_END, DATA_START, SCHEMA, _block_t, _dec, load_market

ROOT = pathlib.Path(__file__).resolve().parents[1]
QUIET_CFG = ROOT / "configs" / "ml_quiet_a.yaml"  # 安靜與流動性的定義逐字沿用（D-155 §一）
SURGE, SURGE_DAYS = 0.15, 5  # 使用者裁決：5 日內漲 ≥ 15%
COOLDOWN = 60  # 同一波只算一次
ARMS = (5, 10, 20)  # 使用者裁決：5／10／20 日扣抵值
BAND_LO, BAND_HI = 0.98, 1.03  # 「附近」：−2%～+3%（D-155 §一，事後不調）
WINDOW = 20  # 拉升後 20 個交易日內要回到附近
HITS = (0.20, 0.30)
ROUND_TRIP = 0.004  # 來回成本（只列參考，不從報酬扣）
DEV = (dt.date(2015, 9, 1), dt.date(2026, 3, 31))
CONFIRM = (dt.date(2026, 5, 1), dt.date(2026, 7, 31))
_S = "stock_id"


def _flags(px: pl.DataFrame, cfg: MlConfig) -> pl.DataFrame:
    """每列掛上 quiet（D-151 安靜股）與 liquid（策略宇宙）兩個成員旗標——不在池內＝False，不是缺值。"""
    assert cfg.sample_state is not None
    quiet = state_pool(px.select("date", _S, "close", "adj"), cfg.sample_state, adjusted=True)
    liquid = liquid_pool(
        px.select("date", _S, "open", "close", "amount", "adj"),
        adv_window=cfg.label.adv_window_sessions,
        min_adv_pctl=cfg.sample_min_adv_pctl,
        min_price=cfg.sample_min_price,
    )
    return (
        px.join(quiet.with_columns(quiet=pl.lit(True)), on=["date", _S], how="left")
        .join(liquid.with_columns(liquid=pl.lit(True)), on=["date", _S], how="left")
        .with_columns(pl.col("quiet").fill_null(False), pl.col("liquid").fill_null(False))
        .sort(_S, "date")
    )


def _rows(px: pl.DataFrame) -> pl.DataFrame:
    """px 的列號（Int64）與日期：事件只記列號，日期一律回 px 查，不經 numpy 轉型。"""
    return px.with_row_index("row").select(pl.col("row").cast(pl.Int64), "date", _S)


def find_events(px: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """回傳 (拉升事件, 各臂回測結果)。逐檔掃，只用 t 日（含）以前的收盤判斷。"""
    sid = px[_S].to_numpy()
    ac = (px["close"] / px["adj"]).to_numpy()
    quiet = px["quiet"].to_numpy()
    liquid = px["liquid"].to_numpy()
    cut = np.flatnonzero(sid[1:] != sid[:-1]) + 1
    starts, ends = np.r_[0, cut], np.r_[cut, len(sid)]
    events, tries = [], []
    for a, b in zip(starts, ends, strict=True):
        last = -(10**9)
        for i in range(a + SURGE_DAYS, b):
            if i - last < COOLDOWN or not quiet[i - SURGE_DAYS]:
                continue
            if not ac[i] / ac[i - SURGE_DAYS] - 1.0 >= SURGE:  # NaN 也不算觸發
                continue
            last = i
            events.append({"row": i, _S: str(sid[i]), "surge_liquid": bool(liquid[i])})
            for n in ARMS:
                status, t_row = "no_pullback", None
                for t in range(i + 1, min(i + WINDOW, b - 1) + 1):
                    if t - n < a:
                        status = "short_history"
                        break
                    kd = ac[t - n]
                    if ac[t] <= kd * BAND_HI:
                        status, t_row = ("entry", t) if ac[t] >= kd * BAND_LO else ("broke", t)
                        break
                if status == "no_pullback" and i + WINDOW > b - 1:
                    status = "censored"  # 資料到尾或下市：不是「沒回檔」，是看不到
                if status == "entry" and t_row is not None and not liquid[t_row]:
                    status = "untradable"
                tries.append(
                    {
                        "row_s": i,
                        _S: str(sid[i]),
                        "arm": n,
                        "status": status,
                        "row_t": t_row,
                        "days": None if t_row is None else t_row - i,
                    }
                )
    rows = _rows(px)
    ev = pl.DataFrame(events, schema_overrides={"row": pl.Int64}).join(
        rows.rename({"date": "s"}), on=["row", _S]
    )
    tr = pl.DataFrame(
        tries, schema_overrides={"row_s": pl.Int64, "row_t": pl.Int64, "days": pl.Int64}
    ).join(rows.rename({"row": "row_s", "date": "s"}), on=["row_s", _S])
    return ev, tr


def _matched(px: pl.DataFrame) -> pl.DataFrame:
    """同日策略宇宙內「同 20 日波動十分位 × 同 20 日漲幅十分位」的格平均（D-143 §十二）。"""
    base = px.filter(
        pl.col("liquid")
        & pl.col("vol20").is_not_null()
        & pl.col("past20").is_not_null()
        & pl.col("fwd20").is_not_null()
        & pl.col("maxfwd").is_not_null()
    ).with_columns(vdec=_dec("vol20", "date"), mdec=_dec("past20", "date"))
    cells = base.group_by("date", "vdec", "mdec").agg(
        c_fwd=pl.col("fwd20").mean(),
        **{f"c_h{int(h * 100)}": (pl.col("maxfwd") >= h).cast(pl.Float64).mean() for h in HITS},
        c_n=pl.len(),
    )
    return base.select("date", _S, "vdec", "mdec").join(cells, on=["date", "vdec", "mdec"])


def _outcomes(rows: pl.DataFrame, px: pl.DataFrame, matched: pl.DataFrame) -> pl.DataFrame:
    """rows 需有 row（px 的列號）；掛上該列的前向報酬與配對格。"""
    cols = px.with_row_index("row").select(
        pl.col("row").cast(pl.Int64), "date", _S, "fwd20", "maxfwd", "minfwd", "vol20", "past20"
    )
    out = rows.with_columns(pl.col("row").cast(pl.Int64)).join(cols, on=["row", _S])
    out = out.filter(pl.col("fwd20").is_not_null())
    out = out.join(matched, on=["date", _S], how="left")
    return out.with_columns(
        d_fwd=pl.col("fwd20") - pl.col("c_fwd"),
        **{f"h{int(h * 100)}": (pl.col("maxfwd") >= h).cast(pl.Float64) for h in HITS},
    )


def _line(tag: str, o: pl.DataFrame) -> None:
    if o.is_empty():
        print(f"  {tag}：0 筆")
        return
    m = o.filter(pl.col("c_fwd").is_not_null())
    mean_d, t, nm = _block_t(m, "d_fwd")
    print(
        f"  {tag}：{o.height:,} 筆　20 日報酬 平均 {float(o['fwd20'].mean()):+.2%}　中位 {float(o['fwd20'].median()):+.2%}　"
        f"勝率 {float((o['fwd20'] > 0).mean()):.1%}　最大跌幅中位 {float(o['minfwd'].median()):+.1%}"
    )
    hits = "　".join(
        f"碰到 +{int(h * 100)}% {float(o[f'h{int(h * 100)}'].mean()):.1%}（配對 {float(m[f'c_h{int(h * 100)}'].mean()):.1%}）"
        for h in HITS
    )
    print(f"    {hits}")
    print(
        f"    對配對組超額 {mean_d:+.2%}（月區塊 t {t:+.2f}，{nm} 個月；配對到 {m.height:,} 筆；來回成本另計 {ROUND_TRIP:.2%}）"
    )


def _by_year(o: pl.DataFrame) -> str:
    m = o.filter(pl.col("c_fwd").is_not_null())
    yr = (
        m.group_by(pl.col("date").dt.year().alias("y"))
        .agg(d=pl.col("d_fwd").mean(), n=pl.len())
        .sort("y")
    )
    return "　".join(f"{r['y']} {r['d']:+.1%}（{r['n']}）" for r in yr.iter_rows(named=True))


def _regime(o: pl.DataFrame, px: pl.DataFrame) -> str:
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        idx = read_index_close(conn, SCHEMA, "TAIEX", DATA_START, DATA_END)
    ctx = market_state_features(idx, px.select("date", _S, "close"), window=20).select(
        "date", "ctx.breadth_ma20"
    )
    p = o.join(ctx, on="date", how="left").filter(
        pl.col("ctx.breadth_ma20").is_not_null() & pl.col("c_fwd").is_not_null()
    )
    if p.height < 3:
        return "樣本不足"
    lo, hi = p["ctx.breadth_ma20"].quantile(1 / 3), p["ctx.breadth_ma20"].quantile(2 / 3)
    parts = []
    for tag, cond in (
        ("冷", pl.col("ctx.breadth_ma20") <= lo),
        ("中", (pl.col("ctx.breadth_ma20") > lo) & (pl.col("ctx.breadth_ma20") <= hi)),
        ("熱", pl.col("ctx.breadth_ma20") > hi),
    ):
        s = p.filter(cond)
        parts.append(f"{tag} {float(s['d_fwd'].mean()):+.2%}（{s.height}）")
    return "　".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument(
        "--confirm", action="store_true", help="確認窗（D-155 §三；開發期結果 commit 後跑一次）"
    )
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    lo, hi = CONFIRM if args.confirm else DEV
    print(
        f"**D-155：不設門檻、只報數字。** {'確認窗（開一次）' if args.confirm else '開發期'}　"
        f"進場日 {lo} ~ {hi}；拉升 {SURGE_DAYS} 日 ≥ +{SURGE:.0%}；帶寬 {BAND_LO}~{BAND_HI}×扣抵值；窗 {WINDOW} 日"
    )
    cfg = load_config(QUIET_CFG, MlConfig)
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        px = _flags(load_market(conn), cfg)
    ev, tr = find_events(px)
    matched = _matched(px)
    in_win = pl.col("s").is_between(lo, hi)
    ev_w = ev.filter(in_win)
    print(
        f"\n── 拉升事件（安靜股、{SURGE_DAYS} 日 ≥ +{SURGE:.0%}，拉升日在窗內）：{ev_w.height:,} 件"
    )
    yr = ev_w.group_by(pl.col("s").dt.year().alias("y")).agg(pl.len()).sort("y")
    print("  逐年：" + "　".join(f"{r['y']} {r['len']}" for r in yr.iter_rows(named=True)))
    print(f"  拉升日已在策略宇宙內：{float(ev_w['surge_liquid'].mean()):.0%}")

    chase = _outcomes(ev_w.filter(pl.col("surge_liquid")).select("row", _S), px, matched)
    print("\n── 對照：追高（拉升日隔天開盤買，拉升日須在策略宇宙內）")
    _line("追高", chase)

    for n in ARMS:
        t_n = tr.filter((pl.col("arm") == n) & in_win)
        counts = t_n.group_by("status").agg(pl.len()).sort("status")
        share = {r["status"]: r["len"] / t_n.height for r in counts.iter_rows(named=True)}
        rows = t_n.filter(pl.col("status") == "entry").select(
            pl.col("row_t").alias("row"), _S, "row_s", "days"
        )
        entries = _outcomes(rows, px, matched).filter(pl.col("date").is_between(lo, hi))
        print(f"\n══ {n} 日扣抵值（事件 {t_n.height:,}）")
        print(
            "  結果："
            + "　".join(
                f"{k} {share.get(k, 0.0):.1%}"
                for k in (
                    "entry",
                    "broke",
                    "no_pullback",
                    "untradable",
                    "censored",
                    "short_history",
                )
            )
            + f"　拉升到進場天數中位 {float(rows['days'].median()) if rows.height else float('nan'):.0f}"
        )
        _line("回檔進場", entries)
        same = chase.join(entries.select(pl.col("row_s").alias("row"), _S), on=["row", _S])
        _line("同一批事件改成追高", same)
        print("  逐年超額（筆數）：" + _by_year(entries))
        print("  大盤寬度三分位超額：" + _regime(entries, px))


if __name__ == "__main__":
    main()
