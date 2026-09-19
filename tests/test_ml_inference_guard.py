"""T-227：推論路徑的特徵存在閘門。

實際踩到的失效模式：`index_daily` 是月頻 `ingest-index`，從未接進每日迴圈，
於是 2026-09-02~09-09 期間它落後 `prices_daily` 六個交易日，
`price.rs_market_20d` 整欄空——而 LightGBM 把缺欄當 NaN 照樣吐分數，
`ml score` 回報「[OK] 1961 檔」，12 個特徵少 1 個，沒有任何一關報錯。
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from twquant.errors import DataError
from twquant.ml.dataset import assert_feature_coverage, assert_inference_features_present

DAY = dt.date(2026, 9, 7)


def _frame(values: list[float | None]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [DAY] * len(values),
            "stock_id": [f"{1000 + i}" for i in range(len(values))],
            "value": values,
        },
        schema={"date": pl.Date, "stock_id": pl.String, "value": pl.Float64},
    )


def test_ac1_all_present_passes() -> None:
    """每個必要特徵都有值時放行。"""
    feats = {"price.mom_20d": _frame([1.0, 2.0]), "price.rs_market_20d": _frame([0.5, 0.7])}
    assert_inference_features_present(feats, list(feats), DAY)


def test_ac2_entirely_null_feature_raises() -> None:
    """整欄 null 即 raise，並在訊息裡指名是哪一個特徵。"""
    feats = {"price.mom_20d": _frame([1.0, 2.0]), "price.rs_market_20d": _frame([None, None])}
    with pytest.raises(DataError, match=r"price\.rs_market_20d"):
        assert_inference_features_present(feats, list(feats), DAY)


def test_ac2_missing_frame_raises() -> None:
    """該日連列都沒有（上游整個沒產出）同樣 raise，不得當成「這天剛好沒資料」放行。"""
    feats = {"price.mom_20d": _frame([1.0, 2.0])}
    with pytest.raises(DataError, match=r"price\.rs_market_20d"):
        assert_inference_features_present(feats, ["price.mom_20d", "price.rs_market_20d"], DAY)


def test_ac2_zero_row_frame_raises() -> None:
    """空表（height=0）與整欄 null 是同一件事。"""
    feats = {"price.mom_20d": _frame([1.0]), "price.rs_market_20d": _frame([])}
    with pytest.raises(DataError, match=r"price\.rs_market_20d"):
        assert_inference_features_present(feats, list(feats), DAY)


def test_ac3_sparse_feature_is_allowed() -> None:
    """**稀疏是正常的，不得擋。**

    月營收類因子本來就只有九成多的股票有值；推論看的是「今天這格資料到了沒」，
    不是「這個特徵在整段樣本裡是不是白佔欄位」——後者是訓練的問題。
    """
    feats = {"fundamental.revenue_yoy": _frame([None, None, None, 0.3])}
    assert_inference_features_present(feats, list(feats), DAY)


def test_ac4_differs_from_training_gate() -> None:
    """同一份稀疏資料：推論閘門放行，訓練閘門（門檻更嚴）也放行；全空則兩者都擋。

    兩個閘門問的是不同問題，這裡把界線釘住，避免日後有人把它們合成一個。
    """
    sparse = {"fundamental.revenue_yoy": _frame([None, None, None, 0.3])}
    assert_inference_features_present(sparse, list(sparse), DAY)
    assert assert_feature_coverage(sparse)["fundamental.revenue_yoy"] == pytest.approx(0.25)

    dead = {"price.rs_market_20d": _frame([None, None])}
    with pytest.raises(DataError):
        assert_inference_features_present(dead, list(dead), DAY)
    with pytest.raises(ValueError):
        assert_feature_coverage(dead)


def test_ac5_only_required_features_are_checked() -> None:
    """模型沒用到的特徵即使全空也不該擋——閘門的對象是「這個模型要的」。"""
    feats = {"price.mom_20d": _frame([1.0]), "unused.factor": _frame([None])}
    assert_inference_features_present(feats, ["price.mom_20d"], DAY)
