"""每日物化按表縮窗（2026-09-23 每日 pipeline OOM 的修正）。

事故：`ml_scores` 長到 4,162 萬列（研究回測寫進去的樣本外分數佔了近 900 萬列），而每日物化
用**全體因子最長的回看**（`fundamental.sue` 850 天）去讀每一張表——11 支 `ml.*` 薄因子只要
當天的分數，卻把四年半的 `ml_scores` 整批載進記憶體，程序在 15.3 GB 被 OOM killer 砍掉。

修正只對「讀取者全都宣告過」的表按表縮窗。這個前提由本檔的靜態測試守著：
因子讀沒宣告的表時讀取層不會攔，若前提被打破，縮窗會讓因子**悄悄少讀歷史**。
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from twquant.data.serving.materialize import (
    _NARROWABLE_TABLES,
    read_since,
    read_since_by_table,
)
from twquant.data.serving.pg_view import PgTableSource
from twquant.factors.registry import get, list_factors

_FACTORS_DIR = Path(__file__).resolve().parents[1] / "src" / "twquant" / "factors"
DAY = dt.date(2026, 9, 23)


def test_ac1_ml_scores_window_follows_its_own_readers() -> None:
    """ml_scores 的讀取下界只看讀它的薄因子（回看 0 天）＝ 當日往前 30 天緩衝，
    而全域下界被 850 天的 `fundamental.sue` 拖到四年多以前。"""
    names = list(list_factors())
    per_table = read_since_by_table(DAY, names)
    assert per_table["ml_scores"] == DAY - dt.timedelta(days=30)
    assert read_since(DAY, names) < DAY - dt.timedelta(days=1500), "全域下界應仍涵蓋最長回看"


def test_ac2_no_readers_means_no_override() -> None:
    """名單裡沒有任何因子讀這張表時不給值——`PgTableSource` 就退回全域下界，不會亂縮。"""
    assert read_since_by_table(DAY, ["price.mom_20d"]) == {}


def test_ac3_only_declared_readers_touch_narrowable_tables() -> None:
    """靜態前提：可縮窗的表，只有宣告過它的因子模組會讀。

    讀取層不檢查因子實際讀了哪些表；若別的模組偷讀 ml_scores 又需要歷史，
    按表縮窗會讓它悄悄少讀——這條測試把那種可能擋在 commit 之前。
    """
    for table in _NARROWABLE_TABLES:
        pattern = re.compile(rf"""table\(\s*["']{table}["']""")
        readers = sorted(
            p.name for p in _FACTORS_DIR.glob("*.py") if pattern.search(p.read_text("utf-8"))
        )
        assert readers == ["ml.py"], f"{table} 的讀取者應只有 ml.py，實際：{readers}"
        declared = [n for n in list_factors() if table in get(n).tables]
        assert declared and all(n.startswith("ml.") for n in declared)


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


def test_ac4_where_clause_is_built_per_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """攔 `pl.read_database` 看組出的 SQL：ml_scores 用覆寫值、其餘表用全域值。"""
    seen: dict[str, str] = {}

    def fake_read(query: str, _conn: object, **_: object) -> pl.DataFrame:
        table = query.split('"."')[1].split('"')[0]
        seen[table] = query
        return pl.DataFrame({"date": [DAY], "stock_id": ["2330"]})

    monkeypatch.setattr(pl, "read_database", fake_read)
    src = PgTableSource(
        _Conn(),
        "serving",
        since=dt.date(2021, 11, 1),
        table_since={"ml_scores": dt.date(2026, 8, 24)},
    )
    src.scan("ml_scores")
    src.scan("prices_daily")
    assert "date >= '2026-08-24'" in seen["ml_scores"]
    assert "date >= '2021-11-01'" in seen["prices_daily"]
