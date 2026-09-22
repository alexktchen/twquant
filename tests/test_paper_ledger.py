"""D-142 §四：紙上交易帳本的防作弊閘門。

每一條測試對應 D-142 §四 的一款。帳本的價值全在「寫下去改不動」，
所以這裡測的不是「會不會算」，而是**該拒絕的有沒有拒絕**。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twquant.config import load_config
from twquant.conventions import TZ
from twquant.errors import ConfigError, DataError
from twquant.reporting.paper_ledger import (
    GENESIS_HASH,
    ClosedTrade,
    DayRecord,
    FillRow,
    HoldingRow,
    LedgerEvent,
    LedgerNote,
    OrderRow,
    PaperTradingConfig,
    SkippedDay,
    TrackOpened,
    append_event,
    ledger_path,
    load_track,
    read_events,
    replay,
    verify_chain,
)

CODE = "PT-TEST"
START = dt.date(2026, 9, 23)


def _cfg(**over: object) -> PaperTradingConfig:
    base: dict[str, object] = {
        "registration": "D-142",
        "strategy": "lw_e0_ctrl",
        "model_id": "lgbm_test",
        "max_participation": 0.05,
        "max_record_lag_days": 3,
        "max_mark_lag_days": 30,
        "tracks": [{"code": CODE, "label": "測試軌", "capital_twd": 1_000_000.0}],
        "verdict": {
            "min_signal_days": 250,
            "min_closed_trades": 200,
            "min_months": 12,
            "forward_rank_ic": 0.03,
            "month_block_t": 2.0,
        },
    }
    return PaperTradingConfig.model_validate(base | over)


def _opened() -> TrackOpened:
    return TrackOpened(
        code=CODE,
        label="測試軌",
        registration="D-142",
        strategy="lw_e0_ctrl",
        strategy_fingerprint="a" * 64,
        cost_fingerprint="b" * 64,
        model_id="lgbm_test",
        capital_twd=1_000_000.0,
        start_date=START,
        credit_dividends=False,
    )


def _at(day: dt.date, hour: int = 19) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hour, 0, tzinfo=TZ)


def _open_track(root: Path, cfg: PaperTradingConfig | None = None) -> LedgerEvent:
    return append_event(root, CODE, _opened(), day=START, recorded_at=_at(START), cfg=cfg or _cfg())


def _flat_day(day: dt.date, *, orders: tuple[OrderRow, ...] = ()) -> DayRecord:
    """一個空手日：沒有持股，權益＝現金。"""
    return DayRecord(cash=1_000_000.0, equity=1_000_000.0, orders_queued=orders)


def test_ac0_chain_links_every_event(tmp_path: Path) -> None:
    cfg = _cfg()
    first = _open_track(tmp_path, cfg)
    second = append_event(
        tmp_path,
        CODE,
        _flat_day(START),
        day=START,
        recorded_at=_at(START),
        cfg=cfg,
    )
    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.hash
    assert second.seq == 1
    verify_chain(read_events(ledger_path(tmp_path, CODE)))


def test_ac0_tampered_event_is_detected(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    append_event(tmp_path, CODE, _flat_day(START), day=START, recorded_at=_at(START), cfg=cfg)
    path = ledger_path(tmp_path, CODE)
    lines = path.read_text(encoding="utf-8").splitlines()
    # 把現金與權益同步改高一成：會計恆等仍然成立（所以解析得過），只有雜湊對不起來。
    lines[1] = lines[1].replace("1000000.0", "1100000.0")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(DataError, match="seq=1 的內容被改過"):
        verify_chain(read_events(path))


def test_ac0_tampering_is_caught_before_the_next_append(tmp_path: Path) -> None:
    # 被動過的帳本不該還能繼續往上疊——那會讓斷點被新事件蓋過去。
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    path = ledger_path(tmp_path, CODE)
    path.write_text(
        path.read_text(encoding="utf-8").replace('"capital_twd":1000000.0', '"capital_twd":9.0'),
        encoding="utf-8",
    )
    with pytest.raises(DataError, match="內容被改過"):
        append_event(tmp_path, CODE, _flat_day(START), day=START, recorded_at=_at(START), cfg=cfg)


def test_ac1_date_must_not_go_backwards(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    day2 = dt.date(2026, 9, 24)
    append_event(tmp_path, CODE, _flat_day(day2), day=day2, recorded_at=_at(day2), cfg=cfg)
    with pytest.raises(DataError, match="日期倒退"):
        append_event(tmp_path, CODE, _flat_day(START), day=START, recorded_at=_at(day2), cfg=cfg)


def test_ac2_refuses_dates_before_the_track_opened(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    with pytest.raises(DataError, match="早於開軌日"):
        append_event(
            tmp_path,
            CODE,
            _flat_day(dt.date(2026, 9, 1)),
            day=dt.date(2026, 9, 1),
            recorded_at=_at(START),
            cfg=cfg,
        )


def test_ac3_refuses_recording_the_future(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    ahead = dt.date(2026, 9, 30)
    with pytest.raises(DataError, match="不得記未來"):
        append_event(tmp_path, CODE, _flat_day(ahead), day=ahead, recorded_at=_at(START), cfg=cfg)


def test_ac3_refuses_recording_the_same_day_twice(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    append_event(tmp_path, CODE, _flat_day(START), day=START, recorded_at=_at(START), cfg=cfg)
    with pytest.raises(DataError, match="已經記過了"):
        append_event(
            tmp_path,
            CODE,
            SkippedDay(reason="想改成 skipped"),
            day=START,
            recorded_at=_at(START),
            cfg=cfg,
        )


def test_ac4_refuses_stock_picking_recorded_too_late(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    picked = _flat_day(START, orders=(OrderRow(stock_id="2330", target_weight=0.1),))
    with pytest.raises(DataError, match="不是紙上交易，是回測"):
        append_event(
            tmp_path,
            CODE,
            picked,
            day=START,
            recorded_at=_at(START + dt.timedelta(days=4)),
            cfg=cfg,
        )


def test_ac4_allows_a_late_mark_because_it_carries_no_decision(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    event = append_event(
        tmp_path,
        CODE,
        _flat_day(START),
        day=START,
        recorded_at=_at(START + dt.timedelta(days=9)),
        cfg=cfg,
    )
    assert event.recorded_lag_days() == 9


def test_ac5_a_hole_stays_a_hole(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    gap = dt.date(2026, 9, 24)
    append_event(
        tmp_path,
        CODE,
        SkippedDay(reason="排程器未產出當日因子（T-275）"),
        day=gap,
        recorded_at=_at(gap),
        cfg=cfg,
    )
    after = dt.date(2026, 9, 25)
    append_event(tmp_path, CODE, _flat_day(after), day=after, recorded_at=_at(after), cfg=cfg)
    state = load_track(tmp_path, CODE)
    assert state.skipped == [(gap, "排程器未產出當日因子（T-275）")]


def test_ac6_a_correction_is_a_new_event_not_a_rewrite(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    append_event(tmp_path, CODE, _flat_day(START), day=START, recorded_at=_at(START), cfg=cfg)
    later = dt.date(2026, 9, 25)
    append_event(
        tmp_path,
        CODE,
        LedgerNote(text="T-277 修正：滑價曾少算，自本日起正確"),
        day=later,
        recorded_at=_at(later),
        cfg=cfg,
    )
    events = read_events(ledger_path(tmp_path, CODE))
    verify_chain(events)
    assert [e.body.kind for e in events] == ["track_opened", "day", "note"]


def test_first_event_must_open_the_track(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="第一筆必須是 track_opened"):
        append_event(
            tmp_path, CODE, _flat_day(START), day=START, recorded_at=_at(START), cfg=_cfg()
        )


def test_track_cannot_be_opened_twice(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    with pytest.raises(DataError, match="只能是鏈首一筆"):
        append_event(tmp_path, CODE, _opened(), day=START, recorded_at=_at(START), cfg=cfg)


def test_replay_folds_cash_positions_and_closed_trades(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    buy = DayRecord(
        fills=(
            FillRow(
                stock_id="2330",
                side="buy",
                shares=1000,
                price=500.0,
                commission=249.0,
                tax=0.0,
                slippage=100.0,
            ),
        ),
        holdings=(HoldingRow(stock_id="2330", shares=1000, close=510.0, cost_basis=500_349.0),),
        cash=499_651.0,
        equity=1_009_651.0,
        orders_queued=(OrderRow(stock_id="2330", target_weight=0.5),),
    )
    append_event(tmp_path, CODE, buy, day=START, recorded_at=_at(START), cfg=cfg)
    sell_day = dt.date(2026, 9, 24)
    sold = DayRecord(
        fills=(
            FillRow(
                stock_id="2330",
                side="sell",
                shares=1000,
                price=520.0,
                commission=259.0,
                tax=1560.0,
                slippage=100.0,
            ),
        ),
        closed=(
            ClosedTrade(
                stock_id="2330",
                opened_on=START,
                closed_on=sell_day,
                shares=1000,
                cost=500_349.0,
                proceeds=518_081.0,
                net_return=518_081.0 / 500_349.0 - 1.0,
                held_sessions=1,
            ),
        ),
        cash=1_017_732.0,
        equity=1_017_732.0,
    )
    append_event(tmp_path, CODE, sold, day=sell_day, recorded_at=_at(sell_day), cfg=cfg)
    state = load_track(tmp_path, CODE)
    assert state.positions == {}
    assert state.pending == ()
    assert state.cash == pytest.approx(1_017_732.0)
    assert len(state.closed) == 1
    assert state.closed[0].net_return == pytest.approx(0.035439, abs=1e-6)
    assert state.start_date == START
    assert state.seq == 3


def test_replay_keeps_the_original_entry_date_across_marks(tmp_path: Path) -> None:
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    holding = HoldingRow(stock_id="2330", shares=1000, close=510.0, cost_basis=500_349.0)
    for offset in range(3):
        day = START + dt.timedelta(days=offset)
        append_event(
            tmp_path,
            CODE,
            DayRecord(holdings=(holding,), cash=100.0, equity=510_100.0),
            day=day,
            recorded_at=_at(day),
            cfg=cfg,
        )
    assert load_track(tmp_path, CODE).positions["2330"].opened_on == START


def test_accounting_identity_is_enforced_at_write_time() -> None:
    with pytest.raises(ValueError, match="會計恆等不成立"):
        DayRecord(
            holdings=(HoldingRow(stock_id="2330", shares=1000, close=500.0, cost_basis=1.0),),
            cash=0.0,
            equity=1_000_000.0,
        )


def test_closed_trade_return_must_match_its_own_cashflow() -> None:
    with pytest.raises(ValueError, match="與現金流推得的"):
        ClosedTrade(
            stock_id="2330",
            opened_on=START,
            closed_on=START,
            shares=1000,
            cost=100.0,
            proceeds=110.0,
            net_return=0.5,  # 現金流說 +10%
            held_sessions=0,
        )


def test_stock_id_must_stay_a_string() -> None:
    with pytest.raises(ValueError, match="非空字串"):
        OrderRow(stock_id=50, target_weight=0.1)  # type: ignore[arg-type]


def test_track_code_rejects_path_fragments() -> None:
    with pytest.raises(DataError, match="軌別代號格式錯誤"):
        ledger_path(Path("."), "../escape")


def test_unopened_track_reads_as_empty_not_error(tmp_path: Path) -> None:
    assert replay(read_events(ledger_path(tmp_path, CODE))).opened is False


def test_mark_lag_limit_must_not_be_stricter_than_the_picking_limit() -> None:
    with pytest.raises(ValueError, match="設定寫反了"):
        _cfg(max_record_lag_days=10, max_mark_lag_days=1)


def test_shipped_config_matches_the_registration() -> None:
    """configs/paper_trading.yaml 必須載得起來，且與 D-142 §五 登記的門檻一致。"""
    cfg = load_config(Path("configs/paper_trading.yaml"), PaperTradingConfig)
    assert cfg.registration == "D-142"
    assert cfg.strategy == "lw_e0_ctrl"
    assert cfg.credit_dividends is False
    assert {t.code for t in cfg.tracks} == {"PT-E0-80M", "PT-E0-RETAIL"}
    assert cfg.verdict.min_closed_trades == 200
    assert cfg.verdict.forward_rank_ic == 0.03
    assert cfg.verdict.month_block_t == 2.0


def test_unknown_config_key_is_refused(tmp_path: Path) -> None:
    """多一個欄位就該爆——寫了卻不生效的配置正是 audit_silent 要抓的那一類。"""
    bad = tmp_path / "paper_trading.yaml"
    bad.write_text("unexpected: 1", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad, PaperTradingConfig)


def test_a_tampered_line_that_breaks_a_contract_dies_at_read(tmp_path: Path) -> None:
    """第二道防線：改壞的那一行連解析都過不了，根本走不到雜湊檢查。"""
    cfg = _cfg()
    _open_track(tmp_path, cfg)
    append_event(tmp_path, CODE, _flat_day(START), day=START, recorded_at=_at(START), cfg=cfg)
    path = ledger_path(tmp_path, CODE)
    lines = path.read_text(encoding="utf-8").splitlines()
    # 只把權益灌水：現金沒跟著動，會計恆等當場不成立。
    lines[1] = lines[1].replace('"equity":1000000.0', '"equity":1100000.0')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(DataError, match="第 2 行不是合法事件"):
        read_events(path)
