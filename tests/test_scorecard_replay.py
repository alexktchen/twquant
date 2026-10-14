"""T-349／D-176：名單績效的模擬帳戶「照公布的名單重播」必須與引擎自己算名單的重播逐筆相同。

網站上的名單＝收盤後公布的那一份（`daily_picks`）。帳戶只能照它操作，不能重算模型——
否則名單績效量的就不是「這套名單」。這裡證明兩條路在同一份資料上結果逐位元相同。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

import twquant.factors.builtins  # noqa: F401
from tests.test_backtest_slots import BASE, N_DAYS, START, _cfg, _wh
from twquant.backtest import costs as ct
from twquant.backtest.engine import BacktestResult, run_backtest
from twquant.data import calendar as cal
from twquant.data.warehouse import Warehouse
from twquant.errors import ConfigError, DataError
from twquant.strategy.build import build_targets, list_weights
from twquant.strategy.config import StrategyConfig
from twquant.strategy.slots import slot_candidates

_START = BASE + dt.timedelta(days=START)
_END = BASE + dt.timedelta(days=N_DAYS - 1)


def _rebalance_cfg(**extra: object) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "list_rb",
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "universe": {"min_adv_twd": None, "min_price": None, "max_jump_ratio": None},
            "overlays": [],
            "selection": {"method": "top_n", "top_n": 2},
            "weighting": "equal",
            "max_weight_per_stock": 0.5,
            "rebalance": {"schedule": "weekly", "day": "MON"},
            "exit_rules": [],
        }
        | extra
    )


def _run(
    wh: Warehouse, cfg: StrategyConfig, picks: dict[dt.date, list[str]] | None
) -> BacktestResult:
    return run_backtest(
        cfg,
        ct.CostConfig(),
        _START,
        _END,
        wh,
        initial_capital=10_000_000.0,
        lot_size=1,
        picks=picks,
    )


def _sessions(wh: Warehouse) -> list[dt.date]:
    return cal.load(wh).between(_START, _END)


def _same(a: BacktestResult, b: BacktestResult) -> None:
    assert a.ledger == b.ledger
    assert a.equity == b.equity
    assert a.unfilled == b.unfilled
    assert a.final_holdings == b.final_holdings


@pytest.mark.parametrize("rules", [[], [{"kind": "time_stop", "sessions": 5}]])
def test_ac1_slots_replay_from_published_top_n_equals_engine(
    tmp_path: Path, rules: list[dict[str, object]]
) -> None:
    """補位型公布前 n 名就夠：持有 h 檔時空位是 n−h，前 n 名裡最多 h 檔已持有。"""
    wh = _wh(tmp_path)
    cfg = _cfg(rules, n=2)
    picks = {d: [sid for sid, _ in slot_candidates(cfg, wh, d)[:2]] for d in _sessions(wh)}
    engine = _run(wh, cfg, None)
    assert any(t.side == "buy" for t in engine.ledger), "前提：夾具要真的有進場"
    _same(_run(wh, cfg, picks), engine)


def test_ac2_rebalance_replay_from_published_list_equals_engine(tmp_path: Path) -> None:
    wh = _wh(tmp_path)
    cfg = _rebalance_cfg()
    picks = {}
    for d in _sessions(wh):
        tg = build_targets(cfg, wh, d)
        rows = sorted(
            ((str(r["stock_id"]), float(r["target_weight"])) for r in tg.to_dicts()),
            key=lambda x: (-x[1], x[0]),
        )
        picks[d] = [sid for sid, w in rows if w > 0.0]
    engine = _run(wh, cfg, None)
    assert {t.stock_id for t in engine.ledger if t.side == "sell"}, "前提：夾具要真的有換股"
    _same(_run(wh, cfg, picks), engine)


def test_ac3_rebalance_day_without_published_list_raises(tmp_path: Path) -> None:
    wh = _wh(tmp_path)
    with pytest.raises(DataError, match="換股日沒有公布的名單"):
        _run(wh, _rebalance_cfg(), {})


def test_ac3_slots_day_without_list_means_no_candidates(tmp_path: Path) -> None:
    """補位型那天沒有名單＝沒有候選（高分爆量大多數日子就是這樣），不是錯誤。"""
    res = _run(_wh(tmp_path), _cfg(n=2), {})
    assert res.ledger == []
    assert res.final_equity == pytest.approx(10_000_000.0)


def test_ac3_list_weights_only_for_score_free_weighting() -> None:
    assert list_weights(_rebalance_cfg(), ["2330", "1101"]) == {"2330": 0.5, "1101": 0.5}
    slot = _rebalance_cfg(weighting="fixed_slot", max_weight_per_stock=0.5)
    assert list_weights(slot, ["2330"]) == {"2330": 0.5}  # 固定格位：少一檔就留現金
    with pytest.raises(ConfigError, match="需要分數或市值"):
        list_weights(_rebalance_cfg(weighting="score_prop"), ["2330", "1101"])
    with pytest.raises(ConfigError, match="overlay"):
        list_weights(_rebalance_cfg(overlays=["breadth"]), ["2330", "1101"])
    with pytest.raises(ValueError, match="重複"):
        list_weights(_rebalance_cfg(), ["2330", "2330"])
