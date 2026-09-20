"""D-120 §六：買進模型 SB 的兩道診斷——**都過才准進回測**。

    uv run --all-groups python scripts/sb_diagnostics.py --model D:/project/twquant/data/models/<model_id>

只讀模型目錄裡的 `oos_scores.parquet`（walk-forward 樣本外預測＋實際標籤），不重訓、不連 PG。
門檻逐字取自 D-120 §六，事前登記、不得事後修改：

1. **校準**：把預測機率分十組，每組「預測機率」與「實際發生率」的差 ≤ 10 個百分點。
   不過 → 不准用 0.5 當進場門檻。
2. **會不會空手**：壓力段（2020-03、2022 全年、2025-04）的「每日過門檻檔數」必須明顯低於
   其餘月份——以**月**為單位比較，Welch t ≥ 2。不過 → 它還是分不出市場好壞，
   「沒有好股票就空手」落空，整條線停下來重想。

刻意**不看任何報酬**：這兩關只問「機率準不準」「壞市場會不會收手」，與賺不賺錢無關。
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from pathlib import Path

import polars as pl

ENTRY_THRESHOLD = 0.5  # D-120 §三
N_BINS = 10
MAX_CALIBRATION_GAP = 0.10  # D-120 §六.1
MIN_T = 2.0  # D-120 §六.2
DEV_END = dt.date(2026, 8, 31)  # D-109：之後為第二保留期
# D-120 §六.2 點名的三段壓力期（以月計）
STRESS_MONTHS: frozenset[str] = frozenset(
    {"2020-03", "2025-04"} | {f"2022-{m:02d}" for m in range(1, 13)}
)


def calibration(oos: pl.DataFrame) -> tuple[pl.DataFrame, bool]:
    """等寬十組的預測機率 vs 實際發生率。空組不計（沒有樣本就沒有可比的東西）。"""
    table = (
        oos.with_columns(_bin=(pl.col("score") * N_BINS).floor().clip(0, N_BINS - 1).cast(pl.Int32))
        .group_by("_bin")
        .agg(
            n=pl.len(),
            predicted=pl.col("score").mean(),
            actual=pl.col("label").mean(),
        )
        .sort("_bin")
        .with_columns(gap=(pl.col("predicted") - pl.col("actual")).abs())
    )
    return table, bool((table["gap"] <= MAX_CALIBRATION_GAP).all())


def stress_test(oos: pl.DataFrame) -> tuple[dict[str, float], bool]:
    """每日過門檻檔數 → 月平均 → 壓力月 vs 其餘月的 Welch t。"""
    daily = oos.group_by("date").agg(n_pass=(pl.col("score") >= ENTRY_THRESHOLD).sum())
    monthly = (
        daily.with_columns(_m=pl.col("date").dt.strftime("%Y-%m"))
        .group_by("_m")
        .agg(avg=pl.col("n_pass").mean())
        .with_columns(stress=pl.col("_m").is_in(sorted(STRESS_MONTHS)))
    )
    hot = monthly.filter(pl.col("stress"))["avg"].to_list()
    calm = monthly.filter(~pl.col("stress"))["avg"].to_list()
    if len(hot) < 2 or len(calm) < 2:
        raise SystemExit(f"壓力月 {len(hot)} 個、其餘 {len(calm)} 個——樣本不足以比較")

    def _mv(xs: list[float]) -> tuple[float, float]:
        m = sum(xs) / len(xs)
        return m, sum((x - m) ** 2 for x in xs) / (len(xs) - 1)

    (mh, vh), (mc, vc) = _mv(hot), _mv(calm)
    se = math.sqrt(vh / len(hot) + vc / len(calm))
    t = (mc - mh) / se if se > 0 else float("inf") if mc > mh else 0.0
    stats = {"stress_avg": mh, "calm_avg": mc, "t": t, "n_stress": len(hot), "n_calm": len(calm)}
    return stats, t >= MIN_T


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True, help="模型目錄（含 oos_scores.parquet）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 主控台預設 cp950，印不出 ≤／✓

    oos = pl.read_parquet(Path(args.model) / "oos_scores.parquet").filter(
        pl.col("label").is_not_null()
    )
    last = oos["date"].max()
    if last is None or last > DEV_END:
        raise SystemExit(f"樣本外預測到 {last}，越過 D-109 的保留期邊界（{DEV_END}），拒絕診斷")
    base = float(oos["label"].mean() or 0.0)
    print(f"樣本外 {oos.height:,} 列（{oos['date'].min()} ~ {last}）　整體發生率 {base:.1%}")
    share = float((oos["score"] >= ENTRY_THRESHOLD).mean() or 0.0)
    print(f"預測機率 ≥ {ENTRY_THRESHOLD} 的列占 {share:.2%}\n")

    table, ok_cal = calibration(oos)
    print(f"【一】校準（每組差距須 ≤ {MAX_CALIBRATION_GAP:.0%}）")
    print(f"  {'機率區間':<12}{'樣本數':>10}{'預測':>9}{'實際':>9}{'差距':>9}")
    for r in table.to_dicts():
        lo = r["_bin"] / N_BINS
        flag = "" if r["gap"] <= MAX_CALIBRATION_GAP else "  ✗"
        print(
            f"  {lo:.1f}~{lo + 1 / N_BINS:.1f}    {r['n']:>10,}{r['predicted']:>9.1%}"
            f"{r['actual']:>9.1%}{r['gap']:>9.1%}{flag}"
        )
    print(f"  → {'通過' if ok_cal else '未通過'}\n")

    stats, ok_stress = stress_test(oos)
    print(f"【二】會不會空手（壓力月每日過門檻檔數須明顯較少，t ≥ {MIN_T}）")
    print(f"  壓力月（{stats['n_stress']:.0f} 個）平均每日 {stats['stress_avg']:.1f} 檔過門檻")
    print(f"  其餘月（{stats['n_calm']:.0f} 個）平均每日 {stats['calm_avg']:.1f} 檔過門檻")
    print(f"  t = {stats['t']:+.2f}  → {'通過' if ok_stress else '未通過'}\n")

    if ok_cal and ok_stress:
        print("**兩關皆過**：SB 可以進 S 系列回測（D-120 §七）。")
    else:
        failed = [n for n, ok in (("校準", ok_cal), ("會不會空手", ok_stress)) if not ok]
        print(
            f"**未通過：{'、'.join(failed)}。** 依 D-120 §六，不進回測；不得調門檻或換特徵後重試，須另立裁決。"
        )


if __name__ == "__main__":
    main()
