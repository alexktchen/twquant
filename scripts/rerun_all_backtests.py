"""T-172：D-085 之後把全部回測重跑一次（引擎現在會收股利現金）。

**為什麼要重跑**：`engine.py` 原本沒有任何收益路徑，除息當天只看到價格跳空下跌、
記為虧損。於是 113 筆回測的每一個 CAGR 都是**價格報酬**，卻一直在跟含息的 0050
（+40.08%）比。實測股利占初始權益 8.86%~24.38%，CAGR 的含息下界高
**+0.89 ~ +3.36pp**，而且幅度不一致——連排序都會變。

**這不消耗 DSR 額度**：依 T-078／T-145 的判準，**修缺陷不是新假設**。
重跑的是同一批已登記的候選，不是新的試驗。

**寫到新目錄**（`<原目錄>_div`），舊結果保留。理由是那才能量出「股利到底值多少」
——同一個策略、同一個視窗、只差引擎的一行，兩份結果相減就是答案。
全部重跑完、比對過，才把報告切到新目錄。

用法：
    python scripts/rerun_all_backtests.py --list          # 只列出要跑什麼
    python scripts/rerun_all_backtests.py                 # 全跑（可續傳）
    python scripts/rerun_all_backtests.py --only C1,F1    # 只跑指定幾格
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
DATA = pathlib.Path("D:/project/twquant/data")
START, END = "2022-07-01", "2026-02-28"  # 保險庫邊界，不得後移
CAPITAL = 80_000_000.0  # D-068
COSTS = "costs.yaml"
SCALES = (("1x", None), ("0x", "0"), ("1.5x", "1.5"))

# (顯示名, 原目錄, 結果包前綴, 策略配置名)。與 `scripts/report_bundle.py` 的 SPECS 同源，
# 但這裡多帶一個配置名——重跑需要它，報告只需要讀結果包。
CELLS: tuple[tuple[str, str, str, str], ...] = (
    ("L0", "ladder", "L0_ml_net_h20_m", "ml_net_h20_m"),
    ("L0-new", "ladder", "L0new_ladder_l0_new", "ladder_l0_new"),
    ("L1", "ladder", "L1_ladder_l1_n40", "ladder_l1_n40"),
    ("L2", "ladder", "L2_ladder_l2_rev", "ladder_l2_rev"),
    ("L3", "ladder", "L3_ladder_l3_simple", "ladder_l3_simple"),
    ("L4", "ladder", "L4_ladder_l4_wt", "ladder_l4_wt"),
    ("X1", "ladder", "X1_ladder_x1_simple20", "ladder_x1_simple20"),
    ("A", "tracks", "A_track_a_smallcap", "track_a_smallcap"),
    ("B1", "tracks", "B1_track_b1_large_enhanced", "track_b1_large_enhanced"),
    ("B1ML", "tracks", "B1ML_track_b1ml_large_enhanced", "track_b1ml_large_enhanced"),
    ("B2", "tracks", "B2_track_b2_broad_capw", "track_b2_broad_capw"),
    ("C1", "tracks_c", "C1_track_c1_breakout", "track_c1_breakout"),
    ("C2", "tracks_c2", "C2_track_c2_wbottom", "track_c2_wbottom"),
    ("C2b", "tracks_c2bc", "C2b_track_c2b_boxbreak", "track_c2b_boxbreak"),
    ("C2c", "tracks_c2bc", "C2c_track_c2c_flaghold", "track_c2c_flaghold"),
    ("C3", "tracks_c3", "C3_track_c3_chipflow", "track_c3_chipflow"),
    ("C4", "tracks_c4", "C4_track_c4_voltarget", "track_c4_voltarget"),
    ("M1", "tracks_m", "M1_track_m1_ml_c1", "track_m1_ml_c1"),
    ("M2", "tracks_m23", "M2_track_m2_ml_flag", "track_m2_ml_flag"),
    ("M3", "tracks_m23", "M3_track_m3_mom_flag", "track_m3_mom_flag"),
    ("M4", "tracks_m", "M4_track_m4_ml_chip", "track_m4_ml_chip"),
    ("M5", "tracks_m", "M5_track_m5_ml_large", "track_m5_ml_large"),
    ("M6", "tracks_m", "M6_track_m6_ml_voltarget", "track_m6_ml_voltarget"),
    ("G1", "tracks_g", "G1_track_g1_ml_revenue", "track_g1_ml_revenue"),
    ("G2", "tracks_g", "G2_track_g2_ml_lowvol", "track_g2_ml_lowvol"),
    ("G3", "tracks_g", "G3_track_g3_ml_voltarget", "track_g3_ml_voltarget"),
    ("G4", "tracks_g", "G4_track_g4_ml_smooth", "track_g4_ml_smooth"),
    ("G5", "tracks_g", "G5_track_g5_ml_large", "track_g5_ml_large"),
    ("G6", "tracks_g", "G6_track_g6_ml_flaggate", "track_g6_ml_flaggate"),
    ("GALL", "tracks_g", "GALL_track_gall_ml_allgates", "track_gall_ml_allgates"),
    ("MG1", "tracks_mg", "MG1_track_mg1_rev", "track_mg1_rev"),
    ("MG2", "tracks_mg", "MG2_track_mg2_lowvol", "track_mg2_lowvol"),
    ("MG3", "tracks_mg", "MG3_track_mg3_voltgt", "track_mg3_voltgt"),
    ("MG4", "tracks_mg", "MG4_track_mg4_ema", "track_mg4_ema"),
    ("MG5", "tracks_mg", "MG5_track_mg5_large", "track_mg5_large"),
    ("F1", "tracks_fr", "F1_track_f1_revpool", "track_f1_revpool"),
    ("F2", "tracks_fr", "F2_track_f2_mompool", "track_f2_mompool"),
    ("F3", "tracks_fr", "F3_track_f3_highpool", "track_f3_highpool"),
    ("R1", "tracks_fr", "R1_track_r1_c1_ddgate", "track_r1_c1_ddgate"),
    ("R2", "tracks_fr", "R2_track_r2_c1_lvgate", "track_r2_c1_lvgate"),
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="只列出要跑什麼")
    ap.add_argument("--only", default="", help="逗號分隔的格名；空＝全部")
    args = ap.parse_args()

    want = {x.strip() for x in args.only.split(",") if x.strip()}
    cells = [c for c in CELLS if not want or c[0] in want]
    if want - {c[0] for c in cells}:
        raise SystemExit(f"未知的格名：{sorted(want - {c[0] for c in cells})}")

    jobs = [(n, d, p, cfg, tag, sc) for n, d, p, cfg in cells for tag, sc in SCALES]
    todo = [j for j in jobs if not (DATA / f"{j[1]}_div" / f"{j[2]}_{j[4]}.json").exists()]
    print(
        f"{len(cells)} 格 × {len(SCALES)} 個成本情境 = {len(jobs)} 筆；待跑 {len(todo)} 筆（已存在者跳過）"
    )
    if args.list:
        for n, d, p, _cfg, tag, _sc in todo:
            print(f"  {n:7s} {tag:5s} → {d}_div/{p}_{tag}.json")
        return

    t0 = time.perf_counter()
    failures: list[str] = []
    for i, (name, d, prefix, cfg, tag, scale) in enumerate(todo, 1):
        out = DATA / f"{d}_div"
        out.mkdir(parents=True, exist_ok=True)
        dest = out / f"{prefix}_{tag}.json"
        cmd = [
            sys.executable,
            "-m",
            "twquant",
            "backtest",
            "--strategy",
            str(REPO / "configs" / "strategies" / f"{cfg}.yaml"),
            "--start",
            START,
            "--end",
            END,
            "--capital",
            str(CAPITAL),
            "--costs",
            COSTS,
            "--out",
            str(dest),
        ]
        if scale is not None:
            cmd += ["--cost-scale", scale]
        el = (time.perf_counter() - t0) / 60
        eta = (el / max(i - 1, 1)) * (len(todo) - i + 1) if i > 1 else 0.0
        print(f"\n[{i}/{len(todo)}] {name} {tag}（已 {el:.0f} 分，剩約 {eta:.0f} 分）", flush=True)
        r = subprocess.run(cmd, cwd=REPO)
        if r.returncode != 0:
            # 一格失敗不該讓其餘白等，但要據實記下（黃金規則 5）
            failures.append(f"{name} {tag}（exit {r.returncode}）")
            print(f"!! {name} {tag} 失敗 exit {r.returncode}", flush=True)

    print(f"\n完成，共 {(time.perf_counter() - t0) / 60:.0f} 分", flush=True)
    if failures:
        print(f"失敗 {len(failures)} 筆：{'；'.join(failures)}", flush=True)


if __name__ == "__main__":
    main()
