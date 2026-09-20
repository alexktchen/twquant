"""D-092 P2：長窗口模型的薄因子 `ml.lgbm_lw_*`。

契約：
- AC-1 三個因子已註冊，類別／方向／前綴正確。
- AC-2 **前綴互不命中**：ml_scores 同時有 `lgbm_net_h20_*` 與 `lgbm_lw_net_h20_*` 時，
  `ml.lgbm_net_20d` 只回短窗口、`ml.lgbm_lw_net_20d` 只回長窗口。
  這是 T-172 把 sample_tag 放在 `lgbm_` 之後的理由——放尾端就會互相命中，
  而那會讓既有 22 支策略在長窗口回測時**靜默**吃到別條線的分數。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

import twquant.factors.builtins  # noqa: F401  # 註冊因子
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors.registry import get

_DAY = dt.date(2026, 1, 5)
_SHORT_ID = "lgbm_net_h20_20260225_6a798962"
_LONG_ID = "lgbm_lw_net_h20_20260225_83890036"


def _wh(tmp_path: Path) -> Warehouse:
    """同一天、同一檔股票，兩個模型各一列分數——只有前綴能區分它們。"""
    rows = [
        {
            "date": _DAY,
            "stock_id": "2330",
            "model_id": mid,
            "score": rank,
            "rank_pct": rank,
            "available_at": dt.datetime.combine(_DAY, dt.time(18), tzinfo=TZ),
        }
        for mid, rank in ((_SHORT_ID, 0.10), (_LONG_ID, 0.90))
    ]
    df = pl.DataFrame(rows).with_columns(pl.col("available_at").cast(schemas.TW_DT))
    wh = Warehouse(tmp_path)
    wh.write("clean", "ml_scores", df)
    return wh


def test_ac1_registered() -> None:
    for name, prefix in (
        ("ml.lgbm_lw_net_20d", "lgbm_lw_net_h20_"),
        ("ml.lgbm_lw_rev_15d", "lgbm_lw_rev_h15_"),
        ("ml.lgbm_lw_dd_60d", "lgbm_lw_dd_h60_"),
    ):
        fdef = get(name)
        assert fdef.category == "ml"
        assert fdef.direction == 1
        assert fdef.params["model_prefix"] == prefix


def test_ac2_prefixes_do_not_cross(tmp_path: Path) -> None:
    view = _wh(tmp_path).view(dt.date(2026, 1, 6))
    fs, fl = get("ml.lgbm_net_20d"), get("ml.lgbm_lw_net_20d")
    assert fs.fn is not None and fl.fn is not None
    assert fs.fn(view, dict(fs.params))["value"].to_list() == [0.10]
    assert fl.fn(view, dict(fl.params))["value"].to_list() == [0.90]


def test_ac5_tb_factor_returns_the_probability_not_the_rank(tmp_path: Path) -> None:
    """D-122：每日補位的進場門檻是**絕對機率**。薄因子若回 rank_pct，崩盤日照樣有 0.99。"""
    rows = [
        {
            "date": _DAY,
            "stock_id": sid,
            "model_id": "lgbm_lw_tb_h20_20260225_a7cee0de_cal",
            "score": prob,
            "rank_pct": rank,
            "available_at": dt.datetime.combine(_DAY, dt.time(18), tzinfo=TZ),
        }
        for sid, prob, rank in (("2330", 0.12, 1.0), ("0050", 0.05, 0.5))
    ]
    wh = Warehouse(tmp_path)
    wh.write(
        "clean",
        "ml_scores",
        pl.DataFrame(rows).with_columns(pl.col("available_at").cast(schemas.TW_DT)),
    )
    fdef = get("ml.lgbm_lw_tb_up_20d")
    assert fdef.fn is not None and fdef.params["model_prefix"] == "lgbm_lw_tb_h20_"
    out = fdef.fn(wh.view(dt.date(2026, 1, 6)), dict(fdef.params)).sort("stock_id")
    assert out["stock_id"].to_list() == ["0050", "2330"]
    assert out["value"].to_list() == [0.05, 0.12]  # 全市場最高也只有 12%——沒人過 70% 門檻
