"""D-147：把第九輪的每日補位策略上架到平台「策略持股」頁。

兩件事要釘住：
1. 每日補位型策略沒有「目標權重」——平台給的是**今日候選前 n 名、每格 1/n**，
   不是實際持股（後者取決於歷史進出場，後端沒有帳戶狀態）。
2. 上架研究策略所用的模型**不得拖垮生產名單**：它失敗只回報、不擋。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from tests.conftest import write_stock_meta
from twquant.cli import _best_effort, _live_targets
from twquant.config import MlConfig, load_config
from twquant.conventions import TZ
from twquant.data.warehouse import Warehouse
from twquant.strategy.config import StrategyConfig

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
_STOCKS = ("1101", "1102", "1216", "2330", "2454")


def _sessions(n: int, start: dt.date = dt.date(2025, 1, 6)) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _wh(tmp_path: Path, days: list[dt.date]) -> Warehouse:
    """五檔股票，成交金額各不相同（1101 最小、2454 最大），價格不動。"""
    rows = []
    for d in days:
        for k, sid in enumerate(_STOCKS, 1):
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": k * 1_000_000,
                    "amount": 100.0 * k * 1_000_000,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ),
                }
            )
    px = pl.DataFrame(rows).with_columns(
        pl.col("volume").cast(pl.Int64),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", px, source="twse")
    write_stock_meta(wh, px)
    return wh


def _slots_cfg(n: int) -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "slots_live_test",
            "description": "每日補位型上架測試",
            "trial_scope": "test",
            "universe": {"markets": ["TWSE"], "min_adv_twd": None, "min_price": None},
            "signals": [{"factor": "volume.adv_20d", "weight": 1.0}],
            "slots": {"n": n, "entry_threshold": 0.0},
        }
    )


def test_ac1_slots_strategy_lists_top_n_candidates_at_one_over_n(tmp_path: Path) -> None:
    """每日補位型：取過門檻的候選前 n 名、每格 1/n，依分數由高到低。"""
    days = _sessions(40)
    wh = _wh(tmp_path, days)
    got = _live_targets(_slots_cfg(3), wh, days[-1])
    assert [sid for sid, _ in got] == ["2454", "2330", "1216"], "應依成交金額由大到小取前 3"
    assert all(w == pytest.approx(1.0 / 3.0) for _, w in got)
    assert sum(w for _, w in got) <= 1.0 + 1e-9


def test_ac2_fewer_candidates_than_slots_leaves_the_rest_in_cash(tmp_path: Path) -> None:
    """候選不足 n 檔時，權重和小於 1——剩下的是現金，不得把權重放大去湊滿。"""
    days = _sessions(40)
    wh = _wh(tmp_path, days)
    got = _live_targets(_slots_cfg(10), wh, days[-1])
    assert len(got) == len(_STOCKS)
    assert sum(w for _, w in got) == pytest.approx(len(_STOCKS) / 10)


def test_ac3_research_model_failures_are_returned_not_raised() -> None:
    """研究模型打分失敗要逐條回傳（呼叫端寫進摘要），不得中斷其餘模型。"""
    ran: list[str] = []

    def run(name: str) -> None:
        ran.append(name)
        if name == "bad":
            raise RuntimeError("找不到模型目錄")

    failed = _best_effort(["ok1", "bad", "ok2"], run)
    assert ran == ["ok1", "bad", "ok2"], "一個失敗不得讓後面的模型不跑"
    assert failed == ["bad：找不到模型目錄"]
    assert _best_effort([], run) == []


def test_ac4_research_models_are_separate_from_production_keys() -> None:
    """上架的研究模型不得混進生產或風控的鍵——那兩個鍵失敗會擋名單，這個不會。"""
    cfg = load_config(_CONFIG_DIR / "ml.yaml", MlConfig)
    research = set(cfg.research_live_models)
    assert research, "D-147 應至少登記一個研究模型"
    assert cfg.production_ranking_model not in research
    assert research.isdisjoint(cfg.production_aux_models)
    assert cfg.recommendation_buckets.risk_gate_model not in research
