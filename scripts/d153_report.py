"""D-153 §二：第十三輪報告——**只報不判**（沿用 d146／d149 的算法，加天花板攫取率與 S − R）。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d153_report.py \\
        --bundles D:/project/twquant/data/tracks_round13

每臂：年化、最大回撤、Calmar、平均曝險、年化周轉、成本佔初始資金；每筆淨期望／中位／勝率／月區塊 t；
命中（持有期內最高收盤 ≥ +20%）佔比；天花板攫取率＝(該臂每筆淨期望 − S0) ÷ (S0 交易的「最高收盤賣」
天花板 − S0)；S − R 之差；並列 E0、tail_b（既有結果，窗口不同）、0050 同窗買進持有、TAIEX。
沒有通過／未通過標籤（D-146）。全在開發期（≤ 2026-04-30），不碰保留段。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import sys

import numpy as np
import polars as pl
import psycopg

from twquant.backtest.costs import CostConfig
from twquant.config import load_config
from twquant.data.serving.ml_store import read_index_close
from twquant.reporting.trade_journal import build_journal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from d143_verdict import DEV_END, _round_trip_cost
from d146_report import LOW_EXPOSURE, _stats
from d151_report import SCHEMA, load_market

CONFIGS = pathlib.Path(__file__).resolve().parents[1] / "configs"
HIT = 0.20  # D-153 §二：持有期內最高收盤 ≥ +20%（＝A20 的標籤）
REFERENCE = {  # 既有結果，並列用（D-141 §三、D-143 §十一）——窗口不同、不是門檻
    "E0（15 檔、月頻、不出場；2017-01~2026-02）": (0.278, -0.414, 0.67),
    "tail_b（第九輪 B 臂；2017-01~2026-02）": (0.128, -0.397, 0.32),
}
_S = "stock_id"


def _trades(bundle: dict[str, object]) -> pl.DataFrame:
    """每筆已結束交易：(stock_id, entry_date, exit_date, entry_price, ret, reason)。"""
    entry_price: dict[tuple[str, str], float] = {}
    rows = []
    for day in build_journal(bundle):
        for m in day.moves:
            if m.action == "enter":
                entry_price[(m.stock_id, day.date)] = float(m.price)
            elif m.action == "exit" and m.ret is not None and m.entry_date is not None:
                rows.append(
                    {
                        _S: m.stock_id,
                        "entry_date": dt.date.fromisoformat(m.entry_date),
                        "exit_date": dt.date.fromisoformat(day.date),
                        "entry_price": entry_price.get((m.stock_id, m.entry_date)),
                        "ret": float(m.ret),
                        "reason": m.reason or "",
                    }
                )
    return pl.DataFrame(rows)


def _max_close_ret(trades: pl.DataFrame, mk: pl.DataFrame) -> pl.DataFrame:
    """每筆交易持有期內（進場日收盤 ~ 出場日收盤）最高還原收盤相對進場價的報酬。"""
    px = mk.select("date", _S, "close", "adj")
    adj_e = px.select(pl.col("date").alias("entry_date"), _S, pl.col("adj").alias("adj_entry"))
    t = trades.with_row_index("k").join(adj_e, on=[_S, "entry_date"], how="left")
    span = t.join(px, on=_S).filter(
        (pl.col("date") >= pl.col("entry_date")) & (pl.col("date") <= pl.col("exit_date"))
    )
    top = span.group_by("k").agg(
        (
            (pl.col("close") / pl.col("adj")).max()
            / (pl.col("entry_price") / pl.col("adj_entry")).first()
            - 1.0
        ).alias("max_ret")
    )
    return t.join(top, on="k", how="left").drop("k")


def _block_t(trades: pl.DataFrame, col: str) -> tuple[float, int]:
    m = (
        trades.group_by(pl.col("entry_date").dt.truncate("1mo"))
        .agg(pl.col(col).mean())[col]
        .to_numpy()
    )
    m = m[np.isfinite(m)]
    if m.size < 2:
        return float("nan"), int(m.size)
    return float(m.mean() / (m.std(ddof=1) / math.sqrt(m.size))), int(m.size)


def _bh(closes: pl.Series) -> tuple[float, float, float]:
    """買進持有：年化、最大回撤、Calmar（252 日／年）。"""
    eq = closes.to_numpy().astype(float)
    ann = float((eq[-1] / eq[0]) ** (252.0 / len(eq)) - 1.0)
    mdd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    return ann, mdd, ann / abs(mdd) if mdd < 0 else float("nan")


def _etf_0050(
    conn: psycopg.Connection, lo: dt.date, hi: dt.date
) -> tuple[float, float, float] | None:
    with conn.cursor() as cur:
        cur.execute(
            f"""select close from "{SCHEMA}".prices_daily
                where stock_id = '0050' and date between %s and %s and close > 0 order by date""",
            (lo, hi),
        )
        rows = [float(r[0]) for r in cur.fetchall()]
    return _bh(pl.Series(rows)) if len(rows) > 252 else None


def main() -> None:
    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument("--bundles", required=True)
    ap.add_argument("--costs", default="costs.yaml")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    rt = _round_trip_cost(load_config(CONFIGS / args.costs, CostConfig))
    paths = sorted(pathlib.Path(args.bundles).glob("quiet_*.json"))
    if not paths:
        raise SystemExit(f"{args.bundles} 底下沒有結果包")
    bundles = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in paths}
    for name, b in bundles.items():
        if dt.date.fromisoformat(str(b["end"])) > DEV_END:
            raise SystemExit(f"{name} 結束於 {b['end']}，越過第二保留期邊界")
    lo = min(dt.date.fromisoformat(str(b["start"])) for b in bundles.values())
    hi = max(dt.date.fromisoformat(str(b["end"])) for b in bundles.values())
    print(
        f"**D-153：不設門檻、只報數字。** 窗口 {lo} ~ {hi}；來回成本 {rt:.3%}（逐筆淨報酬已扣）\n"
    )
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        mk = load_market(conn)
        taiex = read_index_close(conn, SCHEMA, "TAIEX", lo, hi)
        etf = _etf_0050(conn, lo, hi)

    rows: dict[str, dict[str, float]] = {}
    per_trade: dict[str, float] = {}
    ceiling_s0 = float("nan")
    for name, bundle in bundles.items():
        st = _stats(bundle, rt)
        tr = _max_close_ret(_trades(bundle), mk).with_columns(net=pl.col("ret") - rt)
        net = tr["net"].to_numpy()
        t, nm = _block_t(tr, "net")
        hit = float((tr["max_ret"] >= HIT).mean())
        per_trade[name] = float(net.mean())
        if name == "quiet_s0":
            ceiling_s0 = float((tr["max_ret"] - rt).mean())
        reasons = tr.group_by("reason").agg(pl.len().alias("n")).sort("n", descending=True)
        rows[name] = {**st, "net": float(net.mean()), "hit": hit}
        print(f"══ {name}（{bundle['start']} ~ {bundle['end']}）")
        print(
            f"  年化 {st['ann']:+.1%}　最大回撤 {st['mdd']:.1%}　Calmar {st['calmar']:.2f}　"
            f"平均曝險 {st['expo']:.0%}　低曝險日（<{LOW_EXPOSURE:.0%}）佔 {st['low_expo']:.0%}　"
            f"年化周轉 {st['turnover']:.0%}　成本佔初始資金 {st['cost_share']:.1%}"
        )
        print(
            f"  已結束 {len(net):,} 筆／{nm} 個月　每筆淨期望 {net.mean():+.2%}　中位 {np.median(net):+.2%}　"
            f"勝率 {(net > 0).mean():.1%}　月區塊 t {t:+.2f}　持有期內最高收盤 ≥ +{HIT:.0%} 的 {hit:.1%}"
        )
        print(
            "  出場原因："
            + "、".join(f"{r['reason']} {r['n']:,}" for r in reasons.iter_rows(named=True))
        )
        if name == "quiet_s0":
            print(
                f"  S0 交易的最高收盤賣天花板：每筆淨 {ceiling_s0:+.2%}（比抱滿多 {ceiling_s0 - net.mean():+.2%}）"
            )
        print()

    base = per_trade.get("quiet_s0")
    if base is not None and math.isfinite(ceiling_s0) and ceiling_s0 > base:
        print("── 天花板攫取率（(該臂每筆淨期望 − S0) ÷ (S0 天花板 − S0)）──")
        for name in ("quiet_s1", "quiet_s2", "quiet_s3"):
            if name in per_trade:
                cap = (per_trade[name] - base) / (ceiling_s0 - base)
                print(f"  {name}: {cap:+.1%}（每筆 {per_trade[name]:+.2%} vs S0 {base:+.2%}）")
        print()
    print("── S − R（同池同出場，差的是模型排序；R 單一 seed，雜訊約 ±0.3pp／筆）──")
    for s, r in (("quiet_s0", "quiet_r0"), ("quiet_s1", "quiet_r1")):
        if s in rows and r in rows:
            print(
                f"  {s} − {r}: 年化 {rows[s]['ann'] - rows[r]['ann']:+.1%}pp　"
                f"回撤 {rows[s]['mdd'] - rows[r]['mdd']:+.1%}pp　每筆淨 {rows[s]['net'] - rows[r]['net']:+.2%}"
            )
    print("\n── 並列對照（不是門檻）──")
    a, m, c = _bh(taiex.sort("date")["close"])
    print(f"  TAIEX 同窗買進持有（價格指數、不含息）　年化 {a:+.1%}　回撤 {m:.1%}　Calmar {c:.2f}")
    if etf is not None:
        a, m, c = etf
        print(
            f"  0050 同窗買進持有（未還原、不含配息）　　年化 {a:+.1%}　回撤 {m:.1%}　Calmar {c:.2f}"
        )
    for name, (a, m, c) in REFERENCE.items():
        print(f"  {name:32} 年化 {a:+.1%}　回撤 {m:.1%}　Calmar {c:.2f}")


if __name__ == "__main__":
    main()
