"""T-288／D-143：右尾標籤——未來 N 日內最大漲幅是否達門檻（絕對、二元）。

與前八輪用的 `cost_adjusted_return`（橫斷面排名）的差別：那個問「比同日其他股票好多少」，
所以排出來的是前段班、扣掉成本後接近指數；這個問「會不會飆」，是絕對事件。
與 `triple_barrier` 的差別：**先跌不取消後漲**——路徑交給出場規則管，不混進標籤。
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from twquant.config import MlLabelConfig
from twquant.ml.dataset import tail_up_labels
from twquant.ml.train import objective_for

D0 = dt.date(2024, 1, 1)


def _px(closes: list[float], opens: list[float] | None = None, sid: str = "2330") -> pl.DataFrame:
    closes = [float(c) for c in closes]
    opens = [float(o) for o in opens] if opens else None
    n = len(closes)
    return pl.DataFrame(
        {
            "date": [D0 + dt.timedelta(days=i) for i in range(n)],
            "stock_id": [sid] * n,
            "open": opens or closes,
            "close": closes,
        }
    )


def _lab(df: pl.DataFrame, day: int, horizon: int = 5, up: float = 0.50) -> float | None:
    out = tail_up_labels(df, horizon=horizon, up=up)
    row = out.filter(pl.col("date") == D0 + dt.timedelta(days=day))
    return None if row.is_empty() else row["label"][0]


def test_ac1_touching_the_up_barrier_is_one() -> None:
    assert _lab(_px([100, 100, 120, 151, 140, 140, 140, 140]), 0) == 1.0


def test_ac2_not_touching_is_zero() -> None:
    assert _lab(_px([100, 100, 120, 140, 145, 148, 148, 148]), 0) == 0.0


def test_ac3_measured_from_next_open_not_signal_close() -> None:
    """訊號日收盤 100、隔日開盤跳空到 120：那段跳空買不到，漲幅要從 120 起算。

    用收盤當基準會把買不到的跳空算成模型的功勞——這是整個標籤最容易造假的一點。
    """
    closes = [100.0, 120.0, 165.0, 165.0, 165.0, 165.0, 165.0, 165.0]
    assert _lab(_px(closes, closes), 0) == 0.0  # 165/120−1 = +37.5%，沒到 +50%
    closes2 = [100.0, 120.0, 181.0, 181.0, 181.0, 181.0, 181.0, 181.0]
    assert _lab(_px(closes2, closes2), 0) == 1.0  # 181/120−1 = +50.8%


def test_ac4_a_drop_first_does_not_cancel_a_later_rise() -> None:
    """與 triple_barrier 不同：這裡只問「有沒有漲到」，先跌不取消。

    會不會被中途回檔洗掉是**出場規則**（D-143 §四 的 ratchet）的問題；
    混進標籤就變成同時在學「會漲」與「不會先跌」兩件事。
    """
    assert _lab(_px([100, 100, 70, 70, 151, 151, 151, 151]), 0) == 1.0


def test_ac5_incomplete_horizon_is_null_not_zero() -> None:
    """往後不足 horizon 列＝沒有答案。記 0 會把「資料到期」教成「沒飆」。"""
    df = _px([100, 100, 100, 100, 100, 100, 100, 100])
    assert _lab(df, 6) is None
    assert _lab(df, 7) is None
    assert _lab(df, 0) == 0.0


def test_ac6_each_stock_is_labelled_independently() -> None:
    df = pl.concat(
        [
            _px([100, 100, 160, 160, 160, 160, 160, 160], sid="2330"),
            _px([100, 100, 105, 105, 105, 105, 105, 105], sid="2454"),
        ]
    )
    out = tail_up_labels(df, horizon=5, up=0.50)
    first = out.filter(pl.col("date") == D0)
    assert first.filter(pl.col("stock_id") == "2330")["label"][0] == 1.0
    assert first.filter(pl.col("stock_id") == "2454")["label"][0] == 0.0


def test_ac7_rejects_bad_arguments() -> None:
    df = _px([100.0] * 8)
    with pytest.raises(ValueError, match="horizon"):
        tail_up_labels(df, horizon=0, up=0.5)
    with pytest.raises(ValueError, match="up"):
        tail_up_labels(df, horizon=5, up=0.0)
    with pytest.raises(ValueError, match="缺欄位"):
        tail_up_labels(df.drop("open"), horizon=5, up=0.5)


def test_ac8_config_requires_up_and_forbids_down() -> None:
    """右尾只有上緣：給了 barrier_down 是寫了卻不生效的配置（no-op 缺陷）。"""
    ok = MlLabelConfig(transform="tail_up", barrier_up=0.5, total_return=True)
    assert ok.barrier_up == 0.5
    with pytest.raises(ValueError, match="tail_up 須給 barrier_up"):
        MlLabelConfig(transform="tail_up", total_return=True)
    with pytest.raises(ValueError, match="不得給 barrier_down"):
        MlLabelConfig(transform="tail_up", barrier_up=0.5, barrier_down=0.15, total_return=True)


def test_ac9_objective_is_binary() -> None:
    """二元標籤要的是機率；用回歸會吐出 [0,1] 以外的數字。"""
    assert objective_for("tail_up") == "binary"
