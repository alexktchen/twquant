"""指令列：檢查設定、用期交所逐筆重播一天。"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from autotrade.broker.sim import SimBroker
from autotrade.config import load_config
from autotrade.engine import Engine
from autotrade.feed.replay import read_taifex_day
from autotrade.strategy.base import NullStrategy, Strategy

STRATEGIES: dict[str, Callable[[], Strategy]] = {"none": NullStrategy}


def run_replay(config: Path, ticks: Path, day: date, strategy: str) -> dict[str, float | int | str]:
    """用模擬券商重播一天，回傳當日摘要。"""
    cfg = load_config(config)
    contract, rows = read_taifex_day(
        ticks, cfg.contract.symbol, day, cfg.session.start, cfg.session.end
    )
    engine = Engine(cfg, STRATEGIES[strategy](), SimBroker(cfg.sim))
    for tick in rows:
        engine.on_tick(tick)
    return {"day": day.isoformat(), "contract": contract, "ticks": len(rows), **engine.finish()}


def main(argv: Sequence[str] | None = None) -> int:
    """進入點。"""
    parser = argparse.ArgumentParser(prog="autotrade", description="獨立的程式交易系統")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check-config", help="讀設定檔並驗證")
    check.add_argument("config", type=Path)
    replay = sub.add_parser("replay", help="用期交所逐筆檔重播一天（模擬券商）")
    replay.add_argument("--config", type=Path, required=True)
    replay.add_argument("--ticks", type=Path, required=True, help="期交所 Daily_*.csv")
    replay.add_argument("--day", type=date.fromisoformat, required=True)
    replay.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
    args = parser.parse_args(argv)
    if args.command == "check-config":
        cfg = load_config(args.config)
        print(
            f"設定沒問題：{cfg.mode}、{cfg.contract.symbol}、部位 {cfg.risk.min_position}～{cfg.risk.max_position} 口"
        )
        return 0
    summary = run_replay(args.config, args.ticks, args.day, args.strategy)
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0
