"""T-040 / SPEC-007 §6：策略選股池。純函數測試（不碰 PG，CI 可跑）。

fixture 用縮小窗（conc=2、accum=3、ma=2/3、amt=2）便於手算；宇宙兩檔時
pct=rank(average)/count ∈ {0.5, 1.0}，pct_hi=0.9 取前一檔、pct_lo=0.5 取後一檔。
"""

from __future__ import annotations

import datetime as dt
from typing import cast

import polars as pl
import pytest

import twquant.monitoring.screener as screener
from twquant.config import ScreenerConfig, ScreenerPool, ScreenerWindows

D = [dt.date(2026, 6, d) for d in (1, 2, 3, 4, 5, 8)]

CFG = ScreenerConfig(
    liquidity_floor_twd=1_000_000.0,
    windows=ScreenerWindows(conc=2, accum=3, ma_fast=2, ma_slow=3, amt=2),
    pct_hi=0.9,
    pct_lo=0.5,
    yoy_min=0.2,
    streak_days=5,
    pools=(
        ScreenerPool(key="main_accum", name="主力長線吸籌", rule="r"),
        ScreenerPool(key="chip_revenue", name="主力集中×營收", rule="r"),
        ScreenerPool(key="trend_revenue", name="趨勢×營收", rule="r"),
        ScreenerPool(key="triple", name="三面向共振", rule="r"),
        ScreenerPool(key="main_distrib", name="主力出貨警示", rule="r"),
    ),
)


def _prices() -> pl.DataFrame:
    """1111 收盤走升（多頭排列）、2222 走跌；0050 非四碼、3333 流動性不足。"""
    rows = []
    for i, d in enumerate(D):
        rows += [
            {
                "date": d,
                "stock_id": "1111",
                "close": 100.0 + i,
                "volume": 1000,
                "amount": 2_000_000.0,
            },
            {
                "date": d,
                "stock_id": "2222",
                "close": 50.0 - i,
                "volume": 2000,
                "amount": 3_000_000.0,
            },
            {"date": d, "stock_id": "0050", "close": 10.0, "volume": 500, "amount": 5_000_000.0},
            {"date": d, "stock_id": "3333", "close": 20.0, "volume": 100, "amount": 200_000.0},
        ]
    return pl.DataFrame(rows)


def _broker() -> pl.DataFrame:
    """1111 主力持續買超、2222 持續賣超（0050/3333 也給值，驗證宇宙過濾）。"""
    rows = []
    for d in D:
        rows += [
            {"date": d, "stock_id": "1111", "net": 300},
            {"date": d, "stock_id": "2222", "net": -400},
            {"date": d, "stock_id": "0050", "net": 900},
            {"date": d, "stock_id": "3333", "net": 900},
        ]
    return pl.DataFrame(rows)


def _fund() -> pl.DataFrame:
    """1111 只在首日有 YoY=0.3（驗證前向沿用到 as_of，物化落後行情日的常態）；
    2222 無 YoY 資料（缺值不入需 YoY 的池）。"""
    return pl.DataFrame([{"date": D[0], "stock_id": "1111", "yoy": 0.3}])


def _flags() -> pl.DataFrame:
    m = screener.daily_metrics(_prices(), _broker(), _fund(), CFG)
    return screener.assign_pools(m, CFG)


def test_ac7_universe_filter() -> None:
    f = _flags()
    ids = set(f["stock_id"].unique().to_list())
    assert ids == {"1111", "2222"}  # 0050 非四碼、3333 均額 20 萬 < 下限 100 萬
    payload = screener.build_payload(f, CFG, meta={}, n=30)
    assert payload["universe_n"] == 2
    for pool in payload["pools"]:
        for mrow in pool["members"]:
            assert mrow["stock_id"] in ids


def test_ac8_metrics_match_hand_calc() -> None:
    f = _flags().filter((pl.col("date") == D[-1]) & (pl.col("stock_id") == "1111"))
    r = f.row(0, named=True)
    assert r["conc"] == pytest.approx((300 + 300) / (1000 + 1000), abs=1e-9)
    assert r["accum_ratio"] == pytest.approx(300 * 3 / (1000 * 3), abs=1e-9)
    assert r["ma_fast"] == pytest.approx((105 + 104) / 2, abs=1e-9)
    assert r["ma_slow"] == pytest.approx((105 + 104 + 103) / 3, abs=1e-9)
    # 兩檔宇宙：1111 買超佔量比較高 → rank(average)/count = 2/2
    assert r["pct_accum"] == pytest.approx(1.0, abs=1e-9)
    assert r["pct_conc"] == pytest.approx(1.0, abs=1e-9)


def test_ac9_pool_assignment() -> None:
    f = _flags().filter(pl.col("date") == D[-1])
    one = f.filter(pl.col("stock_id") == "1111").row(0, named=True)
    two = f.filter(pl.col("stock_id") == "2222").row(0, named=True)
    # 1111：買超前段 + 多頭排列 + YoY 0.3 → 四個正向池全中、不在警示池
    assert one["in_main_accum"] and one["in_chip_revenue"]
    assert one["in_trend_revenue"] and one["in_triple"]
    assert not one["in_main_distrib"]
    # 2222：賣超後段 → 警示池；YoY 缺值 → 不入需 YoY 的池
    assert two["in_main_distrib"] and not two["in_main_accum"]
    assert not two["in_chip_revenue"] and not two["in_trend_revenue"] and not two["in_triple"]


def test_ac10_streak_resets_on_gap() -> None:
    flags = pl.DataFrame(
        {
            "date": D,
            "stock_id": ["1111"] * 6,
            "in_x": [True, True, False, True, True, True],
        }
    )
    out = screener.add_streaks(flags, ["x"])
    got = out.sort("date")["streak_x"].to_list()
    assert got == [1, 2, 0, 1, 2, 3]  # 中斷歸零重計


def test_ac11_payload_shape_sort_and_cap() -> None:
    payload = screener.build_payload(_flags(), CFG, meta={"1111": "甲公司"}, n=1)
    assert payload["source"] == "pg" and payload["as_of"] == D[-1].isoformat()
    keys = [p["key"] for p in payload["pools"]]
    assert keys == [p.key for p in CFG.pools]
    accum = next(p for p in payload["pools"] if p["key"] == "main_accum")
    assert accum["total"] == 1 and len(accum["members"]) <= 1
    m = accum["members"][0]
    assert m["stock_id"] == "1111" and m["name"] == "甲公司"
    assert m["streak"] >= 1 and isinstance(m["close"], float)
    # n=1 截斷仍回報截斷前總數
    trend = next(p for p in payload["pools"] if p["key"] == "trend_revenue")
    assert trend["total"] >= len(trend["members"])


def test_config_yaml_loads() -> None:
    cfg = screener.screener_cfg()
    assert len(cfg.pools) == 5 and cfg.yoy_min > 0


# ── T-041 快照與回溯（純函數部分；PG 寫讀整合另以 DSN 閘門）─────────────────


def test_ac14_snapshot_frame_matches_live_members() -> None:
    flags = _flags()
    live = screener.build_payload(flags, CFG, meta={}, n=100)
    frame = screener.snapshot_frame(flags, CFG, D[-1], D[-1])
    assert set(frame["date"].to_list()) == {D[-1]}
    for pool in live["pools"]:
        got = set(frame.filter(pl.col("pool") == pool["key"])["stock_id"].to_list())
        want = {m["stock_id"] for m in pool["members"]}
        assert got == want, f"{pool['key']}: {got} != {want}"
    # streak 封頂 streak_days
    assert cast("int", frame["streak"].max() or 0) <= CFG.streak_days


def test_ac13_payload_from_rows_same_shape_as_live() -> None:
    flags = _flags()
    live = screener.build_payload(flags, CFG, meta={"1111": "甲公司"}, n=30)
    frame = screener.snapshot_frame(flags, CFG, D[-1], D[-1])
    rows = frame.to_dicts()
    rebuilt = screener.payload_from_rows(rows, CFG, {"1111": "甲公司"}, 30, D[-1])
    assert rebuilt["as_of"] == live["as_of"]
    assert rebuilt["universe_n"] == live["universe_n"]
    for lp, rp in zip(live["pools"], rebuilt["pools"], strict=True):
        assert lp["key"] == rp["key"] and lp["total"] == rp["total"]
        assert [m["stock_id"] for m in lp["members"]] == [m["stock_id"] for m in rp["members"]]
        if lp["members"]:
            assert lp["members"][0] == rp["members"][0]  # 欄位級一致（round 同精度）


def test_ac13_bad_date_raises() -> None:
    from twquant.config import ServingConfig
    from twquant.errors import DataError

    with pytest.raises(DataError, match="日期格式錯誤"):
        screener.payload_for(None, ServingConfig(pg_schema="serving"), 30, "2026-13-99")


def test_ac12_write_snapshot_idempotent_pg() -> None:
    """需真 PG（TWQUANT_PG_DSN）；無則 skip。獨立 test schema，跑完 DROP。"""
    import os

    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    import twquant.data.serving.schema as sch

    test_schema = "serving_test_t041"
    frame = screener.snapshot_frame(_flags(), CFG, D[-1], D[-1])
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        sch.ensure_schema(conn, test_schema)
        with conn.cursor() as cur:
            screener.write_snapshot(cur, test_schema, frame)
            cur.execute(f'SELECT count(*), max(created_at) FROM "{test_schema}".screener_daily')
            n1, ts1 = cur.fetchone()
            screener.write_snapshot(cur, test_schema, frame)  # 重跑
            cur.execute(f'SELECT count(*), max(created_at) FROM "{test_schema}".screener_daily')
            n2, ts2 = cur.fetchone()
        conn.commit()
        assert n1 == frame.height and n2 == n1  # 列數不變
        assert ts1 == ts2  # 首錄保留（created_at 未被覆寫）
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        conn.close()
