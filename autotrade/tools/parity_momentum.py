"""比對工具：同一天的逐筆，分別用「對照用的原引擎」（reference/day_engine.py，現有模擬單跑的那份）
與 autotrade（動能順勢轉接＋模擬券商）各跑一次，逐筆比對成交。

要在有網友原版程式的電腦上跑（原版不放進 repo）：

  cd autotrade
  python tools/parity_momentum.py --ms-repo C:/t/Momentum_Scalping --config configs/tmf_sim.toml \
      --ticks D:/.../Daily_2026_11_12.csv --day 2026-11-12

可以給多組 --ticks／--day（順序對應）。全部一致回傳 0，有任何不同回傳 1。
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT, ROOT / "src"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from reference import day_engine as ref  # noqa: E402

from autotrade.broker.sim import SimBroker  # noqa: E402
from autotrade.config import Config, load_config  # noqa: E402
from autotrade.engine import Engine  # noqa: E402
from autotrade.feed.replay import read_taifex_day  # noqa: E402
from autotrade.model import Tick  # noqa: E402
from autotrade.strategy.momentum import (  # noqa: E402
    TPE8,
    US,
    MomentumStrategy,
    load_original,
    to_us,
)

Row = tuple[str, float, str, float, str, float]


def reference_rows(
    mods: dict[str, Any], params: dict[str, Any], cfg: Config, contract: str, ticks: Sequence[Tick]
) -> tuple[list[Row], dict[str, int], dict[str, int]]:
    """用對照引擎跑一天，回傳（成交列, 訊號計數, 擋下計數）。"""
    if cfg.momentum is None:
        raise ValueError("設定檔沒有 [momentum]")
    ref_cfg = ref.Config(
        product=cfg.contract.symbol,
        point_value=cfg.contract.point_value,  # type: ignore[arg-type]  # 對照引擎標 int，數值相同
        contracts=cfg.momentum.contracts,
        commission=cfg.contract.commission_twd,
        tax_rate=cfg.contract.tax_rate,
        slip_ticks=1,
        tick=cfg.sim.slippage_points,
        latency_us=int(cfg.sim.latency_s * US),
        eval_interval_us=int(cfg.momentum.eval_interval_s * US),
        refresh_us=int(cfg.momentum.market_refresh_s * US),
    )
    day = ticks[0].ts.date().isoformat()
    engine = ref.DayEngine(mods, params, day, contract, {}, ref_cfg)
    for serial, tick in enumerate(ticks):
        engine.on_tick(to_us(tick.ts), serial, tick.price, tick.qty)
    engine.close()
    rows: list[Row] = []
    for t in engine.trades:
        legs = json.loads(t["exit_legs"])
        rows.append(
            (
                t["entry_time"],
                float(t["entry_price"]),
                legs[-1]["time"],
                float(legs[-1]["price"]),
                str(t["final_exit_reason"]),
                float(t["net_pnl"]),
            )
        )
    return rows, dict(engine.signals), dict(engine.blocked)


def autotrade_rows(
    mods: dict[str, Any], params: dict[str, Any], cfg: Config, ticks: Sequence[Tick]
) -> tuple[list[Row], dict[str, int], dict[str, int], Engine]:
    """用 autotrade 跑一天，回傳（成交列, 訊號計數, 擋下計數, 引擎）。"""
    if cfg.momentum is None:
        raise ValueError("設定檔沒有 [momentum]")
    strategy = MomentumStrategy(mods, params, cfg.momentum, cfg.contract)
    engine = Engine(cfg, strategy, SimBroker(cfg.sim))
    for tick in ticks:
        engine.on_tick(tick)
    rows: list[Row] = []
    for t in strategy.trades:
        if "exit_time" not in t:
            rows.append((_iso(t["entry_time"]), float(t["entry_price"]), "未平倉", 0.0, "", 0.0))
            continue
        rows.append(
            (
                _iso(t["entry_time"]),
                float(t["entry_price"]),
                _iso(t["exit_time"]),
                float(t["exit_price"]),
                str(t["exit_reason"]),
                float(t["net_pnl"]),
            )
        )
    return rows, dict(strategy.signals), dict(strategy.blocked), engine


def _iso(ts: Any) -> str:
    text: str = ts.astimezone(TPE8).isoformat(timespec="microseconds")
    return text


def differences(expected: Sequence[Row], got: Sequence[Row]) -> list[str]:
    """列出兩邊成交的不同處；完全一致回傳空清單。"""
    out = []
    if len(expected) != len(got):
        out.append(f"筆數不同：對照 {len(expected)} 筆、autotrade {len(got)} 筆")
    for i, (a, b) in enumerate(zip(expected, got, strict=False), start=1):
        if a != b:
            out.append(f"第 {i} 筆不同：對照 {a}、autotrade {b}")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """進入點。"""
    parser = argparse.ArgumentParser(description="動能順勢：對照引擎 vs autotrade 逐筆比對")
    parser.add_argument(
        "--ms-repo", type=Path, required=True, help="網友原版 Momentum_Scalping 的資料夾"
    )
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--ticks", type=Path, action="append", required=True)
    parser.add_argument("--day", type=date.fromisoformat, action="append", required=True)
    args = parser.parse_args(argv)
    if len(args.ticks) != len(args.day):
        parser.error("--ticks 與 --day 的數量要一樣")
    cfg = load_config(args.config)
    mods, params = load_original(args.ms_repo)
    failed = 0
    for path, day in zip(args.ticks, args.day, strict=True):
        contract, ticks = read_taifex_day(
            path, cfg.contract.symbol, day, cfg.session.start, cfg.session.end
        )
        expected, exp_signals, exp_blocked = reference_rows(mods, params, cfg, contract, ticks)
        got, got_signals, got_blocked, engine = autotrade_rows(mods, params, cfg, ticks)
        diffs = differences(expected, got)
        if exp_signals != got_signals or exp_blocked != got_blocked:
            diffs.append(
                f"訊號或擋下的計數不同：對照 {exp_signals} {exp_blocked}、autotrade {got_signals} {got_blocked}"
            )
        total = sum(r[5] for r in expected)
        status = "一致" if not diffs else "不一致"
        print(
            f"{day} {contract} 逐筆 {len(ticks)}：對照 {len(expected)} 筆 {total:+.0f} 元，{status}"
        )
        for line in diffs:
            print("  " + line)
        halts = [e.text for e in engine.events if e.kind in ("halt", "reject")]
        for text in halts[:5]:
            print("  autotrade 風控事件：" + text)
        failed += bool(diffs)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
