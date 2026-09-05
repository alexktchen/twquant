"""T-128／T-129：跑 A 軌與 B 軌（B1／B2）的回測（SPEC-013 §4／§5）。

**三個成本情境**，不是兩個：1×、0×、**1.5×**。D-038 §5 明文要求「1.5× 成本後
CAGR／Sharpe 仍 > 0」，只跑 1× 與 0× 無法宣稱完成晉升驗收（D-061／R8）。
1.5× 一律用 `--cost-scale 1.5`（等比縮放整個成本模型），**不是**只改滑價係數
——兩者不同，規格要求指名。

保險庫：`END` 固定 2026-02-28，之後未開封（SPEC-013 §1.3）。
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
OUT = pathlib.Path("D:/project/twquant/data/tracks")
START, END = "2022-07-01", "2026-02-28"  # END 是保險庫邊界，不得後移
CAPITAL = 80_000_000.0  # D-068：10M 下曝險隨檔數由 82% 掉到 65%，汙染「檔數」的比較
COSTS = "costs.yaml"

# 預設是 A/B 軌；C 軌以 --tracks 指定（各自寫不同 --out，不得覆蓋彼此）
TRACKS = [
    ("A", "track_a_smallcap"),
    ("B1", "track_b1_large_enhanced"),
    ("B1ML", "track_b1ml_large_enhanced"),  # D-067：2×2 的第四格，與 B1 只差訊號
    ("B2", "track_b2_broad_capw"),
]
KNOWN = {
    "A": "track_a_smallcap",
    "B1": "track_b1_large_enhanced",
    "B1ML": "track_b1ml_large_enhanced",
    "B2": "track_b2_broad_capw",
    "C1": "track_c1_breakout",
    "C2": "track_c2_wbottom",
    "C2b": "track_c2b_boxbreak",
    "C2c": "track_c2c_flaghold",
    "C3": "track_c3_chipflow",
    "G1": "track_g1_ml_revenue",
    "G2": "track_g2_ml_lowvol",
    "G3": "track_g3_ml_voltarget",
    "G4": "track_g4_ml_smooth",
    "G5": "track_g5_ml_large",
    "G6": "track_g6_ml_flaggate",
    "GALL": "track_gall_ml_allgates",
    "M1": "track_m1_ml_c1",
    "M2": "track_m2_ml_flag",
    "M3": "track_m3_mom_flag",
    "M4": "track_m4_ml_chip",
    "M5": "track_m5_ml_large",
    "M6": "track_m6_ml_voltarget",
    "C4": "track_c4_voltarget",
}
SCALES = [("1x", None), ("0x", "0"), ("1.5x", "1.5")]


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks", default="", help="逗號分隔的軌名（如 C1,C4）；空＝預設 A/B 軌")
    ap.add_argument("--out", default="", help="結果包目錄；空＝預設。**不同批必須寫不同目錄**")
    args = ap.parse_args()
    tracks = TRACKS
    if args.tracks:
        keys = [k.strip() for k in args.tracks.split(",") if k.strip()]
        bad = [k for k in keys if k not in KNOWN]
        if bad:
            raise SystemExit(f"未知軌名 {bad}，可用：{sorted(KNOWN)}")
        tracks = [(k, KNOWN[k]) for k in keys]
        if not args.out:
            raise SystemExit(
                "--tracks 指定了非預設組合，必須一併給 --out，否則會與既有結果混在一起"
            )
    out = pathlib.Path(args.out) if args.out else OUT
    print(f"軌：{[t for t, _ in tracks]}　輸出：{out}", flush=True)

    globals()["OUT"] = out
    out.mkdir(parents=True, exist_ok=True)
    jobs = [(t, c, tag, sc) for t, c in tracks for tag, sc in SCALES]
    t0 = time.perf_counter()
    failures: list[str] = []
    for i, (track, cfg, tag, scale) in enumerate(jobs, 1):
        dest = out / f"{track}_{cfg}_{tag}.json"
        if dest.exists():
            print(f"[{i}/{len(jobs)}] {track} {tag}：結果包已存在，跳過（續傳）", flush=True)
            continue
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
        print(
            f"\n{'=' * 70}\n[{i}/{len(jobs)}] {track} {cfg} {tag}（已 {el:.1f} 分）\n{'=' * 70}",
            flush=True,
        )
        r = subprocess.run(cmd, cwd=REPO)
        if r.returncode != 0:
            # 一軌失敗不該讓其餘白等，但要據實記下（黃金規則 5）
            failures.append(f"{track} {tag}（exit {r.returncode}）")
            print(f"!! {track} {tag} 失敗 exit {r.returncode}", flush=True)

    print(f"\n{'=' * 70}\n完成，共 {(time.perf_counter() - t0) / 60:.1f} 分", flush=True)
    if failures:
        print(f"失敗 {len(failures)} 次：{'；'.join(failures)}", flush=True)
        raise SystemExit(1)
    print(f"{len(jobs)} 次全部成功，結果包在 {OUT}", flush=True)


if __name__ == "__main__":
    main()
