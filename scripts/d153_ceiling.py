"""D-153 §〇 (a)：出場的**天花板**——描述統計，不是測試、不設門檻。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d153_ceiling.py \\
        --arm A20 D:/project/twquant/data/models/<A20 的目錄> [--arm A ...]

每臂每日前 20 名（walk-forward 樣本外，與 `d151_report.py` 同一套篩選），t+1 開盤進場，對照四種出場：
- 抱滿 20 日（`fwd20`）——第十三輪規則臂的基準；
- **停利天花板**：在 20 日內最高收盤賣出（`maxfwd`）；
- **停損天花板**：最後虧損的那些筆在進場價出掉（`max(fwd20, 0)`）；
- 全知：兩者合併（`max(maxfwd, 0)`）。
天花板減抱滿＝出場「最多」能多賺的；規則臂能吃掉多少，決定第十四輪要不要建模型出場（D-153 §〇 順序 (c)）。
只用 walk-forward 樣本外（≤ 2026-04-30），不碰保留段與第二保留期。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys

import polars as pl
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from d151_report import _S, TOP_N, load_market


def _picks(oos: pl.DataFrame, mk: pl.DataFrame) -> pl.DataFrame:
    """與 `d151_report.report_up` 同一套篩選與排名：標籤／波動／60 日漲幅都有值的列才排名。"""
    df = oos.join(
        mk.select("date", _S, "vol20", "past60", "fwd20", "maxfwd", "minfwd"), on=["date", _S]
    ).filter(
        pl.col("label").is_not_null()
        & pl.col("vol20").is_not_null()
        & pl.col("past60").is_not_null()
    )
    return df.filter(pl.col("score").rank("ordinal", descending=True).over("date") <= TOP_N).filter(
        pl.col("fwd20").is_not_null()
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument("--arm", nargs=2, action="append", metavar=("NAME", "MODEL_DIR"), required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print("**D-153 §〇 (a)：出場天花板（描述統計，未扣成本）**")
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        mk = load_market(conn)
    for name, model_dir in args.arm:
        p = _picks(pl.read_parquet(pathlib.Path(model_dir) / "oos_scores.parquet"), mk)
        hold = float(p["fwd20"].mean())
        top = float(p["maxfwd"].mean())
        cut = float(p["fwd20"].clip(lower_bound=0.0).mean())
        both = float(p["maxfwd"].clip(lower_bound=0.0).mean())
        won = p.filter(pl.col("label") == 1.0)
        lost = p.filter(pl.col("label") == 0.0)
        print(
            f"\n══ {name}（{p['date'].min()} ~ {p['date'].max()}；{p.height:,} 筆）\n"
            f"  抱滿 20 日 {hold:+.2%}（中位 {float(p['fwd20'].median()):+.2%}）\n"
            f"  停利天花板（最高收盤賣）{top:+.2%} → 比抱滿多 {top - hold:+.2%}\n"
            f"  停損天花板（虧損筆在進場價出）{cut:+.2%} → 比抱滿多 {cut - hold:+.2%}\n"
            f"  全知 {both:+.2%} → 比抱滿多 {both - hold:+.2%}\n"
            f"  分解：命中者 {won.height:,} 筆 抱滿 {float(won['fwd20'].mean()):+.2%}／最高 {float(won['maxfwd'].mean()):+.2%}；"
            f"未命中 {lost.height:,} 筆 抱滿 {float(lost['fwd20'].mean()):+.2%}／最高 {float(lost['maxfwd'].mean()):+.2%}"
            f"／最低 {float(lost['minfwd'].mean()):+.2%}"
        )
        yr = (
            p.group_by(pl.col("date").dt.year().alias("y"))
            .agg(h=pl.col("fwd20").mean(), t=pl.col("maxfwd").mean())
            .sort("y")
        )
        print(
            "  逐年 抱滿→最高收盤："
            + "　".join(f"{r['y']} {r['h']:+.1%}→{r['t']:+.1%}" for r in yr.iter_rows(named=True))
        )


if __name__ == "__main__":
    main()
