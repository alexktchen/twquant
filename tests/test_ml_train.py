"""T-067：訓練/推論核心（SPEC-010 §1/§4，AC-4/AC-5/AC-8）。

契約（由本測試凍結）：
- `fit_ranker(frame, feature_cols, lgbm)`：對 label 非 null 列訓練 LightGBM 回歸；
  決定性旗標由實作固定注入——同輸入同 seed 兩次，預測 bit-identical（AC-4）。
- `walk_forward_train(frame, splits, feature_cols, lgbm)`：逐窗訓練→測試窗推論，
  回傳 OOS 分數表（date, stock_id, score, label）與各窗 RankIC；決定性同上。
- `save_model`/`load_model`：model.txt + meta.json 落地與載回，預測 roundtrip 相等；
  `load_model(dir, as_of)` 於 train_end ≥ as_of 或 meta 缺欄位時 raise（AC-5）。
- `latest_model_dir(root, as_of)`：取 train_end < as_of 的最新模型目錄；無 → raise。
- `log_training_run`：每次訓練 append research_log 一列，params_hash＝config_hash(cfg)，
  trial_scope 綁 horizon（AC-8，trials 計數供 DSR）。
- `MlConfig`：configs/ml.yaml 可載入；embargo_days < horizon_days → 驗證失敗。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import polars as pl
import pytest
from pydantic import ValidationError

from twquant.config import MlConfig, MlLgbmConfig, config_hash, load_config
from twquant.ml.score import latest_model_dir, load_model
from twquant.ml.splits import walk_forward_splits
from twquant.ml.train import (
    fit_ranker,
    log_training_run,
    make_model_id,
    oos_rank_ic,
    predict_scores,
    save_model,
    walk_forward_train,
)
from twquant.reporting.research_log import read_research_log

_ROOT = Path(__file__).resolve().parent.parent
_LGBM = MlLgbmConfig(num_leaves=7, learning_rate=0.1, min_data_in_leaf=5, n_estimators=25)
_FEATS = ["f1", "f2"]


def _frame(n_days: int = 12, n_stocks: int = 40) -> pl.DataFrame:
    """合成資料：f1 帶訊號、f2 弱噪音，label＝當日百分位。決定性（無亂數源）。"""
    rows = []
    for di in range(n_days):
        d = dt.date(2026, 1, 5) + dt.timedelta(days=di)
        for si in range(n_stocks):
            f1 = ((si * 7 + di * 3) % n_stocks) / n_stocks - 0.5
            f2 = ((si * 13 + di * 11) % 17) / 17 - 0.5
            rows.append((d, f"{1000 + si}", f1, f2, f1 * 0.8 + f2 * 0.05))
    df = pl.DataFrame(
        rows, schema=["date", "stock_id", "f1", "f2", "raw"], orient="row"
    ).with_columns(
        (pl.col("raw").rank("average").over("date") / pl.col("raw").count().over("date")).alias(
            "label"
        )
    )
    return df.drop("raw")


# ── AC-4：決定性 ─────────────────────────────────────────────────────


def test_ac4_fit_and_predict_deterministic() -> None:
    df = _frame()
    a = predict_scores(fit_ranker(df, _FEATS, _LGBM), df, _FEATS)
    b = predict_scores(fit_ranker(df, _FEATS, _LGBM), df, _FEATS)
    assert a.equals(b)  # 同 config 同輸入 → bit-identical


def test_ac4_walk_forward_deterministic_and_shape() -> None:
    df = _frame()
    days = sorted(df["date"].unique().to_list())
    splits = walk_forward_splits(days, train_min_days=6, test_days=2, embargo_days=1)
    r1 = walk_forward_train(df, splits, _FEATS, _LGBM)
    r2 = walk_forward_train(df, splits, _FEATS, _LGBM)
    assert r1.oos_scores.equals(r2.oos_scores)
    assert r1.window_ics == r2.window_ics
    assert len(r1.window_ics) == len(splits)
    # OOS 分數恰覆蓋各測試窗（不含訓練/embargo 日）
    test_days_all = {d for s in splits for d in days if s.test_start <= d <= s.test_end}
    assert set(r1.oos_scores["date"].unique().to_list()) == test_days_all
    # 訊號存在（f1 主導 label）→ 平均 OOS RankIC 應為正
    assert oos_rank_ic(r1.oos_scores) > 0.3


def test_ac4_save_load_roundtrip(tmp_path: Path) -> None:
    df = _frame()
    booster = fit_ranker(df, _FEATS, _LGBM)
    before = predict_scores(booster, df, _FEATS)
    mdir = tmp_path / "models" / "m1"
    save_model(mdir, booster, {"train_end": "2026-01-16", "features": _FEATS})
    loaded, meta = load_model(mdir, as_of=dt.date(2026, 1, 17))
    assert meta["features"] == _FEATS
    assert predict_scores(loaded, df, _FEATS).equals(before)


def test_fit_all_null_labels_raises() -> None:
    df = _frame().with_columns(pl.lit(None, dtype=pl.Float64).alias("label"))
    with pytest.raises(ValueError, match="label"):
        fit_ranker(df, _FEATS, _LGBM)


# ── oos_rank_ic 錨點 ─────────────────────────────────────────────────


def test_oos_rank_ic_bounds() -> None:
    df = _frame(n_days=3)
    perfect = df.select("date", "stock_id", pl.col("label").alias("score"), "label")
    inverted = df.select("date", "stock_id", (-pl.col("label")).alias("score"), "label")
    assert oos_rank_ic(perfect) == pytest.approx(1.0)
    assert oos_rank_ic(inverted) == pytest.approx(-1.0)


# ── AC-5：載入斷言（反前視）──────────────────────────────────────────


def _fake_model_dir(root: Path, name: str, train_end: str) -> Path:
    d = root / name
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"train_end": train_end}), encoding="utf-8")
    return d


def test_ac5_load_model_refuses_future_model(tmp_path: Path) -> None:
    df = _frame()
    mdir = tmp_path / "m"
    save_model(mdir, fit_ranker(df, _FEATS, _LGBM), {"train_end": "2026-06-30"})
    with pytest.raises(ValueError, match="前視"):
        load_model(mdir, as_of=dt.date(2026, 6, 30))  # train_end == as_of 也違規
    with pytest.raises(ValueError, match="前視"):
        load_model(mdir, as_of=dt.date(2026, 5, 1))


def test_ac5_meta_missing_train_end_raises(tmp_path: Path) -> None:
    d = tmp_path / "m"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"features": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="train_end"):
        load_model(d, as_of=dt.date(2026, 8, 1))


def test_ac5_latest_model_dir_picks_newest_valid(tmp_path: Path) -> None:
    _fake_model_dir(tmp_path, "a", "2026-06-30")
    newest = _fake_model_dir(tmp_path, "b", "2026-07-31")
    assert latest_model_dir(tmp_path, as_of=dt.date(2026, 8, 5)) == newest
    assert latest_model_dir(tmp_path, as_of=dt.date(2026, 7, 15)) == tmp_path / "a"
    with pytest.raises(ValueError, match="無可用模型"):
        latest_model_dir(tmp_path, as_of=dt.date(2026, 6, 30))


# ── AC-8：實驗登記 ───────────────────────────────────────────────────


def test_ac8_log_training_run_appends_one_row(tmp_path: Path) -> None:
    cfg = MlConfig()
    log_training_run(tmp_path, cfg, model_id="lgbm_h10_x", oos_ic=0.042)
    log = read_research_log(tmp_path)
    assert log.height == 1
    row = log.to_dicts()[0]
    assert row["entry_type"] == "ml_train"
    assert row["strategy"] == "lgbm_h10_x"
    assert row["params_hash"] == config_hash(cfg)
    assert row["metric_name"] == "oos_rank_ic"
    assert row["metric_value"] == pytest.approx(0.042)
    assert row["trial_scope"] == "ml:h10"


def test_model_id_binds_horizon_train_end_and_config() -> None:
    cfg = MlConfig()
    mid = make_model_id(dt.date(2026, 7, 31), cfg)
    assert mid.startswith("lgbm_h10_20260731_")
    assert config_hash(cfg)[:8] in mid


# ── MlConfig ─────────────────────────────────────────────────────────


def test_ml_yaml_loads_and_converts() -> None:
    cfg = load_config(_ROOT / "configs" / "ml.yaml", MlConfig)
    assert cfg.horizon_days == 10
    assert cfg.split.embargo_days >= cfg.horizon_days
    assert cfg.split.train_min_days == int(cfg.split.train_min_years * cfg.split.days_per_year)
    assert cfg.split.test_days == cfg.split.test_months * cfg.split.days_per_month
    assert cfg.split.vault_days == cfg.split.vault_months * cfg.split.days_per_month


def test_ml_config_embargo_must_cover_horizon() -> None:
    with pytest.raises(ValidationError, match="embargo"):
        MlConfig.model_validate({"horizon_days": 10, "split": {"embargo_days": 5}})
