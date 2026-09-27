"""D-149 §四：第十一輪報告——**只報數字、不判**（沿用 d146_report 的算法，加觸發日佔比）。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d149_report.py \
        --bundles D:/project/twquant/data/tracks_tail11

每格揭露年化、最大回撤、Calmar、平均曝險、低曝險日佔比、**觸發日佔比與平均倍數**（F1／F2）、
**格子倍數分布與缺 σ 筆數**（F3）、
減碼賣出筆數、命中率倍率、每筆淨期望、中位數、勝率、筆數、月區塊 t、年化周轉、成本佔初始資金比，
並與 tail_b、第十輪 E 臂、E0、V1 並列。沒有任何通過／未通過標籤。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys

import numpy as np
import polars as pl

from twquant.backtest.costs import CostConfig
from twquant.config import load_config
from twquant.reporting.trade_journal import build_journal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from d143_verdict import DEV_END, _lift, _outcomes, _round_trip_cost
from d146_report import LOW_EXPOSURE, _stats

CONFIGS = pathlib.Path(__file__).resolve().parents[1] / "configs"
REFERENCE = {  # 既有結果，並列用（D-143 §十一、D-146 §七、D-141 §三）——不是門檻
    "tail_b（第九輪 B 臂，對照）": (0.128, -0.397, 0.32),
    "E 臂（第十輪，V1 絕對波動目標）": (0.045, -0.263, 0.17),
    "E0（15 檔、月頻、不出場）": (0.278, -0.414, 0.67),
    "V1（波動度減碼）": (0.196, -0.269, 0.73),
}


def _exposure_stats(bundle: dict[str, object]) -> str:
    rows = bundle.get("exposure")
    if not isinstance(rows, list) or not rows:
        return ""
    trig = [bool(r[2]) for r in rows]
    mults = np.array([float(r[1]) for r in rows])
    hit = [float(r[1]) for r in rows if r[2]]
    return (
        f"  觸發日佔比 {sum(trig) / len(trig):.1%}（{sum(trig):,}／{len(trig):,} 晚）　"
        f"平均倍數 {mults.mean():.2f}　觸發時平均倍數 {np.mean(hit) if hit else float('nan'):.2f}"
    )


def _size_stats(bundle: dict[str, object]) -> str:
    """F3：每筆新進場的格子倍數分布；σ 為 null 的是缺 σ、以下限計（D-149 §三 補充）。"""
    rows = bundle.get("entry_sizes")
    if not isinstance(rows, list) or not rows:
        return ""
    mults = np.array([float(r[2]) for r in rows])
    missing = sum(1 for r in rows if r[3] is None)
    floor = float(mults.min())
    return (
        f"  進場 {len(rows):,} 筆　平均格子倍數 {mults.mean():.2f}　中位 {np.median(mults):.2f}　"
        f"壓到下限 {int((mults <= floor + 1e-12).sum()):,} 筆（其中缺 σ {missing} 筆）　"
        f"維持一格 {int((mults >= 1.0 - 1e-12).sum()):,} 筆"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument("--bundles", required=True)
    ap.add_argument("--costs", default="costs.yaml")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    rt = _round_trip_cost(load_config(CONFIGS / args.costs, CostConfig))
    paths = sorted(pathlib.Path(args.bundles).glob("*.json"))
    if not paths:
        raise SystemExit(f"{args.bundles} 底下沒有結果包")
    bundles = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in paths]
    for p, b in bundles:  # 第十一輪全在開發期；越界就是跑錯窗口，不是要開保留期
        if dt.date.fromisoformat(str(b["end"])) > DEV_END:
            raise SystemExit(f"{p.name} 結束於 {b['end']}，越過第二保留期邊界")
    print(f"來回成本（手續費×2 ＋ 證交稅）＝ {rt:.3%}；逐筆報酬另扣此數")
    print("**本輪不設門檻（D-149，沿用 D-146 規矩）——以下全部只報不判。**\n")
    lo = min(dt.date.fromisoformat(str(b["start"])) for _, b in bundles)
    hi = max(dt.date.fromisoformat(str(b["end"])) for _, b in bundles)
    outcomes: pl.DataFrame = _outcomes(lo, hi)

    for path, bundle in bundles:
        st = _stats(bundle, rt)
        journal = build_journal(bundle)
        exits = [m for d in journal for m in d.moves if m.action == "exit" and m.ret is not None]
        entries = [
            (dt.date.fromisoformat(str(d.date)), m.stock_id)
            for d in journal
            for m in d.moves
            if m.action == "enter"
        ]
        print(f"══ {path.stem}　（{bundle['start']} ~ {bundle['end']}）")
        print(
            f"  年化 {st['ann']:+.1%}　最大回撤 {st['mdd']:.1%}　Calmar {st['calmar']:.2f}　"
            f"平均曝險 {st['expo']:.0%}　低曝險日（<{LOW_EXPOSURE:.0%}）佔 {st['low_expo']:.0%}"
        )
        for extra in (_exposure_stats(bundle), _size_stats(bundle)):
            if extra:
                print(extra)
        print(
            f"  年化周轉 {st['turnover']:.0%}　成本佔初始資金 {st['cost_share']:.1%}　"
            f"減碼賣出 {st['n_trims']} 筆"
        )
        if exits:
            net = np.array([float(m.ret) for m in exits]) - rt  # type: ignore[arg-type]
            months: dict[str, list[float]] = {}
            for m, r in zip(exits, net, strict=True):
                months.setdefault(str(m.entry_date)[:7], []).append(float(r))
            means = [sum(v) / len(v) for v in months.values()]
            mm = sum(means) / len(means)
            var = sum((x - mm) ** 2 for x in means) / (len(means) - 1) if len(means) > 1 else 0.0
            se = math.sqrt(var / len(means)) if var > 0 else 0.0
            t = mm / se if se > 0 else float("nan")
            print(
                f"  已結束 {len(net):,} 筆／{len(months)} 個月　每筆淨期望 {net.mean():+.2%}　"
                f"中位 {np.median(net):+.2%}　月區塊 t {t:+.2f}　勝率 {(net > 0).mean():.1%}"
            )
        if entries:
            got, base, n = _lift(entries, outcomes)
            ratio = got / base if base and base > 0 else float("nan")
            print(f"  命中率倍率 {ratio:.2f}×（{got:.2%} vs 同日全市場 {base:.2%}；{n:,} 筆）")
        print()

    print("── 並列對照（既有結果，不是門檻）──")
    for name, (a, m, c) in REFERENCE.items():
        print(f"  {name:34} 年化 {a:+.1%}　回撤 {m:.1%}　Calmar {c:.2f}")


if __name__ == "__main__":
    main()
