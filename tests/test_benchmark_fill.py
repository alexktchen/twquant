"""D-176 修訂三／T-357：名單績效的對照（0050）缺行情、缺除權息從 FinMind 補——只補缺的、已有的比對、不覆蓋。"""

from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.data.serving.benchmark_fill import plan_fill
from twquant.errors import DataError

_ROOT = Path(__file__).resolve().parents[1]
_D = [dt.date(2026, 7, 20), dt.date(2026, 7, 21), dt.date(2026, 7, 22), dt.date(2026, 7, 23)]
_UPTO = _D[2]


def _fm_price(day: dt.date, close: float, sid: str = "0050") -> dict[str, Any]:
    """FinMind TaiwanStockPrice 原始一列（量是股、金額是元）。"""
    return {
        "date": day.isoformat(),
        "stock_id": sid,
        "Trading_Volume": 10_000_000,
        "Trading_money": int(close * 10_000_000),
        "open": close - 0.5,
        "max": close + 1.0,
        "min": close - 1.0,
        "close": close,
        "spread": 0.5,
        "Trading_turnover": 12_000,
    }


def _fm_div(day: dt.date, before: float, after: float, kind: str = "除息") -> dict[str, Any]:
    """FinMind TaiwanStockDividendResult 原始一列。"""
    return {
        "date": day.isoformat(),
        "stock_id": "0050",
        "before_price": before,
        "after_price": after,
        "stock_and_cache_dividend": round(before - after, 2),
        "stock_or_cache_dividend": kind,
        "max_price": 0.0,
        "min_price": 0.0,
        "open_price": 0.0,
        "reference_price": after,
    }


def _fm() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    prices = [_fm_price(d, 60.0 + i) for i, d in enumerate(_D)]
    prices.append(_fm_price(_D[0], 500.0, sid="2330"))  # 混進別檔：不補
    return prices, [_fm_div(_D[1], 61.0, 60.6)]


def _pg_prices(*rows: tuple[dt.date, float]) -> pl.DataFrame:
    return pl.DataFrame(
        [(d, c - 0.5, c + 1.0, c - 1.0, c) for d, c in rows],
        schema={
            "date": pl.Date,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
        },
        orient="row",
    )


def _pg_events(*rows: tuple[dt.date, str, float]) -> pl.DataFrame:
    return pl.DataFrame(
        list(rows),
        schema={"date": pl.Date, "event_type": pl.String, "adj_factor": pl.Float64},
        orient="row",
    )


def test_t357_ac1_only_missing_rows_up_to_the_market_latest_day() -> None:
    """已有的那天只比對；別檔、全市場還沒有行情的那天（_D[3]）都不補。"""
    prices, divs = _fm()
    plan = plan_fill("0050", _UPTO, prices, divs, _pg_prices((_D[0], 60.0)), _pg_events())
    assert plan.prices.get_column("date").to_list() == [_D[1], _D[2]]
    assert set(plan.prices.get_column("stock_id").to_list()) == {"0050"}
    assert plan.prices.get_column("volume").to_list() == [10_000_000, 10_000_000]  # 股，不是張
    assert plan.prices_checked == 1
    ev = plan.events.row(0, named=True)
    assert (ev["date"], ev["event_type"]) == (_D[1], "cash_dividend")
    assert ev["adj_factor"] == pytest.approx(60.6 / 61.0)
    assert ev["available_at"].tzinfo is not None
    # 全部都有了：什麼都不補（重跑冪等）
    full = _pg_prices(*((d, 60.0 + i) for i, d in enumerate(_D[:3])))
    again = plan_fill(
        "0050", _UPTO, prices, divs, full, _pg_events((_D[1], "cash_dividend", 60.6 / 61.0))
    )
    assert (again.prices.height, again.events.height) == (0, 0)
    assert (again.prices_checked, again.events_checked) == (3, 1)
    # 除息日在 upto 之後：也等全市場有行情再補
    late = plan_fill("0050", _D[0], prices, divs, _pg_prices(), _pg_events())
    assert late.events.height == 0 and late.prices.height == 1


def test_t357_ac2_disagreeing_existing_rows_raise_instead_of_overwriting() -> None:
    prices, divs = _fm()
    with pytest.raises(DataError, match="價格與 FinMind 不同"):
        plan_fill("0050", _UPTO, prices, divs, _pg_prices((_D[0], 61.0)), _pg_events())
    with pytest.raises(DataError, match="除權息與 FinMind 不同"):
        plan_fill(
            "0050", _UPTO, prices, divs, _pg_prices(), _pg_events((_D[1], "cash_dividend", 0.9))
        )
    with pytest.raises(DataError, match="除權息與 FinMind 不同"):
        plan_fill(
            "0050",
            _UPTO,
            prices,
            divs,
            _pg_prices(),
            _pg_events((_D[1], "stock_dividend", 60.6 / 61.0)),
        )


def test_t357_ac3_empty_finmind_raises() -> None:
    """FinMind 一列行情都沒有＝來源壞了（0050 不會整段沒交易），不當成「沒有要補的」。"""
    with pytest.raises(DataError, match="FinMind 沒有 0050 的行情"):
        plan_fill("0050", _UPTO, [], [], _pg_prices(), _pg_events())


def test_t357_ac4_runs_before_the_scorecard_everywhere() -> None:
    """每日 pipeline 與「績效」手動按鈕都先補對照、再算名單績效；雲端不略過（靜態）。"""
    from twquant.config import ServingConfig, load_config

    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert source.index('PipelineStep("對照行情"') < source.index('PipelineStep("名單績效"')
    cfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "對照行情" not in cfg.cloud.skip_pipeline_steps
    assert "0050" in cfg.cloud.keep_stocks  # 雲端修剪不能再把 0050 剪掉
    wf = (_ROOT / ".github" / "workflows" / "scorecard.yml").read_text(encoding="utf-8")
    assert wf.index("twquant benchmark-fill") < wf.index("twquant scorecard")


# ── PG（無 DSN 則 skip）────────────────────────────────────────────────────

_SCHEMA = "serving_test_benchmark_fill"


def _connect_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    from twquant.data.serving import schema as sch

    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{_SCHEMA}"')
    conn.commit()
    sch.ensure_tables(conn, (sch.spec_of("prices_daily"), sch.spec_of("corporate_events")), _SCHEMA)
    conn.commit()
    return conn


def test_t357_ac5_pg_fill_is_idempotent_and_capped_by_the_market() -> None:
    from twquant.data.serving.benchmark_fill import market_latest, read_existing, write_fill

    conn = _connect_or_skip()
    try:
        with conn.cursor() as cur:
            # 全市場行情到 _D[2]；0050 只有 _D[0]
            cur.execute(
                f'INSERT INTO "{_SCHEMA}".prices_daily (date, stock_id, open, high, low, close, '
                "volume, amount, available_at) VALUES "
                "(%s, '2330', 1, 1, 1, 1, 1, 1, now()), (%s, '0050', 59.5, 61, 59, 60, 1, 1, now())",
                (_D[2], _D[0]),
            )
        conn.commit()
        assert market_latest(conn, _SCHEMA, "0050") == _D[2]
        prices, divs = _fm()
        for _ in range(2):
            pg_p, pg_e = read_existing(conn, _SCHEMA, "0050", _D[0], _D[3])
            write_fill(conn, _SCHEMA, plan_fill("0050", _D[2], prices, divs, pg_p, pg_e))
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT date, close, volume FROM "{_SCHEMA}".prices_daily '
                "WHERE stock_id = '0050' ORDER BY date"
            )
            got = cur.fetchall()
            cur.execute(f'SELECT date, event_type, adj_factor FROM "{_SCHEMA}".corporate_events')
            events = cur.fetchall()
        # 已有的 _D[0] 沒被覆蓋（量還是 1），_D[1]、_D[2] 補上，_D[3] 不補
        assert [(d, c, v) for d, c, v in got] == [
            (_D[0], 60.0, 1),
            (_D[1], 61.0, 10_000_000),
            (_D[2], 62.0, 10_000_000),
        ]
        assert [(d, t) for d, t, _ in events] == [(_D[1], "cash_dividend")]
        assert events[0][2] == pytest.approx(60.6 / 61.0)
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        conn.commit()
        conn.close()
