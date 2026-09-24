"""ML 排序層薄因子（SPEC-010 §6）：讀 ml_scores 表，不做任何新計算。純函數、無 IO。

分數由研究軌訓練的模型於 `ml score`（邊界層）落地成 ml_scores（含 available_at），
本模組只透過 `view.table("ml_scores")` 讀取——黃金規則 1 與 IO 靜態閘門不變。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView


def _scores_for_model(
    view: PitView,
    params: dict[str, object],
    default_prefix: str,
    value_col: str = "rank_pct",
) -> pl.DataFrame:
    """從 PIT-safe ml_scores 取精確模型或同系列最新分數。"""
    prefix = str(params.get("model_prefix", default_prefix))
    model_id = params.get("model_id")
    scores = view.table("ml_scores")
    if model_id is not None:
        scores = scores.filter(pl.col("model_id") == str(model_id))
    else:
        scores = scores.filter(pl.col("model_id").str.starts_with(prefix))
    out = (
        scores.sort(["date", "stock_id", "model_id"], descending=[False, False, True])
        .unique(subset=["date", "stock_id"], keep="first", maintain_order=True)
        .select("date", "stock_id", pl.col(value_col).alias("value"))
        .collect()
        .sort(["date", "stock_id"])
    )
    raw = params.get("smooth_days", 0)
    smooth = int(raw) if isinstance(raw, int | float | str) else 0
    if smooth <= 0:
        return out
    # D-046 候選 3、4：對每檔自己的分數序列做 EMA（span=smooth_days、adjust=False）。
    # 列基而非日曆基——分數缺日既不補 0 也不插值，缺的那天不輸出；只用 ≤ 截點的列
    # （view 已保證），故 assert_no_lookahead 對 smooth_days>0 同樣成立。
    return (
        out.sort(["stock_id", "date"])
        .with_columns(pl.col("value").ewm_mean(span=smooth, adjust=False).over("stock_id"))
        .sort(["date", "stock_id"])
    )


@factor(
    name="ml.lgbm_rank_10d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_h10_", "smooth_days": 0},
)
def lgbm_rank_10d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """LightGBM 10 日排序分數；model_id 可精確 pin，否則同系列取最新。

    model_id 格式 lgbm_h{h}_{train_end}_{hash}（SPEC-010 §4）——同前綴下字典序
    即新舊序，故以 model_id 降冪去重。前綴綁定 horizon，屬因子身分（如 mom_20d
    的 20），非調參對象。
    """
    return _scores_for_model(view, params, "lgbm_h10_")


@factor(
    name="ml.lgbm_net_20d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_net_h20_", "smooth_days": 0},
)
def lgbm_net_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """LightGBM H20 成本感知連續報酬分數；只讀 PIT-safe ml_scores。"""
    return _scores_for_model(view, params, "lgbm_net_h20_")


# ── 長窗口模型（D-088，sample_tag=lw）────────────────────────────────────
# T-172 刻意讓 `lgbm_net_h20_` 撈不到 `lgbm_lw_net_h20_*`：兩個樣本上訓練的模型不是同一條線，
# 分數不得混。要用長窗口分數就得明確指名——這三個因子就是那個「明確指名」。
# 標籤定義、方向、用途限制全部與短窗口版逐字相同，只差模型前綴。


@factor(
    name="ml.lgbm_lw_net_20d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_lw_net_h20_", "smooth_days": 0},
)
def lgbm_lw_net_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """長窗口（2015-09 起訓練）的 H20 成本感知分數；語意同 `ml.lgbm_net_20d`。"""
    return _scores_for_model(view, params, "lgbm_lw_net_h20_")


@factor(
    name="ml.lgbm_lw_rev_15d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_lw_rev_h15_", "smooth_days": 0},
)
def lgbm_lw_rev_15d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """長窗口（2017-01 起訓練）的營收驚奇分數；語意同 `ml.lgbm_rev_15d`。"""
    return _scores_for_model(view, params, "lgbm_lw_rev_h15_")


@factor(
    name="ml.lgbm_lw_dd_60d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_lw_dd_h60_", "smooth_days": 0},
)
def lgbm_lw_dd_60d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """長窗口（2017-01 起訓練）的 60 日回撤分數；用途限制同 `ml.lgbm_dd_60d`（只能當閘門）。

    D-089 已量出這個模型 94% 是 `price.vol_20d` 的機械關係；LW-R1 對 LW-R2 那條邊
    就是在問剩下的 6% 有沒有經濟價值。
    """
    return _scores_for_model(view, params, "lgbm_lw_dd_h60_")


# ── 籌碼特徵修正版（D-103，sample_tag=lwc）────────────────────────────
# 與 `ml.lgbm_lw_*` 的差別只有一個特徵（`chip.foreign_streak`）。前綴另立而非共用，
# 理由同 T-172：薄因子以前綴取分數，共用會讓兩個模型的分數混進同一條線。


@factor(
    name="ml.lgbm_lwc_rev_15d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_lwc_rev_h15_", "smooth_days": 0},
)
def lgbm_lwc_rev_15d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """營收驚奇分數（加 `chip.foreign_streak`）；語意同 `ml.lgbm_lw_rev_15d`。"""
    return _scores_for_model(view, params, "lgbm_lwc_rev_h15_")


@factor(
    name="ml.lgbm_lwc_dd_60d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_lwc_dd_h60_", "smooth_days": 0},
)
def lgbm_lwc_dd_60d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """60 日回撤分數（加 `chip.foreign_streak`）；用途限制同 `ml.lgbm_lw_dd_60d`（只能當閘門）。"""
    return _scores_for_model(view, params, "lgbm_lwc_dd_h60_")


@factor(
    name="ml.lgbm_rev_15d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_rev_h15_", "smooth_days": 0},
)
def lgbm_rev_15d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """F 系列（D-074）：下次公布的月營收驚奇分數。高分＝驚奇為正。

    只讀 PIT-safe ml_scores。第二段的用法是**池子閘門**（前 30%），
    與 M3 的「動能當池子」逐字對照，故 F − M3 這條邊只換了池子的定義。
    """
    return _scores_for_model(view, params, "lgbm_rev_h15_")


@factor(
    name="ml.lgbm_dd_60d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_dd_h60_", "smooth_days": 0},
)
def lgbm_dd_60d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """R 系列（D-075）：未來 60 日回撤分數。**低分＝預測跌得深**（百分位方向同標籤）。

    用途寫死為**風控閘門**：`prefilter` 保留前 90%，即剔除預測回撤最深的一成。
    **不得作為 `signals` 進入排序**——先導只證明它能排序「誰會跌得深」，
    未證明它能排序「誰會漲得多」；把風控訊號拿去選股是換了問題還沿用結論。
    """
    return _scores_for_model(view, params, "lgbm_dd_h60_")


# ── S 系列買進模型（D-120／D-124）──────────────────────────────────────────
# **回傳的是機率本身（`score`），不是橫斷面分位。** 每日補位的進場門檻是絕對的
# （「校準後機率 ≥ 70%」）；若回 rank_pct，崩盤日照樣有 0.99，門檻形同虛設。
# `ml_scores.score` 裡放的必須是**校準後**的機率（model_id 以 `_cal` 結尾的那一份）。


@factor(
    name="ml.lgbm_lw_tb_up_20d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"model_prefix": "lgbm_lw_tb_h20_", "smooth_days": 0},
)
def lgbm_lw_tb_up_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """20 日內「先漲 10%」的校準後機率（絕對值，不可當橫斷面排序因子用）。"""
    return _scores_for_model(view, params, "lgbm_lw_tb_h20_", value_col="score")


# ── 第九輪右尾模型（D-143）────────────────────────────────────────────────
# A／B 兩臂的 model_id 只差 sample_tag（`lgbm_taila_tu_h60_*` vs `lgbm_tailb_tu_h60_*`），
# 共用前綴會互相命中——**故本因子強制要求 `model_id`**，不接受前綴回退。


@factor(
    name="ml.lgbm_tail_up_60d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"smooth_days": 0},
)
def lgbm_tail_up_60d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """60 個交易日內最大漲幅 ≥ +50% 的機率（絕對值，不可當橫斷面排序因子用）。"""
    if params.get("model_id") is None:
        raise ValueError(
            "ml.lgbm_tail_up_60d 須指定 model_id：A／B 兩臂前綴相近，"
            "前綴回退會讓兩臂的分數互相命中（D-143 §三）"
        )
    return _scores_for_model(view, params, "lgbm_tail", value_col="score")


# ── 第十二輪安靜股模型（D-151／D-152）與第十三輪對照（D-153）─────────────────
# A／A20／A25 的 model_id 只差 sample_tag（`lgbm_quietA_tu_h20_*`、`lgbm_quietA20_tu_h20_*`…），
# 與上面同一個理由：強制 `model_id`。

_VALUE_COLS = ("score", "rank_pct")


@factor(
    name="ml.lgbm_tail_up_20d",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"smooth_days": 0, "value_col": "score"},
)
def lgbm_tail_up_20d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """20 個交易日內最大漲幅 ≥ 門檻的機率（絕對值）；`value_col=rank_pct` 回同日池內百分位。

    池＝該模型當日有分數的股票（安靜 ∩ 流動，D-151 §十三）；沒分數的股票沒有值，
    當進場因子時不是候選、當 `score_below` 出場因子時不觸發（`backtest/exits.py`）。
    """
    if params.get("model_id") is None:
        raise ValueError(
            "ml.lgbm_tail_up_20d 須指定 model_id：A／A20／A25 前綴相近，"
            "前綴回退會讓各臂的分數互相命中（D-152 §一）"
        )
    col = str(params.get("value_col", "score"))
    if col not in _VALUE_COLS:
        raise ValueError(f"ml.lgbm_tail_up_20d 的 value_col 只能是 {_VALUE_COLS}，收到 {col!r}")
    return _scores_for_model(view, params, "lgbm_quiet", value_col=col)


@factor(
    name="ml.pool_random",
    category="ml",
    direction=1,
    tables=["ml_scores"],
    lookback_days=0,
    params={"seed": 0},
)
def pool_random(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """對照用（D-153 §一）：`model_id` 當日有分數的股票各給一個固定 seed 的 [0, 1) 亂數，其餘沒有值。

    同一個 (date, stock_id, seed) 永遠得到同一個數——回測可重現；換 seed 才換名單。
    用途是「同池、同出場、不用模型排序」的對照，不是訊號。
    """
    if params.get("model_id") is None:
        raise ValueError("ml.pool_random 須指定 model_id：池子＝該模型當日有分數的股票")
    seed = int(str(params.get("seed", 0)))
    pool = _scores_for_model(view, params, "", value_col="score")
    span = float(2**53)
    return pool.select(
        "date",
        "stock_id",
        ((pl.struct(["date", "stock_id"]).hash(seed=seed) % 2**53).cast(pl.Float64) / span).alias(
            "value"
        ),
    )
