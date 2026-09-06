"""T-068：ml_scores 資料合約＋薄因子（SPEC-010 §5/§6，AC-6/AC-7/AC-9）。

契約（由本測試凍結）：
- `ml_scores` pandera schema：(date, stock_id, model_id, score, rank_pct, available_at)，
  驗證失敗 raise QualityGateError（AC-7）；clean 層寫入即過驗證。
- 因子 `ml.lgbm_rank_10d`（category="ml"）：讀 `view.table("ml_scores")`，取
  model_id 前綴（params.model_prefix）系列的 rank_pct；同 (date, stock) 多模型
  取 model_id 字典序最新（id 含 train_end 日期，字典序＝新舊）；PIT-safe，
  必過 assert_no_lookahead（AC-6）。
- `cross_rank_pct`（dataset）：同日橫斷面百分位 (0,1]、null 傳遞——`ml score`
  寫 rank_pct 用。
- IC 對照（AC-9）：compute_ic_report 可對 ml 因子輸出與基準因子輸出同介面產出。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401  # 副作用：註冊全因子（含 ml.*）
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.errors import QualityGateError
from twquant.factors.ic import compute_ic_report
from twquant.factors.registry import get
from twquant.factors.testing import assert_no_lookahead
from twquant.ml.dataset import cross_rank_pct

_DAYS = [dt.date(2026, 6, 1) + dt.timedelta(days=i) for i in range(8)]  # 8 個連續曆日
_SIDS = ["1101", "2330", "0050"]


def _tw(days: list[dt.date], hour: int) -> pl.Series:
    return pl.Series(
        [dt.datetime(d.year, d.month, d.day, hour, 0, tzinfo=TZ) for d in days],
        dtype=schemas.TW_DT,
    )


def _scores(rows: list[tuple[dt.date, str, str, float, float]], hour: int = 19) -> pl.DataFrame:
    """(date, stock_id, model_id, score, rank_pct) → ml_scores 批（available_at 當日 hour 時）。"""
    dates = [r[0] for r in rows]
    return pl.DataFrame(
        {
            "date": dates,
            "stock_id": [r[1] for r in rows],
            "model_id": [r[2] for r in rows],
            "score": [r[3] for r in rows],
            "rank_pct": [r[4] for r in rows],
            "available_at": _tw(dates, hour),
        }
    )


def _prices(days: list[dt.date], sids: list[str]) -> pl.DataFrame:
    rows = [(d, s) for d in days for s in sids]
    close = [100.0 + i for i in range(len(rows))]
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [r[1] for r in rows],
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": [1000] * len(rows),
            "amount": [1e6] * len(rows),
            "limit_up_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "limit_down_price": pl.Series([None] * len(rows), dtype=pl.Float64),
            "available_at": _tw([r[0] for r in rows], 18),
        }
    )


def _lake(tmp_path: Path, scores: pl.DataFrame) -> Warehouse:
    wh = Warehouse(tmp_path / "lake")
    wh.write("clean", "prices_daily", _prices(_DAYS, _SIDS))
    wh.write("clean", "ml_scores", scores)
    return wh


def _full_scores(model: str = "lgbm_h10_20260531_aaaaaaaa") -> pl.DataFrame:
    rows = []
    for di, d in enumerate(_DAYS):
        for si, s in enumerate(_SIDS):
            score = (si + di) % 3 / 3.0
            rows.append((d, s, model, score, (si + di) % 3 / 3.0 + 1 / 3))
    return _scores(rows)


# ── AC-7：資料合約 ───────────────────────────────────────────────────


def test_ac7_schema_accepts_valid_batch() -> None:
    out = schemas.validate("ml_scores", _full_scores())
    assert out.height == len(_DAYS) * len(_SIDS)


def test_ac7_schema_rejects_missing_column_and_bad_dtype() -> None:
    good = _full_scores()
    with pytest.raises(QualityGateError):
        schemas.validate("ml_scores", good.drop("rank_pct"))
    with pytest.raises(QualityGateError):
        schemas.validate("ml_scores", good.with_columns(pl.col("stock_id").cast(pl.Int64)))


# ── AC-6：薄因子與反前視 ─────────────────────────────────────────────


def test_ac6_factor_registered_under_ml_category() -> None:
    fdef = get("ml.lgbm_rank_10d")
    assert fdef.category == "ml"
    assert "ml_scores" in fdef.tables


def test_ac6_factor_no_lookahead(tmp_path: Path) -> None:
    wh = _lake(tmp_path, _full_scores())
    assert_no_lookahead("ml.lgbm_rank_10d", wh, cut_dates=[_DAYS[2], _DAYS[4], _DAYS[-1]])


def test_factor_filters_prefix_and_dedups_latest_model(tmp_path: Path) -> None:
    d = _DAYS[0]
    scores = _scores(
        [
            (d, "1101", "lgbm_h10_20260430_aaaaaaaa", 0.1, 0.10),  # 舊模型
            (d, "1101", "lgbm_h10_20260531_aaaaaaaa", 0.9, 0.90),  # 新模型 → 採用
            (d, "2330", "lgbm_h10_20260430_aaaaaaaa", 0.5, 0.50),  # 只有舊模型 → 沿用
            (d, "0050", "lgbm_h20_20260531_aaaaaaaa", 0.7, 0.70),  # 他系列 → 排除
        ]
    )
    wh = _lake(tmp_path, scores)
    fdef = get("ml.lgbm_rank_10d")
    assert fdef.fn is not None
    out = fdef.fn(wh.view(_DAYS[-1]), fdef.params)
    if isinstance(out, pl.LazyFrame):
        out = out.collect()
    got = {r["stock_id"]: r["value"] for r in out.to_dicts()}
    assert got == {"1101": pytest.approx(0.90), "2330": pytest.approx(0.50)}


def test_ac6_factor_can_pin_exact_model_id(tmp_path: Path) -> None:
    d = _DAYS[0]
    old = "lgbm_h10_20260430_aaaaaaaa"
    new = "lgbm_h10_20260531_aaaaaaaa"
    scores = _scores(
        [
            (d, "1101", old, 0.1, 0.10),
            (d, "1101", new, 0.9, 0.90),
            (d, "2330", old, 0.5, 0.50),
            (d, "2330", new, 0.6, 0.60),
        ]
    )
    wh = _lake(tmp_path, scores)
    fdef = get("ml.lgbm_rank_10d")
    assert fdef.fn is not None
    out = fdef.fn(wh.view(_DAYS[-1]), {**fdef.params, "model_id": old})
    if isinstance(out, pl.LazyFrame):
        out = out.collect()
    got = {r["stock_id"]: r["value"] for r in out.to_dicts()}
    assert got == {"1101": pytest.approx(0.10), "2330": pytest.approx(0.50)}


# ── T-090：分數 EMA 平滑（D-046 候選 3、4）───────────────────────────
# 診斷（D-046）：前 20 名週留存僅 36%，離開者 39% 直接掉到 100 名外——周轉來自分數
# 本身大幅重排；而 IC 隨 horizon 單調上升到 h=20，代表重排多為噪音。平滑是對症的工具。
# EMA 是**列基**（每檔自己的歷史列），分數缺日不得補 0；只用 ≤ 訊號日的分數（PIT）。


def _run_factor(
    wh: Warehouse, as_of: dt.date, **params: object
) -> dict[tuple[dt.date, str], float]:
    fdef = get("ml.lgbm_rank_10d")
    assert fdef.fn is not None
    out = fdef.fn(wh.view(as_of), {**fdef.params, **params})
    if isinstance(out, pl.LazyFrame):
        out = out.collect()
    return {(r["date"], r["stock_id"]): r["value"] for r in out.to_dicts()}


def test_ac1_smooth_days_zero_is_bit_identical_to_default(tmp_path: Path) -> None:
    """AC-1：smooth_days=0（預設）與未帶參數的輸出逐位元相同——既有回測不得被本任務改動。"""
    wh = _lake(tmp_path, _full_scores())
    fdef = get("ml.lgbm_rank_10d")
    assert fdef.fn is not None
    base = fdef.fn(wh.view(_DAYS[-1]), fdef.params)
    zero = fdef.fn(wh.view(_DAYS[-1]), {**fdef.params, "smooth_days": 0})
    if isinstance(base, pl.LazyFrame):
        base = base.collect()
    if isinstance(zero, pl.LazyFrame):
        zero = zero.collect()
    assert base.equals(zero)
    assert fdef.params.get("smooth_days", 0) == 0, "預設必須是不平滑"


def test_ac2_smoothed_factor_no_lookahead(tmp_path: Path) -> None:
    """AC-2：平滑後仍過反前視——EMA 只能吃 ≤ 切點的分數。"""
    wh = _lake(tmp_path, _full_scores())
    assert_no_lookahead(
        "ml.lgbm_rank_10d", wh, cut_dates=[_DAYS[2], _DAYS[4], _DAYS[-1]], params={"smooth_days": 3}
    )


def test_ac3_ema_matches_hand_computation(tmp_path: Path) -> None:
    """AC-3：span=3 → α=0.5、adjust=False：e1=v1、e2=½v2+½e1、e3=½v3+½e2。"""
    m = "lgbm_h10_20260531_aaaaaaaa"
    d0, d1, d2 = _DAYS[:3]
    wh = _lake(
        tmp_path,
        _scores([(d0, "1101", m, 0.2, 0.2), (d1, "1101", m, 0.8, 0.8), (d2, "1101", m, 0.5, 0.5)]),
    )
    got = _run_factor(wh, d2, smooth_days=3)
    assert got[(d0, "1101")] == pytest.approx(0.2)
    assert got[(d1, "1101")] == pytest.approx(0.5)  # ½·0.8 + ½·0.2
    assert got[(d2, "1101")] == pytest.approx(0.5)  # ½·0.5 + ½·0.5


def test_ac4_missing_day_is_not_filled_with_zero(tmp_path: Path) -> None:
    """AC-4：分數缺日的股票不得產生虛假平滑值——EMA 走該檔自己的列，缺日既不補 0 也不輸出。"""
    m = "lgbm_h10_20260531_aaaaaaaa"
    d0, d1, d2 = _DAYS[:3]
    wh = _lake(tmp_path, _scores([(d0, "2330", m, 0.4, 0.4), (d2, "2330", m, 0.8, 0.8)]))  # d1 缺
    got = _run_factor(wh, d2, smooth_days=3)
    assert (d1, "2330") not in got, "缺日不得憑空出現一列"
    assert got[(d2, "2330")] == pytest.approx(0.6)  # ½·0.8 + ½·0.4；若補 0 會得 0.5


# ── cross_rank_pct（ml score 寫 rank_pct 用）─────────────────────────


def test_cross_rank_pct_hand_computed() -> None:
    d = _DAYS[0]
    df = pl.DataFrame(
        {
            "date": [d, d, d, d],
            "stock_id": ["A", "B", "C", "D"],
            "score": pl.Series([0.9, 0.1, 0.5, None], dtype=pl.Float64),
        }
    )
    out = cross_rank_pct(df, "score", "rank_pct")
    got = {r["stock_id"]: r["rank_pct"] for r in out.to_dicts()}
    assert got["A"] == pytest.approx(3 / 3)
    assert got["C"] == pytest.approx(2 / 3)
    assert got["B"] == pytest.approx(1 / 3)
    assert got["D"] is None  # null 傳遞、不丟列


# ── AC-9：IC 對照報告 ────────────────────────────────────────────────


def test_ac9_ic_report_on_ml_factor(tmp_path: Path) -> None:
    wh = _lake(tmp_path, _full_scores())
    fdef = get("ml.lgbm_rank_10d")
    assert fdef.fn is not None
    factor_df = fdef.fn(wh.view(_DAYS[-1]), fdef.params)
    if isinstance(factor_df, pl.LazyFrame):
        factor_df = factor_df.collect()
    prices = _prices(_DAYS, _SIDS).select("date", "stock_id", "close")
    report = compute_ic_report("ml.lgbm_rank_10d", factor_df, prices, horizons=[2])
    assert report.factor == "ml.lgbm_rank_10d"
    assert report.horizons  # 報告可產出（與基準因子同介面，供 T-069 對照）
