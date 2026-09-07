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
CELLS = (
    ("LW-C1", "lw_c1_breakout"),
    ("LW-B2", "lw_b2_broad_capw"),
    ("LW-X1", "lw_x1_simple20"),
    ("LW-C1abs", "lw_c1_abs_threshold"),  # D-062 的驗證格
)


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
    args = ap.parse_args()
    out_dir = pathlib.Path(str(OUT) + ("_nodiv" if args.nodiv else ""))
    jobs = [(n, c, tag, sc) for n, c in CELLS for tag, sc in SCALES]
    todo = [j for j in jobs if not (out_dir / f"{j[0]}_{j[1]}_{j[2]}.json").exists()]
    print(
        f"{len(CELLS)} 格 × {len(SCALES)} 情境 = {len(jobs)} 筆；待跑 {len(todo)}（已存在者跳過）"
    )
    print(
        f"視窗 {START} ~ {END}　資金 {CAPITAL:,.0f}　scope lw:long_window"
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
