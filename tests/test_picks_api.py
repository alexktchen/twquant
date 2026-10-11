"""T-332：雲端網站的每日名單 API（純函數＋PG 整合，無 DSN 則整合部分 skip）。"""

from __future__ import annotations

import datetime as dt
import os
from typing import Any

import pytest

from twquant.config import ServingConfig
from twquant.conventions import TZ
from twquant.errors import DataError
from twquant.monitoring.picks_api import annotate, recent_flags

_SCHEMA = "serving_test_picks_api"
_D1, _D2, _D3 = dt.date(2026, 10, 6), dt.date(2026, 10, 7), dt.date(2026, 10, 8)


def test_t332_ac5_streak_and_new() -> None:
    """連續上榜含今天、中斷即停；新進＝前一個有名單的日子不在名單裡。"""
    marks = annotate(["A", "B", "C"], [["A", "B"], ["A", "X"], ["A"]])
    assert marks == {"A": (4, False), "B": (2, False), "C": (1, True)}
    assert annotate(["A"], []) == {"A": (1, True)}  # 第一天：全部是新進


def test_t332_ac10_recent_flags_oldest_first_and_unscored_days() -> None:
    """近 N 日方格：舊到新；那天這條線沒名單（模型沒打分）是 None，不當成「不在榜」。"""
    window = [["A"], None, ["A", "B"]]  # 由近到遠
    assert recent_flags("A", window) == [True, None, True]
    assert recent_flags("B", window) == [True, None, False]


def _cfg_or_skip() -> tuple[ServingConfig, Any]:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    from twquant.data.serving.schema import ensure_schema

    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
    conn.commit()
    ensure_schema(conn, _SCHEMA)
    at = dt.datetime(2026, 10, 8, 20, 30, tzinfo=TZ)
    with conn.cursor() as cur:
        for d, close in ((_D1, 100.0), (_D2, 110.0), (_D3, 99.0)):
            cur.execute(
                f'INSERT INTO "{_SCHEMA}".prices_daily (date, stock_id, open, high, low, close, '
                "volume, amount, available_at) VALUES (%s, '2330', 1, 1, 1, %s, 1, 1, %s)",
                (d, close, at),
            )
        cur.execute(
            f'INSERT INTO "{_SCHEMA}".stock_meta (stock_id, name, market, industry) '
            "VALUES ('2330', '台積電', 'TWSE', '半導體業')"
        )
        for d, line, sids in (
            (_D1, "QUIET", ["2330"]),
            (_D2, "QUIET", ["2330", "1101"]),
            (_D3, "QUIET", ["2330", "2317"]),
            (_D3, "CORE", ["2317"]),
        ):
            for rank, sid in enumerate(sids, start=1):
                cur.execute(
                    f'INSERT INTO "{_SCHEMA}".daily_picks VALUES (%s, %s, %s, %s, %s, %s, %s)',
                    (d, line, rank, sid, 0.5 if line == "QUIET" else None, "m", at),
                )
    conn.commit()
    return ServingConfig(enabled=True, pg_schema=_SCHEMA), conn


def _drop(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
    conn.commit()
    conn.close()


def test_t332_ac6_picks_payload_reads_pg() -> None:
    cfg, conn = _cfg_or_skip()
    from twquant.monitoring.picks_api import pick_dates, picks_payload, stock_pick_days

    try:
        names = {"CORE": "大盤精選", "QUIET": "潛伏飆股"}
        out = picks_payload(cfg, None, names)
        assert out["date"] == "2026-10-08"
        assert [ln["code"] for ln in out["lines"]] == ["CORE", "QUIET"]  # 依 pick_board 順序
        # D-174：登記過但當天沒有股票的線也要列出，名單為空
        out3 = picks_payload(cfg, None, {**names, "QUIETV": "高分爆量（驗證中）"})
        assert [ln["code"] for ln in out3["lines"]] == ["CORE", "QUIET", "QUIETV"]
        assert out3["lines"][2]["picks"] == []
        quiet = out["lines"][1]["picks"]
        top = quiet[0]
        assert (top["sid"], top["name"], top["close"], top["streak"], top["new"]) == (
            "2330",
            "台積電",
            99.0,
            3,
            False,
        )
        assert top["chg"] == pytest.approx(99.0 / 110.0 - 1.0)
        assert out["window"] == ["2026-10-06", "2026-10-07", "2026-10-08"]
        assert top["recent"] == [True, True, True]
        assert top["chg6"] == pytest.approx(99.0 / 100.0 - 1.0)  # 窗口第一天 10-06 收盤 100
        core_top = out["lines"][0]["picks"][0]
        assert core_top["recent"] == [None, None, True]  # CORE 只有 10-08 有名單
        assert (quiet[1]["sid"], quiet[1]["streak"], quiet[1]["new"], quiet[1]["close"]) == (
            "2317",
            1,
            True,
            None,  # 沒有行情就是 None，不補 0
        )
        assert quiet[1]["recent"] == [False, False, True] and quiet[1]["chg6"] is None
        older = picks_payload(cfg, _D2, names)
        older_quiet = next(ln for ln in older["lines"] if ln["code"] == "QUIET")
        assert older["date"] == "2026-10-07" and older_quiet["picks"][0]["streak"] == 2
        # D-174：那天 CORE 沒有名單也照樣列出（空的），不是整條線消失
        assert next(ln for ln in older["lines"] if ln["code"] == "CORE")["picks"] == []
        with pytest.raises(DataError, match="沒有名單"):
            picks_payload(cfg, dt.date(2026, 10, 9), names)
        assert pick_dates(cfg, 2) == ["2026-10-08", "2026-10-07"]
        assert stock_pick_days(cfg, "2330")["days"] == {
            "QUIET": ["2026-10-06", "2026-10-07", "2026-10-08"]
        }
        with pytest.raises(DataError, match="非法"):
            stock_pick_days(cfg, "2330;drop")
    finally:
        _drop(conn)


def test_t332_ac7_picks_routes(monkeypatch: pytest.MonkeyPatch) -> None:
    """端點層：最新／指定日期、沒有名單 404、格式錯 400（直接呼叫路由函式，同 test_api）。"""
    cfg, conn = _cfg_or_skip()
    from fastapi import HTTPException

    from twquant.monitoring import api, stock_api

    monkeypatch.setattr(stock_api, "serving_cfg", lambda: cfg)
    try:
        assert api.get_picks(None)["date"] == "2026-10-08"
        assert api.get_picks("2026-10-07")["date"] == "2026-10-07"
        for bad, code in (("2026-10-09", 404), ("10/08", 400)):
            with pytest.raises(HTTPException) as exc:
                api.get_picks(bad)
            assert exc.value.status_code == code
        dates = api.get_pick_dates(250)["dates"]
        assert isinstance(dates, list) and dates[0] == "2026-10-08"
        days = api.get_stock_picks("2330")["days"]
        assert isinstance(days, dict) and days["QUIET"][-1] == "2026-10-08"
    finally:
        _drop(conn)
