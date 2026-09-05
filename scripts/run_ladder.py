"""T-127：跑控制組階梯的 12 次回測（SPEC-013 §3.3）。

6 階 × 2 成本情境（1×、0×）。L0 是既有的 `ml_net_h20_m`，不改配置，
其 scope 仍是 `bt:ml_net_h20_turnover`——它是對照基準，不是新候選（D-061／R5）。

每次都落地完整結果包（T-131），之後的子區間切分與報表都從同一份 artifact 產生，
**不重跑**（SPEC-013 §3.6）。

保險庫：`END` 固定 2026-02-28，之後未開封。改這個常數即違反 SPEC-013 §1.3。
"""

from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
START, END = "2022-07-01", "2026-02-28"  # END 是保險庫邊界，不得後移
DEFAULT_CAPITAL = 80_000_000.0  # D-068：10M 下曝險隨檔數由 82% 掉到 65%，汙染「檔數」的比較
COSTS = "costs.yaml"  # 1× 的基準；0× 由 --cost-scale 0 產生

RUNGS = [
    ("L0", "ml_net_h20_m"),  # 既有候選，scope 為 bt:ml_net_h20_turnover
    ("L1", "ladder_l1_n40"),
    ("L2", "ladder_l2_rev"),
    ("L3", "ladder_l3_simple"),
    ("L4", "ladder_l4_wt"),
    ("X1", "ladder_x1_simple20"),
    # T-146／D-066 的處理組：與 L0 逐字相同，只換成 T-145 重訓的乾淨模型。
    # 對照組就是同批的 X1，故兩者必然跑在同一份資料上（結果包指紋可驗）。
    ("L0new", "ladder_l0_new"),
]
SCALES = [("1x", None), ("0x", "0")]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--capital",
        type=float,
        default=DEFAULT_CAPITAL,
        help="初始資金。改這個會改變成交結果（整張捨去的比例），故輸出目錄也要跟著換",
    )
    ap.add_argument(
        "--out",
        default="D:/project/twquant/data/ladder",
        help="結果包目錄。**不同資金必須寫不同目錄**——同名覆蓋會讓兩組數字混在一起",
    )
    args = ap.parse_args()
    capital, out = args.capital, pathlib.Path(args.out)
    if capital != DEFAULT_CAPITAL and out == pathlib.Path("D:/project/twquant/data/ladder"):
        raise SystemExit(
            f"--capital {capital:,.0f} 不是預設值卻用預設目錄——會覆蓋 80M 的那批。請一併給 --out。"
        )
    print(f"資金 {capital:,.0f}、輸出 {out}", flush=True)

    OUT = out
    OUT.mkdir(parents=True, exist_ok=True)
    CAPITAL = capital
    total = len(RUNGS) * len(SCALES)
    t0 = time.perf_counter()
    failures: list[str] = []
    for i, ((rung, cfg), (tag, scale)) in enumerate(((r, s) for r in RUNGS for s in SCALES), 1):
        dest = OUT / f"{rung}_{cfg}_{tag}.json"
        if dest.exists():
            print(f"[{i}/{total}] {rung} {tag}：結果包已存在，跳過（續傳）", flush=True)
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
            f"\n{'=' * 70}\n[{i}/{total}] {rung} {cfg} {tag}（已 {el:.1f} 分）\n{'=' * 70}",
            flush=True,
        )
        r = subprocess.run(cmd, cwd=REPO)
        if r.returncode != 0:
            # 不中止整批：一階失敗不該讓其餘 11 次白等，但要據實記下（黃金規則 5）
            failures.append(f"{rung} {tag}（exit {r.returncode}）")
            print(f"!! {rung} {tag} 失敗 exit {r.returncode}", flush=True)

    print(f"\n{'=' * 70}\n完成，共 {(time.perf_counter() - t0) / 60:.1f} 分", flush=True)
    if failures:
        print(f"失敗 {len(failures)} 次：{'；'.join(failures)}", flush=True)
        raise SystemExit(1)
    print(f"{total} 次全部成功，結果包在 {OUT}", flush=True)


if __name__ == "__main__":
    main()
