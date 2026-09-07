"""F／R 兩條線第二段的**機械判定**（D-074／D-075／D-080／D-081）。

判定對象是事前寫定的，不得看了結果再挑：

| 線 | 判定邊 | 判準 |
|---|---|---|
| F | **F1 − F2**（池子：營收驚奇分數 vs `mom_60d`），另報 F1 − F3（vs `high_52w`） | D-066 三層 |
| R | **R1 − R2**（閘門：回撤分數 vs `vol_20d`） | ΔMDD ≥ +5pp 且 ΔCAGR ≥ −3pp，或 ΔSharpe ≥ +0.15 |

`F1 − M1`、`R1 − C1` 也算、也印，但**不是判定依據**——那些邊同時換了兩件事以上。

日報酬高度自相關（持股期一個月），把每日配對差當獨立樣本會把顯著性高估約 4.5 倍，
故一律用**月度區塊標準誤**（D-061／R1）。

用法：
    python scripts/fr_verdict.py
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
    "F1": ROOT / "tracks_fr/F1_track_f1_revpool_1x.json",
    "F2": ROOT / "tracks_fr/F2_track_f2_mompool_1x.json",
    "F3": ROOT / "tracks_fr/F3_track_f3_highpool_1x.json",
    "R1": ROOT / "tracks_fr/R1_track_r1_c1_ddgate_1x.json",
    "R2": ROOT / "tracks_fr/R2_track_r2_c1_lvgate_1x.json",
    "M1": ROOT / "tracks_m/M1_track_m1_ml_c1_1x.json",
    "C1": ROOT / "tracks_c/C1_track_c1_breakout_1x.json",
}
# D-066 三層（F 線沿用）。門檻是常數；改動即違反 D-074。
T_A, DCAGR_A, DMDD_A, T_B, DSHARPE_B = 2.0, 0.03, -0.03, 0.0, 0.10
# D-075 第二段（風控導向，與其他線不同）
R_DMDD, R_DCAGR, R_DSHARPE = 0.05, -0.03, 0.15


def _load(k: str) -> dict | None:
    p = CELLS[k]
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _curve(b: dict) -> dict[dt.date, float]:
    return {dt.date.fromisoformat(str(r[0])): float(r[1]) for r in b["equity"]}


def _edge(a: dict, b: dict) -> tuple[float, float, float, float | None, int]:
    """(ΔCAGR, ΔMDD, ΔSharpe, 月度區塊 t, 區塊數)。ΔMDD 為正＝回撤變淺。"""
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


def _exposure(b: dict) -> float | None:
    d = b.get("diagnostics") or {}
    v = d.get("exposure") or d.get("mean_exposure")
    return float(v) if isinstance(v, int | float) else None


def _row(k: str, b: dict) -> str:
    m = b["metrics"]
    ex = _exposure(b)
    return (
        f"{k:5s} {m['cagr']:+8.2%} {m['mdd']:+8.2%} {m['sharpe']:+7.2f} "
        f"{m.get('turnover', float('nan')):8.0%} " + (f"{ex:7.0%}" if ex is not None else "      —")
    )


def main() -> None:
    cells = {k: _load(k) for k in CELLS}
    missing = [k for k, v in cells.items() if v is None]
    if missing:
        # 靜默略過會讓「判定」建立在半組結果上（黃金規則 5）
        raise SystemExit(f"缺結果包：{missing}。先跑 scripts/run_tracks.py")

    print("各格（1× 成本、80M 資金、2022-07-01 ~ 2026-02-28）")
    print(f"{'格':5s} {'CAGR':>8s} {'MDD':>8s} {'Sharpe':>7s} {'周轉':>8s} {'曝險':>7s}")
    for k in ("F1", "F2", "F3", "M1", "R1", "R2", "C1"):
        print(_row(k, cells[k]))

    print("\n" + "=" * 74)
    print("F 系列（D-074／D-081）：判定邊是 F1 − F2")
    print("=" * 74)
    for lhs, rhs, role in (
        ("F1", "F2", "**判定**"),
        ("F1", "F3", "更嚴對照"),
        ("F1", "M1", "參考"),
    ):
        dc, dm, ds, t, nb = _edge(cells[lhs], cells[rhs])
        ts = "—" if t is None else f"{t:+.2f}"
        print(
            f"{lhs} − {rhs}（{role}）：ΔCAGR {dc:+.2%}　ΔMDD {dm:+.2%}　"
            f"ΔSharpe {ds:+.2f}　月度區塊 t {ts}（{nb} 個月）"
        )
        if rhs == "F2":
            tier = (
                "甲（繼續投入）"
                if (t is not None and t >= T_A) or (dc >= DCAGR_A and dm >= DMDD_A)
                else "乙（保留為零件、停止投入）"
                if (t is not None and t >= T_B) or ds >= DSHARPE_B
                else "丙（整條線關閉）"
            )
            print(f"  → **{tier}**（D-066 三層，門檻事前寫定）")

    print("\n" + "=" * 74)
    print("R 系列（D-075／D-080）：判定邊是 R1 − R2")
    print("=" * 74)
    for lhs, rhs, role in (("R1", "R2", "**判定**"), ("R1", "C1", "參考"), ("R2", "C1", "參考")):
        dc, dm, ds, t, nb = _edge(cells[lhs], cells[rhs])
        ts = "—" if t is None else f"{t:+.2f}"
        print(
            f"{lhs} − {rhs}（{role}）：ΔCAGR {dc:+.2%}　ΔMDD {dm:+.2%}　"
            f"ΔSharpe {ds:+.2f}　月度區塊 t {ts}（{nb} 個月）"
        )
        if rhs == "R2":
            ok = (dm >= R_DMDD and dc >= R_DCAGR) or ds >= R_DSHARPE
            print(
                f"  → **{'通過' if ok else '未通過（整條線關閉）'}**"
                f"（ΔMDD ≥ {R_DMDD:.0%} 且 ΔCAGR ≥ {R_DCAGR:.0%}，或 ΔSharpe ≥ {R_DSHARPE:+.2f}）"
            )

    print("\n成本敏感度（D-038 §5：1.5× 成本後 CAGR／Sharpe 仍 > 0）")
    for k in ("F1", "F2", "F3", "R1", "R2"):
        p = pathlib.Path(str(CELLS[k]).replace("_1x.json", "_1.5x.json"))
        p0 = pathlib.Path(str(CELLS[k]).replace("_1x.json", "_0x.json"))
        m15 = json.loads(p.read_text(encoding="utf-8"))["metrics"]
        m0 = json.loads(p0.read_text(encoding="utf-8"))["metrics"]
        print(
            f"{k:5s} 0×：{m0['cagr']:+7.2%}／{m0['sharpe']:+5.2f}　"
            f"1.5×：{m15['cagr']:+7.2%}／{m15['sharpe']:+5.2f}"
        )

    print("\n判定依 D-074／D-075／D-080／D-081 事前寫定的門檻機械套用。")
    print("研究用途，非投資建議。尚無任何策略通過晉升判定，也未部署於實盤。")


if __name__ == "__main__":
    main()
