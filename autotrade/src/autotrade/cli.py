"""指令列：檢查設定、用期交所逐筆重播一天、把狀態寫成檔案給網站、跑跟雲端溝通的中繼程式。"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from autotrade.broker.sim import SimBroker
from autotrade.config import Config, ConfigError, load_config
from autotrade.engine import Engine
from autotrade.export import dumps, env_value, snapshot
from autotrade.feed.replay import read_taifex_day
from autotrade.journal import Journal, read_journal
from autotrade.relay import CONSUMERS, Relay, open_pg, run
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
    status_out: Path | None = None,
) -> dict[str, float | int | str]:
    """用模擬券商重播一天，回傳當日摘要；給 journal 就寫事件日誌，給 status_out 就寫給網站的狀態檔。"""
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
    if status_out is not None:
        last = engine.last_tick
        if last is None:
            raise ValueError(f"{day} 沒有行情，寫不出狀態檔")
        snap = snapshot(engine, last.ts, source="replay", status="closed", strategy=strategy)
        write_text_atomic(status_out, dumps(snap))
    return {"day": day.isoformat(), "contract": contract, "ticks": len(rows), **summary}


def write_text_atomic(path: Path, text: str) -> None:
    """先寫暫存檔再改名，讀的人不會看到寫到一半的檔。資料夾不存在就 raise。"""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def run_relay(
    config: Path,
    folder: Path,
    consumer: str | None,
    status: Path | None,
    dsn_env: str,
    env_file: Path | None,
    once: bool,
) -> None:
    """跑中繼程式：資料庫 ↔ 本機資料夾裡的指令檔與回報檔。跟交易程式是不同的程序。"""
    cfg = load_config(config)
    if cfg.control is None:
        raise ConfigError("設定檔沒有 [control]，沒有東西要中繼")
    if not folder.is_dir():
        raise FileNotFoundError(f"資料夾不存在：{folder}")
    dsn = os.environ.get(dsn_env)
    if not dsn and env_file is not None:
        dsn = env_value(env_file.read_text(encoding="utf-8"), dsn_env)
    if not dsn:
        raise ConfigError(f"環境變數 {dsn_env} 沒有設定")
    db = open_pg(dsn, timeout_s=cfg.control.poll_s)
    db.ensure()
    relay = Relay(db, consumer or cfg.mode, folder, cfg.control.poll_s, status)
    try:
        run(relay, once=once)
    except KeyboardInterrupt:
        print("中繼程式停止")


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
    replay.add_argument("--status-out", type=Path, help="給網站的狀態檔要寫到哪（請給絕對路徑）")
    relay = sub.add_parser("relay", help="中繼程式：把遠端停止指令拿下來、把結果與狀態送上去")
    relay.add_argument("--config", type=Path, required=True)
    relay.add_argument("--dir", type=Path, required=True, help="跟交易程式共用的資料夾（絕對路徑）")
    relay.add_argument("--consumer", choices=CONSUMERS, help="預設用設定檔的 mode")
    relay.add_argument("--status", type=Path, help="狀態檔；有更新就送上去")
    relay.add_argument("--dsn-env", default="TWQUANT_PG_DSN", help="放連線字串的環境變數名稱")
    relay.add_argument("--env-file", type=Path, help="環境變數沒設時，改從這個 .env 讀同名的變數")
    relay.add_argument("--once", action="store_true", help="只跑一輪就結束")
    verify = sub.add_parser("verify-journal", help="驗證事件日誌的雜湊鏈")
    verify.add_argument("journal", type=Path)
    args = parser.parse_args(argv)
    if args.command == "check-config":
        cfg = load_config(args.config)
        print(
            f"設定沒問題：{cfg.mode}、{cfg.contract.symbol}、部位 {cfg.risk.min_position}～{cfg.risk.max_position} 口"
        )
        return 0
    if args.command == "relay":
        run_relay(
            args.config,
            args.dir,
            args.consumer,
            args.status,
            args.dsn_env,
            args.env_file,
            args.once,
        )
        return 0
    if args.command == "verify-journal":
        records = read_journal(args.journal)
        print(f"日誌沒問題：{len(records)} 筆")
        return 0
    summary = run_replay(
        args.config,
        args.ticks,
        args.day,
        args.strategy,
        args.journal,
        args.ms_repo,
        args.status_out,
    )
    for key, value in summary.items():
        print(f"{key}: {value}")
    return 0
