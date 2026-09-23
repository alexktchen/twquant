"""D-146 §四：第十輪報告——**只報數字、不判**。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d145_report.py \
        --bundles D:/project/twquant/data/tracks_tail10

使用者 2026-09-23：「不設門檻、只報數字，跑這一輪」。本腳本沒有任何通過／未通過標籤；
每格揭露年化、最大回撤、Calmar、平均曝險、低曝險日佔比、命中率倍率、每筆淨期望、筆數、
月區塊 t、年化周轉、成本佔初始資金比，並與 tail_b（D-143 B 臂）、E0、V1 並列。

C／B′ 的窗口只有兩年（2024-03 ~ 2026-02）——D-146 §三 事前寫明「只看方向、不看大小」，
表尾會再印一次這句話，免得看數字的人忘了。
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

CONFIGS = pathlib.Path(__file__).resolve().parents[1] / "configs"
LOW_EXPOSURE = 0.5  # 「低曝險日」＝部位價值不到權益一半的日子（D-134 §四.3 的揭露項目）
REFERENCE = {  # 既有結果，並列用（D-141 §三、D-143 §十一）
    "tail_b（D-143 B 臂，2017~2026-02）": (0.128, -0.397, 0.32),
    "E0（15 檔、月頻、不出場）": (0.278, -0.414, 0.67),
    "V1（波動度減碼）": (0.196, -0.269, 0.73),
}


def _stats(bundle: dict[str, object], rt: float) -> dict[str, float]:
    eq = np.array([e[1] for e in bundle["equity"]], dtype=float)  # type: ignore[index]
    pos = np.array([e[3] for e in bundle["equity"]], dtype=float)  # type: ignore[index]
    ann = (eq[-1] / float(bundle["initial_capital"])) ** (252.0 / len(eq)) - 1.0  # type: ignore[arg-type]
    mdd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
    expo = pos / np.maximum(eq, 1.0)
    led = bundle.get("ledger") or []
    traded = sum(float(t["shares"]) * float(t["price"]) for t in led)  # type: ignore[index]
    costs = sum(float(t["commission"]) + float(t["tax"]) + float(t["slippage"]) for t in led)  # type: ignore[index]
    years = len(eq) / 252.0
    return {
        "ann": ann,
        "mdd": mdd,
        "calmar": ann / abs(mdd) if mdd < 0 else float("nan"),
        "expo": float(expo.mean()),
        "low_expo": float((expo < LOW_EXPOSURE).mean()),
        "turnover": traded / float(eq.mean()) / years / 2.0,  # 買＋賣各算一次 → 除以 2
        "cost_share": costs / float(bundle["initial_capital"]),  # type: ignore[arg-type]
        "n_trims": sum(1 for t in led if t.get("reason") == "exposure_target"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=str(__doc__).splitlines()[0])
    ap.add_argument("--bundles", required=True)
    ap.add_argument("--costs", default="costs.yaml")
    ap.add_argument("--no-lift", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    rt = _round_trip_cost(load_config(CONFIGS / args.costs, CostConfig))
    paths = sorted(pathlib.Path(args.bundles).glob("*.json"))
    if not paths:
        raise SystemExit(f"{args.bundles} 底下沒有結果包")
    bundles = [(p, json.loads(p.read_text(encoding="utf-8"))) for p in paths]
    for p, b in bundles:
        if dt.date.fromisoformat(str(b["end"])) > DEV_END:
            raise SystemExit(f"{p.name} 結束於 {b['end']}，越過第二保留期邊界")
    print(f"來回成本（手續費×2 ＋ 證交稅）＝ {rt:.3%}；逐筆報酬另扣此數")
    print("**本輪不設門檻（D-146 §〇）——以下全部只報不判。**\n")
    outcomes = pl.DataFrame()
    if not args.no_lift:
        lo = min(dt.date.fromisoformat(str(b["start"])) for _, b in bundles)
        hi = max(dt.date.fromisoformat(str(b["end"])) for _, b in bundles)
        outcomes = _outcomes(lo, hi)

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
        if not args.no_lift and entries:
            got, base, n = _lift(entries, outcomes)
            ratio = got / base if base and base > 0 else float("nan")
            print(f"  命中率倍率 {ratio:.2f}×（{got:.2%} vs 同日全市場 {base:.2%}；{n:,} 筆）")
        print()

    print("── 並列對照（既有結果，不是門檻）──")
    for name, (a, m, c) in REFERENCE.items():
        print(f"  {name:36} 年化 {a:+.1%}　回撤 {m:.1%}　Calmar {c:.2f}")
    print(
        "\n提醒（D-146 §三 事前寫定）：C 與 B′ 的回測窗只有兩年、約 8 個 walk-forward 視窗，"
        "**只看方向、不看大小**；回撤與年化的差異視為雜訊。"
    )


if __name__ == "__main__":
    main()
