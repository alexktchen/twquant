"""T-127 AC-12：由 12 份結果包產出階梯結果表（SPEC-013 §3.7）。

**只填表，不下結論**。§3.7 明列三種不成立的推論（「X1 也輸 → 問題不在 ML」等），
本腳本刻意不產生任何因果陳述，只輸出可核對的量。

不確定性一律用**月度區塊**標準誤：持股期 20 個交易日、日報酬高度自相關，
把每日配對差當獨立樣本會嚴重高估顯著性（D-061／R1）。

子區間由**既有曲線切出，不重跑**（SPEC-013 §3.6）——結果包存在正是為了這件事。
"""

from __future__ import annotations

import datetime as dt
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from twquant.backtest.validation import paired_block_stats  # noqa: E402
from twquant.reporting.result_bundle import metrics_from_bundle, read_bundle  # noqa: E402

LADDER = pathlib.Path("D:/project/twquant/data/ladder")
OUT = pathlib.Path(__file__).resolve().parents[1] / "docs/reports/ladder-results.md"

RUNGS = [
    ("L0", "ml_net_h20_m", "現況基準（既有候選，scope 為 bt:ml_net_h20_turnover）"),
    ("L1", "ladder_l1_n40", "= L0，唯一變更 top_n 20 → 40"),
    ("L2", "ladder_l2_rev", "= L1，唯一變更宇宙加營收篩"),
    ("L3", "ladder_l3_simple", "= L2，唯一變更訊號 ML → 四因子"),
    ("L4", "ladder_l4_wt", "= L3，唯一變更等權 → 分數加權 p=2"),
    ("X1", "ladder_x1_simple20", "= L0，**只**換訊號為四因子（隔離格）"),
]
# 配對比較：每階與其**前一階**；X1 與 L0（它是 L0 的單一變更，不是 L4 的）
PAIRS = [("L1", "L0"), ("L2", "L1"), ("L3", "L2"), ("L4", "L3"), ("X1", "L0")]
SUBWINDOWS = [
    ("前段 2022-07~2023-12", dt.date(2022, 7, 1), dt.date(2023, 12, 31), "廣度正常期"),
    ("後段 2024-01~2026-02", dt.date(2024, 1, 1), dt.date(2026, 2, 28), "權值股集中期"),
]


def _curve(b: dict[str, object]) -> dict[dt.date, float]:
    return {dt.date.fromisoformat(r[0]): float(r[1]) for r in b["equity"]}  # type: ignore[union-attr,index]


def _cagr(curve: dict[dt.date, float], lo: dt.date, hi: dt.date) -> float | None:
    """區間年化報酬。以**曆年**折算（T-118：`_TRADING_DAYS` 寫死 252 與台股不符）。"""
    pts = sorted((d, v) for d, v in curve.items() if lo <= d <= hi)
    if len(pts) < 2 or pts[0][1] <= 0:
        return None
    years = (pts[-1][0] - pts[0][0]).days / 365.25
    if years <= 0:
        return None
    return (pts[-1][1] / pts[0][1]) ** (1.0 / years) - 1.0


def _mdd(curve: dict[dt.date, float], lo: dt.date, hi: dt.date) -> float | None:
    vals = [v for d, v in sorted(curve.items()) if lo <= d <= hi]
    if not vals:
        return None
    peak, worst = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1.0)
    return worst


def _daily_diff(
    a: dict[dt.date, float], b: dict[dt.date, float], lo: dt.date, hi: dt.date
) -> tuple[list[dt.date], list[float]]:
    """兩條曲線在**共同交易日**上的逐日報酬差。日期不對齊時只取交集。"""
    common = sorted(set(a) & set(b) & {d for d in a if lo <= d <= hi})
    days: list[dt.date] = []
    diffs: list[float] = []
    for i in range(1, len(common)):
        p, c = common[i - 1], common[i]
        if a[p] <= 0 or b[p] <= 0:
            continue
        days.append(c)
        diffs.append((a[c] / a[p] - 1.0) - (b[c] / b[p] - 1.0))
    return days, diffs


def _pct(x: float | None, d: int = 2) -> str:
    return "—" if x is None else f"{x * 100:+.{d}f}%"


def main() -> None:
    bundles: dict[tuple[str, str], dict[str, object]] = {}
    missing: list[str] = []
    for rung, cfg, _ in RUNGS:
        for tag in ("1x", "0x"):
            p = LADDER / f"{rung}_{cfg}_{tag}.json"
            if p.exists():
                bundles[(rung, tag)] = read_bundle(p)
            else:
                missing.append(f"{rung} {tag}")
    if missing:
        # 據實標明缺哪幾格，不以部分結果冒充完整階梯（黃金規則 5）
        print(f"缺 {len(missing)} 份結果包：{'、'.join(missing)}", flush=True)

    # 完整性檢查：**只能比同一階的 1× vs 0×**。跨階比指紋無意義——不同策略讀不同的表
    # （ML 階讀 ml_scores、四因子階讀財報與月營收），指紋本來就不同，比了必然假警報。
    # 同一階的兩個成本情境讀完全相同的表，指紋不同即代表兩次跑之間資料變了。
    fp_warn: list[str] = []
    checked = 0
    for rung, _, _ in RUNGS:
        a, b = bundles.get((rung, "1x")), bundles.get((rung, "0x"))
        if a is None or b is None:
            continue
        checked += 1
        if str(a["data_fingerprint"]) != str(b["data_fingerprint"]):
            fp_warn.append(
                f"- **{rung}**：1× 與 0× 的資料指紋不同"
                f"（`{str(a['data_fingerprint'])[:12]}…` vs `{str(b['data_fingerprint'])[:12]}…`）"
                "——兩次跑之間資料變了，該階的零成本對照無效"
            )
    if fp_warn:
        fp_warn = ["⚠ **資料一致性檢查未過**：", ""] + fp_warn + [""]
        print(f"!! {len(fp_warn) - 3} 階的 1×／0× 指紋不一致", flush=True)

    lo, hi = dt.date(2022, 7, 1), dt.date(2026, 2, 28)
    lines: list[str] = [
        "# T-127 控制組階梯結果（SPEC-013 §3.7）",
        "",
        f"產出時間：{dt.date.today().isoformat()}　區間 {lo} ~ {hi}（保險庫邊界）",
        "",
        "**本表只填量，不下結論。** SPEC-013 §3.7 明列三種**不成立**的推論，"
        "任何引用本表的文字都不得出現它們：",
        "",
        "- 「X1 也輸基準 → 問題不在 ML」。反例：基準 +10%、ML −10%、四因子 +5%——"
        "兩者都輸基準，但 ML 仍比四因子差 15pp。",
        "- 「零成本仍輸 → 成本不是元凶」。正確說法只到「移除成本仍不足以解釋全部落後」。",
        "- 「B 軌贏了 → 架構貢獻大於訊號貢獻」。B1／B2 同時換了四樣東西，無法歸因。",
        "",
        "不確定性一律用**月度區塊**標準誤（持股期 20 個交易日、日報酬高度自相關，"
        "日頻 t 值會嚴重高估顯著性）。`|t| < 2` 一律標為**未能區分**——那是合法結果。",
        "",
    ]
    if missing:
        lines += [f"> ⚠ 缺 {len(missing)} 份結果包：{'、'.join(missing)}。下表不完整。", ""]
    lines += fp_warn
    if checked and not fp_warn:
        lines += [
            f"資料一致性：{checked} 階的 1×／0× 指紋皆相同，零成本對照成立。"
            "（**跨階不比指紋**——不同策略讀不同的表，指紋必然不同，比了是假警報。）",
            "",
        ]

    for tag, label in (("1x", "1× 全成本"), ("0x", "0× 零成本反事實")):
        lines += [
            f"## {label}",
            "",
            "| 階 | 變更 | CAGR | Sharpe | MDD | 年化周轉 |",
            "|---|---|---|---|---|---|",
        ]
        for rung, _, desc in RUNGS:
            b = bundles.get((rung, tag))
            if b is None:
                lines.append(f"| **{rung}** | {desc} | — | — | — | — |")
                continue
            m = metrics_from_bundle(b)
            lines.append(
                f"| **{rung}** | {desc} | {_pct(m.cagr)} | {m.sharpe:+.3f} | "
                f"{_pct(m.mdd)} | {m.turnover * 100:.0f}% |"
            )
        lines.append("")

        lines += [
            f"### {label}：配對比較（每階 vs 前一階）",
            "",
            "| 比較 | ΔCAGR | ΔMDD | Δ日報酬（日均） | 區塊 SE | t | 判定 |",
            "|---|---|---|---|---|---|---|",
        ]
        for hi_r, lo_r in PAIRS:
            a, c = bundles.get((hi_r, tag)), bundles.get((lo_r, tag))
            if a is None or c is None:
                lines.append(f"| {hi_r} − {lo_r} | — | — | — | — | — | 缺結果包 |")
                continue
            ca, cb = _curve(a), _curve(c)
            d_cagr = (_cagr(ca, lo, hi) or 0.0) - (_cagr(cb, lo, hi) or 0.0)
            d_mdd = (_mdd(ca, lo, hi) or 0.0) - (_mdd(cb, lo, hi) or 0.0)
            days, diffs = _daily_diff(ca, cb, lo, hi)
            st = paired_block_stats(days, diffs)
            se = "—" if st.block_se is None else f"{st.block_se * 100:.4f}pp"
            t = "—" if st.t_stat is None else f"{st.t_stat:+.2f}"
            verdict = (
                "**未能區分**" if st.indistinguishable else ("較優" if st.mean > 0 else "較差")
            )
            lines.append(
                f"| {hi_r} − {lo_r} | {_pct(d_cagr)} | {_pct(d_mdd)} | "
                f"{st.mean * 100:+.4f}pp | {se} | {t} | {verdict} |"
            )
        lines.append("")

    # ── 子區間：由既有曲線切出，不重跑 ──────────────────────────────
    lines += [
        "## 子區間符號一致性（由既有曲線切出，**未重跑**）",
        "",
        "某階的結論若在兩段**符號相反**，即標記「窗口相依，不可外推」——"
        "這是本專案目前最大的未知，不得用單一數字掩蓋（SPEC-013 §3.6）。",
        "",
        "| 比較 | " + " | ".join(f"{n}<br>{d}" for n, _, _, d in SUBWINDOWS) + " | 一致性 |",
        "|---|" + "---|" * (len(SUBWINDOWS) + 1),
    ]
    for hi_r, lo_r in PAIRS:
        a, c = bundles.get((hi_r, "1x")), bundles.get((lo_r, "1x"))
        if a is None or c is None:
            lines.append(
                f"| {hi_r} − {lo_r} | " + " | ".join(["—"] * len(SUBWINDOWS)) + " | 缺結果包 |"
            )
            continue
        ca, cb = _curve(a), _curve(c)
        cells, signs = [], []
        for _, s_lo, s_hi, _ in SUBWINDOWS:
            x, y = _cagr(ca, s_lo, s_hi), _cagr(cb, s_lo, s_hi)
            if x is None or y is None:
                cells.append("—")
                signs.append(0)
            else:
                cells.append(_pct(x - y))
                signs.append(1 if x - y > 0 else -1)
        ok = len(set(signs)) == 1 and 0 not in signs
        lines.append(
            f"| {hi_r} − {lo_r} | "
            + " | ".join(cells)
            + " | "
            + ("一致" if ok else "**窗口相依，不可外推**")
            + " |"
        )

    lines += [
        "",
        "## 登記備註",
        "",
        "- `trial_scope` 為 `bt:ladder_diag`（YAML 寫 `ladder_diag`，前綴由框架加）。"
        "L0 不在此 scope——它不改配置，仍屬 `bt:ml_net_h20_turnover`，是對照基準而非新候選。",
        "- **多一筆未登記的 trial**：2026-09-05 曾以 `ladder_x1_simple20` 在 "
        "2025-01-01~2025-03-31 跑過一次管線煙霧測試，區間不同故產生一個額外的 "
        "distinct `params_hash`。D-060 登記 10 個，實際 11 個。據實記錄，不隱藏。",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已寫出 {OUT}（{len(bundles)}/12 份結果包）", flush=True)


if __name__ == "__main__":
    main()
