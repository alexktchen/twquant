"""D-114 §四／§五：E 系列判定表，並按**事前寫死的規則**挑送檢的那一支（T-232）。

    uv run --all-groups python scripts/e_verdict.py --bundles D:/project/twquant/data/tracks_e_long_nodiv

送檢規則（D-114 §四，不得事後修改）：長窗口最大回撤 ≤ 20% 者之中取 **Calmar 最高**的一支；
**沒有一支 ≤ 20% 就不送檢**，結論是「這組出場規則不夠」，不得放寬門檻再挑。

本腳本**只讀結果包**，不重跑回測。E0 是對照組：每一欄都同時報「絕對值」與「對 E0 的差」，
因為要回答的是「出場規則值多少」，不是「哪一格數字最好看」。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

import numpy as np

MAX_MDD = 0.20  # D-114 ①
VAULT_END = dt.date(2026, 8, 31)
# D-114 §四：送檢那一支要在**長窗口**上選。舊金庫期照跑照報，但**不得用於挑選**
# （D-106 §5／D-107 §7）——這條由程式擋，不靠人記得。
SELECTION_START = dt.date(2020, 1, 1)
CONTROL = "E0"


def _mdd(x: np.ndarray) -> float:
    return float((x / np.maximum.accumulate(x) - 1.0).min())


def _row(path: pathlib.Path) -> dict[str, object]:
    b = json.loads(path.read_text(encoding="utf-8"))
    if dt.date.fromisoformat(b["end"]) > VAULT_END:
        raise SystemExit(f"{path.name} 結束於 {b['end']}，越過第二保留期邊界，拒絕判定")
    eq = np.array([e[1] for e in b["equity"]], dtype=float)
    total = eq[-1] / float(b["initial_capital"]) - 1.0
    ann = (1.0 + total) ** (252.0 / len(eq)) - 1.0
    mdd = _mdd(eq)
    sells = [t for t in b["ledger"] if t["side"] == "sell"]
    exits = [t for t in sells if t.get("reason")]
    return {
        "code": path.name.split("_")[0],
        "ann": ann,
        "mdd": mdd,
        "calmar": ann / abs(mdd) if mdd < 0 else float("inf"),
        "turnover": float(b["metrics"]["turnover"]),
        "n_exit": len(exits),
        "exit_share": len(exits) / len(sells) if sells else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundles", required=True, help="結果包目錄")
    args = ap.parse_args()
    # Windows 主控台預設 cp950，印不出 ≤／✓（實測 UnicodeEncodeError）。改成 UTF-8 才看得到判定。
    sys.stdout.reconfigure(encoding="utf-8")
    paths = sorted(pathlib.Path(args.bundles).glob("*.json"))
    if not paths:
        raise SystemExit(f"{args.bundles} 沒有結果包")
    rows = [_row(p) for p in paths]
    starts = {json.loads(p.read_text(encoding="utf-8"))["start"] for p in paths}
    selectable = all(dt.date.fromisoformat(d) < SELECTION_START for d in starts)
    base = next((r for r in rows if r["code"] == CONTROL), None)

    print(
        f"{'代號':<7}{'年化':>8}{'Δ年化':>8}{'最大回撤':>9}{'Δ回撤':>8}{'Calmar':>8}{'周轉':>7}{'出場賣出':>9}  ①回撤≤20%"
    )
    for r in rows:
        d_ann = f"{r['ann'] - base['ann']:+.1%}" if base else "—"  # type: ignore[operator]
        d_mdd = f"{r['mdd'] - base['mdd']:+.1%}" if base else "—"  # type: ignore[operator]
        ok = "✓" if r["mdd"] >= -MAX_MDD else "✗"  # type: ignore[operator]
        print(
            f"{r['code']:<8}{r['ann']:>8.1%}{d_ann:>9}{r['mdd']:>9.1%}{d_mdd:>9}"
            f"{r['calmar']:>8.2f}{r['turnover']:>7.1f}{r['n_exit']:>6}／{r['exit_share']:>4.0%}  {ok}"
        )

    eligible = [r for r in rows if r["mdd"] >= -MAX_MDD and r["code"] != CONTROL]  # type: ignore[operator]
    print()
    if not selectable:
        print(
            f"**此視窗不得用於挑選**（起日 {sorted(starts)[0]} 落在舊金庫期內，D-114 §四）。\n"
            "上表只是照跑照報；送檢那一支必須在長窗口（tracks_e_long_nodiv）上選。"
        )
        return
    if not eligible:
        print(
            f"**不送檢**：沒有一支長窗口回撤 ≤ {MAX_MDD:.0%}（最淺的是 "
            f"{max(rows, key=lambda r: r['mdd'])['code']} {max(r['mdd'] for r in rows):.1%}）。"  # type: ignore[type-var]
            "\n結論為「這組出場規則不夠」——D-114 §四 明文禁止放寬門檻再挑。"
        )
        return
    winner = max(eligible, key=lambda r: r["calmar"])  # type: ignore[arg-type]
    print(
        f"**送檢**：{winner['code']}（回撤 {winner['mdd']:.1%}、Calmar {winner['calmar']:.2f}）"
        f"——合格 {len(eligible)} 支中 Calmar 最高。下一步是 10 檔敏感度（--n10）。"
    )


if __name__ == "__main__":
    main()
