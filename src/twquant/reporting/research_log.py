"""research_log（SPEC-002 §4 / LOOP.md 嘗試次數登記）。

每次 IC 檢驗/回測都 append 一筆，供 deflated Sharpe 的 trials 計數（防過擬合）。
append-only 分片寫入（SPEC-001 §7）：每次寫一個新小檔，禁止重寫整檔（並行安全）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

import polars as pl

from twquant.conventions import now_tw

if TYPE_CHECKING:
    from twquant.backtest.costs import CostConfig
    from twquant.strategy.config import StrategyConfig

COLUMNS = [
    "entry_type",  # ic_test | backtest
    "strategy",
    "factor",
    "params_hash",
    "date",  # 檢驗執行日（字串）
    "metric_name",
    "metric_value",
    "trial_scope",  # 同 scope 下 distinct params_hash 數 = deflated Sharpe 的 trials
]

_SCHEMA = {
    "entry_type": pl.String,
    "strategy": pl.String,
    "factor": pl.String,
    "params_hash": pl.String,
    "date": pl.String,
    "metric_name": pl.String,
    "metric_value": pl.Float64,
    "trial_scope": pl.String,
}


def _dir(data_dir: str | Path) -> Path:
    return Path(data_dir) / "research_log"


def append_research_log(data_dir: str | Path, entries: list[dict[str, object]]) -> Path:
    """append 一批紀錄為新分片。回傳分片路徑。"""
    d = _dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    df = pl.DataFrame(entries, schema=_SCHEMA)
    ts = now_tw().strftime("%Y%m%dT%H%M%S%f")
    final = d / f"part-{ts}.parquet"
    tmp = d / f"part-{ts}.parquet.tmp"
    df.write_parquet(tmp)
    tmp.replace(final)
    return final


def read_research_log(data_dir: str | Path) -> pl.DataFrame:
    """讀全部分片（union）。無資料回傳空表。"""
    files = sorted(_dir(data_dir).glob("part-*.parquet"))
    if not files:
        return pl.DataFrame(schema=_SCHEMA)
    return pl.read_parquet(files)


def trials_count(data_dir: str | Path, trial_scope: str) -> int:
    """某 trial_scope 下 distinct params_hash 數（deflated Sharpe 的 trials）。"""
    log = read_research_log(data_dir)
    if log.is_empty():
        return 0
    return log.filter(pl.col("trial_scope") == trial_scope)["params_hash"].n_unique()


def backtest_trial_scope(strategy: StrategyConfig) -> str:
    """回測試驗家族；明示 scope 讓同族變體不被策略顯示名稱拆散。"""
    family = strategy.trial_scope or strategy.name
    return f"bt:{family}"


def backtest_params_hash(
    *,
    strategy: StrategyConfig,
    cost: CostConfig,
    start: dt.date,
    end: dt.date,
    initial_capital: float,
    max_participation: float,
    source: str,
) -> str:
    """完整回測經濟配置的 canonical hash（排除顯示/家族 metadata）。"""
    strategy_payload = strategy.model_dump(
        mode="json", exclude={"name", "description", "trial_scope"}
    )
    payload = {
        "strategy": strategy_payload,
        "cost": cost.model_dump(mode="json"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "initial_capital": initial_capital,
        "max_participation": max_participation,
        "source": source,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()[:16]


def backtest_log_entries(
    *,
    strategy: StrategyConfig,
    cost: CostConfig,
    start: dt.date,
    end: dt.date,
    initial_capital: float,
    max_participation: float,
    source: str,
    metrics: Mapping[str, float],
) -> list[dict[str, object]]:
    """以單一 canonical 身分建立回測 metrics 登記列。"""
    params_hash = backtest_params_hash(
        strategy=strategy,
        cost=cost,
        start=start,
        end=end,
        initial_capital=initial_capital,
        max_participation=max_participation,
        source=source,
    )
    scope = backtest_trial_scope(strategy)
    return [
        {
            "entry_type": "backtest",
            "strategy": strategy.name,
            "factor": "",
            "params_hash": params_hash,
            "date": end.isoformat(),
            "metric_name": name,
            "metric_value": float(value),
            "trial_scope": scope,
        }
        for name, value in sorted(metrics.items())
    ]
