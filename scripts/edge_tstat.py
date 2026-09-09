"""兩格之間的配對 t 值，用**月度區塊標準誤**（D-061／R1）。

日頻 t 值會把顯著性灌水約 4.5 倍——同一個月的日報酬高度相關，
當成獨立觀測等於假裝樣本量大了 20 倍。故先把日差彙總成月差，再對月差做 t 檢定。

用法：python scripts/edge_tstat.py <A 的結果包> <B 的結果包>
判定 A − B：正值代表 A 較好。
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
from pathlib import Path

import polars as pl


def _daily(path: Path) -> pl.DataFrame:
    b = json.loads(path.read_text(encoding="utf-8"))
    rows = [(dt.date.fromisoformat(e[0]), float(e[1])) for e in b["equity"]]
    return (
        pl.DataFrame(rows, schema=["date", "eq"], orient="row")
        .sort("date")
        .with_columns(pl.col("eq").pct_change().alias("r"))
        .drop_nulls("r")
        .select("date", "r")
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    a, b = Path(sys.argv[1]), Path(sys.argv[2])
    ja, jb = _daily(a), _daily(b)
    j = ja.join(jb, on="date", how="inner", suffix="_b")
    if j.height < 250:
        raise SystemExit(f"共同交易日只有 {j.height} 天，太短")
    # 月度區塊：先把日差複合成月差（不是月差的算術平均——報酬要複合）
    m = (
        j.with_columns((pl.col("r") - pl.col("r_b")).alias("d"))
        .with_columns(pl.col("date").dt.strftime("%Y-%m").alias("mo"))
        .group_by("mo")
        .agg(
            ((pl.col("r") + 1.0).product() - (pl.col("r_b") + 1.0).product()).alias("dm"),
            pl.len().alias("n"),
        )
        .sort("mo")
    )
    d = m["dm"].to_numpy()
    n = len(d)
    mean = float(d.mean())
    sd = float(d.std(ddof=1))
    t = mean / (sd / math.sqrt(n)) if sd else float("nan")
    print(f"{a.stem}  −  {b.stem}")
    print(f"  共同交易日 {j.height}、月數 {n}")
    print(f"  月差平均 {mean * 100:+.3f}%／月　標準差 {sd * 100:.3f}%")
    print(f"  **t = {t:+.2f}**（月度區塊標準誤，D-061）")
    # 對照：日頻 t 會灌水多少
    dd = (j["r"] - j["r_b"]).to_numpy()
    td = float(dd.mean()) / (float(dd.std(ddof=1)) / math.sqrt(len(dd)))
    print(f"  （若用日頻會算成 t = {td:+.2f}——灌水 {abs(td / t) if t else 0:.1f} 倍，故不用）")
    print(
        f"  D-066 三層：甲 t ≥ +2.0／乙 t ≥ 0／丙 t < 0 → **{'甲' if t >= 2.0 else '乙' if t >= 0 else '丙'}層**"
    )


if __name__ == "__main__":
    main()
