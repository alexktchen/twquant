"""T-349／T-350／D-176：名單績效——模擬帳戶（A）與逐筆（B）的彙整、即時／事後補的分段、0050 對照。"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from tests.conftest import write_stock_meta
from tests.test_backtest_slots import BASE, N_DAYS, START, _cfg, _close
from twquant.backtest import costs as ct
from twquant.backtest.engine import SplitAdjustment, Trade, run_backtest
from twquant.backtest.list_trades import new_entries
from twquant.config import load_config
from twquant.conventions import TZ
from twquant.data import calendar as cal
from twquant.data import schemas
from twquant.data.serving.scorecard_store import PublishedPick
from twquant.data.warehouse import Warehouse
from twquant.reporting.pick_board import BoardStrategy
from twquant.reporting.scorecard import (
    ScorecardConfig,
    build_scorecards,
    ledger_rows,
    live_since,
    nav_summary,
    publication_flags,
    trade_stats,
)
from twquant.strategy.build import build_targets
from twquant.strategy.config import StrategyConfig
from twquant.strategy.slots import slot_candidates

_START = BASE + dt.timedelta(days=START)
_END = BASE + dt.timedelta(days=N_DAYS - 1)
_D = [dt.date(2026, 10, 5) + dt.timedelta(days=i) for i in range(12)]  # 10-05 是週一
_CUT = dt.time(9, 0)


def _at(day: dt.date, hour: int) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, tzinfo=TZ)


# ── 即時／事後補 ────────────────────────────────────────────────────────────
def test_ac6_live_means_written_before_the_next_session_opens() -> None:
    sessions = [_D[0], _D[1], _D[2], _D[3]]
    written = {
        _D[0]: _at(_D[5], 10),  # 好幾天後才補 → 事後補
        _D[1]: _at(_D[2], 8),  # 隔天 08:00 寫好 → 即時（09:00 開盤前）
        _D[2]: _at(_D[3], 9),  # 隔天 09:00 整才寫好 → 來不及開盤買，事後補
        _D[3]: _at(_D[3], 18),
    }
    flags = publication_flags(written, written, sessions, _CUT, allow_empty=False)
    assert flags == {_D[0]: False, _D[1]: True, _D[2]: False, _D[3]: True}
    assert live_since(flags) == _D[3]
    assert live_since({_D[0]: True, _D[1]: False}) is None
    # 最後一天是週五：下一個開盤日是週一
    fri = dt.date(2026, 10, 9)
    assert publication_flags(
        {fri: _at(fri + dt.timedelta(days=3), 8)}, {}, [fri], _CUT, allow_empty=False
    ) == {fri: True}
    with pytest.raises(ValueError, match="時區"):
        publication_flags({fri: dt.datetime(2026, 10, 9, 18)}, {}, [fri], _CUT, allow_empty=False)


def test_ac6_empty_days_follow_the_last_day_with_stocks() -> None:
    """高分爆量沒股票的日子沒有自己的寫入時間：前一個有股票的日子是事後補，就不能算即時。"""
    sessions = _D[:5]
    day_written = {d: _at(d, 18) for d in sessions}  # 其他線每天都即時
    line = {_D[1]: _at(_D[9], 12), _D[3]: _at(_D[3], 18)}  # 這條線第 1 天是後來補的
    flags = publication_flags(line, day_written, sessions, _CUT, allow_empty=True)
    assert flags == {_D[0]: False, _D[1]: False, _D[2]: False, _D[3]: True, _D[4]: True}
    # 不允許空名單的線：沒股票＝沒公布，不在結果裡
    assert set(publication_flags(line, day_written, sessions, _CUT, allow_empty=False)) == {
        _D[1],
        _D[3],
    }


# ── 統計 ────────────────────────────────────────────────────────────────────
def test_ac7_trade_stats() -> None:
    s = trade_stats([0.10, -0.20, 0.05, -0.15, 0.0], big_loss=-0.15)
    assert s["n"] == 5
    assert s["mean"] == pytest.approx(-0.04)
    assert s["median"] == pytest.approx(0.0)
    assert s["win_rate"] == pytest.approx(0.4)  # 0 不算賺
    assert s["big_loss_rate"] == pytest.approx(0.4)  # 剛好 −15% 算大虧
    assert (s["best"], s["worst"]) == (0.10, -0.20)
    empty = trade_stats([], big_loss=-0.15)
    assert empty["n"] == 0 and empty["mean"] is None and empty["win_rate"] is None  # 不是 0


def test_ac8_nav_summary_with_benchmark() -> None:
    s = nav_summary([100.0, 120.0, 90.0, 110.0], [100.0, 101.0, 102.0, 103.0])
    assert s["ret"] == pytest.approx(0.10)
    assert s["mdd"] == pytest.approx(90.0 / 120.0 - 1.0)
    assert s["bench_ret"] == pytest.approx(0.03) and s["bench_mdd"] == 0.0
    assert s["short"] is True  # 不滿一年：年化只是換算
    assert nav_summary([100.0, 110.0], None)["bench_ret"] is None
    with pytest.raises(ValueError, match="長度"):
        nav_summary([100.0, 110.0], [100.0])


# ── 帳戶自己的買賣紀錄 ──────────────────────────────────────────────────────
def _t(day: int, sid: str, side: str, shares: int, price: float, reason: str) -> Trade:
    return Trade(_D[day], sid, side, shares, price, 0.0, 0.0, 0.0, reason)


def test_ac11_account_trades_show_sells_with_price_change() -> None:
    ledger = [
        _t(0, "2330", "buy", 1000, 100.0, "entry"),
        _t(1, "2330", "buy", 1000, 110.0, "entry"),  # 加碼：均價 105
        _t(2, "2330", "sell", 500, 126.0, "time_stop"),  # 部分賣出：均價不變
        _t(3, "2330", "sell", 1500, 94.5, "time_stop"),
        _t(3, "1101", "buy", 2000, 40.0, "entry"),
    ]
    rows = ledger_rows(ledger, [], {"2330": "台積電"})
    assert [(r["date"], r["sid"], r["side"]) for r in rows] == [
        (_D[3].isoformat(), "2330", "sell"),  # 日子新到舊；同一天照實際順序：先賣後買
        (_D[3].isoformat(), "1101", "buy"),
        (_D[2].isoformat(), "2330", "sell"),
        (_D[1].isoformat(), "2330", "buy"),
        (_D[0].isoformat(), "2330", "buy"),
    ]
    assert rows[2]["ret"] == pytest.approx(126.0 / 105.0 - 1.0)
    assert rows[0]["ret"] == pytest.approx(94.5 / 105.0 - 1.0)
    assert rows[1]["ret"] is None and rows[3]["ret"] is None  # 買進沒有價差
    assert rows[0]["name"] == "台積電" and rows[1]["name"] is None
    assert rows[0]["reason"] == "time_stop"
    # 已持有又買＝加碼；沒賣光＝減碼；賣光＝出清
    assert [r["move"] for r in rows] == ["close", "open", "trim", "add", "open"]


def test_ac11_split_changes_shares_not_cost() -> None:
    ledger = [
        _t(0, "6669", "buy", 1000, 100.0, "entry"),
        _t(2, "6669", "sell", 2000, 55.0, "time_stop"),
    ]
    splits = [SplitAdjustment(_D[1], "6669", 0.5, 1000, 2000)]  # 1 拆 2：均價 100 → 50
    rows = ledger_rows(ledger, splits, {})
    assert rows[0]["ret"] == pytest.approx(55.0 / 50.0 - 1.0)
    with pytest.raises(ValueError, match="先賣後買"):
        ledger_rows([_t(0, "2330", "sell", 1000, 100.0, "time_stop")], [], {})


# ── 整條線：照名單重播＋逐筆＋對照 ──────────────────────────────────────────
def _bench(i: int) -> float:
    """0050：每天 +0.2%，第 50 天除息 2%（價格跟著跳空）。"""
    return 100.0 * 1.002**i * (0.98 if i >= 50 else 1.0)


def _wh(tmp_path: Path, *, with_bench: bool) -> Warehouse:
    rows = []
    ids = ["1101", "2330", "2454", "2603", *(["0050"] if with_bench else [])]
    for i in range(N_DAYS):
        d = BASE + dt.timedelta(days=i)
        for sid in ids:
            c = _bench(i) if sid == "0050" else _close(sid, i)
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 100_000,
                    "amount": 1.0e9,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
                }
            )
    df = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df, source="twse")
    write_stock_meta(wh, df)
    if with_bench:
        ex = BASE + dt.timedelta(days=50)
        ev = pl.DataFrame(
            [
                {
                    "date": ex,
                    "stock_id": "0050",
                    "event_type": "cash_dividend",
                    "adj_factor": 0.98,
                    "ref_price": None,
                    "available_at": dt.datetime(ex.year, ex.month, ex.day, 18, tzinfo=TZ),
                }
            ],
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "event_type": pl.String,
                "adj_factor": pl.Float64,
                "ref_price": pl.Float64,
                "available_at": schemas.TW_DT,
            },
        )
        wh.write("clean", "corporate_events", ev, source="finmind")
    return wh


def _rebalance() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "core_like",
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "universe": {"min_adv_twd": None, "min_price": None, "max_jump_ratio": None},
            "overlays": [],
            "selection": {"method": "top_n", "top_n": 2},
            "weighting": "equal",
            "max_weight_per_stock": 0.5,
            "rebalance": {"schedule": "weekly", "day": "MON"},
            "exit_rules": [],
        }
    )


def _line(code: str, *, allow_empty: bool = False) -> BoardStrategy:
    return BoardStrategy(
        code=code, name=f"線{code}", config="x", summary="測試", allow_empty=allow_empty
    )


def _cfg_sc(**kw: Any) -> ScorecardConfig:
    base: dict[str, Any] = {
        "start": _START,
        "initial_capital": 10_000_000.0,
        "lot_size": 1,
        "max_participation": 0.05,
        "warmup_days": 60,
        "benchmark": "0050",
        "live_cutoff": "09:00",
        "hold_sessions": 5,
        "big_loss": -0.15,
    }
    return ScorecardConfig.model_validate(base | kw)


def _publish(
    wh: Warehouse, sessions: list[dt.date], live_from: dt.date, skip: dt.date
) -> list[PublishedPick]:
    """三條線的名單：補位型、月換股型、門檻很高（多數日子空）的補位型。live_from 之前的是事後一起補的。"""
    out: list[PublishedPick] = []
    late = _at(sessions[-1] + dt.timedelta(days=3), 12)
    slot, rb, rare = _cfg(n=2), _rebalance(), _cfg(n=2, threshold=0.2)
    for d in sessions:
        when = _at(d, 18) if d >= live_from else late
        for code, cfg in (("SLOT", slot), ("RB", rb), ("RARE", rare)):
            if code == "SLOT" and d == skip:
                continue  # 那天補位線漏寫
            if cfg.slots is not None:
                ids = [sid for sid, _ in slot_candidates(cfg, wh, d)[:2]]
            else:
                tg = build_targets(cfg, wh, d)
                ids = sorted(str(r["stock_id"]) for r in tg.to_dicts() if r["target_weight"] > 0)
            out.extend(PublishedPick(d, code, k, sid, when) for k, sid in enumerate(ids, start=1))
    return out


def _build(
    tmp_path: Path, *, with_bench: bool = True
) -> tuple[list[dict[str, Any]], Warehouse, list[PublishedPick]]:
    wh = _wh(tmp_path, with_bench=with_bench)
    sessions = cal.load(wh).between(_START, _END)
    published = _publish(wh, sessions, live_from=sessions[18], skip=sessions[10])
    cfg = _cfg_sc()
    lines = [
        (_line("SLOT"), _cfg(n=2)),
        (_line("RB"), _rebalance()),
        (_line("RARE", allow_empty=True), _cfg(n=2, threshold=0.2)),
    ]
    payloads = build_scorecards(
        source=wh,
        published=published,
        names={"1101": "台泥", "2330": "台積電"},
        lines=lines,
        cfg=cfg,
        cost=ct.CostConfig(),
        as_of=_END,
        generated_at=_at(_END, 19),
    )
    return payloads, wh, published


def test_ac8_account_is_the_engine_replay_of_the_published_lists(tmp_path: Path) -> None:
    payloads, wh, published = _build(tmp_path)
    assert [p["code"] for p in payloads] == ["SLOT", "RB", "RARE"]
    slot = payloads[0]
    sessions = cal.load(wh).between(_START, _END)
    picks: dict[dt.date, list[str]] = {}
    for p in published:
        if p.line == "SLOT":
            picks.setdefault(p.date, []).append(p.stock_id)
    res = run_backtest(_cfg(n=2), ct.CostConfig(), _START, _END, wh, lot_size=1, picks=picks)
    assert [r["d"] for r in slot["nav"]] == [d.isoformat() for d in sessions]
    assert slot["nav"][0]["v"] == pytest.approx(10_000_000.0)
    assert slot["account"]["all"]["ret"] == pytest.approx(res.final_equity / 10_000_000.0 - 1.0)
    assert slot["account"]["equity"] == pytest.approx(res.final_equity)
    assert any(r["x"] and r["x"] > 0.5 for r in slot["nav"]), "前提：帳戶真的有買股票"
    acct = slot["account"]["trades"]
    assert len(acct) == len(res.ledger)
    assert {r["side"] for r in acct} == {"buy"}  # 這個夾具的補位線沒有出場規則：只買不賣
    rb = payloads[1]["account"]["trades"]
    assert {r["side"] for r in rb} == {"buy", "sell"}  # 換股線每週換股：帳本裡有賣
    assert all(r["ret"] is not None for r in rb if r["side"] == "sell")
    json.dumps(payloads, allow_nan=False)  # 網站讀得出來（沒有 NaN）


def test_ac6_live_and_backfill_are_split(tmp_path: Path) -> None:
    payloads, wh, _ = _build(tmp_path)
    sessions = cal.load(wh).between(_START, _END)
    slot, rare = payloads[0], payloads[2]
    assert slot["live_since"] == sessions[18].isoformat()
    live = slot["account"]["live"]
    assert live["start"] == sessions[18].isoformat()
    navs = {r["d"]: r["v"] for r in slot["nav"]}
    assert live["ret"] == pytest.approx(
        navs[_END.isoformat()] / navs[sessions[18].isoformat()] - 1.0
    )
    stats = slot["trades"]["stats"]
    assert stats["backfill"]["n"] + stats["live"]["n"] == stats["all"]["n"]
    assert {r["live"] for r in slot["trades"]["rows"]} == {True, False}
    # 高分爆量那種線：空名單的日子跟著前一個有股票的日子，所以即時段不會比第一個即時有股票的日子早
    if rare["live_since"] is not None:
        assert rare["live_since"] >= sessions[18].isoformat()


def test_ac5_gap_day_is_listed_and_does_not_make_everything_new(tmp_path: Path) -> None:
    payloads, wh, published = _build(tmp_path)
    sessions = cal.load(wh).between(_START, _END)
    slot = payloads[0]
    assert slot["gaps"] == [sessions[10].isoformat()]
    assert payloads[2]["gaps"] == []  # 允許空名單的線：其他線有寫的日子都算有公布
    lists: dict[dt.date, list[str]] = {}
    for p in published:
        if p.line == "SLOT":
            lists.setdefault(p.date, []).append(p.stock_id)
    # 每一筆新進名單不是成為一筆，就是因為還抱著同一檔而略過（D-176 修訂二）
    stats = slot["trades"]["stats"]
    assert len(slot["trades"]["rows"]) + stats["repeat"] == len(new_entries(lists, sessions))
    held: dict[str, str | None] = {}
    for r in sorted(slot["trades"]["rows"], key=lambda r: r["signal"]):
        assert held.get(r["sid"], "") is not None and str(held.get(r["sid"], "")) <= r["signal"], (
            f"{r['sid']} {r['signal']}：上一筆還沒賣就又買"
        )
        if r["status"] == "closed":
            held[r["sid"]] = r["exit_date"]
        elif r["status"] in ("open", "pending"):
            held[r["sid"]] = None
    after_gap = [r for r in slot["trades"]["rows"] if r["signal"] == sessions[11].isoformat()]
    assert len(after_gap) < len(lists[sessions[11]]) or not lists[sessions[11]]


def test_ac7_trade_rows_have_names_and_statuses(tmp_path: Path) -> None:
    payloads, _, _ = _build(tmp_path)
    rows = payloads[0]["trades"]["rows"]
    assert rows == sorted(rows, key=lambda r: (r["signal"], r["sid"]), reverse=True)  # 新到舊
    assert {r["status"] for r in rows} <= {"closed", "open", "unfilled", "pending"}
    assert any(r["status"] == "closed" for r in rows)
    named = [r for r in rows if r["sid"] == "2330"]
    assert named and all(r["name"] == "台積電" for r in named)
    closed = [r["ret"] for r in rows if r["status"] == "closed"]
    # 明細的報酬四捨五入到 6 位（縮短 JSON），統計用的是沒捨入的值
    mean = payloads[0]["trades"]["stats"]["all"]["mean"]
    assert mean == pytest.approx(sum(closed) / len(closed), abs=1e-6)


def test_ac8_benchmark_is_total_return_and_aligned(tmp_path: Path) -> None:
    payloads, wh, _ = _build(tmp_path)
    sessions = cal.load(wh).between(_START, _END)
    p = payloads[0]
    assert p["benchmark"] == {"code": "0050", "error": None}
    b = [r["b"] for r in p["nav"]]
    assert b[0] == pytest.approx(10_000_000.0)
    i0, i1 = START, START + len(sessions) - 1
    total = _bench(i1) / _bench(i0) / 0.98  # 含息：除息的跳空加回來
    assert b[-1] / b[0] == pytest.approx(total, rel=1e-6)
    assert p["account"]["all"]["bench_ret"] == pytest.approx(total - 1.0, rel=1e-6)


def test_ac8_missing_benchmark_is_reported_not_zero(tmp_path: Path) -> None:
    payloads, _, _ = _build(tmp_path, with_bench=False)
    p = payloads[0]
    assert p["benchmark"]["error"]
    assert all(r["b"] is None for r in p["nav"])
    assert p["account"]["all"]["bench_ret"] is None


def test_ac8_config_is_registered() -> None:
    cfg = load_config(Path("configs/scorecard.yaml"), ScorecardConfig)
    assert (cfg.start, cfg.hold_sessions, cfg.big_loss, cfg.benchmark) == (
        dt.date(2026, 5, 4),
        20,
        -0.15,
        "0050",
    )
    assert cfg.live_cutoff == dt.time(9, 0)


# ── PG 讀寫（無 DSN 則 skip）──────────────────────────────────────────────
def test_ac8_pg_round_trip() -> None:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    from twquant.data.serving.picks_store import replace_line
    from twquant.data.serving.scorecard_store import read_published, write_scorecard

    schema = "serving_test_scorecard"
    conn = psycopg.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()
        replace_line(conn, schema, _D[1], "Q", [("2330", 0.5), ("1101", 0.4)], "m", _at(_D[1], 18))
        replace_line(conn, schema, _D[0], "Q", [("2454", 0.3)], "m", _at(_D[2], 18))
        got = read_published(conn, schema, _D[0], _D[1])
        assert [(p.date, p.rank, p.stock_id) for p in got] == [
            (_D[0], 1, "2454"),
            (_D[1], 1, "2330"),
            (_D[1], 2, "1101"),
        ]
        assert got[0].created_at == _at(_D[2], 18)
        write_scorecard(conn, schema, "Q", _D[1], {"a": 1, "名": "測"}, _at(_D[1], 19))
        write_scorecard(conn, schema, "Q", _D[2], {"a": 2}, _at(_D[2], 19))  # 同一條線整列覆蓋
        with conn.cursor() as cur:
            cur.execute(f'SELECT line, as_of, payload FROM "{schema}".scorecard')
            assert cur.fetchall() == [("Q", _D[2], {"a": 2})]
        with pytest.raises(ValueError):
            write_scorecard(conn, schema, "Q", _D[2], {"a": float("nan")}, _at(_D[2], 19))
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        conn.commit()
        conn.close()
