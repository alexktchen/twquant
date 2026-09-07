"""T-172 的判讀：股利入帳（D-085）到底改變了什麼。

同一個策略、同一個視窗、同一份資料，**只差引擎的一行**——舊結果在原目錄、
新結果在 `<原目錄>_div`，兩份相減就是股利的價值。這是為什麼重跑要寫新目錄而不是覆蓋。

`scripts/dividend_drag.py` 是**事前估算**（把股利當期末一次入帳、無再投資），
本腳本是**事後實測**（引擎逐日入帳、再投資、含複利與交易成本）。兩者的差距本身
就是「再投資值多少」的答案。

用法：
    python scripts/dividend_effect.py
    python scripts/dividend_effect.py --scale 0x
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
# 格的清單只有一份真值來源（重跑腳本），不在這裡重列一份——否則兩邊會漂
from rerun_all_backtests import CELLS, DATA

# 基準（含息）：策略含息之後才是同口徑的比較對象
_BENCH = {"0050 含息": 0.4008, "0051 中型100 含息": 0.3157, "0056 高股息 含息": 0.2500}


def _load(path: pathlib.Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale", default="1x", choices=("1x", "0x", "1.5x"))
    args = ap.parse_args()

    rows = []
    for name, d, prefix, _cfg in CELLS:
        old = _load(DATA / d / f"{prefix}_{args.scale}.json")
        new = _load(DATA / f"{d}_div" / f"{prefix}_{args.scale}.json")
        if new is None:
            continue
        div = sum(x["cash"] for x in (new.get("dividends") or []))
        rows.append(
            {
                "name": name,
                "old_cagr": old["metrics"]["cagr"] if old else None,
                "new_cagr": new["metrics"]["cagr"],
                "old_mdd": old["metrics"]["mdd"] if old else None,
                "new_mdd": new["metrics"]["mdd"],
                "old_sharpe": old["metrics"]["sharpe"] if old else None,
                "new_sharpe": new["metrics"]["sharpe"],
                "div_cash": div,
                "div_share": div / new["initial_capital"],
                "n_div": len(new.get("dividends") or []),
            }
        )
    if not rows:
        raise SystemExit(f"找不到任何 {args.scale} 的新結果包——重跑還沒開始或還沒完成")

    done = [r for r in rows if r["old_cagr"] is not None]
    print(f"成本情境 {args.scale}　可比 {len(done)}/{len(rows)} 格（其餘缺舊結果）\n")
    print(
        f"{'格':8s} {'股利/初始':>9s} {'筆數':>5s} "
        f"{'舊 CAGR':>9s} {'新 CAGR':>9s} {'Δ':>8s} "
        f"{'舊 Sharpe':>10s} {'新 Sharpe':>10s} {'Δ':>7s} {'ΔMDD':>8s}"
    )
    for r in sorted(done, key=lambda x: -x["new_cagr"]):
        dc = (r["new_cagr"] - r["old_cagr"]) * 100
        ds = r["new_sharpe"] - r["old_sharpe"]
        dm = (r["new_mdd"] - r["old_mdd"]) * 100
        print(
            f"{r['name']:8s} {r['div_share']:9.2%} {r['n_div']:5d} "
            f"{r['old_cagr']:+9.2%} {r['new_cagr']:+9.2%} {dc:+7.2f}pp "
            f"{r['old_sharpe']:10.2f} {r['new_sharpe']:10.2f} {ds:+7.2f} {dm:+7.2f}pp"
        )

    if done:
        deltas = sorted((r["new_cagr"] - r["old_cagr"]) * 100 for r in done)
        print(
            f"\nΔCAGR 分布：最小 {deltas[0]:+.2f}pp、中位 {deltas[len(deltas) // 2]:+.2f}pp、"
            f"最大 {deltas[-1]:+.2f}pp"
        )
        print("**修正幅度不一致 ⇒ 排序也會變**，不是所有人一起平移。")

    print(f"\n對含息基準（{args.scale} 成本）：")
    beats = []
    for label, b in _BENCH.items():
        over = [r["name"] for r in rows if r["new_cagr"] > b]
        beats.append(
            f"  {label:20s} {b:+.2%}　CAGR 贏過它的：{len(over)} 格"
            + (f"（{'、'.join(over[:6])}）" if over else "")
        )
    print("\n".join(beats))
    print(
        "\n**CAGR 贏不等於通過**：D-038 §5 還要 MDD ≤ 30%、1.5× 成本後仍為正、"
        "以及 DSR（試驗計數已達 190）。研究用途，非投資建議。"
    )


if __name__ == "__main__":
    main()
