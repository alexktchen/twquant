"""E0 與 A20 的組合（2026-09-28，使用者問「有沒有不一樣的策略」）——**事後描述統計，不是登記的試驗**。

    uv run --all-groups python scripts/blend_e0_a20.py

只拿兩條現成的權益曲線做組合、不改任何訊號：E0＝`data/tracks_e_long_nodiv/E0_lw_e0_ctrl_1x.json`（2019-05~2026-02），
A20＝第十三輪 S0 的 1,000 萬・零股組 `data/tracks_round13/quiet_s0_10m_lot1.json`（D-153 §八）。
兩者重疊的交易日上，每月初把權重拉回目標（A20 0%／30%／50%／70%／100%），報年化、最大回撤、Calmar 與相關係數。
注意：權重是在同一段資料上看的，只能拿來回答「有沒有分散效果」，不能拿來挑權重。
"""

from __future__ import annotations

import json
import pathlib
import sys

import numpy as np
import polars as pl

DATA = pathlib.Path("D:/project/twquant/data")
E0 = DATA / "tracks_e_long_nodiv" / "E0_lw_e0_ctrl_1x.json"
A20 = DATA / "tracks_round13" / "quiet_s0_10m_lot1.json"
WEIGHTS = (0.0, 0.3, 0.5, 0.7, 1.0)  # A20 的權重
DAYS_PER_YEAR = 252  # 與 d146_report._stats 同一慣例


def _curve(path: pathlib.Path) -> tuple[dict[str, object], pl.DataFrame]:
    b = json.loads(path.read_text(encoding="utf-8"))
    eq = pl.DataFrame(
        [(e[0], float(e[1])) for e in b["equity"]], schema=["date", "eq"], orient="row"
    )
    return b, eq.with_columns(pl.col("date").str.to_date())


def _stats(eq: np.ndarray) -> tuple[float, float, float]:
    ann = float((eq[-1] / eq[0]) ** (DAYS_PER_YEAR / len(eq)) - 1.0)
    mdd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    return ann, mdd, ann / abs(mdd)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    be, e0 = _curve(E0)
    ba, a20 = _curve(A20)
    print(f"E0 {be['start']} ~ {be['end']}｜A20 {ba['start']} ~ {ba['end']}")
    df = e0.rename({"eq": "e0"}).join(a20.rename({"eq": "a20"}), on="date").sort("date")
    r = df.with_columns(
        pl.col("e0").pct_change().alias("re0"), pl.col("a20").pct_change().alias("ra")
    ).drop_nulls()
    m = (
        r.group_by(pl.col("date").dt.truncate("1mo"))
        .agg(
            ((1 + pl.col("re0")).product() - 1).alias("m0"),
            ((1 + pl.col("ra")).product() - 1).alias("ma"),
        )
        .sort("date")
    )
    print(
        f"重疊 {df['date'].min()} ~ {df['date'].max()}（{df.height} 個交易日）；相關係數 "
        f"日 {np.corrcoef(r['re0'], r['ra'])[0, 1]:.2f}、月 {np.corrcoef(m['m0'], m['ma'])[0, 1]:.2f}"
    )
    re0, ra = r["re0"].to_numpy(), r["ra"].to_numpy()
    month = r["date"].dt.strftime("%Y-%m").to_numpy()
    for w in WEIGHTS:
        v0, va, out = 1.0 - w, w, [1.0]
        for i in range(len(re0)):
            if i > 0 and month[i] != month[i - 1]:
                tot = v0 + va
                v0, va = tot * (1.0 - w), tot * w
            v0 *= 1.0 + re0[i]
            va *= 1.0 + ra[i]
            out.append(v0 + va)
        a, d, c = _stats(np.array(out))
        tag = {0.0: "E0 單獨", 1.0: "A20 單獨"}.get(w, f"E0 {1 - w:.0%}＋A20 {w:.0%}")
        print(f"  {tag:<16} 年化 {a:+.1%}　最大回撤 {d:.1%}　Calmar {c:.2f}")


if __name__ == "__main__":
    main()
