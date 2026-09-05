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
COSTS = "costs.yaml"

TRACKS = [
    ("A", "track_a_smallcap"),
    ("B1", "track_b1_large_enhanced"),
    ("B2", "track_b2_broad_capw"),
]
SCALES = [("1x", None), ("0x", "0"), ("1.5x", "1.5")]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [(t, c, tag, sc) for t, c in TRACKS for tag, sc in SCALES]
    t0 = time.perf_counter()
    failures: list[str] = []
    for i, (track, cfg, tag, scale) in enumerate(jobs, 1):
        dest = OUT / f"{track}_{cfg}_{tag}.json"
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
