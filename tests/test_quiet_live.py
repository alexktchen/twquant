"""T-318／D-163：潛伏飆股每日推論的池子與防呆（`ml/quiet_live.py`）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.config import MlConfig, load_config
from twquant.data import schemas
from twquant.errors import DataError
from twquant.ml.quiet_live import inference_frame, score_rows

ROOT = Path(__file__).resolve().parents[1] / "configs"
DAYS = [dt.date(2026, 1, 1) + dt.timedelta(days=i) for i in range(90)]
SIDS = [f"{1101 + i}" for i in range(10)]
HOT = "1101"  # 60 日漲了一倍：不安靜
THIN = "1102"  # 成交金額墊底：不流動


def _cfg() -> MlConfig:
    return load_config(ROOT / "ml_quiet_a20.yaml", MlConfig).model_copy(
        update={"features": ["price.mom_20d"], "context_features": []}
    )


def _px() -> pl.DataFrame:
    rows = []
    for i, d in enumerate(DAYS):
        for k, sid in enumerate(SIDS):
            close = 50.0 * (1.0 + i / 60) if sid == HOT else 50.0 + (0.5 if (i + k) % 2 else -0.5)
            amount = 1.0e6 if sid == THIN else 1.0e9 + k * 1.0e7
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": close,
                    "close": close,
                    "amount": amount,
                    "adj": 1.0,
                }
            )
    return pl.DataFrame(rows)


def _feats(null_on: dt.date | None = None) -> dict[str, pl.DataFrame]:
    rows = [
        {"date": d, "stock_id": s, "value": None if d == null_on else 0.1}
        for d in DAYS
        for s in SIDS
    ]
    return {
        "price.mom_20d": pl.DataFrame(
            rows, schema={"date": pl.Date, "stock_id": pl.String, "value": pl.Float64}
        )
    }


def test_ac1_only_quiet_and_liquid_names_are_scored() -> None:
    day = DAYS[-1]
    frame = inference_frame(_px(), _feats(), pl.DataFrame(), _cfg(), ["price.mom_20d"], day, day)
    ids = set(frame["stock_id"].to_list())
    assert ids, "池子不該是空的"
    assert HOT not in ids, "60 日漲一倍的股票不是安靜股"
    assert THIN not in ids, "成交金額墊底的股票不在流動池"
    assert frame["date"].unique().to_list() == [day]


def test_ac2_a_feature_empty_for_a_whole_day_stops_scoring() -> None:
    day = DAYS[-1]
    with pytest.raises(DataError, match="整欄空"):
        inference_frame(
            _px(), _feats(null_on=day), pl.DataFrame(), _cfg(), ["price.mom_20d"], day, day
        )


def test_ac2_models_without_a_state_pool_are_rejected() -> None:
    cfg = _cfg().model_copy(update={"sample_state": None})
    with pytest.raises(ValueError, match="sample_state"):
        inference_frame(_px(), _feats(), pl.DataFrame(), cfg, ["price.mom_20d"], DAYS[-1], DAYS[-1])


class _Booster:
    def predict(self, x: object) -> list[float]:
        return [0.3, 0.1]


def test_ac3_rows_match_the_ml_scores_schema_and_available_at_is_the_nominal_cutoff() -> None:
    day = dt.date(2026, 9, 24)
    frame = pl.DataFrame({"date": [day, day], "stock_id": ["2330", "0050"], "f": [1.0, 2.0]})
    rows = score_rows(frame, _Booster(), ["f"], "lgbm_quietA20_x")  # type: ignore[arg-type]
    schemas.validate("ml_scores", rows)
    assert rows["rank_pct"].to_list() == [1.0, 0.5]
    assert rows["stock_id"].to_list() == ["2330", "0050"]
    stamp = rows["available_at"][0]
    assert (stamp.hour, stamp.date()) == (19, day)  # T-228：名目 19:00，不是執行時刻
