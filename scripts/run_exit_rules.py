"""D-114 §四：跑 E 系列（每日出場機制）的長窗口與舊金庫期回測（T-232）。

    uv run --all-groups python scripts/run_exit_rules.py --list
    uv run --all-groups python scripts/run_exit_rules.py --window long
    uv run --all-groups python scripts/run_exit_rules.py --window vault

**登記的紀律由本腳本強制，不靠人記得**：

- `--n10` 只能帶**一個**策略名（D-114 §四 的 10 檔敏感度是「送檢那一支」加跑一次，
  不是整批再跑一輪）；不給名字就不跑任何 10 檔版本。
- 視窗上界寫死 2026-02-28（長窗口）與 2026-08-31（舊金庫期）。
  **第二保留期（2026-09-09 起）不可跑**，`--end` 超過即拒絕。
- 全部寫同一個 `trial_scope: lw:exit_rules_e`，試驗數進同一本 `data/research_log/`。

輸出目錄與 `TWQUANT_DATA_DIR` 都固定在主 checkout 的絕對路徑（CLAUDE.md §0.2 的兩個 data/ 陷阱）。
**這不是保險起見**：實測第一批 8 個試驗的 `research_log` 全寫進了 worktree 自己的 `data/`，
那等於 D-114 §四.1 明文禁止的「另開一本讓計數歸零」。`--out` 管得到結果包，管不到登記簿。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import pathlib
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
DATA = pathlib.Path("D:/project/twquant/data")
CAPITAL = 80_000_000.0  # 與長窗口既有批次同水位（D-068）
COSTS = "costs.yaml"
VAULT_END = dt.date(2026, 8, 31)  # D-109：之後為第二保留期，任何情況都不得跑

# (代號, 配置檔名)。E0 是對照組，必跑——沒有它就不知道出場規則各自值多少。
CELLS: tuple[tuple[str, str], ...] = (
    ("E0", "lw_e0_ctrl"),
    ("E1a", "lw_e1a_sl15"),
    ("E1b", "lw_e1b_sl20"),
    ("E2", "lw_e2_ma10"),
    ("E3", "lw_e3_ma20"),
    ("E5", "lw_e5_sl20_ma20"),
    ("E6", "lw_e6_rank_out"),
    ("E7", "lw_e7_dd_gate"),
)

WINDOWS = {
    # 與 LW-M1 既有結果包逐日相同，可配對比較
    "long": ("2019-05-02", "2026-02-28", DATA / "tracks_e_long_nodiv"),
    "vault": ("2026-03-02", "2026-08-31", DATA / "tracks_e_vault_nodiv"),
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--window", choices=sorted(WINDOWS), default="long")
    ap.add_argument("--only", default="", help="逗號分隔的代號，如 E0,E3；空＝全部")
    ap.add_argument(
        "--n10",
        default="",
        help="送檢那一支的代號，加跑一次 10 檔敏感度（只能一個，D-114 §四）",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list:
        for code, cfg in CELLS:
            print(f"{code:<5}{cfg}")
        return

    start, end, out_dir = WINDOWS[args.window]
    if dt.date.fromisoformat(end) > VAULT_END:
        raise SystemExit(f"視窗迄日 {end} 越過 D-109 的保留期邊界（{VAULT_END}），拒絕執行")

    wanted = [c for c in CELLS if not args.only or c[0] in args.only.split(",")]
    if args.n10:
        codes = [c.strip() for c in args.n10.split(",") if c.strip()]
        if len(codes) != 1:
            raise SystemExit("--n10 只能帶一個代號：10 檔是送檢那一支的敏感度，不是整批再跑一輪")
        match = [c for c in CELLS if c[0] == codes[0]]
        if not match:
            raise SystemExit(f"--n10 未知代號 {codes[0]}")
        wanted = [*wanted, (f"{match[0][0]}n10", f"{match[0][1]}_n10")]

    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    for code, cfg in wanted:
        out = out_dir / f"{code}_{cfg}_1x.json"
        cmd = [
            sys.executable,
            "-m",
            "twquant",
            "backtest",
            "--strategy",
            str(REPO / "configs" / "strategies" / f"{cfg}.yaml"),
            "--start",
            start,
            "--end",
            end,
            "--capital",
            str(CAPITAL),
            "--costs",
            COSTS,
            "--no-dividends",  # 與 LW-M1 既有長窗口批次同口徑（價格報酬）
            "--out",
            str(out),
        ]
        print(f"\n=== {code} ({cfg}) {start}~{end} → {out.name}", flush=True)
        if args.dry_run:
            print(" ".join(cmd))
            continue
        env = os.environ | {"TWQUANT_DATA_DIR": str(DATA)}  # 研究登記簿寫正本，不寫 worktree
        rc = subprocess.run(cmd, cwd=REPO, env=env, check=False).returncode
        if rc != 0:
            print(f"[FAIL] {code} 回傳 {rc}——停止，不跳過（失敗要看得見）", file=sys.stderr)
            raise SystemExit(rc)
    print(f"\n[OK] {len(wanted)} 格，耗時 {(time.perf_counter() - t0) / 60:.1f} 分")


if __name__ == "__main__":
    main()
