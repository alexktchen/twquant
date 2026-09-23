"""T-168：分割／反分割要調股數，不是當成股利或虧損。

**缺陷**：`corporate_events` 有 28 筆 split（adj 0.042~0.5）與 6 筆 reverse_split（2.0~7.0），
而引擎完全不看它們。1:20 分割當天價格掉 95%，D-029 的跳價防護會把部位當成資料異常
強制出清，並把它記成 −94% 的**真實虧損**。

D-143 第九輪實測踩到兩次：5314（2025-03-31，1:20）記 −90.7%、6919（2025-07-21，1:10）
記 −89.3%，各賠掉 15 格中的一格。更糟的是它**不對稱**——標籤那一側早就還原了
（`attach_adjustment` 連乘全部 adj_factor），所以模型是用乾淨資料訓練、回測吃假虧損。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from tests.conftest import write_stock_meta
from twquant.backtest.costs import CostConfig
from twquant.backtest.engine import BacktestResult, run_backtest
from twquant.config import load_config
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.strategy.config import StrategyConfig

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
_STOCKS = ("1101", "2330", "2454")
_PRICE = 400.0
_CAPITAL = 10_000_000.0


def _sessions(n: int, start: dt.date = dt.date(2025, 1, 6)) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _wh(
    tmp_path: Path,
    days: list[dt.date],
    *,
    split_day: dt.date | None = None,
    ratio: float = 0.25,
    sids: tuple[str, ...] = ("2330",),
    with_events: bool = True,
) -> Warehouse:
    """三檔等價股票；`sids` 裡的股票在 `split_day` 當天起價格乘上 `ratio`（1:4 → 0.25）。

    **預設只讓一檔分割**：宇宙衛生濾網（`max_jump_ratio=0.35`）會把跳價的股票踢出
    候選 60 個交易日，三檔同時分割會讓宇宙變成 0 檔而 raise。那是同一個缺陷的
    第二個落點（濾網也把分割當成資料異常），但不在 T-168 的範圍內——本檔只驗引擎。
    """
    rows = []
    for d in days:
        for sid in _STOCKS:
            c = _PRICE
            if split_day is not None and d >= split_day and sid in sids:
                c = _PRICE * ratio
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 10_000_000,
                    "amount": c * 10_000_000,
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
    if split_day is not None and with_events:
        etype = "split" if ratio < 1.0 else "reverse_split"
        ev = pl.DataFrame(
            [
                {
                    "date": split_day,
                    "stock_id": sid,
                    "event_type": etype,
                    "adj_factor": ratio,
                    "ref_price": None,
                    "available_at": dt.datetime(
                        split_day.year, split_day.month, split_day.day, 18, 0, tzinfo=TZ
                    ),
                }
                for sid in sids
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


def _cfg() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "split_test",
            "description": "分割調股數測試",
            "trial_scope": "test",
            "universe": {"markets": ["TWSE"], "min_adv_twd": None, "min_price": None},
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "selection": {"method": "top_n", "top_n": 3},
            "weighting": "equal",
            "max_weight_per_stock": 0.4,
            "rebalance": {"schedule": "monthly", "day": "MON", "execution": "next_open"},
        }
    )


def _run(wh: Warehouse, days: list[dt.date]) -> BacktestResult:
    return run_backtest(
        _cfg(),
        load_config(_CONFIG_DIR / "costs.yaml", CostConfig),
        days[0],
        days[-1],
        wh,
        initial_capital=_CAPITAL,
    )


def _equity_on(res: BacktestResult, day: dt.date) -> float:
    for p in res.equity:
        if p.date == day:
            return p.equity
    raise AssertionError(f"權益曲線沒有 {day}")


def test_ac1_equity_is_continuous_through_a_1_for_4_split(tmp_path: Path) -> None:
    """AC-3：1:4 分割前後權益連續——價格掉 75%，但股數變四倍，部位價值不變。"""
    days = _sessions(60)
    split = days[42]  # 避開再平衡日，才能把權益的變化單獨歸因於分割
    res = _run(_wh(tmp_path, days, split_day=split, ratio=0.25), days)
    before = _equity_on(res, days[41])
    after = _equity_on(res, split)
    assert after == pytest.approx(before, rel=1e-9), f"分割前 {before:,.0f}、分割後 {after:,.0f}"


def test_ac2_without_the_fix_the_jump_guard_would_have_dumped_the_position(
    tmp_path: Path,
) -> None:
    """AC-2：調整後不得觸發跳價防護。

    對照組是**同樣的價格跳水但沒有事件紀錄**——那才是真的 −75%，部位應該被清掉。
    兩者的差別只有 `corporate_events` 有沒有那一筆，藉此證明修正生效的是事件路徑。
    """
    days = _sessions(60)
    split = days[42]  # 避開再平衡日，才能把權益的變化單獨歸因於分割
    with_ev = _run(_wh(tmp_path / "a", days, split_day=split, ratio=0.25), days)
    without_ev = _run(
        _wh(tmp_path / "b", days, split_day=split, ratio=0.25, with_events=False), days
    )
    assert _equity_on(with_ev, split) == pytest.approx(_equity_on(with_ev, days[41]), rel=1e-9)
    # 三檔等權，只有一檔分割 → 沒有事件紀錄時組合約掉 1/3 × 75% ≈ 25%
    hit = _equity_on(without_ev, split) / _equity_on(without_ev, days[41])
    assert 0.70 < hit < 0.80, f"沒有事件紀錄時該吃到約 −25%，實際 {hit - 1:+.1%}"


def test_ac3_reverse_split_multiplies_the_price_and_divides_the_shares(tmp_path: Path) -> None:
    """反分割（adj_factor > 1）方向相反：價格 ×4、股數 ÷4，權益一樣連續。

    `_dividends_by_date` 刻意只收 adj_factor < 1，所以反分割**從來沒有任何路徑處理過**。
    """
    days = _sessions(60)
    split = days[42]  # 避開再平衡日，才能把權益的變化單獨歸因於分割
    res = _run(_wh(tmp_path, days, split_day=split, ratio=4.0), days)
    assert _equity_on(res, split) == pytest.approx(_equity_on(res, days[41]), rel=1e-9)


def test_ac4_cost_basis_moves_with_the_shares_not_the_money(tmp_path: Path) -> None:
    """AC-1：調的是股數與成本股數，`cost_amount` 不動——同一筆錢換到更多股。

    若誤把金額也縮放，均價會維持不變，於是分割後的報酬會憑空多出 1/ratio 倍。
    這裡用期末權益驗證：全程只有分割、沒有真實漲跌，期末權益應等於初始資金扣成本。
    """
    days = _sessions(60)
    split = days[42]  # 避開再平衡日，才能把權益的變化單獨歸因於分割
    with_split = _run(_wh(tmp_path / "a", days, split_day=split, ratio=0.25), days)
    no_split = _run(_wh(tmp_path / "b", days), days)
    # 比**分割隔日**而非期末：分割後該檔會被宇宙衛生濾網踢出候選，
    # 下一個再平衡日會把它賣掉，期末就不只差分割這一件事了。
    assert _equity_on(with_split, days[43]) == pytest.approx(
        _equity_on(no_split, days[43]), rel=1e-6
    ), "只有分割、沒有真實漲跌時，分割隔日的權益應與完全沒有分割的情形相同"


def test_ac5_a_split_in_a_stock_we_do_not_hold_changes_nothing(tmp_path: Path) -> None:
    """沒持有的股票分割不得影響權益（只更新 `last_close`，不碰現金與部位）。"""
    days = _sessions(60)
    split = days[42]  # 避開再平衡日，才能把權益的變化單獨歸因於分割
    res = _run(
        _wh(tmp_path, days, split_day=split, ratio=0.25, sids=("9999",)), days
    )  # 不存在的代號
    assert _equity_on(res, split) == pytest.approx(_equity_on(res, days[41]), rel=1e-9)
