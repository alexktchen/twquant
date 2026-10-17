"""T-374／D-188：大盤體檢的每日一列（純運算）。"""

from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.config import ServingConfig, SystemConfig, load_config
from twquant.data.serving import schema as sch
from twquant.errors import DataError
from twquant.reporting.market_daily import MarketDaily, _streak, market_daily

_ROOT = Path(__file__).resolve().parent.parent
D = [dt.date(2026, 10, 1) + dt.timedelta(days=k) for k in range(5)]  # 5 個「交易日」
PAT = r"^[1-9][0-9]{3}$"


def _prices() -> pl.DataFrame:
    rows: list[tuple[dt.date, str, float, float, float | None, float | None]] = []
    # 2330：一路漲，最後一天漲停且創新高；2317：一路跌，最後一天跌停且創新低；1101：平盤；0050（ETF）不算
    for i, d in enumerate(D):
        rows.append((d, "2330", 100.0 + i, 1e9, 100.0 + i if i == 4 else None, None))
        rows.append((d, "2317", 100.0 - i, 2e9, None, 100.0 - i if i == 4 else None))
        rows.append((d, "1101", 50.0, 5e8, 55.0, 45.0))
        rows.append((d, "0050", 200.0 + i, 9e9, None, None))
    return pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "close": pl.Float64,
            "amount": pl.Float64,
            "limit_up_price": pl.Float64,
            "limit_down_price": pl.Float64,
        },
        orient="row",
    )


def _index() -> pl.DataFrame:
    rows = [(d, "TAIEX", 20000.0 + 100 * i) for i, d in enumerate(D)] + [
        (d, "TPEX", 250.0) for d in D
    ]
    return pl.DataFrame(
        rows, schema={"date": pl.Date, "index_id": pl.String, "close": pl.Float64}, orient="row"
    )


def _inst() -> pl.DataFrame:
    # 外資：前兩天賣、後三天買（連 3 天）；投信都買 1000 股；自營 0
    rows = []
    for i, d in enumerate(D):
        f = 10_000.0 if i >= 2 else -10_000.0
        rows.append((d, "2330", f, 1000.0, 0.0))
        rows.append((d, "0050", 99_999.0, 0.0, 0.0))  # ETF 不算
    return pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "foreign_net": pl.Float64,
            "trust_net": pl.Float64,
            "dealer_net": pl.Float64,
        },
        orient="row",
    )


def _margin() -> pl.DataFrame:
    rows = [(D[3], "2330", 1000.0, 10.0), (D[4], "2330", 1100.0, 12.0), (D[4], "0050", 5.0, 0.0)]
    return pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "margin_balance": pl.Float64,
            "short_balance": pl.Float64,
        },
        orient="row",
    )


def _run(day: dt.date = D[4], avg: int = 4, window: int = 4) -> MarketDaily:
    return market_daily(
        day,
        _prices(),
        _index(),
        _inst(),
        _margin(),
        stock_pattern=PAT,
        avg_sessions=avg,
        high_low_window=window,
    )


# ── AC-1：漲跌家數、漲停跌停、新高新低、量能只算四碼普通股 ─────────────────────
def test_ac1_breadth_limits_highs_lows_and_turnover() -> None:
    r = _run()
    assert (r.up, r.down, r.flat) == (1, 1, 1)
    assert (r.limit_up, r.limit_down) == (1, 1)
    assert (r.new_high, r.new_low) == (1, 1)
    assert r.amount == pytest.approx(1e9 + 2e9 + 5e8)  # 0050 的 9e9 不算
    assert r.amount_avg == pytest.approx(3.5e9) and r.amount_ratio == pytest.approx(1.0)


def test_ac1_windows_too_short_give_none_not_zero() -> None:
    r = _run(avg=10, window=10)
    assert r.amount_avg is None and r.amount_ratio is None
    assert (r.new_high, r.new_low) == (None, None)  # 視窗內每檔天數不足 → 不算新高新低


# ── AC-2：指數漲跌、三大法人折 TWD、外資連續天數、融資變化 ───────────────────────
def test_ac2_index_institutional_and_margin() -> None:
    r = _run()
    assert r.taiex_close == 20400.0 and r.taiex_chg == pytest.approx(20400 / 20300 - 1)
    assert r.otc_close == 250.0 and r.otc_chg == pytest.approx(0.0)
    assert r.foreign_net == pytest.approx(10_000 * 104.0)  # 股數 × 當天收盤；0050 不算
    assert r.trust_net == pytest.approx(1000 * 104.0) and r.dealer_net == 0.0
    assert r.foreign_streak == 3
    # 融資：股數 × 當天收盤（10-04 收 104、10-03 收 103）
    assert r.margin_balance == 1100.0 * 104 and r.margin_delta == 1100.0 * 104 - 1000.0 * 103
    assert r.short_balance == 12.0 * 104


def test_ac2_streak_counts_consecutive_same_sign_from_the_latest_day() -> None:
    assert _streak([5.0, 3.0, -1.0, 2.0]) == 2
    assert _streak([-5.0, -3.0, -1.0]) == -3
    assert _streak([0.0, 3.0]) == 0 and _streak([]) == 0


def test_ac2_missing_day_raises_not_zero() -> None:
    with pytest.raises(DataError, match="沒有行情"):
        _run(day=dt.date(2026, 10, 9))


# ── AC-3：PG 欄位與 dataclass 同名；pipeline 有這一步、雲端不略過；參數在 configs ─────
def test_ac3_spec_matches_dataclass_and_pipeline_runs_it() -> None:
    names = [f.name for f in dataclasses.fields(MarketDaily)]
    assert list(sch.MARKET_DAILY_SPEC.column_names) == [*names, "created_at"]
    assert sch.MARKET_DAILY_SPEC.pk == ("date",)
    cfg = load_config(_ROOT / "configs" / "system.yaml", SystemConfig)
    assert cfg.market_daily.avg_sessions == 20 and cfg.market_daily.high_low_window == 20
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "大盤體檢" not in scfg.cloud.skip_pipeline_steps
    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert source.index('PipelineStep("族群資金"') < source.index('PipelineStep("大盤體檢"')


def test_ac1_limit_prices_fall_back_to_prev_close_proxy() -> None:
    px = _prices().with_columns(
        pl.lit(None, dtype=pl.Float64).alias("limit_up_price"),
        pl.lit(None, dtype=pl.Float64).alias("limit_down_price"),
    )
    # 2330 最後一天從 103 漲到 113（> 103×1.1＝113.3 取到 0.5 檔＝113.0）→ 漲停；2317 從 97 跌到 87.5（< 97×0.9＝87.3 進位 87.3）→ 不算跌停
    px = px.with_columns(
        pl.when((pl.col("stock_id") == "2330") & (pl.col("date") == D[4]))
        .then(113.0)
        .when((pl.col("stock_id") == "2317") & (pl.col("date") == D[4]))
        .then(87.5)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    r = market_daily(
        D[4], px, _index(), _inst(), _margin(), stock_pattern=PAT, avg_sessions=4, high_low_window=4
    )
    assert (r.limit_up, r.limit_down) == (1, 0)
