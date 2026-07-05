"""投組狀態持久化（SPEC-005 §2，D-010）。

kill-switch 狀態存 data/state/portfolio_state.json（單檔、含 schema_version）。
原子寫（temp+rename）。**fail-safe**：檔案缺失或損毀 → 讀出 HALTED（禁止靜默回
NORMAL）。此模組在純運算層之外（factors/strategy/backtest/portfolio 禁寫檔）。
"""

from __future__ import annotations

import json
from pathlib import Path

from twquant.portfolio.core import STATE_SCHEMA_VERSION, PortfolioState

_REL = ("state", "portfolio_state.json")


def state_path(data_dir: str | Path) -> Path:
    return Path(data_dir).joinpath(*_REL)


def load_state(data_dir: str | Path) -> PortfolioState:
    """讀狀態；缺失或損毀 → HALTED（fail-safe，D-010）。"""
    p = state_path(data_dir)
    if not p.exists():
        return PortfolioState(status="HALTED", trigger_reason="state 檔缺失（fail-safe）")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return PortfolioState(
            status=d["status"],
            entered_at=d.get("entered_at", ""),
            trigger_reason=d.get("trigger_reason", ""),
            freeze_next_day=bool(d.get("freeze_next_day", False)),
            equity_high_water=float(d.get("equity_high_water", 0.0)),
            schema_version=int(d.get("schema_version", STATE_SCHEMA_VERSION)),
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return PortfolioState(status="HALTED", trigger_reason="state 檔損毀（fail-safe）")


def save_state(data_dir: str | Path, state: PortfolioState) -> None:
    """原子寫狀態檔。"""
    p = state_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": state.status,
        "entered_at": state.entered_at,
        "trigger_reason": state.trigger_reason,
        "freeze_next_day": state.freeze_next_day,
        "equity_high_water": state.equity_high_water,
        "schema_version": state.schema_version,
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
