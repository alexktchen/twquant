"""D-071／M 系列的**機械判定**：ML 當「池子」到底值多少。

D-071 明文寫定：判定對象是 **M2 − M3**，因為那是唯一乾淨的隔離——兩者逐字相同，
只把閘門因子從 `ml.lgbm_net_20d` 換成 `price.mom_60d`。

**不得以 M1／M4／M5／M6 任一格好看就宣稱 ML 有用**：那些格都同時換了兩件事以上。
本腳本因此把它們列為「參考」而非判定依據，且在輸出中明講。

門檻沿用 D-066 三層，是常數；改動即違反 D-071。

用法：
    python scripts/d071_verdict.py
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.backtest.validation import paired_block_stats

ROOT = pathlib.Path("D:/project/twquant/data")
CELLS = {
    "M2": ROOT / "tracks_m23/M2_track_m2_ml_flag_1x.json",
    "M3": ROOT / "tracks_m23/M3_track_m3_mom_flag_1x.json",
    "M1": ROOT / "tracks_m/M1_track_m1_ml_c1_1x.json",
    "M4": ROOT / "tracks_m/M4_track_m4_ml_chip_1x.json",
    "M5": ROOT / "tracks_m/M5_track_m5_ml_large_1x.json",
    "M6": ROOT / "tracks_m/M6_track_m6_ml_voltarget_1x.json",
    "C1": ROOT / "tracks_c/C1_track_c1_breakout_1x.json",
    "C3": ROOT / "tracks_c3/C3_track_c3_chipflow_1x.json",
    "B1": ROOT / "tracks/B1_track_b1_large_enhanced_1x.json",
}
T_A, DCAGR_A, DMDD_A, T_B, DSHARPE_B = 2.0, 0.03, -0.03, 0.0, 0.10


def _load(k: str) -> dict | None:
    p = CELLS[k]
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _curve(b: dict) -> dict[dt.date, float]:
    return {dt.date.fromisoformat(str(r[0])): float(r[1]) for r in b["equity"]}


def _edge(a: dict, b: dict) -> tuple[float, float, float, float | None, int]:
    ca, cb = _curve(a), _curve(b)
    com = sorted(set(ca) & set(cb))
    days, diffs = [], []
    for i in range(1, len(com)):
        p, c = com[i - 1], com[i]
        if ca[p] > 0 and cb[p] > 0:
            days.append(c)
            diffs.append((ca[c] / ca[p] - 1.0) - (cb[c] / cb[p] - 1.0))
    st = paired_block_stats(days, diffs)
    t = None if st.block_se in (None, 0.0) else st.mean / st.block_se
    ma, mb = a["metrics"], b["metrics"]
    return (
        ma["cagr"] - mb["cagr"],
        ma["mdd"] - mb["mdd"],
        ma["sharpe"] - mb["sharpe"],
        t,
        st.n_blocks,
    )


def _exposure(b: dict) -> float:
    eq = [(float(r[1]), float(r[2])) for r in b["equity"] if float(r[1]) > 0]
    return sum(1.0 - c / e for e, c in eq) / len(eq) if eq else 0.0


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    have = {k: b for k in CELLS if (b := _load(k)) is not None}
    print(f"{'格':6}{'CAGR':>9}{'MDD':>9}{'Sharpe':>8}{'周轉':>8}{'曝險':>7}")
    for k, b in have.items():
        m = b["metrics"]
        print(
            f"{k:6}{m['cagr']:>+9.2%}{m['mdd']:>9.1%}{m['sharpe']:>8.2f}"
            f"{m['turnover']:>8.0%}{_exposure(b):>7.0%}"
        )

    if "M2" not in have or "M3" not in have:
        print("\n缺 M2 或 M3，無法判定——那是唯一乾淨的隔離對。")
        return
    caps = {have[k]["initial_capital"] for k in ("M2", "M3")}
    if len(caps) != 1:
        raise SystemExit(f"M2／M3 資金不一致 {caps}（D-070：跨策略比較必須同資金）")

    dc, dm, ds, t, nb = _edge(have["M2"], have["M3"])
    ts = "—" if t is None else f"{t:+.2f}"
    print(f"\n{'=' * 72}\n判定對象：M2 − M3（ML 當池子 vs 動能當池子，其餘逐字相同）\n{'=' * 72}")
    print(f"  ΔCAGR {dc:+.2%}　ΔMDD {dm:+.2%}　ΔSharpe {ds:+.2f}　月度區塊 t {ts}（{nb} 個月）")
    a_t = t is not None and t >= T_A
    a_pt = dc >= DCAGR_A and dm >= DMDD_A
    b_t = t is not None and t >= T_B
    b_sh = ds >= DSHARPE_B
    tier = "甲" if (a_t or a_pt) else ("乙" if (b_t or b_sh) else "丙")
    print("\n逐條判準（D-071 事前寫定）：")
    print(f"  甲-1 t ≥ +{T_A}                → {ts:>7}  {'達成' if a_t else '未達'}")
    print(
        f"  甲-2 ΔCAGR ≥ +{DCAGR_A:.0%} 且 ΔMDD ≥ {DMDD_A:.0%} "
        f"→ {dc:+.2%} / {dm:+.2%}  {'達成' if a_pt else '未達'}"
    )
    print(f"  乙-1 t ≥ {T_B:.1f}                  → {ts:>7}  {'達成' if b_t else '未達'}")
    print(f"  乙-2 ΔSharpe ≥ +{DSHARPE_B:.2f}        → {ds:+.2f}   {'達成' if b_sh else '未達'}")
    meaning = {
        "甲": "ML 當池子確有貢獻，可續作",
        "乙": "保留但不再投入",
        "丙": "**ML 連當池子都沒用**，從候選中移除",
    }[tier]
    print(f"\n{'=' * 72}\n判定：**{tier} 層**　{meaning}\n{'=' * 72}")

    print("\n參考邊（**不得據以判定**——每一條都同時換了兩件事以上）：")
    for lab, a, b in (
        ("M1 − C1（ML 當池子，技術排序）", "M1", "C1"),
        ("M4 − C3（ML 當池子，籌碼排序）", "M4", "C3"),
        ("M5 − B1（ML 當池子，大型加權）", "M5", "B1"),
        ("M6 − M1（風險管理）", "M6", "M1"),
    ):
        if a in have and b in have:
            d1, d2, d3, tt, _ = _edge(have[a], have[b])
            print(
                f"  {lab:34} ΔCAGR {d1:+.2%}  ΔMDD {d2:+.2%}  "
                f"ΔSharpe {d3:+.2f}  t {'—' if tt is None else f'{tt:+.2f}'}"
            )


if __name__ == "__main__":
    main()
