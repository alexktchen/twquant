"""權證條款、日行情與摘要（T-385／D-191 方向 4）。錄下的 2026-10-16 回應（各裁成 40 列），不連網。"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.config import ServingConfig, SystemConfig, WarrantConfig, load_config
from twquant.conventions import TZ
from twquant.data.serving import schema as sch
from twquant.data.sources import warrant as wr
from twquant.errors import DataError
from twquant.reporting import warrant_summary as ws

_ROOT = Path(__file__).resolve().parent.parent
_FIX = _ROOT / "tests" / "fixtures" / "http" / "warrant"
DAY = dt.date(2026, 10, 16)
AT = dt.datetime(2026, 10, 18, 2, 0, tzinfo=TZ)
CFG = WarrantConfig()


def _json(name: str) -> Any:
    return json.loads((_FIX / name).read_text(encoding="utf-8"))


def _terms() -> pl.DataFrame:
    return wr.terms_frame(
        wr.parse_terms(_json("twse_t187ap37_L.json"), market="twse", fetched_at=AT)
        + wr.parse_terms(_json("tpex_t187ap37_O.json"), market="tpex", fetched_at=AT)
    )


def _daily() -> pl.DataFrame:
    rows = wr.parse_twse_daily(
        _json("twse_MI_INDEX_0999_20261016.json"), kind="call", day=DAY, fetched_at=AT
    )
    rows += wr.parse_twse_daily(
        _json("twse_MI_INDEX_0999P_20261016.json"), kind="put", day=DAY, fetched_at=AT
    )
    rows += wr.parse_tpex_daily(
        _json("tpex_warrant_daily_quts_20261016.json"), day=DAY, fetched_at=AT
    )
    return wr.daily_frame(rows)


# ── AC-1：條款解析——民國日期、配發數量÷1000＝ratio、認購認售、兩市場同欄位；與 PG spec 同名同序 ──
def test_ac1_terms_parse_and_match_spec() -> None:
    df = _terms()
    assert list(df.columns) == list(wr.TERMS_SCHEMA) == list(sch.WARRANT_TERMS_SPEC.column_names)
    r = df.filter(pl.col("warrant_id") == "030001").to_dicts()[0]
    assert (r["kind"], r["category"], r["underlying_name"], r["market"]) == (
        "call",
        "一般型",
        "友達",
        "twse",
    )
    assert (r["expiry_date"], r["last_trade_date"]) == (dt.date(2027, 10, 4), dt.date(2027, 9, 30))
    assert (r["strike"], r["ratio"], r["cap"], r["issued_k_units"]) == (53.0, 0.065, 0.0, 5000.0)
    assert r["underlying_id"] is None and r["valid_from"] == dt.date(2026, 10, 16)
    assert set(df["market"].unique().to_list()) == {"twse", "tpex"}
    assert df.height == 80


# ── AC-2：條款只有變了才算新版本 ────────────────────────────────────────────────
def test_ac2_new_versions_only_when_terms_change() -> None:
    fresh = _terms()
    day = dt.date(2026, 10, 20)
    assert wr.new_versions(fresh, fresh.clear(), day).height == fresh.height  # 表空：全部是新版本
    same = wr.new_versions(fresh, fresh, day)
    assert same.height == 0  # 一字沒變：沒有新版本
    latest = fresh.with_columns(
        pl.when(pl.col("warrant_id") == "030001")
        .then(pl.lit(50.0))
        .otherwise(pl.col("strike"))
        .alias("strike")
    ).filter(pl.col("warrant_id") != "030002")  # 030001 履約價舊版不同；030002 PG 沒看過
    nv = wr.new_versions(fresh, latest, day)
    assert set(nv["warrant_id"].to_list()) == {"030001", "030002"}
    assert nv["valid_from"].unique().to_list() == [day]
    # 只有名稱變了（不在版本欄位）不算
    renamed = fresh.with_columns(pl.lit("改名").alias("underlying_name"))
    assert wr.new_versions(fresh, renamed, day).height == 0


# ── AC-3：日行情解析——兩市場、認購認售、休市 0 列、「--」是 null、櫃買不是當天 raise ────────
def test_ac3_daily_parse_both_markets() -> None:
    df = _daily()
    assert list(df.columns) == list(wr.DAILY_SCHEMA) == list(sch.WARRANT_DAILY_SPEC.column_names)
    assert sch.WARRANT_DAILY_SPEC.pk == ("warrant_id", "date")
    assert set(df["kind"].unique().to_list()) == {"call", "put"}
    r = df.filter(pl.col("warrant_id") == "030001").to_dicts()[0]
    assert (r["underlying_id"], r["close"], r["volume_units"], r["trade_value"], r["trades"]) == (
        "2409",
        0.69,
        133000.0,
        94780.0,
        12.0,
    )
    assert (r["last_bid"], r["last_ask"], r["underlying_close"]) == (0.70, 0.71, 35.9)
    put_idx = df.filter(pl.col("underlying_id") == "IX0001").to_dicts()[
        0
    ]  # 指數標的照留，摘要才過濾
    assert put_idx["kind"] == "put" and put_idx["close"] is None  # 「--」→ null
    tpex = df.filter(pl.col("market") == "tpex")
    assert tpex.height == 40 and tpex["last_bid"].is_null().all()
    assert tpex.filter(pl.col("warrant_id") == "72622U")["kind"][0] == "put"


def test_ac3_closed_day_and_wrong_tpex_date() -> None:
    closed = {"stat": "OK", "date": "20261017", "tables": [{"fields": [], "data": []}]}
    assert wr.parse_twse_daily(closed, kind="call", day=dt.date(2026, 10, 17), fetched_at=AT) == []
    with pytest.raises(DataError, match="stat"):
        wr.parse_twse_daily({"stat": "很抱歉"}, kind="call", day=DAY, fetched_at=AT)
    with pytest.raises(DataError, match="只給最新一天"):
        wr.parse_tpex_daily(
            _json("tpex_warrant_daily_quts_20261016.json"), day=dt.date(2026, 10, 15), fetched_at=AT
        )
    bad = _json("tpex_warrant_daily_quts_20261016.json")
    bad[0] = {**bad[0], "Name": "看不出方向"}
    with pytest.raises(DataError, match="分不出認購認售"):
        wr.parse_tpex_daily(bad, day=DAY, fetched_at=AT)
    rows = wr.parse_twse_daily(
        _json("twse_MI_INDEX_0999_20261016.json"), kind="call", day=DAY, fetched_at=AT
    )
    with pytest.raises(DataError, match="重複"):
        wr.daily_frame(rows + rows[:1])


# ── AC-4：Black-Scholes 與隱含波動反解互為反函數；無套利界外 → None ─────────────────
def test_ac4_implied_vol_roundtrip_and_bounds() -> None:
    s, k, t, r = 100.0, 105.0, 60 / 365, 0.016
    for kind in ("call", "put"):
        for sigma in (0.2, 0.45, 0.9):
            px = ws.bs_price(kind, s, k, t, r, sigma)
            iv = ws.implied_vol(kind, px, s, k, t, r)
            assert iv is not None and math.isclose(iv, sigma, rel_tol=1e-6)
    assert ws.implied_vol("call", 5.0, s, 90.0, t, r) is None  # 低於內含價值（無套利下界）
    assert ws.implied_vol("call", 99.0, s, k, t, r) is None  # 高於上界
    assert ws.implied_vol("call", 1.0, s, k, 0.0, r) is None
    with pytest.raises(ValueError):
        ws.bs_price("straddle", s, k, t, r, 0.2)


# ── AC-5：摘要——每檔四碼標的一列、認購認售金額、對標的成交金額比、價平 IV 的挑法 ────────
def test_ac5_summary_per_underlying() -> None:
    day = dt.date(2026, 10, 16)
    s = 100.0
    sigma = 0.35
    strike_atm, strike_far = 102.0, 130.0
    t_days = 60
    ratio = 0.1
    px_atm = ws.bs_price("call", s, strike_atm, t_days / 365, CFG.risk_free_rate, sigma) * ratio
    terms = pl.DataFrame(
        {
            "valid_from": [day] * 4,
            "warrant_id": ["W1", "W2", "W3", "W4"],
            "market": ["twse"] * 4,
            "underlying_id": [None] * 4,
            "underlying_name": ["甲"] * 4,
            "kind": ["call", "call", "put", "call"],
            "style": ["5"] * 4,
            "category": ["一般型", "一般型", "一般型", "一般型"],
            "expiry_date": [
                day + dt.timedelta(days=t_days),
                day + dt.timedelta(days=t_days),
                day + dt.timedelta(days=t_days),
                day + dt.timedelta(days=10),
            ],
            "last_trade_date": [day + dt.timedelta(days=t_days - 2)] * 4,
            "strike": [strike_atm, strike_far, strike_atm, strike_atm],
            "ratio": [ratio] * 4,
            "cap": [0.0] * 4,
            "floor": [0.0] * 4,
            "issued_k_units": [5000.0] * 4,
            "available_at": [AT] * 4,
        },
        schema=wr.TERMS_SCHEMA,
    )
    daily = pl.DataFrame(
        {
            "date": [day] * 5,
            "warrant_id": ["W1", "W2", "W3", "W4", "E1"],
            "market": ["twse"] * 5,
            "underlying_id": ["1234", "1234", "1234", "1234", "006208"],  # E1 是 ETF 標的，不進摘要
            "kind": ["call", "call", "put", "call", "call"],
            "close": [px_atm, 0.5, None, 1.0, 1.0],
            "volume_units": [1000.0, 2000.0, 0.0, 10.0, 1.0],
            "trade_value": [3000.0, 1000.0, 0.0, 10.0, 1.0],
            "trades": [3.0, 2.0, 0.0, 1.0, 1.0],
            "last_bid": [px_atm - 0.01, None, None, None, None],
            "last_ask": [px_atm + 0.01, None, None, None, None],
            "underlying_close": [s] * 5,
            "available_at": [AT] * 5,
        },
        schema=wr.DAILY_SCHEMA,
    )
    amounts = pl.DataFrame({"stock_id": ["1234"], "amount": [100_000.0]})
    out = ws.summarize(daily, terms, amounts, day, CFG, AT)
    assert (
        list(out.columns) == list(ws.SUMMARY_SCHEMA) == list(sch.WARRANT_SUMMARY_SPEC.column_names)
    )
    assert out.height == 1 and out["underlying_id"][0] == "1234"
    r = out.to_dicts()[0]
    assert (r["n_call"], r["n_put"], r["call_value"], r["put_value"]) == (3, 1, 4010.0, 0.0)
    assert math.isclose(r["value_ratio"], 4010.0 / 100_000.0)
    # 價平那檔（W1，距到期 60 天、|K/S−1|=2%）用買賣中價反解 → 回到 35%；W2 太價外、W4 距到期太近都不選
    assert r["iv_call_days"] == 60 and math.isclose(r["iv_call"], sigma, rel_tol=1e-3)
    assert r["iv_put"] is None and r["iv_put_days"] is None  # 認售沒成交、沒買賣價 → 算不出來
    assert ws.summarize(daily.clear(), terms, amounts, day, CFG, AT).is_empty()
    with pytest.raises(ValueError, match="時區"):
        ws.summarize(daily, terms, amounts, day, CFG, dt.datetime(2026, 10, 16))


# ── AC-6：配置與每日 pipeline（地端雲端都跑、排在供應層之後；雲端窗口） ─────────────────
def test_ac6_config_and_pipeline_wiring() -> None:
    scfg = load_config(_ROOT / "configs" / "system.yaml", SystemConfig)
    assert scfg.warrants.iv_min_days < scfg.warrants.iv_max_days
    assert 0 < scfg.warrants.risk_free_rate < 0.1
    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert 'PipelineStep("權證"' in source
    assert source.index('PipelineStep("供應層"') < source.index('PipelineStep("權證"')
    assert '@app.command(name="warrants")' in source
    serving = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "權證" not in serving.cloud.skip_pipeline_steps
    assert (
        serving.cloud.windows["warrant_daily"] >= 1
        and serving.cloud.windows["warrant_summary_daily"] >= 20
    )
    assert "warrant_terms" not in serving.cloud.windows  # 沒有 date 欄，prune 會炸；版本表全留
