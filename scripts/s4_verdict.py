"""D-128 §四：第四輪判定——**正期望值＋足夠筆數**，勝率只報不判。

    uv run --all-groups python scripts/s4_verdict.py --bundles D:/project/twquant/data/tracks_s4_long_nodiv

判準（事前登記、不得事後修改）：
- **① 夠多筆**：已結束交易 ≥ 300 筆。
- **② 正期望值**：每筆平均報酬（成本後）> 0，且**以月為單位**的 t ≥ 2。
  月區塊是這一輪真正的關卡：前三輪的優勢全部集中在三段崩盤後，月區塊檢定會把那種形狀打掉。
- **③ 賺得比成本多**：成本後年化 > 0。
- 勝率、盈虧比、組合回撤：**只報不判**（使用者 D-128 已明確放棄勝率目標）。

逐筆報酬取 `trade_journal` 的價格報酬，**另扣一次來回成本**（成本配置的實際值），
因為 `ret` 本身不含費用——判「期望值為正」不能用不含成本的數字。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import sys

import numpy as np

from twquant.backtest.costs import CostConfig
from twquant.config import load_config
from twquant.reporting.trade_journal import build_journal

DEV_END = dt.date(2026, 8, 31)
MIN_TRADES = 300
MIN_T = 2.0
CONFIGS = pathlib.Path(__file__).resolve().parents[1] / "configs"


def _round_trip_cost(cfg: CostConfig) -> float:
    """來回成本佔部位的比例：手續費兩趟（打折後）＋ 賣出證交稅，再乘成本倍率。

    滑價**不在這裡**——它是價格相依的，已經反映在權益曲線與年化報酬裡；
    這個數字只用來把不含費用的逐筆 `ret` 調整成「扣過手續費與稅」的淨報酬。
    """
    fee = float(cfg.commission_rate) * float(cfg.commission_discount) * 2
    return (fee + float(cfg.tax_rate_sell)) * float(cfg.cost_scale)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundles", required=True)
    ap.add_argument("--costs", default="costs.yaml")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    cost_cfg = load_config(CONFIGS / args.costs, CostConfig)
    rt = _round_trip_cost(cost_cfg)
    print(f"來回成本（手續費×2 ＋ 證交稅）＝ {rt:.3%}；逐筆報酬另扣此數後才判期望值\n")

    for path in sorted(pathlib.Path(args.bundles).glob("*.json")):
        bundle = json.loads(path.read_text(encoding="utf-8"))
        if dt.date.fromisoformat(bundle["end"]) > DEV_END:
            raise SystemExit(f"{path.name} 結束於 {bundle['end']}，越過保留期邊界，拒絕判定")
        eq = np.array([e[1] for e in bundle["equity"]], dtype=float)
        ann = (eq[-1] / float(bundle["initial_capital"])) ** (252.0 / len(eq)) - 1.0
        mdd = float((eq / np.maximum.accumulate(eq) - 1.0).min())
        exits = [
            m
            for d in build_journal(bundle)
            for m in d.moves
            if m.action == "exit" and m.ret is not None
        ]
        if not exits:
            print(f"══ {path.stem}：沒有任何已結束交易 → **未通過**\n")
            continue
        net = np.array([float(m.ret) for m in exits]) - rt  # type: ignore[arg-type]
        months: dict[str, list[float]] = {}
        for m, r in zip(exits, net, strict=True):
            months.setdefault(str(m.entry_date)[:7], []).append(float(r))
        month_means = [sum(v) / len(v) for v in months.values()]
        mm = sum(month_means) / len(month_means)
        var = (
            sum((x - mm) ** 2 for x in month_means) / (len(month_means) - 1)
            if len(month_means) > 1
            else 0.0
        )
        se = math.sqrt(var / len(month_means)) if var > 0 else 0.0
        t = mm / se if se > 0 else (float("inf") if mm > 0 else 0.0)
        wins = net[net > 0]
        losses = net[net <= 0]

        print(f"══ {path.stem}")
        print(
            f"  已結束交易 {len(net):,} 筆（須 ≥ {MIN_TRADES}）　"
            f"分布在 {len(months)} 個月　中位持有 {np.median([m.held_sessions for m in exits]):.0f} 日"
        )
        print(
            f"  **每筆期望值（扣成本）{net.mean():+.2%}**　中位數 {np.median(net):+.2%}　"
            f"月平均 {mm:+.2%}　**月區塊 t = {t:+.2f}**（須 ≥ {MIN_T}）"
        )
        print(
            f"  勝率 {(net > 0).mean():.1%}　平均獲利 {wins.mean() if len(wins) else float('nan'):+.1%}　"
            f"平均虧損 {losses.mean() if len(losses) else float('nan'):+.1%}　"
            f"（只報不判）"
        )
        print(f"  成本後年化 {ann:+.1%}　組合最大回撤 {mdd:.1%}（只報不判）")
        checks = [
            (f"筆數 ≥ {MIN_TRADES}", len(net) >= MIN_TRADES),
            ("期望值 > 0", float(net.mean()) > 0),
            (f"月區塊 t ≥ {MIN_T}", t >= MIN_T),
            ("年化 > 0", ann > 0),
        ]
        print("  " + "　".join(f"{'✓' if ok else '✗'} {n}" for n, ok in checks))
        print(f"  → **{'通過' if all(ok for _, ok in checks) else '未通過'}**\n")


if __name__ == "__main__":
    main()
