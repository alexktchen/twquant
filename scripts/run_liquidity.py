"""D-140 §三：跑第八輪 LQ 系列（E0 基座 ＋ 嚴格流動性門檻）的長窗口回測（T-274）。

    uv run --all-groups python scripts/run_vol_target.py --list
    uv run --all-groups python scripts/run_vol_target.py

與 `run_exit_rules.py` 同一套紀律：視窗上界寫死、`TWQUANT_DATA_DIR` 指正本（研究登記簿不分家）、
失敗即停不跳過。四格是 D-133 §二 事前指定的網格，**不得看結果再加格**，故本腳本不提供
新增參數的旗標——要改參數就得先改登記。

對照組 E0 不在此重跑：它的長窗口結果包已存在（`tracks_e_long_nodiv/E0_lw_e0_ctrl_1x.json`，
年化 +27.8%／MDD −41.4%／Calmar 0.67），逐日同窗口可直接配對比較。
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

# (代號, 配置檔名)。四格一次跑完；對照組 E0 用既有結果包，不重跑。
CELLS: tuple[tuple[str, str], ...] = (
    ("LQ1", "lw_lq1_liq"),  # 日均成交 >= 3 億（~175 檔/日）
    ("LQ2", "lw_lq2_liq"),  # 日均成交 >= 10 億（~56 檔/日）
)

WINDOWS = {
    # 與 LW-M1 既有結果包逐日相同，可配對比較
    "long": ("2019-05-02", "2026-02-28", DATA / "tracks_lq_long_nodiv"),
}  # 與 E0／LW-M1 既有結果包逐日相同，可配對比較


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--window", choices=sorted(WINDOWS), default="long")
    ap.add_argument("--only", default="", help="逗號分隔的代號，如 LQ1；空＝全部")
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
