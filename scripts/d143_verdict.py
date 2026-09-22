"""D-143 §七：第九輪判定——右尾分類（A／B 臂）與規則臂（D）的完整揭露。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d143_verdict.py \
        --bundles D:/project/twquant/data/tracks_tail

**判準只有一條，而且是使用者訂的**（D-134 §四.1，D-143 §〇 把它從 20% 改為 35%）：
部署門檻 ＝ **組合最大回撤 ≤ 35%**。其餘統計一律「照算照報但不作為通過條件」
——t 值、筆數、命中率倍率都揭露，讓使用者知道數字可不可信，但不再串成交集刷掉策略。

揭露項目（D-143 §七）：
- **命中率倍率**：進場後 60 個交易日內最大漲幅 ≥ +50% 的比例 ÷ **同日全市場**的基礎率。
  這是這一輪唯一能回答「模型有沒有看到右尾」的量；用全期基礎率會被行情結構帶偏，
  故逐日配對後再平均。
- 每筆淨期望值（另扣一次來回成本）、筆數、月區塊 t（**只報**）。
- 年化、最大回撤（對照 35%）、Calmar、平均曝險、最大單筆虧損。
- 與 E0（+27.8%／−41.4%）、V1（+19.6%／−26.9%）並列。

逐筆報酬取 `trade_journal` 的價格報酬並**另扣一次來回成本**，因為 `ret` 本身不含費用。
結束日越過 2026-08-31 的結果包一律拒收（D-109／D-114 的第二保留期邊界）。
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

from twquant.backtest.costs import CostConfig
from twquant.config import load_config
from twquant.reporting.trade_journal import build_journal

DEV_END = dt.date(2026, 8, 31)
MDD_LIMIT = 0.35  # 使用者訂的部署門檻（D-143 §〇），不是統計門檻
HORIZON = 60
EVENT = 0.50
CONFIGS = pathlib.Path(__file__).resolve().parents[1] / "configs"
BASELINES = {  # D-141 §三 的既有結果，供並列對照（不是門檻）
    "E0（15 檔、月頻、不出場）": (0.278, -0.414),
    "V1（波動度減碼）": (0.196, -0.269),
}


def _round_trip_cost(cfg: CostConfig) -> float:
    """來回成本佔部位的比例：手續費兩趟（打折後）＋ 賣出證交稅，再乘成本倍率。

    滑價不在這裡——它價格相依，已反映在權益曲線與年化報酬裡。
    """
    fee = float(cfg.commission_rate) * float(cfg.commission_discount) * 2
    return (fee + float(cfg.tax_rate_sell)) * float(cfg.cost_scale)


def _outcomes(lo: dt.date, hi: dt.date) -> pl.DataFrame:
    """(date, stock_id, hit)：訊號日隔天開盤進場後 HORIZON 日內最大漲幅是否達 EVENT。

    與 `ml.dataset.tail_up_labels` 同一個定義（t+1 開盤為基準），但這裡是**事後**
    對照用，直接從 PG 算，不經過模型。|日報酬| > 11% 的公司行動日使該視窗失效。
    """
    import psycopg

    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        raise SystemExit("需要 TWQUANT_PG_DSN 才能算命中率倍率")
    with psycopg.connect(dsn, connect_timeout=30) as conn, conn.cursor() as cur:
        cur.execute(
            """select date, stock_id, open, close from serving.prices_daily
               where date between %s and %s and stock_id ~ '^[1-9][0-9]{3}$' and close > 0""",
            (lo - dt.timedelta(days=10), hi + dt.timedelta(days=HORIZON * 2 + 30)),
        )
        rows = cur.fetchall()
    px = (
        pl.DataFrame(rows, schema=["date", "stock_id", "open", "close"], orient="row")
        .with_columns(
            pl.col("date").cast(pl.Date),
            pl.col("open").cast(pl.Float64),
            pl.col("close").cast(pl.Float64),
        )
        .sort(["stock_id", "date"])
    )
    g = "stock_id"
    px = px.with_columns(
        _bad=((pl.col("close") / pl.col("close").shift(1).over(g) - 1).abs() > 0.11)
        .fill_null(False)
        .cast(pl.Int8)
    )
    entry = pl.col("open").shift(-1).over(g)
    fmax = pl.col("close").shift(-1).rolling_max(HORIZON, min_samples=HORIZON).shift(-(HORIZON - 1))
    badw = pl.col("_bad").shift(-1).rolling_sum(HORIZON, min_samples=HORIZON).shift(-(HORIZON - 1))
    return (
        px.with_columns(_entry=entry, _fmax=fmax.over(g), _badw=badw.over(g))
        .filter(pl.col("_fmax").is_not_null() & (pl.col("_badw") == 0) & (pl.col("_entry") > 0))
        .with_columns(hit=(pl.col("_fmax") / pl.col("_entry") - 1.0) >= EVENT)
        .select("date", "stock_id", "hit")
    )


def _lift(entries: list[tuple[dt.date, str]], outcomes: pl.DataFrame) -> tuple[float, float, int]:
    """(進場命中率, 同日全市場基礎率, 可比對筆數)。逐日配對後再平均，避免行情結構帶偏。"""
    if not entries:
        return float("nan"), float("nan"), 0
    want = pl.DataFrame(
        {"date": [d for d, _ in entries], "stock_id": [s for _, s in entries]}
    ).with_columns(pl.col("date").cast(pl.Date))
    matched = want.join(outcomes, on=["date", "stock_id"], how="inner")
    if matched.is_empty():
        return float("nan"), float("nan"), 0
    base = outcomes.group_by("date").agg(pl.col("hit").mean().alias("base"))
    per_day = (
        matched.group_by("date")
        .agg(pl.col("hit").mean().alias("got"), pl.len().alias("n"))
        .join(base, on="date", how="inner")
    )
    return float(per_day["got"].mean()), float(per_day["base"].mean()), int(matched.height)


def main() -> None:
    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument("--bundles", required=True)
    ap.add_argument("--costs", default="costs.yaml")
    ap.add_argument("--no-lift", action="store_true", help="跳過命中率倍率（不連 PG）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    rt = _round_trip_cost(load_config(CONFIGS / args.costs, CostConfig))
    paths = sorted(pathlib.Path(args.bundles).glob("*.json"))
    if not paths:
        raise SystemExit(f"{args.bundles} 底下沒有結果包")
    print(f"來回成本（手續費×2 ＋ 證交稅）＝ {rt:.3%}；逐筆報酬另扣此數")
    print(f"部署門檻（使用者訂，D-143 §〇）：最大回撤 ≤ {MDD_LIMIT:.0%}。其餘統計只報不擋\n")

    bundles = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in paths]
    for p, b in bundles:
        if dt.date.fromisoformat(b["end"]) > DEV_END:
            raise SystemExit(f"{p.name} 結束於 {b['end']}，越過第二保留期邊界，拒絕判定")
    outcomes = pl.DataFrame()
    if not args.no_lift:
        lo = min(dt.date.fromisoformat(b["start"]) for _, b in bundles)
        hi = max(dt.date.fromisoformat(b["end"]) for _, b in bundles)
        print(f"讀取 {lo} ~ {hi} 的實際結果以計算命中率倍率……", flush=True)
        outcomes = _outcomes(lo, hi)
        print(f"  可比對 {outcomes.height:,} 個股票日\n")

    for path, bundle in bundles:
        eq = np.array([e[1] for e in bundle["equity"]], dtype=float)
        pos = np.array([e[3] for e in bundle["equity"]], dtype=float)
        ann = (eq[-1] / float(bundle["initial_capital"])) ** (252.0 / len(eq)) - 1.0
        mdd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
        calmar = ann / abs(mdd) if mdd < 0 else float("nan")
        expo = float(np.mean(pos / np.maximum(eq, 1.0)))
        journal = build_journal(bundle)
        exits = [m for d in journal for m in d.moves if m.action == "exit" and m.ret is not None]
        entries = [
            (dt.date.fromisoformat(str(d.date)), m.stock_id)
            for d in journal
            for m in d.moves
            if m.action == "enter"
        ]
        print(f"══ {path.stem}")
        if not exits:
            print("  沒有任何已結束交易\n")
            continue
        net = np.array([float(m.ret) for m in exits]) - rt  # type: ignore[arg-type]
        months: dict[str, list[float]] = {}
        for m, r in zip(exits, net, strict=True):
            months.setdefault(str(m.entry_date)[:7], []).append(float(r))
        means = [sum(v) / len(v) for v in months.values()]
        mm = sum(means) / len(means)
        var = sum((x - mm) ** 2 for x in means) / (len(means) - 1) if len(means) > 1 else 0.0
        se = math.sqrt(var / len(means)) if var > 0 else 0.0
        tstat = mm / se if se > 0 else (float("inf") if mm > 0 else 0.0)
        if not args.no_lift and entries:
            got, base, n = _lift(entries, outcomes)
            ratio = got / base if base and base > 0 else float("nan")
            print(
                f"  **命中率倍率 {ratio:.2f}×**（進場後 60 日內漲 ≥ 50%：{got:.2%}，"
                f"同日全市場 {base:.2%}；可比對 {n:,} 筆進場）"
            )
        print(
            f"  已結束交易 {len(net):,} 筆　分布在 {len(months)} 個月　"
            f"中位持有 {np.median([m.held_sessions for m in exits]):.0f} 日"
        )
        print(
            f"  每筆期望值（扣成本）{net.mean():+.2%}　中位數 {np.median(net):+.2%}　"
            f"月區塊 t = {tstat:+.2f}（只報）"
        )
        print(
            f"  勝率 {(net > 0).mean():.1%}　最大單筆虧損 {net.min():+.1%}　"
            f"最大單筆獲利 {net.max():+.1%}（只報）"
        )
        ok = mdd >= -MDD_LIMIT
        print(
            f"  年化 {ann:+.1%}　**最大回撤 {mdd:.1%}**　Calmar {calmar:.2f}　平均曝險 {expo:.0%}"
        )
        print(f"  → 對照使用者門檻（回撤 ≤ {MDD_LIMIT:.0%}）：**{'達標' if ok else '未達標'}**\n")

    print("── 並列對照（D-141 §三 的既有結果，不是門檻）──")
    for name, (a, m) in BASELINES.items():
        print(
            f"  {name:26} 年化 {a:+.1%}　回撤 {m:.1%}　對照 35%：{'達標' if m >= -0.35 else '未達標'}"
        )


if __name__ == "__main__":
    main()
