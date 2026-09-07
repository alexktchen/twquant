"""T-130／D-087：長窗口重跑（2017-01-03 ~ 2026-02-28）。

**視窗起日不是 2015-06**：資料回補到 2015-06-01，但 `fundamental.roe` 要 TTM 盈餘、
需要四季財報，最早只到 2016-08-15；四因子齊全的檔數在 2016-06 是 0 檔、2017-06 才有
1,379 檔。三格候選都用 `require_all_factors: true`，提早開跑會拿到暖機假象而非早期市場。

**與既有 113 筆完全不可比**：引擎現在收股利（D-085，含息報酬）、視窗不同、
宇宙定義不同（相對門檻，D-062）。只與本批的其他格與含息基準比。

用法：
    python scripts/run_long_window.py --list
    python scripts/run_long_window.py
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path("D:/project/twquant/data/tracks_lw")
# 保險庫邊界不變；起日見 module docstring
START, END = "2017-01-03", "2026-02-28"
CAPITAL = 80_000_000.0  # D-068，與既有批次同水位（雖不可比，至少資金水位一致）
COSTS = "costs.yaml"
SCALES = (("1x", None), ("0x", "0"), ("1.5x", "1.5"))
# 兩批格子是**兩次獨立的事前登記**（D-087 與 D-091），寫不同目錄、不同 trial_scope，
# 不得合併成一批看——D-087 明文「不在本批之後才決定加格」。
CELL_SETS: dict[str, tuple[tuple[str, str], ...]] = {
    "public": (  # D-087：公開因子架構，四格皆無風控
        ("LW-C1", "lw_c1_breakout"),
        ("LW-B2", "lw_b2_broad_capw"),
        ("LW-X1", "lw_x1_simple20"),
        ("LW-C1abs", "lw_c1_abs_threshold"),  # D-062 的驗證格
    ),
    "risk": (  # D-091：風控層能不能把丙層救回來。純公開因子，不需 ML 分數
        ("LW-R2", "lw_r2_c1_lvgate"),  # C1 ＋ 剔除波動最高 10%
        ("LW-C4", "lw_c4_voltarget"),  # C1 ＋ 波動率縮放（Barroso–Santa-Clara）
        ("LW-B1", "lw_b1_large_enhanced"),  # 市值前 100 選 30、市值加權（beta ≈ 1）
    ),
    "turnover": (  # D-092 P1：帶狀區間降周轉
        ("LW-C1band", "lw_c1_band"),
        ("LW-X1band", "lw_x1_band"),
    ),
    "ml": (  # D-092 P2：長窗口 ML 訊號進各載具（需先 backfill-scores）
        ("LW-M1", "lw_m1_ml_c1"),
        ("LW-R1", "lw_r1_c1_ddgate"),
        ("LW-B1ML", "lw_b1ml_large_enhanced"),
        ("LW-MG3", "lw_mg3_voltgt"),
    ),
    "timing": (  # D-092 P4：Faber 200 日擇時 overlay
        ("LW-B2trend", "lw_b2_trend"),
        ("LW-X1trend", "lw_x1_trend"),
        ("LW-B1trend", "lw_b1_trend"),
        ("LW-R2trend", "lw_r2_trend"),  # 預期無增量——為了確認反事實
    ),
    "public_all": (  # D-092 P5a：其餘純公開因子策略
        ("LW-A", "lw_a_smallcap"),
        ("LW-C2", "lw_c2_wbottom"),
        ("LW-C2b", "lw_c2b_boxbreak"),
        ("LW-C2c", "lw_c2c_flaghold"),
        ("LW-C3", "lw_c3_chipflow"),
        ("LW-F2", "lw_f2_mompool"),
        ("LW-F3", "lw_f3_highpool"),
        ("LW-M3", "lw_m3_mom_flag"),
        ("LW-MGALLm", "lw_mgallm_mom_allgates"),
        ("LW-L3", "lw_l3_simple"),
        ("LW-L4", "lw_l4_wt"),
    ),
    "ml_all": (  # D-092 P5b：其餘 ML 策略（P2 的四格另在 "ml"）
        ("LW-F1", "lw_f1_revpool"),
        ("LW-G1", "lw_g1_ml_revenue"),
        ("LW-G2", "lw_g2_ml_lowvol"),
        ("LW-G3", "lw_g3_ml_voltarget"),
        ("LW-G4", "lw_g4_ml_smooth"),
        ("LW-G5", "lw_g5_ml_large"),
        ("LW-G6", "lw_g6_ml_flaggate"),
        ("LW-GALL", "lw_gall_ml_allgates"),
        ("LW-M2", "lw_m2_ml_flag"),
        ("LW-M4", "lw_m4_ml_chip"),
        ("LW-M5", "lw_m5_ml_large"),
        # LW-M6 與 LW-MG3 改指同一個長窗口模型後逐字相同，只跑 MG3（在 "ml" 集）
        ("LW-MG1", "lw_mg1_rev"),
        ("LW-MG2", "lw_mg2_lowvol"),
        ("LW-MG4", "lw_mg4_ema"),
        ("LW-MG5", "lw_mg5_large"),
        ("LW-MGALL", "lw_mgall_ml_allgates"),
        ("LW-L0new", "lw_l0_new"),
        ("LW-L1", "lw_l1_n40"),
        ("LW-L2", "lw_l2_rev"),
    ),
    "f1_timing": (  # D-092 P6：營收驚奇 × 兩條擇時規則並排（需 ML 分數）
        ("LW-F1abs", "lw_f1_antonacci"),  # Antonacci 12 個月絕對動能
        ("LW-F1trend", "lw_f1_trend"),  # Faber 200 日均線
    ),
    "matrix": (  # D-092 P7：六條文獻規則 × 七個代表性基座
        ("LW-C1-faber", "lw_c1_faber"),
        ("LW-C1-anto", "lw_c1_anto"),
        ("LW-C1-mop", "lw_c1_mop"),
        ("LW-C1-mm", "lw_c1_mm"),
        ("LW-C1-dm", "lw_c1_dm"),
        ("LW-C1-bsc", "lw_c1_bsc"),
        ("LW-B2-faber", "lw_b2_faber"),
        ("LW-B2-anto", "lw_b2_anto"),
        ("LW-B2-mop", "lw_b2_mop"),
        ("LW-B2-mm", "lw_b2_mm"),
        ("LW-B2-dm", "lw_b2_dm"),
        ("LW-B2-bsc", "lw_b2_bsc"),
        ("LW-X1-faber", "lw_x1_faber"),
        ("LW-X1-anto", "lw_x1_anto"),
        ("LW-X1-mop", "lw_x1_mop"),
        ("LW-X1-mm", "lw_x1_mm"),
        ("LW-X1-dm", "lw_x1_dm"),
        ("LW-X1-bsc", "lw_x1_bsc"),
        ("LW-B1-faber", "lw_b1_faber"),
        ("LW-B1-anto", "lw_b1_anto"),
        ("LW-B1-mop", "lw_b1_mop"),
        ("LW-B1-mm", "lw_b1_mm"),
        ("LW-B1-dm", "lw_b1_dm"),
        ("LW-B1-bsc", "lw_b1_bsc"),
        ("LW-R2-faber", "lw_r2_faber"),
        ("LW-R2-anto", "lw_r2_anto"),
        ("LW-R2-mop", "lw_r2_mop"),
        ("LW-R2-mm", "lw_r2_mm"),
        ("LW-R2-dm", "lw_r2_dm"),
        ("LW-R2-bsc", "lw_r2_bsc"),
        ("LW-M1-faber", "lw_m1_faber"),
        ("LW-M1-anto", "lw_m1_anto"),
        ("LW-M1-mop", "lw_m1_mop"),
        ("LW-M1-mm", "lw_m1_mm"),
        ("LW-M1-dm", "lw_m1_dm"),
        ("LW-M1-bsc", "lw_m1_bsc"),
        ("LW-F1-faber", "lw_f1_faber"),
        ("LW-F1-anto", "lw_f1_anto"),
        ("LW-F1-mop", "lw_f1_mop"),
        ("LW-F1-mm", "lw_f1_mm"),
        ("LW-F1-dm", "lw_f1_dm"),
        ("LW-F1-bsc", "lw_f1_bsc"),
    ),
}
OUT_SUFFIX = {
    "public": "",
    "f1_timing": "_f1_timing",
    "matrix": "_matrix",
    "risk": "_risk",
    "turnover": "_turnover",
    "ml": "_ml",
    "timing": "_timing",
    "public_all": "_all",
    "ml_all": "_all_ml",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    # D-086：含息與不含息兩次相減才分離得出「股利」與「部位變大後撞參與率上限」。
    # 兩種模式寫**不同目錄**，且結果包自帶 `credit_dividends`——不會混淆。
    ap.add_argument(
        "--no-dividends",
        dest="nodiv",
        action="store_true",
        help="價格報酬口徑（寫入 tracks_lw_nodiv）",
    )
    ap.add_argument("--set", choices=sorted(CELL_SETS), default="public", help="哪一批登記的格子")
    ap.add_argument(
        "--scales", default="1x,0x,1.5x", help="逗號分隔的成本情境子集（如只跑 1x 先看結果）"
    )
    args = ap.parse_args()
    cells = CELL_SETS[args.set]
    out_dir = pathlib.Path(str(OUT) + OUT_SUFFIX[args.set] + ("_nodiv" if args.nodiv else ""))
    want = set(args.scales.split(","))
    scales = [(t, s) for t, s in SCALES if t in want]
    jobs = [(n, c, tag, sc) for n, c in cells for tag, sc in scales]
    todo = [j for j in jobs if not (out_dir / f"{j[0]}_{j[1]}_{j[2]}.json").exists()]
    print(
        f"[{args.set}] {len(cells)} 格 × {len(scales)} 情境 = {len(jobs)} 筆；待跑 {len(todo)}（已存在者跳過）"
    )
    print(
        f"視窗 {START} ~ {END}　資金 {CAPITAL:,.0f}　scope 依配置（{args.set}）"
        f"　口徑 {'價格報酬（--no-dividends）' if args.nodiv else '含息'}"
    )
    if args.list:
        for n, c, tag, _ in todo:
            print(f"  {n:10s} {tag:5s} → {out_dir.name}/{n}_{c}_{tag}.json")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    failures: list[str] = []
    for i, (name, cfg, tag, scale) in enumerate(todo, 1):
        dest = out_dir / f"{name}_{cfg}_{tag}.json"
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
        if args.nodiv:
            cmd += ["--no-dividends"]
        el = (time.perf_counter() - t0) / 60
        print(f"\n[{i}/{len(todo)}] {name} {tag}（已 {el:.0f} 分）", flush=True)
        r = subprocess.run(cmd, cwd=REPO)
        if r.returncode != 0:
            failures.append(f"{name} {tag}（exit {r.returncode}）")
            print(f"!! {name} {tag} 失敗 exit {r.returncode}", flush=True)
    print(f"\n完成，共 {(time.perf_counter() - t0) / 60:.0f} 分", flush=True)
    if failures:
        print(f"失敗 {len(failures)} 筆：{'；'.join(failures)}", flush=True)


if __name__ == "__main__":
    main()
