"""T-032 / SPEC-009 AC-3：因子物化與 PitView 現算一致 + 逐日 as_of（需真 PG，無則 skip）。

用合成行情（四碼普通股）建 warehouse → materialize_range → 讀回 factor_values 與 fresh
compute 比對（容差 1e-9），並確認逐日各自成列（非以最新回填）。獨立 test schema，跑完 DROP。
"""

from __future__ import annotations

import datetime as dt
import logging
import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError

_SCHEMA = "serving_test_mat"
_STOCKS = {"1111": 100.0, "2222": 50.0, "3333": 200.0, "4444": 80.0}


def _prices(days: int, stocks: dict[str, float]) -> pl.DataFrame:
    base = dt.date(2026, 6, 1)
    rows = []
    for i in range(days):
        d = base + dt.timedelta(days=i)
        at = dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ)
        for sid, p0 in stocks.items():
            c = float(p0 + i)
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": c * 1000.0,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": at,
                }
            )
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )


def _connect_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    conn.autocommit = True
    return conn


def _drop(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')


def test_compute_universe_logs_unexpected_factor_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """D-020 issue 1：_compute_universe 吞掉的因子不再靜默——非預期錯誤留 WARNING，
    宇宙其餘因子照常物化（不因單一因子失敗整天消失）。無需 PG。"""
    import twquant.factors.builtins  # noqa: F401
    from twquant.data.serving import materialize as smat
    from twquant.factors import compute as compute_mod

    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, _STOCKS))
    as_of = dt.date(2026, 6, 24)
    real = compute_mod.compute

    def flaky(names: list[str], view: Any, dates: Any = None) -> pl.DataFrame:
        if names == ["price.mom_20d"]:
            raise RuntimeError("boom")  # 非 DataError → 視為非預期
        return real(names, view, dates=dates)

    monkeypatch.setattr(compute_mod, "compute", flaky)
    with caplog.at_level(logging.WARNING, logger="twquant.serving"):
        merged = smat._compute_universe(wh, as_of)

    assert merged is not None  # 其他因子仍成功物化
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("price.mom_20d" in r.getMessage() for r in warnings), [
        r.getMessage() for r in warnings
    ]


def test_compute_universe_expected_data_gap_logged_below_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """D-020 issue 1：預期資料缺口（DataError，如缺表/史料不足）仍留日誌但不升級為
    WARNING 噪音——與非預期錯誤區分。"""
    import twquant.factors.builtins  # noqa: F401
    from twquant.data.serving import materialize as smat
    from twquant.factors import compute as compute_mod

    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, _STOCKS))
    as_of = dt.date(2026, 6, 24)
    real = compute_mod.compute

    def flaky(names: list[str], view: Any, dates: Any = None) -> pl.DataFrame:
        if names == ["price.mom_20d"]:
            raise DataError("chip_table", as_of, "clean 層無資料")
        return real(names, view, dates=dates)

    monkeypatch.setattr(compute_mod, "compute", flaky)
    with caplog.at_level(logging.DEBUG, logger="twquant.serving"):
        merged = smat._compute_universe(wh, as_of)

    assert merged is not None
    recs = [r for r in caplog.records if "price.mom_20d" in r.getMessage()]
    assert recs, "預期資料缺口也必須留下日誌，不得完全靜默"
    assert all(r.levelno < logging.WARNING for r in recs), [
        (r.levelname, r.getMessage()) for r in recs
    ]


# ── T-094：因子子集物化（籌碼回補後只重算 7 個籌碼因子，不重跑全表 7 小時）──────────


def test_t094_compute_universe_subset_only_computes_requested_factors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-1：factors=子集 → 只對子集呼叫 compute；預設 None → 全部註冊因子（既有行為）。"""
    import twquant.factors.builtins  # noqa: F401
    from twquant.data.serving import materialize as smat
    from twquant.factors import compute as compute_mod
    from twquant.factors.registry import list_factors

    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, _STOCKS))
    as_of = dt.date(2026, 6, 24)
    real = compute_mod.compute
    seen: list[str] = []

    def spy(names: list[str], view: Any, dates: Any = None) -> pl.DataFrame:
        seen.extend(names)
        return real(names, view, dates=dates)

    monkeypatch.setattr(compute_mod, "compute", spy)
    smat._compute_universe(wh, as_of, factors=["price.mom_20d", "price.reversal_5d"])
    assert seen == ["price.mom_20d", "price.reversal_5d"]
    seen.clear()
    smat._compute_universe(wh, as_of)
    assert seen == list_factors(), "factors=None 必須維持全部註冊因子"


def test_t094_subset_rejects_unknown_factor(tmp_path: Path) -> None:
    """子集含未註冊因子 → raise，不得靜默略過（否則「重算籌碼因子」打錯名字會靜默什麼都沒算）。"""
    import twquant.factors.builtins  # noqa: F401
    from twquant.data.serving import materialize as smat
    from twquant.errors import TwquantError

    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, _STOCKS))
    with pytest.raises(TwquantError, match="chip.no_such_factor"):
        smat._compute_universe(wh, dt.date(2026, 6, 24), factors=["chip.no_such_factor"])


def test_t094_subset_intersecting_composite_is_refused(tmp_path: Path) -> None:
    """AC-2：子集若含 composite 因子 → raise。子集模式不重算 composite_daily，
    若允許就會讓 factor_values 與 composite_daily 對同一因子出現兩個版本。"""
    import twquant.factors.builtins  # noqa: F401
    from twquant.config import ServingConfig
    from twquant.data.serving import materialize as smat
    from twquant.errors import TwquantError

    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, _STOCKS))
    cfg = ServingConfig(pg_schema=_SCHEMA, composite_factors=("price.reversal_5d",))
    with pytest.raises(TwquantError, match="composite"):
        smat._materialize_on(wh, None, cfg, dt.date(2026, 6, 24), factors=["price.reversal_5d"])


def test_t094_subset_mode_writes_only_factor_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-2：子集模式只 upsert factor_values 與 factor_cross_stats（且只含子集因子），不碰 composite_daily。"""
    import twquant.factors.builtins  # noqa: F401
    from twquant.config import ServingConfig
    from twquant.data.serving import materialize as smat

    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, _STOCKS))
    writes: list[tuple[str, set[str]]] = []

    def fake_upsert(cur: Any, schema: str, spec: Any, df: pl.DataFrame) -> None:
        writes.append(
            (spec.name, set(df["factor"].unique().to_list()) if "factor" in df.columns else set())
        )

    class _Cur:
        def __enter__(self) -> "_Cur":
            return self

        def __exit__(self, *a: Any) -> None:
            return None

    class _Conn:
        def cursor(self) -> _Cur:
            return _Cur()

        def commit(self) -> None:
            return None

    monkeypatch.setattr(smat, "upsert", fake_upsert)
    cfg = ServingConfig(pg_schema=_SCHEMA, composite_factors=("price.reversal_5d",))
    r = smat._materialize_on(wh, _Conn(), cfg, dt.date(2026, 6, 24), factors=["price.mom_20d"])
    assert r.factors == 1 and r.composite_rows == 0
    tables = [w[0] for w in writes]
    assert "composite_daily" not in tables, tables
    assert set(tables) == {"factor_values", "factor_cross_stats"}
    assert all(w[1] == {"price.mom_20d"} for w in writes), writes


def test_ac3_materialized_value_matches_compute(tmp_path: Path) -> None:
    conn = _connect_or_skip()
    import twquant.factors.builtins  # noqa: F401
    from twquant.data.serving import materialize as smat
    from twquant.factors.compute import compute

    cfg_schema = _SCHEMA
    from twquant.config import ServingConfig

    cfg = ServingConfig(pg_schema=cfg_schema)
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(25, _STOCKS))
    lo, hi = dt.date(2026, 6, 20), dt.date(2026, 6, 25)
    try:
        _drop(conn)
        results = smat.materialize_range(wh, cfg, lo, hi)
        assert len(results) == 6  # 6 個交易日逐日物化
        assert all(r.factor_rows > 0 for r in results)

        d21 = dt.date(2026, 6, 21)
        expected = (
            compute(["price.mom_20d"], wh.view(d21), dates=[d21])
            .filter(pl.col("stock_id") == "1111")["value"]
            .item()
        )
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT value FROM "{_SCHEMA}".factor_values '
                "WHERE factor = %s AND stock_id = %s AND date = %s",
                ("price.mom_20d", "1111", d21),
            )
            got = cur.fetchone()[0]
        assert got == pytest.approx(expected, abs=1e-9)
        assert got == pytest.approx(0.2, abs=1e-9)  # 120/100 - 1

        with conn.cursor() as cur:
            cur.execute(
                f'SELECT count(DISTINCT date) FROM "{_SCHEMA}".factor_values WHERE factor = %s',
                ("price.mom_1d",),
            )
            assert cur.fetchone()[0] == 6  # mom_1d 逐日各自成列（6 日皆可算，非最新回填）
            cur.execute(
                f'SELECT n_universe FROM "{_SCHEMA}".factor_cross_stats '
                "WHERE factor = %s AND date = %s",
                ("price.mom_20d", d21),
            )
            assert cur.fetchone()[0] == 4  # 四檔宇宙
            cur.execute(f'SELECT count(*) FROM "{_SCHEMA}".composite_daily WHERE date = %s', (d21,))
            assert cur.fetchone()[0] == 4

        # D-020：composite_daily 只由有效子集因子構成（非全因子等權）
        from twquant.factors.registry import get

        with conn.cursor() as cur:
            cur.execute(
                f'SELECT factor, z FROM "{_SCHEMA}".factor_values WHERE stock_id=%s AND date=%s',
                ("1111", d21),
            )
            fv = {r[0]: float(r[1]) for r in cur.fetchall()}
            present = [f for f in cfg.composite_factors if f in fv]
            assert present, "子集因子應至少有一個可算（reversal_5d/residual_reversal_5d）"
            assert "price.mom_20d" in fv  # 非子集因子仍寫入 factor_values（體檢卡展示）
            expected = sum(fv[f] * get(f).direction for f in present) / len(present)
            cur.execute(
                f'SELECT signed_z_mean FROM "{_SCHEMA}".composite_daily '
                "WHERE stock_id=%s AND date=%s",
                ("1111", d21),
            )
            assert cur.fetchone()[0] == pytest.approx(expected, abs=1e-9)
    finally:
        _drop(conn)
        conn.close()
