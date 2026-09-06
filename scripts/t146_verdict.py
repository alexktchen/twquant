"""T-146：D-066 證偽測試的**機械判定**。L0-new（乾淨模型）vs X1（四因子）。

D-066 明文要求「判定依準則機械套用，**執行者不下結論**」。所以判定寫成程式：
輸入是兩個結果包，輸出是甲／乙／丙。人只讀輸出，不挑門檻。

事前登記的三層（D-066，結果出來前寫定，不得再改）：

| 層 | 判準（任一） | 授權什麼 |
|---|---|---|
| 甲 繼續投入開發 | 月度區塊 t ≥ +2.0，或 ΔCAGR ≥ +3pp 且 ΔMDD 不劣於 −3pp | 可再調特徵、調參、開新世代 |
| 乙 保留但停止開發 | t ≥ 0，或 Sharpe 差 ≥ +0.10 | 留在因子庫與組合裡，但不再投入調優 |
| 丙 退場 | 兩者皆未達 | 從候選訊號中移除 |

「未能區分」對**甲層**算未達（不能從「差異不顯著」推出 ML 沒問題）；
對**乙層**則看點估計的符號與 Sharpe 差——乙層問的是「留不留」而非「贏不贏」。

不確定性一律用**月度區塊**標準誤：持股期 20 個交易日、日報酬高度自相關，
把每日配對差當獨立樣本會把顯著性高估約 4.5 倍（D-061／R1）。

用法：
    python scripts/t146_verdict.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import argparse

from twquant.backtest.validation import paired_block_stats

_AP = argparse.ArgumentParser()
_AP.add_argument(
    "--dir",
    default="D:/project/twquant/data/ladder",
    help="結果包目錄。**穩健性檢查用**：同一組判準套在不同資金的那批上，"
    "若判定會隨資金翻轉，那件事本身就必須回報",
)
_ARGS = _AP.parse_args()
LADDER = pathlib.Path(_ARGS.dir)
TREAT = LADDER / "L0new_ladder_l0_new_1x.json"  # 處理組：乾淨模型
CONTROL = LADDER / "X1_ladder_x1_simple20_1x.json"  # 對照組：四因子，其餘逐字同

# 事前登記的門檻。改動這四個常數即違反 D-066——它們是在看到結果之前寫定的。
T_TIER_A = 2.0
DCAGR_TIER_A = 0.03
DMDD_TIER_A = -0.03
T_TIER_B = 0.0
DSHARPE_TIER_B = 0.10


def _curve(b: dict[str, object]) -> dict[dt.date, float]:
    return {dt.date.fromisoformat(str(r[0])): float(r[1]) for r in b["equity"]}  # type: ignore[index]


def _daily_diff(
    a: dict[dt.date, float], b: dict[dt.date, float]
) -> tuple[list[dt.date], list[float]]:
    """兩條曲線在**共同交易日**上的逐日報酬差。日期不對齊時只取交集。"""
    common = sorted(set(a) & set(b))
    days: list[dt.date] = []
    diffs: list[float] = []
    for i in range(1, len(common)):
        p, c = common[i - 1], common[i]
        if a[p] <= 0 or b[p] <= 0:
            continue
        days.append(c)
        diffs.append((a[c] / a[p] - 1.0) - (b[c] / b[p] - 1.0))
    return days, diffs


def main() -> None:
    print(f"結果包目錄：{LADDER}")
    missing = [p.name for p in (TREAT, CONTROL) if not p.exists()]
    if missing:
        raise SystemExit(f"缺結果包 {missing}——先跑 scripts/run_ladder.py")
    treat = json.loads(TREAT.read_text(encoding="utf-8"))
    ctrl = json.loads(CONTROL.read_text(encoding="utf-8"))

    # 同一份資料是配對比較的前提。**不能直接比 data_fingerprint**：它雜湊的是該策略
    # 實際讀到的表集合，而訊號不同必然讀不同的表（L0-new 讀 ml_scores、X1 讀
    # financial_statements + monthly_revenue），指紋因此永遠不同——那是設計如此，
    # 不是資料有問題。正確的判準是**共用表的列數與水位一致**。
    tt = treat.get("data_tables") or {}
    tc = ctrl.get("data_tables") or {}
    shared = sorted(set(tt) & set(tc))
    if not shared:
        raise SystemExit("兩組沒有任何共用資料表，無從驗證是否同一份資料。")
    mismatch = [k for k in shared if tt[k] != tc[k]]
    print("資料一致性（比共用表，不比整體指紋——訊號不同必然讀不同的表）：")
    for k in shared:
        ok = tt[k] == tc[k]
        print(f"  {k:24} {tt[k][0]:>12,} 列 @ {tt[k][1]}　{'一致 ✓' if ok else '**不一致 ✗**'}")
    only_t, only_c = sorted(set(tt) - set(tc)), sorted(set(tc) - set(tt))
    if only_t or only_c:
        print(f"  各自獨有：處理組 {only_t or '—'}　對照組 {only_c or '—'}（訊號不同所致，非缺陷）")
    if mismatch:
        raise SystemExit(
            f"共用表 {mismatch} 的列數或水位不同——兩組不在同一份資料上，配對比較無效。"
        )

    mt, mc = treat["metrics"], ctrl["metrics"]
    d_cagr = mt["cagr"] - mc["cagr"]
    d_mdd = mt["mdd"] - mc["mdd"]  # mdd 是負值；差為正＝回撤較淺＝較好
    d_sharpe = mt["sharpe"] - mc["sharpe"]
    days, diffs = _daily_diff(_curve(treat), _curve(ctrl))
    st = paired_block_stats(days, diffs)
    t = None if st.block_se in (None, 0.0) else st.mean / st.block_se

    print(f"\n{'':22}{'L0-new':>12}{'X1':>12}{'差':>12}")
    print(f"{'CAGR':22}{mt['cagr']:>+12.2%}{mc['cagr']:>+12.2%}{d_cagr:>+12.2%}")
    print(f"{'MDD':22}{mt['mdd']:>12.2%}{mc['mdd']:>12.2%}{d_mdd:>+12.2%}")
    print(f"{'Sharpe':22}{mt['sharpe']:>12.2f}{mc['sharpe']:>12.2f}{d_sharpe:>+12.2f}")
    se = "—" if st.block_se is None else f"{st.block_se * 100:.4f}pp"
    ts = "—" if t is None else f"{t:+.2f}"
    print(
        f"\n配對日報酬差：均值 {st.mean * 100:+.4f}pp／日、月度區塊 SE {se}"
        f"、**t = {ts}**（{st.n_blocks} 個月區塊、{len(diffs)} 個交易日）"
    )

    # ── 機械判定：先甲後乙，任一子條件成立即該層 ──────────────────────
    a_t = t is not None and t >= T_TIER_A
    a_pt = d_cagr >= DCAGR_TIER_A and d_mdd >= DMDD_TIER_A
    b_t = t is not None and t >= T_TIER_B
    b_sh = d_sharpe >= DSHARPE_TIER_B
    tier = "甲" if (a_t or a_pt) else ("乙" if (b_t or b_sh) else "丙")

    print("\n逐條判準（事前登記，不得看到結果後調整）：")
    print(f"  甲-1 月度區塊 t ≥ +{T_TIER_A}          → {ts:>7}  {'達成' if a_t else '未達'}")
    print(
        f"  甲-2 ΔCAGR ≥ +{DCAGR_TIER_A:.0%} 且 ΔMDD ≥ {DMDD_TIER_A:.0%}  "
        f"→ {d_cagr:+.2%} / {d_mdd:+.2%}  {'達成' if a_pt else '未達'}"
    )
    print(f"  乙-1 t ≥ {T_TIER_B:.1f}                    → {ts:>7}  {'達成' if b_t else '未達'}")
    print(
        f"  乙-2 Sharpe 差 ≥ +{DSHARPE_TIER_B:.2f}         "
        f"→ {d_sharpe:+.2f}   {'達成' if b_sh else '未達'}"
    )

    meaning = {
        "甲": "繼續投入開發——可再調特徵、調參、開新世代",
        "乙": "保留但停止開發——ML 留在因子庫與組合裡（如 B1-ML），"
        "但**明文禁止**以「它還有價值」為由重啟特徵工程或調參",
        "丙": "退場——從候選訊號中移除",
    }[tier]
    print(f"\n{'=' * 68}\n判定：**{tier} 層**　{meaning}\n{'=' * 68}")
    if t is None:
        print("註：月度區塊 SE 不可得（區塊數 < 2），t 判準一律視為未達。")


if __name__ == "__main__":
    main()
