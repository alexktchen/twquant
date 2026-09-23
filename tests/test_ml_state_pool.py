"""T-303／D-151：只在某種「狀態」的股票上訓練與預測——安靜股（模型 A）、已飆股（模型 B）。

D-143 §十二 量到：讓模型看全部股票，它就走捷徑挑已經在飆的（配對倍率 1.08×）。
這裡守的是篩選本身：只用 t 日（含）以前的資料、門檻方向正確、兩種狀態的設定不能寫錯，
以及沒設 `sample_state` 時既有模型的 id 逐位元不變。
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from twquant.config import MlConfig, MlSampleStateConfig
from twquant.ml.dataset import state_pool
from twquant.ml.train import _hashable_dump

_QUIET = MlSampleStateConfig(
    kind="quiet", past_window=60, max_past_ret=0.20, vol_window=20, max_vol_pctl=0.70
)
_HOT = MlSampleStateConfig(kind="hot", past_window=20, min_past_ret=0.30)


def _days(n: int) -> list[dt.date]:
    out: list[dt.date] = []
    d = dt.date(2025, 1, 6)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _px(paths: dict[str, list[float]]) -> pl.DataFrame:
    days = _days(len(next(iter(paths.values()))))
    return pl.DataFrame(
        [
            {"date": days[i], "stock_id": sid, "close": c, "adj": 1.0}
            for sid, closes in paths.items()
            for i, c in enumerate(closes)
        ]
    )


_N = 100
_CALM = [100.0 * (1.0 + (0.001 if i % 2 else -0.001)) for i in range(_N)]  # 幾乎不動


def _members(pool: pl.DataFrame, day: dt.date) -> set[str]:
    return set(pool.filter(pl.col("date") == day)["stock_id"].to_list())


def _universe() -> dict[str, list[float]]:
    """十檔平靜股當背景（讓波動百分位有意義），外加三個主角。"""
    paths = {f"11{i:02d}": [c * (1.0 + 0.0005 * i) for c in _CALM] for i in range(10)}
    paths["2330"] = [100.0 * 1.006**i for i in range(_N)]  # 60 日漲約 43%：已經在飆
    paths["2454"] = [100.0 * 1.001**i for i in range(_N)]  # 60 日漲約 6%：安靜
    paths["3008"] = [100.0 * (1.08 if i % 2 else 0.92) for i in range(_N)]  # 不漲但波動極大
    return paths


def test_ac1_quiet_excludes_recent_winners_and_the_most_volatile() -> None:
    px = _px(_universe())
    day = _days(_N)[-1]
    got = _members(state_pool(px, _QUIET, adjusted=True), day)
    assert "2454" in got
    assert "2330" not in got, "過去 60 日已漲 43% 不是安靜股"
    assert "3008" not in got, "波動在全體最高的 30% 不是安靜股"


def test_ac2_hot_keeps_only_recent_big_winners() -> None:
    paths = _universe()
    paths["6669"] = [100.0] * (_N - 20) + [100.0 * 1.015**k for k in range(1, 21)]  # 20 日約 +35%
    day = _days(_N)[-1]
    got = _members(state_pool(_px(paths), _HOT, adjusted=True), day)
    assert got == {"6669"}


def test_ac3_membership_uses_only_the_past() -> None:
    """把 t 日之後的價格改掉，t 日（含）以前的名單逐列不變。"""
    paths = _universe()
    px = _px(paths)
    cut = _days(_N)[70]
    poked = px.with_columns(
        pl.when(pl.col("date") > cut).then(pl.col("close") * 3.0).otherwise(pl.col("close"))
    )
    for cfg in (_QUIET, _HOT):
        a = state_pool(px, cfg, adjusted=True).filter(pl.col("date") <= cut)
        b = state_pool(poked, cfg, adjusted=True).filter(pl.col("date") <= cut)
        assert a.equals(b)


def test_ac4_adjusted_price_hides_a_reverse_split_from_the_past_return() -> None:
    """2:1 反分割讓收盤翻倍：不還原的話過去 60 日「漲了 +100%」，會被踢出安靜股、誤入已飆股。

    判斷日選在反分割後第 29 個交易日：20 日波動窗已不含反分割那天（波動條件照登記沿用
    `price.vol_20d` 的算法、用未還原收盤，分割日的跳動會讓它之後 20 天被當成高波動——登記內的近似），
    而 60 日報酬窗仍橫跨它，正好只考還原價這一件事。
    """
    paths = _universe()
    close = [100.0 * 1.001**i for i in range(_N)]
    at = 70
    px = _px({**paths, "2454": [c * 2.0 if i >= at else c for i, c in enumerate(close)]})
    px = px.with_columns(
        pl.when((pl.col("stock_id") == "2454") & (pl.col("date") >= _days(_N)[at]))
        .then(2.0)
        .otherwise(1.0)
        .alias("adj")
    )
    day = _days(_N)[-1]
    assert "2454" in _members(state_pool(px, _QUIET, adjusted=True), day)
    assert "2454" not in _members(state_pool(px, _HOT, adjusted=True), day)
    assert "2454" not in _members(state_pool(px, _QUIET, adjusted=False), day)  # 不還原就錯


def test_ac5_config_shapes_are_enforced() -> None:
    with pytest.raises(ValueError):
        MlSampleStateConfig(kind="quiet", past_window=60, max_past_ret=0.2)  # 缺波動條件
    with pytest.raises(ValueError):
        MlSampleStateConfig(kind="hot", past_window=20, min_past_ret=0.3, vol_window=20)
    with pytest.raises(ValueError):
        MlSampleStateConfig(
            kind="quiet",
            past_window=60,
            max_past_ret=0.2,
            min_past_ret=0.3,
            vol_window=20,
            max_vol_pctl=0.7,
        )


def test_ac6_unset_state_keeps_existing_model_ids() -> None:
    """沒設 sample_state 時不進訓練指紋——既有模型的目錄名與 ml_scores 的 id 不能因此改變。"""
    assert "sample_state" not in _hashable_dump(MlConfig())
    with_state = MlConfig.model_validate({"sample_state": _QUIET.model_dump()})
    assert "sample_state" in _hashable_dump(with_state)


def test_ac7_ml_dataset_imports_first_without_a_cycle() -> None:
    """全新程序**先**匯入 `ml.dataset`（`ml train` 就是這樣）不得循環匯入。

    `ml.dataset` 匯入 `backtest.costs` 會觸發 `backtest/__init__` → engine → exposure；
    exposure 若在模組層回頭匯入 `ml.dataset` 就炸。同一程序裡先匯入過 backtest 的測試抓不到這件事。
    """
    import subprocess
    import sys

    for mod in ("twquant.ml.dataset", "twquant.backtest.exposure", "twquant.cli"):
        run = subprocess.run(
            [sys.executable, "-c", f"import {mod}"], capture_output=True, text=True, check=False
        )
        assert run.returncode == 0, f"先匯入 {mod} 失敗：{run.stderr[-500:]}"
