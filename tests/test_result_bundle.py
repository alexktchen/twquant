"""T-131：統一回測結果包（SPEC-013 §3.3，D-061／R8）。

`backtest` 目前只印指標並把純量寫進 `research_log`，`BacktestResult` 的
`equity`／`ledger`／`unfilled` 全部丟棄。照 SPEC-013 §3.3 跑完 12 次階梯之後，
§3.6「只切既有曲線、不得重跑」所需的 artifact **根本不存在**。

三件事必須成立，否則結果包不能當作重跑的替代品：
1. 同一輸入產出 bit-identical 的結果包（否則無法判斷差異來自資料還是序列化）
2. 帶資料指紋，資料換過即可偵測（否則會拿舊曲線配新結論）
3. 由結果包重算的指標與引擎當初印出的一致（否則報表數字與回測脫節）
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from twquant.backtest.costs import CostConfig
from twquant.backtest.engine import BacktestResult, EquityPoint, Holding, Trade, Unfilled
from twquant.backtest.metrics import compute_metrics
from twquant.errors import DataError
from twquant.reporting.result_bundle import (
    bundle_backtest,
    data_fingerprint,
    metrics_from_bundle,
    read_bundle,
    write_bundle,
)
from twquant.strategy.config import StrategyConfig

_S, _E = dt.date(2026, 1, 2), dt.date(2026, 1, 7)
_D = [dt.date(2026, 1, d) for d in (2, 5, 6, 7)]
_FP = "fp-abc123"


def _cfg() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "t131",
            "trial_scope": "ladder_diag",
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "selection": {"method": "top_n", "top_n": 2},
            "max_weight_per_stock": 0.6,
        }
    )


def _result() -> BacktestResult:
    equity = [
        EquityPoint(_D[0], 1_000_000.0, 400_000.0, 600_000.0),
        EquityPoint(_D[1], 1_020_000.0, 400_000.0, 620_000.0),
        EquityPoint(_D[2], 1_005_000.0, 400_000.0, 605_000.0),
        EquityPoint(_D[3], 1_060_000.0, 1_060_000.0, 0.0),
    ]
    return BacktestResult(
        equity=equity,
        ledger=[Trade(_D[0], "1101", "buy", 1000, 40.0, 57.0, 0.0, 12.0)],
        unfilled=[Unfilled(_D[1], "2330", "buy", 500, "limit_up")],
        metrics=compute_metrics([e.equity for e in equity], turnover_annualized=1.25),
        initial_capital=1_000_000.0,
        final_equity=1_060_000.0,
        final_holdings=[Holding("1101", 1000, 60.0, 60_000.0, 0.06)],
    )


def _bundle() -> dict[str, Any]:
    return bundle_backtest(
        _result(),
        strategy=_cfg(),
        cost=CostConfig(),
        start=_S,
        end=_E,
        initial_capital=1_000_000.0,
        max_participation=0.05,
        source="pg",
        data_fingerprint=_FP,
    )


# ── AC-1：決定性 ───────────────────────────────────────────────────
def test_ac1_same_inputs_produce_bit_identical_bundles() -> None:
    """兩次序列化必須逐位元相同——否則無法分辨差異來自資料還是序列化順序。"""
    a = json.dumps(_bundle(), ensure_ascii=False, sort_keys=True)
    b = json.dumps(_bundle(), ensure_ascii=False, sort_keys=True)
    assert a == b


def test_ac1_roundtrip_through_disk_preserves_everything(tmp_path: Path) -> None:
    p = tmp_path / "b.json"
    write_bundle(p, _bundle())
    assert read_bundle(p) == _bundle()


# ── AC-2：資料指紋 ─────────────────────────────────────────────────
def test_ac2_fingerprint_changes_when_row_count_changes() -> None:
    base = data_fingerprint({"prices_daily": (1000, "2026-01-07T18:00:00+08:00")})
    more = data_fingerprint({"prices_daily": (1001, "2026-01-07T18:00:00+08:00")})
    assert base != more


def test_ac2_fingerprint_changes_when_availability_moves() -> None:
    """重抓／修正會推進 max(available_at)，即使列數不變也要偵測得到。"""
    base = data_fingerprint({"prices_daily": (1000, "2026-01-07T18:00:00+08:00")})
    revised = data_fingerprint({"prices_daily": (1000, "2026-01-08T18:00:00+08:00")})
    assert base != revised


def test_ac2_fingerprint_is_order_independent() -> None:
    """表的列舉順序不得改變指紋，否則同一份資料會算出兩個值。"""
    a = data_fingerprint({"prices_daily": (10, "x"), "monthly_revenue": (5, "y")})
    b = data_fingerprint({"monthly_revenue": (5, "y"), "prices_daily": (10, "x")})
    assert a == b


def test_ac2_empty_fingerprint_input_raises() -> None:
    """沒有任何表就算不出有意義的指紋；回傳一個空字串的雜湊等於假裝有防護。"""
    with pytest.raises(DataError, match="指紋"):
        data_fingerprint({})


def test_ac2_bundle_carries_the_fingerprint() -> None:
    assert _bundle()["data_fingerprint"] == _FP


# ── AC-3：由結果包重算的指標與引擎一致 ─────────────────────────────
def test_ac3_metrics_recomputed_from_bundle_match_the_engine() -> None:
    """報表若自己重算指標卻與回測不符，兩邊必有一邊是錯的。"""
    engine = _result().metrics
    got = metrics_from_bundle(_bundle())
    for field in ("cagr", "sharpe", "sortino", "mdd", "calmar", "turnover", "win_rate"):
        assert getattr(got, field) == pytest.approx(getattr(engine, field), abs=1e-12), field
    assert got.n_days == engine.n_days


def test_ac3_tampered_equity_is_detected_by_recomputation() -> None:
    """反向確認：改掉曲線後重算的指標必須不同（證明上一條不是恆真）。"""
    b = _bundle()
    eq = b["equity"]
    eq[-1] = [eq[-1][0], 2_000_000.0, eq[-1][2], eq[-1][3]]
    b["equity"] = eq
    assert metrics_from_bundle(b).cagr != pytest.approx(_result().metrics.cagr)


# ── 內容完整性 ─────────────────────────────────────────────────────
def test_bundle_keeps_ledger_unfilled_and_holdings() -> None:
    """這三樣正是現行 CLI 丟棄的東西——歷年持股與未成交診斷都靠它們。"""
    b = _bundle()
    assert len(b["ledger"]) == 1
    assert len(b["unfilled"]) == 1
    assert len(b["final_holdings"]) == 1
    assert b["unfilled"][0]["reason"] == "limit_up"


def test_bundle_records_the_full_run_identity() -> None:
    """區間、資金、參與率、成本與策略配置都要在包裡，否則無從得知這是哪一次跑的。"""
    b = _bundle()
    assert b["start"] == _S.isoformat() and b["end"] == _E.isoformat()
    assert b["initial_capital"] == 1_000_000.0
    assert b["max_participation"] == 0.05
    assert b["source"] == "pg"
    assert b["strategy"]["name"] == "t131"
    assert "commission_rate" in b["cost"]


def test_bundle_is_json_serializable_without_custom_encoder() -> None:
    """日期等型別必須在建包時就轉成字串——留給呼叫端轉會各寫各的。"""
    json.dumps(_bundle(), ensure_ascii=False)
