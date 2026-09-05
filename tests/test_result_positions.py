"""T-125：由成交流水重建歷史持倉（SPEC-013 §6.2，AC-9／AC-10）。

`BacktestResult` 只存期末快照 `final_holdings`，歷史部位從未落地——「歷年持股」
報表因此只能由 `ledger` 逐筆累加重建。這個重建必須可驗證，否則報表上的持股清單
是憑空生出來的。

另含 T-124 的成本檔解析（AC-8）：現行 CLI 寫死 `costs.yaml`，
使 `costs_finlab.yaml` 從 CLI 根本無法使用。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twquant.backtest.diagnostics import positions_from_ledger
from twquant.backtest.engine import BacktestResult, EquityPoint, Holding, Trade
from twquant.backtest.metrics import compute_metrics
from twquant.errors import DataError

_D = [dt.date(2026, 1, d) for d in (2, 5, 6, 7)]


def _result(ledger: list[Trade], final: list[Holding] | None = None) -> BacktestResult:
    equity = [EquityPoint(d, 1000.0, 1000.0, 0.0) for d in _D]
    return BacktestResult(
        equity=equity,
        ledger=ledger,
        unfilled=[],
        metrics=compute_metrics([e.equity for e in equity], turnover_annualized=0.0),
        initial_capital=1000.0,
        final_equity=1000.0,
        final_holdings=final or [],
    )


def _t(d: dt.date, sid: str, side: str, shares: int) -> Trade:
    return Trade(d, sid, side, shares, 10.0, 0.0, 0.0, 0.0)


def test_ac9_positions_carry_forward_between_trades() -> None:
    """買進後未賣出的日子仍須有持倉列——只在成交日產生列會讓持股表出現空窗。"""
    res = _result([_t(_D[0], "1101", "buy", 1000)])
    pos = positions_from_ledger(res)
    assert pos.columns == ["date", "stock_id", "shares"]
    got = {(r["date"], r["stock_id"]): r["shares"] for r in pos.to_dicts()}
    assert got == {(d, "1101"): 1000 for d in _D}


def test_ac9_partial_sell_then_reentry_is_tracked() -> None:
    """部分出場、清空、再進場：每一段的股數都要對。"""
    res = _result(
        [
            _t(_D[0], "1101", "buy", 1000),
            _t(_D[1], "1101", "sell", 400),
            _t(_D[2], "1101", "sell", 600),  # 清空
            _t(_D[3], "1101", "buy", 200),  # 再進場
        ]
    )
    got = {(r["date"], r["stock_id"]): r["shares"] for r in positions_from_ledger(res).to_dicts()}
    assert got == {
        (_D[0], "1101"): 1000,
        (_D[1], "1101"): 600,
        (_D[3], "1101"): 200,
    }, "清空當日不得留下 0 股的列"


def test_ac9_final_day_matches_final_holdings() -> None:
    """重建的最後一日必須與引擎自己的 `final_holdings` **完全相符**。

    這是整個重建唯一能對到引擎內部狀態的錨點；不相符即代表重建邏輯與引擎脫節。
    """
    ledger = [
        _t(_D[0], "1101", "buy", 1000),
        _t(_D[0], "2330", "buy", 500),
        _t(_D[2], "1101", "sell", 300),
    ]
    final = [
        Holding("1101", 700, 10.0, 7000.0, 0.7),
        Holding("2330", 500, 10.0, 5000.0, 0.5),
    ]
    pos = positions_from_ledger(_result(ledger, final))
    last = {r["stock_id"]: r["shares"] for r in pos.filter(pos["date"] == _D[-1]).to_dicts()}
    assert last == {h.stock_id: h.shares for h in final}


def test_ac10_oversell_raises_instead_of_going_negative() -> None:
    """放空未實作，負股數只可能是重建邏輯錯誤或流水損壞——必須 raise（黃金規則 5）。"""
    res = _result([_t(_D[0], "1101", "buy", 100), _t(_D[1], "1101", "sell", 300)])
    with pytest.raises(DataError, match="負"):
        positions_from_ledger(res)


def test_ac10_trade_outside_equity_dates_raises() -> None:
    """成交日不在權益曲線的日期內 → 靜默略過會讓持股表少掉一段，必須 raise。"""
    res = _result([_t(dt.date(2026, 2, 20), "1101", "buy", 100)])
    with pytest.raises(DataError, match="不在"):
        positions_from_ledger(res)


def test_ac10_unknown_side_raises() -> None:
    res = _result([Trade(_D[0], "1101", "short", 100, 10.0, 0.0, 0.0, 0.0)])
    with pytest.raises(DataError, match="side"):
        positions_from_ledger(res)


def test_ac9_empty_ledger_yields_empty_frame_with_schema() -> None:
    """空流水回傳空表但保有欄位——下游 join 才不會因缺欄位而炸。"""
    pos = positions_from_ledger(_result([]))
    assert pos.is_empty()
    assert pos.columns == ["date", "stock_id", "shares"]


# ── T-124：成本檔可從 CLI 指定（AC-8）──────────────────────────────
def test_ac8_default_costs_path_is_unchanged() -> None:
    """未指定時必須解析到 `configs/costs.yaml`——既有回測結果須維持可重現。"""
    from twquant.cli import _resolve_costs

    assert _resolve_costs("costs.yaml").name == "costs.yaml"
    assert _resolve_costs("costs.yaml").parent.name == "configs"


def test_ac8_relative_name_resolves_under_configs() -> None:
    """`--costs costs_finlab.yaml` 要能找到——這正是先前從 CLI 無法使用的那個檔。"""
    from twquant.cli import _resolve_costs

    p = _resolve_costs("costs_finlab.yaml")
    assert p.parent.name == "configs" and p.name == "costs_finlab.yaml"
    assert p.exists(), "configs/costs_finlab.yaml 應存在（finlab 口徑成本）"


def test_ac8_absolute_path_is_used_as_is(tmp_path: Path) -> None:
    from twquant.cli import _resolve_costs

    target = tmp_path / "my_costs.yaml"
    target.write_text("commission_rate: 0.001\n", encoding="utf-8")
    assert _resolve_costs(str(target)) == target
