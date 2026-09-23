"""每日路徑的 PG 讀取來源（T-299）：物化與策略持股快照共用同一個縮窗建構函數。

2026-09-23 每日 pipeline 在 15 GB 被 OOM 砍掉兩次。第一次在物化（T-294 修了）；修好之後
換成策略持股快照——它自己組 `PgTableSource`、沒有任何下界，整張 `ml_scores`（4,160 萬列）
整批載入。兩處各自組參數、只修了一處，所以這裡守的是「兩處都經過同一個函數」。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.cli as cli
import twquant.factors.builtins  # noqa: F401
from twquant.data.serving import store
from twquant.data.warehouse import TableSource
from twquant.factors.registry import list_factors
from twquant.strategy.config import StrategyConfig

DAY = dt.date(2026, 9, 23)
_ML_LOWER = f"date >= '{DAY - dt.timedelta(days=30)}'"  # 薄因子回看 0 天＋30 天緩衝（T-294）


class _Cur:
    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, _sql: str, _params: object = None) -> None:
        return None

    def fetchone(self) -> tuple[int]:
        return (1,)  # 表存在、有 date 欄


class _Conn:
    def cursor(self) -> _Cur:
        return _Cur()

    def close(self) -> None:
        return None


def _capture_sql(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """攔 `pl.read_database`，記下每張表組出的 SQL。"""
    seen: dict[str, str] = {}

    def fake_read(query: str, _conn: object, **_: object) -> pl.DataFrame:
        table = query.split('"."')[1].split('"')[0]
        seen[table] = query
        return pl.DataFrame({"date": [DAY], "stock_id": ["2330"]})

    monkeypatch.setattr(pl, "read_database", fake_read)
    return seen


def test_ac1_helper_narrows_ml_scores_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """ml_scores 只讀近 30 天；其餘表仍涵蓋全體最長回看（四年以上）。"""
    seen = _capture_sql(monkeypatch)
    src = cli._daily_pg_source(_Conn(), "serving", DAY, list(list_factors()))
    src.scan("ml_scores")
    src.scan("prices_daily")
    assert _ML_LOWER in seen["ml_scores"]
    lower = dt.date.fromisoformat(seen["prices_daily"].split("date >= '")[1][:10])
    assert lower < DAY - dt.timedelta(days=1500), f"prices_daily 下界 {lower} 不夠涵蓋最長回看"


def test_ac2_strategy_targets_step_reads_narrowed_ml_scores(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """策略持股快照：每支已登記策略拿到的來源，讀 ml_scores 時都帶縮窗下界。"""
    seen = _capture_sql(monkeypatch)
    calls: list[str] = []

    def fake_live(
        cfg: StrategyConfig, source: TableSource, day: dt.date
    ) -> list[tuple[str, float]]:
        calls.append(cfg.name)
        source.scan("ml_scores")
        return []

    monkeypatch.setattr(store, "connect", lambda _cfg: _Conn())
    monkeypatch.setattr(cli, "_live_targets", fake_live)
    cli._strategy_targets_for_day(DAY, tmp_path)
    assert calls, "strategies_live.yaml 應至少登記一支策略"
    assert _ML_LOWER in seen["ml_scores"]
