"""T-230：由結果包的 ledger 產出「每個交易日誰進、誰出」的換股清單。"""

from __future__ import annotations

from typing import Any

import pytest

from twquant.errors import DataError
from twquant.reporting.trade_journal import build_journal, journal_to_dicts

SESSIONS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]


def _t(date: str, sid: str, side: str, shares: int, price: float) -> dict[str, Any]:
    return {"date": date, "stock_id": sid, "side": side, "shares": shares, "price": price}


def _bundle(
    ledger: list[dict[str, Any]], unfilled: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    return {
        "equity": [[d, 1.0, 1.0, 0.0] for d in SESSIONS],
        "ledger": ledger,
        "unfilled": unfilled or [],
    }


def test_ac1_enter_and_exit_are_classified_by_position_crossing_zero() -> None:
    days = build_journal(
        _bundle(
            [
                _t("2024-01-02", "2330", "buy", 1000, 500.0),
                _t("2024-01-05", "2330", "sell", 1000, 550.0),
                _t("2024-01-05", "0050", "buy", 2000, 100.0),
            ]
        )
    )
    assert [d.date for d in days] == ["2024-01-02", "2024-01-05"]
    assert [(m.stock_id, m.action) for m in days[0].moves] == [("2330", "enter")]
    assert [(m.stock_id, m.action) for m in days[1].moves] == [("0050", "enter"), ("2330", "exit")]


def test_ac2_add_and_trim_keep_the_position_open() -> None:
    days = build_journal(
        _bundle(
            [
                _t("2024-01-02", "2330", "buy", 1000, 500.0),
                _t("2024-01-03", "2330", "buy", 1000, 520.0),
                _t("2024-01-04", "2330", "sell", 500, 530.0),
            ]
        )
    )
    assert [d.moves[0].action for d in days] == ["enter", "add", "trim"]
    assert [d.moves[0].shares for d in days] == [1000, 1000, -500]
    assert days[-1].moves[0].shares_after == 1500


def test_ac3_exit_reports_return_on_average_cost_and_sessions_held() -> None:
    days = build_journal(
        _bundle(
            [
                _t("2024-01-02", "2330", "buy", 1000, 400.0),
                _t("2024-01-03", "2330", "buy", 1000, 600.0),  # 均價 500
                _t("2024-01-08", "2330", "sell", 2000, 550.0),
            ]
        )
    )
    out = days[-1].moves[0]
    assert out.action == "exit"
    assert out.entry_date == "2024-01-02"
    assert out.held_sessions == 4
    assert out.ret == pytest.approx(0.10)


def test_ac4_holdings_count_is_after_the_days_trades() -> None:
    days = build_journal(
        _bundle(
            [
                _t("2024-01-02", "2330", "buy", 1000, 500.0),
                _t("2024-01-02", "2317", "buy", 1000, 100.0),
                _t("2024-01-03", "2317", "sell", 1000, 101.0),
            ]
        )
    )
    assert [d.n_holdings_after for d in days] == [2, 1]


def test_ac5_same_bundle_gives_identical_journal() -> None:
    ledger = [
        _t("2024-01-02", "2330", "buy", 1000, 500.0),
        _t("2024-01-02", "2317", "buy", 1000, 100.0),
        _t("2024-01-04", "2330", "sell", 1000, 480.0),
    ]
    assert journal_to_dicts(build_journal(_bundle(ledger))) == journal_to_dicts(
        build_journal(_bundle(list(reversed(ledger[:2])) + ledger[2:]))
    )


def test_ac6_unfilled_orders_surface_on_their_day() -> None:
    unfilled = [
        {"date": "2024-01-03", "stock_id": "2317", "side": "buy", "shares": 1000, "reason": "cash"}
    ]
    days = build_journal(_bundle([_t("2024-01-02", "2330", "buy", 1000, 500.0)], unfilled))
    assert days[1].date == "2024-01-03"
    assert days[1].moves == []
    assert days[1].unfilled[0]["reason"] == "cash"


def test_ac7_overselling_raises_instead_of_being_smoothed_over() -> None:
    with pytest.raises(DataError, match="帳上只有"):
        build_journal(_bundle([_t("2024-01-02", "2330", "sell", 1000, 500.0)]))


def test_ac8_stock_id_keeps_leading_zero() -> None:
    days = build_journal(_bundle([_t("2024-01-02", "0050", "buy", 1000, 100.0)]))
    assert days[0].moves[0].stock_id == "0050"


def test_ac9_exit_rule_reason_is_carried_to_the_move() -> None:
    ledger = [
        _t("2024-01-02", "2330", "buy", 1000, 500.0),
        _t("2024-01-04", "2330", "sell", 1000, 440.0) | {"reason": "stop_loss"},
    ]
    days = build_journal(_bundle(ledger))
    assert days[0].moves[0].reason is None
    assert days[1].moves[0].reason == "stop_loss"
