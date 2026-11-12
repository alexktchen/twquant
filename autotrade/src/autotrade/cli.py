"""指令列：檢查設定、用期交所逐筆重播一天。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from autotrade.broker.sim import SimBroker
from autotrade.config import Config, load_config
from autotrade.engine import Engine
from autotrade.feed.replay import read_taifex_day
from autotrade.journal import Journal, read_journal
from autotrade.strategy.base import NullStrategy, Strategy
from autotrade.strategy.momentum import MomentumStrategy, load_original

STRATEGIES = ("none", "momentum")


def build_strategy(name: str, cfg: Config, ms_repo: Path | None) -> Strategy:
    """依名稱建立策略。momentum 需要網友原版程式的資料夾。"""
    if name == "none":
        return NullStrategy()
    if name == "momentum":
        if ms_repo is None:
            raise ValueError(
                "--strategy momentum 要給 --ms-repo（網友原版 Momentum_Scalping 的資料夾）"
            )
        if cfg.momentum is None:
            raise ValueError("設定檔沒有 [momentum]")
        mods, params = load_original(ms_repo)
        return MomentumStrategy(mods, params, cfg.momentum, cfg.contract)
    raise ValueError(f"不認得的策略：{name!r}（可用：{STRATEGIES}）")


def run_replay(
    config: Path,
    ticks: Path,
    day: date,
    strategy: str,
    journal: Path | None = None,
    ms_repo: Path | None = None,
) -> dict[str, float | int | str]:
    """用模擬券商重播一天，回傳當日摘要；給 journal 路徑就把事件寫進日誌。"""
    cfg = load_config(config)
    contract, rows = read_taifex_day(
        ticks, cfg.contract.symbol, day, cfg.session.start, cfg.session.end
    )
    log = Journal(journal) if journal is not None else None
    engine = Engine(
        cfg,
        build_strategy(strategy, cfg, ms_repo),
        SimBroker(cfg.sim),
        sink=log.write if log else None,
    )
    try:
        for tick in rows:
            engine.on_tick(tick)
        summary = engine.finish()
    finally:
        if log is not None:
            log.close()
    return {"day": day.isoformat(), "contract": contract, "ticks": len(rows), **summary}


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
    replay.add_argument("--ms-repo", type=Path, help="網友原版 Momentum_Scalping 的資料夾")
    replay.add_argument("--journal", type=Path, help="事件日誌要寫到哪個檔案（請給絕對路徑）")
    verify = sub.add_parser("verify-journal", help="驗證事件日誌的雜湊鏈")
    verify.add_argument("journal", type=Path)
    args = parser.parse_args(argv)
    if args.command == "check-config":
        cfg = load_config(args.config)
        print(
            f"設定沒問題：{cfg.mode}、{cfg.contract.symbol}、部位 {cfg.risk.min_position}～{cfg.risk.max_position} 口"
        )
        return 0
    if args.command == "verify-journal":
        records = read_journal(args.journal)
        print(f"日誌沒問題：{len(records)} 筆")
        return 0
    summary = run_replay(
        args.config, args.ticks, args.day, args.strategy, args.journal, args.ms_repo
    )
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0
