"""T-055 雲端滾動窗口：prune / broker_flow 聚合 / seed 的整合測試（無 DSN 則 skip）。

AC-窗口：configs 滾動窗 ≥ 因子最大回看（靜態，不需 PG）。
AC-prune：修剪到 N 個交易日、重跑冪等。
AC-seed：window ＋四碼過濾正確落到目標 schema。
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas

_SRC = "serving_test_cloud_src"
_DST = "serving_test_cloud_dst"


def _connect_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    return conn


def _drop(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SRC}" CASCADE')
        cur.execute(f'DROP SCHEMA IF EXISTS "{_DST}" CASCADE')
    conn.commit()


def _prices(date: dt.date, stock: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date],
            "stock_id": [stock],
            "open": [100.0],
            "high": [101.0],
            "low": [99.0],
            "close": [100.5],
            "volume": [1000],
            "amount": [100500.0],
            "limit_up_price": pl.Series([None], dtype=pl.Float64),
            "limit_down_price": pl.Series([None], dtype=pl.Float64),
            "available_at": pl.Series(
                [dt.datetime(date.year, date.month, date.day, 18, 0, tzinfo=TZ)],
                dtype=schemas.TW_DT,
            ),
        }
    )


def _brokers(date: dt.date, stock: str = "2330") -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [date, date],
            "stock_id": [stock, stock],
            "broker_id": ["甲", "乙"],
            "buy_shares": [7000, 1000],
            "sell_shares": [2000, 4000],
            "avg_price": pl.Series([None, None], dtype=pl.Float64),
            "available_at": pl.Series(
                [dt.datetime(date.year, date.month, date.day, 18, 0, tzinfo=TZ)] * 2,
                dtype=schemas.TW_DT,
            ),
        }
    )


def _seed_prices(conn: Any, schema: str, dates: list[dt.date], stocks: list[str]) -> None:
    from twquant.data.serving import schema as sch
    from twquant.data.serving import sync as ssync

    sch.ensure_schema(conn, schema)
    # 同表同交易只能 upsert 一次（staging temp 撞名）→ 每日把全部股票併成一個 df
    for d in dates:
        df = pl.concat([_prices(d, s) for s in stocks])
        with conn.cursor() as cur:
            ssync.upsert(cur, schema, sch.spec_of("prices_daily"), df)
        conn.commit()


def test_ac_windows_cover_max_factor_lookback() -> None:
    """滾動窗（configs/serving.yaml cloud.windows）≥ 吃該表的因子最大回看。"""
    from pathlib import Path

    import twquant.factors.builtins  # noqa: F401  # 副作用註冊全部因子
    from twquant.config import ServingConfig, load_config
    from twquant.factors import registry

    cfg = load_config(Path("configs/serving.yaml"), ServingConfig)
    win = cfg.cloud.windows
    assert "prices_daily" in win and "factor_values" in win
    excluded = set(cfg.cloud.exclude_factors)
    for name in registry.list_factors():
        if name in excluded:
            continue  # 雲端明確不算的因子（另有測試驗證它們真的超窗）
        fdef = registry.get(name)
        tables = set(fdef.tables)
        if not tables <= set(win):
            # 混用「未修剪表」（如 monthly_revenue 全史）的因子：lookback 由未修剪表驅動，
            # 修剪表僅作日頻 spine（fundamental.py join_asof），窗口不受其約束——人工審查已確認。
            continue
        for table in tables:
            assert fdef.lookback_days <= win[table], (
                f"{name} 回看 {fdef.lookback_days} 超過 {table} 窗 {win[table]}"
            )


def test_t057_connect_disables_prepared_statements() -> None:
    """store.connect 必須停用 prepared statements（Supabase pooler 相容，T-057）。"""
    pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    from twquant.config import ServingConfig
    from twquant.data.serving import store

    cfg = ServingConfig(enabled=True)
    conn = store.connect(cfg)
    try:
        assert conn.prepare_threshold is None
    finally:
        conn.close()


def test_ac_prune_window_and_idempotent() -> None:
    conn = _connect_or_skip()
    from twquant.data.serving.cloud import prune

    days = [dt.date(2026, 6, d) for d in (22, 23, 24, 25, 26)]
    try:
        _drop(conn)
        _seed_prices(conn, _SRC, days, ["2330"])
        results = prune(conn, _SRC, {"prices_daily": 3})
        assert results[0].deleted == 2  # 5 個交易日 → 留最新 3 日
        with conn.cursor() as cur:
            cur.execute(f'SELECT min(date), count(DISTINCT date) FROM "{_SRC}".prices_daily')
            assert cur.fetchone() == (dt.date(2026, 6, 24), 3)
        again = prune(conn, _SRC, {"prices_daily": 3})
        assert again[0].deleted == 0  # 冪等
        wide = prune(conn, _SRC, {"prices_daily": 10})  # 窗大於現有日數 → 不刪
        assert wide[0].deleted == 0
    finally:
        _drop(conn)
        conn.close()


def test_t331_ac1_prune_keeps_industry_members_outside_pattern() -> None:
    """特別股（非四碼、有產業別）在 industry_member_tables 裡要留下；ETF（無產業別）照刪。"""
    conn = _connect_or_skip()
    from twquant.data.serving.cloud import prune

    days = [dt.date(2026, 6, 25), dt.date(2026, 6, 26)]
    pattern = "^[1-9][0-9]{3}$"
    try:
        _drop(conn)
        _seed_prices(conn, _SRC, days, ["2330", "2887G", "0050"])
        with conn.cursor() as cur:
            cur.execute(
                f'INSERT INTO "{_SRC}".stock_meta (stock_id, name, market, industry) VALUES '
                "('2330', '台積電', 'TWSE', '半導體業'), ('2887G', '台新戊特二', 'TWSE', '金融保險業'), "
                "('0050', '元大台灣50', 'TWSE', NULL)"
            )
        conn.commit()

        def remaining() -> set[str]:
            with conn.cursor() as cur:
                cur.execute(f'SELECT DISTINCT stock_id FROM "{_SRC}".prices_daily')
                return {r[0] for r in cur.fetchall()}

        kept = prune(
            conn,
            _SRC,
            {"prices_daily": 10},
            pattern,
            ("prices_daily",),
            industry_member_tables=("prices_daily",),
        )
        assert kept[0].deleted == 2  # 只刪 0050 的兩天
        assert remaining() == {"2330", "2887G"}
        again = prune(
            conn,
            _SRC,
            {"prices_daily": 10},
            pattern,
            ("prices_daily",),
            industry_member_tables=("prices_daily",),
        )
        assert again[0].deleted == 0  # 冪等
        strict = prune(conn, _SRC, {"prices_daily": 10}, pattern, ("prices_daily",))
        assert strict[0].deleted == 2  # 沒列進 industry_member_tables → 特別股照刪（舊行為）
        assert remaining() == {"2330"}
    finally:
        _drop(conn)
        conn.close()


def test_t349_ac9_prune_keeps_benchmark_stock() -> None:
    """D-176：成績單對照 0050 不是四碼普通股，`keep_stocks` 列了就留下；其他 ETF 照刪。"""
    conn = _connect_or_skip()
    from twquant.data.serving.cloud import prune

    days = [dt.date(2026, 6, 25), dt.date(2026, 6, 26)]
    pattern = "^[1-9][0-9]{3}$"
    try:
        _drop(conn)
        _seed_prices(conn, _SRC, days, ["2330", "0050", "0056"])
        kept = prune(
            conn, _SRC, {"prices_daily": 10}, pattern, ("prices_daily",), keep_stocks=("0050",)
        )
        assert kept[0].deleted == 2  # 只刪 0056 的兩天
        with conn.cursor() as cur:
            cur.execute(f'SELECT DISTINCT stock_id FROM "{_SRC}".prices_daily')
            assert {r[0] for r in cur.fetchall()} == {"2330", "0050"}
    finally:
        _drop(conn)
        conn.close()


def test_t349_ac9_benchmark_is_kept_and_seeded() -> None:
    """成績單的對照代號要同時在修剪白名單與雲端切片裡，否則當晚就被修掉或重灌後不見（靜態）。"""
    from pathlib import Path

    import yaml

    from twquant.config import ServingConfig, load_config
    from twquant.reporting.scorecard import ScorecardConfig

    bench = load_config(Path("configs/scorecard.yaml"), ScorecardConfig).benchmark
    cfg = load_config(Path("configs/serving.yaml"), ServingConfig)
    assert bench in cfg.cloud.keep_stocks
    seed = yaml.safe_load(Path("configs/cloud_seed.yaml").read_text(encoding="utf-8"))["tables"]
    assert any(bench in (p.get("in") or {}).get("stock_id", []) for p in seed["prices_daily"])


def test_t331_ac2_seed_slices_fit_prune_windows() -> None:
    """configs/cloud_seed.yaml 灌進去的天數不能超過 cloud.windows，否則當晚 prune 就修掉（靜態）。"""
    from pathlib import Path

    import yaml

    from twquant.config import ServingConfig, load_config
    from twquant.data.serving.schema import spec_of

    cfg = load_config(Path("configs/serving.yaml"), ServingConfig)
    win = cfg.cloud.windows
    assert set(cfg.cloud.industry_member_tables) <= set(cfg.cloud.pattern_tables)
    seed = yaml.safe_load(Path("configs/cloud_seed.yaml").read_text(encoding="utf-8"))["tables"]
    for table, pieces in seed.items():
        spec_of(table)  # 未知表名 raise
        for piece in pieces:
            n = piece.get("last_sessions")
            if n is not None and table in win:
                assert n <= win[table], f"{table} 灌 {n} 日，但雲端窗口只留 {win[table]} 日"


def test_broker_flow_aggregation_idempotent() -> None:
    conn = _connect_or_skip()
    from twquant.data.serving import schema as sch
    from twquant.data.serving import sync as ssync
    from twquant.data.serving.cloud import refresh_broker_flow

    d = dt.date(2026, 6, 30)
    try:
        _drop(conn)
        sch.ensure_schema(conn, _SRC)
        with conn.cursor() as cur:
            ssync.upsert(cur, _SRC, sch.spec_of("broker_trades"), _brokers(d))
        conn.commit()
        n = refresh_broker_flow(conn, _SRC, [d])
        assert n == 1  # 一檔一日一列
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT buy_shares, sell_shares, net_shares, n_brokers "
                f'FROM "{_SRC}".broker_flow_daily WHERE stock_id = %s AND date = %s',
                ("2330", d),
            )
            assert cur.fetchone() == (8000, 6000, 2000, 2)
        assert refresh_broker_flow(conn, _SRC, [d]) == 1  # 重跑冪等（DO UPDATE）
    finally:
        _drop(conn)
        conn.close()


def test_ac_seed_window_and_stock_pattern() -> None:
    conn = _connect_or_skip()
    dst_conn = _connect_or_skip()  # COPY OUT 與 COPY IN 不能共用同一條連線
    from twquant.data.serving.cloud import seed_tables

    days = [dt.date(2026, 6, d) for d in (24, 25, 26)]
    try:
        _drop(conn)
        # 四碼普通股 2330 ＋ ETF 0050：pattern 過濾後者
        _seed_prices(conn, _SRC, days, ["2330", "0050"])
        results = seed_tables(
            conn,
            dst_conn,
            src_schema=_SRC,
            dst_schema=_DST,
            tables=["prices_daily"],
            windows={"prices_daily": 2},
            stock_pattern="^[1-9][0-9]{3}$",
            pattern_tables=("prices_daily",),
        )
        assert results[0].rows == 2  # 2 日 × 1 檔（0050 被過濾）
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT count(*), min(date), count(DISTINCT stock_id) FROM "{_DST}".prices_daily'
            )
            assert cur.fetchone() == (2, dt.date(2026, 6, 25), 1)
            cur.execute(f'SELECT DISTINCT stock_id FROM "{_DST}".prices_daily')
            assert cur.fetchall() == [("2330",)]
    finally:
        _drop(conn)
        conn.close()
        dst_conn.close()


def test_cloud_exclude_list_only_holds_factors_that_truly_exceed_a_window() -> None:
    """exclude_factors 不能拿來藏錯：每個被排除的因子都必須至少對一張修剪表超窗。"""
    from pathlib import Path

    import twquant.factors.builtins  # noqa: F401
    from twquant.config import ServingConfig, load_config
    from twquant.factors import registry

    cfg = load_config(Path("configs/serving.yaml"), ServingConfig)
    win = cfg.cloud.windows
    for name in cfg.cloud.exclude_factors:
        fdef = registry.get(name)  # 未註冊 → raise
        over = [t for t in fdef.tables if t in win and fdef.lookback_days > win[t]]
        assert over, f"{name} 沒有任何一張修剪表超窗，不該列在 exclude_factors"
