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
    "revenue": (  # 營收線優先批：F 系列三格（隔離設計）＋ 兩個營收濾網 ＋ P6 兩條擇時
        # F1 − F2 才是「營收驚奇值多少」的判定；F3 是更嚴的對照。缺任一格都判不了。
        ("LW-F1", "lw_f1_revpool"),  # 營收驚奇池 × C1 排序
        ("LW-F2", "lw_f2_mompool"),  # 動能池（隔離對照）
        ("LW-F3", "lw_f3_highpool"),  # 52 週高點池（更嚴對照）
        ("LW-MG1", "lw_mg1_rev"),  # ML 池 ＋ 營收成長濾網
        ("LW-G1", "lw_g1_ml_revenue"),  # ML ＋ 營收成長濾網
        ("LW-L2", "lw_l2_rev"),  # 階梯 L2：宇宙加營收篩
        ("LW-F1abs", "lw_f1_antonacci"),  # P6：Antonacci
        ("LW-F1trend", "lw_f1_trend"),  # P6：Faber
    ),
    "revmom": (  # D-092 P8：營收驚奇 × 動能的隔離三格
        ("LW-RM-rev", "lw_rm_rev"),
        ("LW-RM-mom", "lw_rm_mom"),
        ("LW-RM-both", "lw_rm_both"),  # 判定格
    ),
    # D-102：矩陣依基座需不需要 ML 分數拆兩半——**兩半的視窗不同，不能同批跑**。
    "chip_feat": (  # D-103 第二段：籌碼特徵修正版的 F1
        ("LW-F1c", "lw_f1c_revpool"),
    ),
    "conc_timing": (  # D-099：50 檔版的 C1 × 三條擇時規則——檢定集中度假說
        ("LW-C1n50", "lw_c1_n50"),  # 基座（無 overlay），對照 LW-C1
        ("LW-C1n50-faber", "lw_c1n50_faber"),
        ("LW-C1n50-anto", "lw_c1n50_anto"),
        ("LW-C1n50-mop", "lw_c1n50_mop"),
    ),
    "band_timing": (  # D-095：降周轉基座 × 三條擇時規則，檢定 D-094 的解釋
        ("LW-C1band-faber", "lw_c1band_faber"),
        ("LW-C1band-anto", "lw_c1band_anto"),
        ("LW-C1band-mop", "lw_c1band_mop"),
    ),
    "matrix_pub": (  # 五個公開因子基座 × 6 規則；2017-01-03 起
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
    ),
    "matrix_ml": (  # M1／F1 兩個 ML 基座 × 6 規則；2019-05-02 起（分數起日之後）
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
    "chip_feat": "_chipfeat",
    "conc_timing": "_conctiming",
    "band_timing": "_bandtiming",
    "matrix_pub": "_matrix",
    "matrix_ml": "_matrix_ml",
    "revenue": "_revenue",
    "revmom": "_revmom",
    "risk": "_risk",
    "turnover": "_turnover",
    "ml": "_ml",
    "timing": "_timing",
    "public_all": "_all",
    "ml_all": "_all_ml",
}


# 一格跑一次就好：兩條線同時跑同一個 set 時，用檔案系統的原子建檔當認領。
# 這比「事先把格子分兩半」穩：任一條線提早結束或某格失敗，另一條會自然接手，
# 不必有人重新分配。
_STALE_HOURS = 3.0  # 單格實測約 17 分；3 小時仍無結果即視為那條線死了


def _claim(path: pathlib.Path) -> pathlib.Path | None:
    """原子地認領一格。認領成功回傳 claim 檔路徑；已被別人認領回傳 None。

    **過期的認領要能回收**：若認領檔超過 `_STALE_HOURS` 而對應結果仍不存在，
    代表認領它的程序死了（本輪已經發生過一次：佇列腳本被就地編輯而中止）。
    不回收的話那一格會永遠沒人跑，而且是靜默的。
    """
    try:
        path.touch(exist_ok=False)
        return path
    except FileExistsError:
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        if age_h < _STALE_HOURS:
            return None
        print(f"  （認領已過期 {age_h:.1f} 小時，回收）", flush=True)
        path.touch()  # 更新時間戳＝改由本行程認領
        return path


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
    # ML 策略跑不了 2017-01-03：walk-forward 的樣本外分數要先有兩年訓練資料，
    # 三個長窗口模型的第一個分數日分別是 2017-09-26／2019-01-30／2019-04-30。
    # 從更早開跑會在第一個再平衡日就撞上「池子被清空」而中止（D-102）。
    ap.add_argument("--start", default=START, help=f"回測起日；空＝{START}")
    ap.add_argument("--out-suffix", default="", help="輸出目錄額外後綴（不同視窗必須分開存）")
    args = ap.parse_args()
    cells = CELL_SETS[args.set]
    out_dir = pathlib.Path(
        str(OUT) + OUT_SUFFIX[args.set] + args.out_suffix + ("_nodiv" if args.nodiv else "")
    )
    want = set(args.scales.split(","))
    scales = [(t, s) for t, s in SCALES if t in want]
    jobs = [(n, c, tag, sc) for n, c in cells for tag, sc in scales]
    todo = [j for j in jobs if not (out_dir / f"{j[0]}_{j[1]}_{j[2]}.json").exists()]
    print(
        f"[{args.set}] {len(cells)} 格 × {len(scales)} 情境 = {len(jobs)} 筆；待跑 {len(todo)}（已存在者跳過）"
    )
    print(
        f"視窗 {args.start} ~ {END}　資金 {CAPITAL:,.0f}　scope 依配置（{args.set}）"
        f"　口徑 {'價格報酬（--no-dividends）' if args.nodiv else '含息'}"
    )
    if args.list:
        for n, c, tag, _ in todo:
            print(f"  {n:10s} {tag:5s} → {out_dir.name}/{n}_{c}_{tag}.json")
        return
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    failures: list[str] = []
    skipped = 0
    for i, (name, cfg, tag, scale) in enumerate(todo, 1):
        dest = out_dir / f"{name}_{cfg}_{tag}.json"
        if dest.exists():  # 另一條線在本次 todo 算出來之後跑完了它
            skipped += 1
            continue
        claim = _claim(out_dir / f".claim_{name}_{cfg}_{tag}")
        if claim is None:
            skipped += 1
            print(f"[{i}/{len(todo)}] {name} {tag}：另一條線正在跑，跳過", flush=True)
            continue
        cmd = [
            sys.executable,
            "-m",
            "twquant",
            "backtest",
            "--strategy",
            str(REPO / "configs" / "strategies" / f"{cfg}.yaml"),
            "--start",
            args.start,
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
            claim.unlink(missing_ok=True)  # 失敗就放掉，讓另一條線（或重跑）能接手
    print(
        f"\n完成，共 {(time.perf_counter() - t0) / 60:.0f} 分"
        + (f"（另有 {skipped} 格由別條線負責）" if skipped else ""),
        flush=True,
    )
    if failures:
        print(f"失敗 {len(failures)} 筆：{'；'.join(failures)}", flush=True)


if __name__ == "__main__":
    main()
