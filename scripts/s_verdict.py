"""D-124 §七：S 系列判定——**逐筆交易**的勝率與盈虧比。

    uv run --all-groups python scripts/s_verdict.py --bundles D:/project/twquant/data/tracks_s_long_nodiv

與 `e_verdict.py` 的差別：那支看組合層級的回撤與 Calmar；本支看**每一筆交易**——
使用者的目標是「每次出手勝率 70~80%，錯了停損、對了抱緊」（D-124 §〇），
組合回撤只報不判。一筆＝一次進場到出清（由 `reporting.trade_journal` 的 exit 事件界定）。

判準（事前登記、不得事後修改）：
- **H**：逐筆勝率 ≥ 70%、已結束交易 ≥ 100 筆、成本後年化 > 0
- **L**：盈虧比 ≥ 2（平均獲利 ÷ 平均虧損）、已結束交易 ≥ 30 筆、成本後年化 > 0；勝率只報不判

`ret` 是價格報酬（不含費用與股利，與 `trade_journal` 同口徑）；成本反映在年化與權益曲線裡。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

import numpy as np

from twquant.reporting.trade_journal import build_journal

DEV_END = dt.date(2026, 8, 31)
RULES = {  # 代號前綴 → (最少筆數, 最低勝率, 最低盈虧比)
    "Sh": (100, 0.70, None),
    "Sl": (30, None, 2.0),
}


def _stats(path: pathlib.Path) -> dict[str, object]:
    bundle = json.loads(path.read_text(encoding="utf-8"))
    if dt.date.fromisoformat(bundle["end"]) > DEV_END:
        raise SystemExit(f"{path.name} 結束於 {bundle['end']}，越過第二保留期邊界，拒絕判定")
    eq = np.array([e[1] for e in bundle["equity"]], dtype=float)
    total = eq[-1] / float(bundle["initial_capital"]) - 1.0
    ann = (1.0 + total) ** (252.0 / len(eq)) - 1.0
    mdd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    exits = [
        m
        for d in build_journal(bundle)
        for m in d.moves
        if m.action == "exit" and m.ret is not None
    ]
    rets = np.array([m.ret for m in exits], dtype=float)
    wins, losses = rets[rets > 0], rets[rets <= 0]
    reasons: dict[str, int] = {}
    for m in exits:
        reasons[m.reason or "rebalance"] = reasons.get(m.reason or "rebalance", 0) + 1
    exposure = float(np.mean([e[3] / e[1] for e in bundle["equity"]]))
    return {
        "code": path.name.split("_")[0],
        "n": len(exits),
        "win_rate": float((rets > 0).mean()) if len(rets) else float("nan"),
        "avg_win": float(wins.mean()) if len(wins) else float("nan"),
        "avg_loss": float(losses.mean()) if len(losses) else float("nan"),
        "profit_factor": float(wins.mean() / -losses.mean())
        if len(wins) and len(losses) and losses.mean() < 0
        else float("nan"),
        "median_hold": float(np.median([m.held_sessions for m in exits]))
        if exits
        else float("nan"),
        "worst": float(rets.min()) if len(rets) else float("nan"),
        "ann": ann,
        "mdd": mdd,
        "exposure": exposure,
        "reasons": reasons,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundles", required=True, help="結果包目錄")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    paths = sorted(pathlib.Path(args.bundles).glob("*.json"))
    if not paths:
        raise SystemExit(f"{args.bundles} 沒有結果包")

    for row in (_stats(p) for p in paths):
        code = str(row["code"])
        need_n, need_win, need_pf = RULES.get(code[:2], (0, None, None))
        print(f"\n══ {code}")
        print(
            f"  已結束交易 {row['n']:,} 筆（須 ≥ {need_n}）　"
            f"中位持有 {row['median_hold']:.0f} 個交易日　平均持股比例 {row['exposure']:.0%}"
        )
        print(
            f"  **勝率 {row['win_rate']:.1%}**　平均獲利 {row['avg_win']:+.1%}　"
            f"平均虧損 {row['avg_loss']:+.1%}　盈虧比 {row['profit_factor']:.2f}　"
            f"最慘單筆 {row['worst']:+.1%}"
        )
        print(f"  成本後年化 {row['ann']:+.1%}　組合最大回撤 {row['mdd']:.1%}（只報不判）")
        print(
            "  出場原因："
            + "、".join(
                f"{k} {v}" for k, v in sorted(row["reasons"].items(), key=lambda kv: -kv[1])
            )  # type: ignore[union-attr,arg-type]
        )
        checks: list[tuple[str, bool]] = [
            (f"筆數 ≥ {need_n}", int(row["n"]) >= need_n),  # type: ignore[call-overload]
            ("成本後年化 > 0", float(row["ann"]) > 0),  # type: ignore[arg-type]
        ]
        if need_win is not None:
            checks.append((f"勝率 ≥ {need_win:.0%}", float(row["win_rate"]) >= need_win))  # type: ignore[arg-type]
        if need_pf is not None:
            checks.append((f"盈虧比 ≥ {need_pf}", float(row["profit_factor"]) >= need_pf))  # type: ignore[arg-type]
        ok = all(passed for _, passed in checks)
        print("  " + "　".join(f"{'✓' if passed else '✗'} {name}" for name, passed in checks))
        print(f"  → **{'通過' if ok else '未通過'}**")


if __name__ == "__main__":
    main()
