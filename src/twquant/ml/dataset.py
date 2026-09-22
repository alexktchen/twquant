"""資料集組裝（SPEC-010 §2）：前向報酬標籤與特徵×標籤對齊。純函數、無 IO。

標籤只存在於訓練組裝路徑（研究軌）；推論路徑（ml score，T-067）不得 import 本模組
的標籤函數——PIT 上「特徵在 t、標籤看 t+1 之後」的不對稱由這條邊界維持。
"""

from __future__ import annotations

import datetime as dt
import warnings
from collections.abc import Mapping, Sequence
from typing import cast

import numpy as np
import polars as pl

from twquant.backtest.costs import CostConfig, trade_cost
from twquant.errors import DataError
from twquant.panel import session_index

# 事件表開頭可容忍的空窗（交易日）。約一季——真實的事件表在區間開頭本來就可能
# 有幾週沒有除權息（台股除權息集中在 6~8 月），但不可能空一整季以上。
_MAX_UNCOVERED_SESSIONS = 60


def attach_adjustment(prices: pl.DataFrame, events: pl.DataFrame) -> pl.DataFrame:
    """為 prices 掛上累積除權息還原因子 `adj`（T-098／D-054）。純函數。

    `adj(t) = Π{ adj_factor(e) : ex_date(e) ≤ t }`——**固定過去錨**，只依賴 t 以前的
    事件，故與 as_of 無關。錨之前的事件對報酬比值 `adj(t1)/adj(t2)` 恆等抵銷，
    所以只載入所需期間的事件即可。

    以 as-of join（backward）而非等值 join：實測約 5.75% 的除權息日在 `prices_daily`
    沒有對應列（颱風休市、停牌），等值 join 會**靜默**丟掉那些調整。
    """
    if events.is_empty():
        # 靜默回傳 adj=1.0 會讓 total_return=True 的訓練**原樣退回未還原標籤**，
        # 而呼叫端仍會印「標籤改用總報酬」——正是 D-054 要消滅的失效模式。
        # 本函數只在呼叫端已選擇 total_return 時才被呼叫，故空表一定是錯誤狀態。
        raise ValueError(
            "corporate_events 讀到 0 筆事件，但標籤已要求總報酬（D-054）。"
            "空事件表會靜默退回未還原標籤——6~8 月近兩成的「最差一成」是除息假象。"
            "請先確認供應層 corporate_events 已載入（scripts/load_corporate_events.py）。"
        )
    # **空表不是唯一的失效模式**（T-169）：事件表只涵蓋價格區間的一部分時，
    # 未涵蓋的那幾年會**靜默地**變成價格報酬，而其餘年份是總報酬——同一份訓練資料
    # 裡兩種標籤定義，模型看到的是一個不存在的體制轉折。實測踩到過：長歷史載到
    # 2015-06 之後，corporate_events 仍只有 2019 起，2015-06~2018-12 的 886 個交易日
    # 對應 0 筆事件（台股一年約 1,400~1,600 筆），而舊的空表檢查完全攔不住。
    px_lo = cast("dt.date | None", prices["date"].min())
    ev_lo = cast("dt.date | None", events["date"].min())
    if px_lo is not None and ev_lo is not None and ev_lo > px_lo:
        gap = prices.filter(pl.col("date") < ev_lo)["date"].n_unique()
        if gap > _MAX_UNCOVERED_SESSIONS:
            raise ValueError(
                f"corporate_events 最早只到 {ev_lo}，而行情從 {px_lo} 開始"
                f"——前面 {gap:,} 個交易日沒有任何事件可還原。"
                "那些日子的標籤會靜默退回價格報酬，與其餘年份的總報酬不同口徑"
                f"（容忍上限 {_MAX_UNCOVERED_SESSIONS} 個交易日，約一季，"
                "留給事件表尾端的正常空窗）。請先回補事件表，或把訓練起日移到事件涵蓋範圍內。"
            )
    bad = events.filter(pl.col("adj_factor").is_null() | (pl.col("adj_factor") <= 0.0))
    if bad.height:
        raise ValueError(
            f"adj_factor 須 > 0 且非 null，違規 {bad.height} 列（首列：{bad.row(0)}）"
            "——0 或負值會經累乘污染該股全部歷史"
        )
    cum = (
        events.select("date", "stock_id", "adj_factor")
        .sort("stock_id", "date")
        .with_columns(adj=pl.col("adj_factor").cum_prod().over("stock_id"))
        .select("stock_id", "date", "adj")
        .sort("date")
    )
    return (
        prices.sort("date")
        .join_asof(cum, on="date", by="stock_id", strategy="backward")
        .with_columns(adj=pl.col("adj").fill_null(1.0))
    )


def _return_price(adjusted: bool, columns: Sequence[str]) -> pl.Expr:
    """報酬用價格：`adjusted` 時除以累積還原因子 `adj`（T-098／D-054）。

    `adj` 是固定過去錨的累乘（只含 ex_date ≤ 該列日期的事件），故與 as_of 無關。
    缺欄卻要求還原 → raise：靜默回退成價格報酬正是 D-054 要消滅的失效模式。
    """
    if not adjusted:
        return pl.col("open")
    if "adj" not in columns:
        raise ValueError(
            "adjusted=True 需要 adj 欄（固定過去錨的累積除權息還原因子）；"
            "缺欄時不得回退成未還原價格——6~8 月的標籤會有近兩成是除息假象（D-054）"
        )
    return pl.col("open") / pl.col("adj")


def forward_open_returns(
    prices: pl.DataFrame,
    horizon: int,
    *,
    adjusted: bool = False,
    max_span_sessions: int | None = None,
) -> pl.DataFrame:
    """open(t+1) → open(t+1+h) 前向報酬。輸入需含 (date, stock_id, open)。

    「t+1」是各股票自身的下一個有行情列——停牌期間不可成交，進出場順延（與回測
    引擎的可成交語意一致）。未來列不足者 fwd_ret 為 null（保留列，不靜默丟棄）。

    `adjusted=True` 時改算**總報酬**（含股利），需另含 `adj` 欄。標籤是前瞻構造，
    不受 `assert_no_lookahead` 與還原價錨定規則（D-055）約束。

    **`max_span_sessions`（T-139）**：`shift` 是**列位移**，某檔中間缺 N 個開市日時，
    「h 日報酬」實際橫跨數月而不帶任何標記——它只是一個看起來很大的數字，會被模型
    當成真實的極端報酬學進去。實測 `prices_daily` 有 413 處 >5 開市日的個股缺口
    （最大 260 日），h=20 下約 8,673 個訓練樣本受影響。

    給定時，出場列與當前列的**開市日跨距**超過此值即設 null。跨距以全市場日曆計，
    故連假不受影響（全市場休市不佔開市日）。**預設 None ＝不啟用**，既有配置的
    結果逐位元不變；是否啟用由配置顯式決定。
    """
    if horizon < 1:
        raise ValueError(f"horizon 須 ≥ 1，得到 {horizon}")
    if max_span_sessions is not None and max_span_sessions < horizon + 1:
        raise ValueError(
            f"max_span_sessions({max_span_sessions}) 不得小於 horizon+1({horizon + 1})"
            "：出場列至少在 h+1 個開市日之後，設得更小會讓所有標籤變 null"
        )
    px = _return_price(adjusted, prices.columns)
    entry = px.shift(-1).over("stock_id")
    exit_ = px.shift(-(1 + horizon)).over("stock_id")
    if max_span_sessions is not None:
        prices = session_index(prices)
        sidx = pl.col("_sidx").cast(pl.Int64)
        span = sidx.shift(-(1 + horizon)).over("stock_id", order_by="date") - sidx
        exit_ = pl.when(span <= max_span_sessions).then(exit_).otherwise(None)
    return (
        prices.sort("stock_id", "date")
        .with_columns(
            # schema 允許 open >= 0，故 0 是合法值。未防護時 open(t+1)=0 → +inf，
            # 而 excess_rank_labels 的 mean().over("date") 會把 inf 擴散到整個橫斷面，
            # 讓當日所有 label 塌成同一個常數。成本感知路徑早有同一道防護。
            pl.when((entry > 0.0) & (exit_ > 0.0))
            .then(exit_ / entry - 1.0)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("fwd_ret")
        )
        .select("date", "stock_id", "fwd_ret")
        .sort("date", "stock_id")
    )


def excess_rank_labels(fwd: pl.DataFrame) -> pl.DataFrame:
    """同日超額（減宇宙均值）→ 橫斷面百分位 label ∈ (0, 1]；fwd_ret null → label null。

    rank 轉換下超額化不改變順位；保留此步是讓 label.transform（SPEC-010 §1）
    日後可換成非 rank 目標時語意不變。
    """
    return (
        fwd.with_columns(
            (pl.col("fwd_ret") - pl.col("fwd_ret").mean().over("date")).alias("_excess")
        )
        .with_columns(
            (
                pl.col("_excess").rank(method="average").over("date")
                / pl.col("_excess").count().over("date")
            ).alias("label")
        )
        .select("date", "stock_id", "label")
        .sort("date", "stock_id")
    )


def cost_adjusted_return_labels(
    prices: pl.DataFrame,
    *,
    horizon: int,
    adv_window_sessions: int,
    position_notional_twd: float,
    downside_penalty: float,
    cost_cfg: CostConfig,
    adjusted: bool = False,
    max_span_sessions: int | None = None,
) -> pl.DataFrame:
    """建立 H 日成本後超額報酬標籤；ADV 僅含訊號日及以前資料。

    `adjusted=True` 時毛報酬改用還原價（總報酬，T-098／D-054），但**成本仍用真實
    成交價**——滑價的半價差是價格相依的（`costs.py:_half_spread_frac`），股數也由
    真實價決定；用還原價會同時算錯這兩項。

    **`max_span_sessions`（T-144）**：本函式有**自己的** `shift(-(1 + horizon))`，與
    `forward_open_returns` 完全獨立。T-139 只防護了後者，而凍結模型的配置是
    `transform: cost_adjusted_return`——於是那道防護對它**是 no-op**：旗標看起來接上了，
    實際什麼都沒擋。這裡補上同一道防護：出場列與當前列的開市日跨距超過此值即設 null。
    跨距以全市場日曆計，故連假不受影響。**預設 None ＝不啟用**，既有結果逐位元不變。
    """
    required = {"date", "stock_id", "open", "amount"}
    missing = sorted(required - set(prices.columns))
    if missing:
        raise ValueError(f"成本感知標籤缺行情欄位：{', '.join(missing)}")
    if horizon < 1:
        raise ValueError(f"horizon 須 >= 1，得到 {horizon}")
    if adv_window_sessions < 1:
        raise ValueError(f"adv_window_sessions 須 >= 1，得到 {adv_window_sessions}")
    if position_notional_twd <= 0:
        raise ValueError(f"position_notional_twd 須 > 0，得到 {position_notional_twd}")
    if downside_penalty < 0:
        raise ValueError(f"downside_penalty 須 >= 0，得到 {downside_penalty}")
    if max_span_sessions is not None and max_span_sessions < horizon + 1:
        raise ValueError(
            f"max_span_sessions({max_span_sessions}) 不得小於 horizon+1({horizon + 1})"
            "：出場列至少在 h+1 個開市日之後，設得更小會讓所有標籤變 null"
        )

    def estimated_cost_rate(row: dict[str, float | None]) -> float | None:
        entry_raw, exit_raw, adv_raw = row["_entry"], row["_exit"], row["_adv"]
        if entry_raw is None or exit_raw is None or adv_raw is None:
            return None
        entry, exit_, adv = float(entry_raw), float(exit_raw), float(adv_raw)
        if entry <= 0.0 or exit_ <= 0.0 or adv <= 0.0:
            return None
        shares = int(position_notional_twd // entry)
        if shares < 1:
            return None
        participation = position_notional_twd / adv
        buy = trade_cost(
            side="buy", price=entry, shares=shares, participation=participation, cfg=cost_cfg
        )
        sell = trade_cost(
            side="sell", price=exit_, shares=shares, participation=participation, cfg=cost_cfg
        )
        return (buy.total + sell.total) / (entry * shares)

    px_ret = _return_price(adjusted, prices.columns)
    exit_px = pl.col("open").shift(-(1 + horizon)).over("stock_id")
    exit_r = px_ret.shift(-(1 + horizon)).over("stock_id")
    if max_span_sessions is not None:
        prices = session_index(prices)
        sidx = pl.col("_sidx").cast(pl.Int64)
        span = sidx.shift(-(1 + horizon)).over("stock_id", order_by="date") - sidx
        ok = span <= max_span_sessions
        # 出場價與報酬基準價都要遮：只遮後者的話，成本仍會用一個已判定不可用的
        # 出場價算出來，label 變成「乾淨的毛報酬減掉髒的成本」。
        exit_px = pl.when(ok).then(exit_px).otherwise(None)
        exit_r = pl.when(ok).then(exit_r).otherwise(None)
    prepared = (
        prices.sort("stock_id", "date")
        .with_columns(
            pl.col("open").shift(-1).over("stock_id").alias("_entry"),
            exit_px.alias("_exit"),
            # 報酬基準價：adjusted 時為還原價，否則與 _entry/_exit 同值
            px_ret.shift(-1).over("stock_id").alias("_entry_r"),
            exit_r.alias("_exit_r"),
            pl.col("amount")
            .rolling_median(window_size=adv_window_sessions, min_samples=adv_window_sessions)
            .over("stock_id")
            .alias("_adv"),
        )
        .with_columns(
            # schema 允許 open >= 0，故 0 是合法值；但 SPEC-011 §2 要求「價格無效」時
            # label 為 null。少了這道防護，除以 0 會得到 inf，再被下一步的當日均值
            # 擴散到整個橫斷面（成本路徑早已有相同判斷，見 estimated_cost_rate）。
            pl.when(
                (pl.col("_entry") > 0.0)
                & (pl.col("_exit") > 0.0)
                & (pl.col("_entry_r") > 0.0)
                & (pl.col("_exit_r") > 0.0)
            )
            .then(pl.col("_exit_r") / pl.col("_entry_r") - 1.0)
            .otherwise(pl.lit(None, dtype=pl.Float64))
            .alias("_gross"),
            pl.struct("_entry", "_exit", "_adv")
            .map_elements(estimated_cost_rate, return_dtype=pl.Float64)
            .alias("_cost"),
        )
        # mean() 略過 null，故均值即 SPEC-011 §2 的「當日有效宇宙」平均。
        .with_columns((pl.col("_gross") - pl.col("_gross").mean().over("date")).alias("_excess"))
        .with_columns((pl.col("_excess") - pl.col("_cost")).alias("_net"))
    )
    return (
        prepared.with_columns(
            pl.when(pl.col("_net") < 0.0)
            .then(pl.col("_net") * (1.0 + downside_penalty))
            .otherwise(pl.col("_net"))
            .alias("label")
        )
        .select("date", "stock_id", "label")
        .sort("date", "stock_id")
    )


def cross_rank_pct(frame: pl.DataFrame, value_col: str, out_col: str) -> pl.DataFrame:
    """同日橫斷面百分位 (0,1]；null 傳遞、不丟列（`ml score` 寫 rank_pct 用，SPEC-010 §5）。"""
    return frame.with_columns(
        (
            pl.col(value_col).rank(method="average").over("date")
            / pl.col(value_col).count().over("date")
        ).alias(out_col)
    )


def assert_feature_coverage(
    features: Mapping[str, pl.DataFrame], *, min_non_null_share: float = 0.0
) -> dict[str, float]:
    """訓練前的特徵覆蓋閘門（T-088 AC-3）：任一特徵無列或非空比例 ≤ 門檻即 raise，回傳各特徵非空比例。

    Phase 1 的 25 個 pin 特徵裡有 2 個在 factor_values 從未有值（D-045）、1 個全 null（T-082），
    LightGBM 照樣「成功」訓練——全空特徵在模型裡是白佔的欄位，而沒有任何一關報錯。
    只掛在訓練路徑；生產 `ml score` 單日缺某因子屬正常，不得套用。
    """
    if not 0.0 <= min_non_null_share < 1.0:
        raise ValueError(f"min_non_null_share 須介於 [0, 1)，得到 {min_non_null_share}")
    shares: dict[str, float] = {}
    dead: list[str] = []
    for name, df in features.items():
        n = df.height
        share = float(df["value"].is_not_null().sum() / n) if n else 0.0
        shares[name] = share
        if n == 0 or share <= min_non_null_share:
            dead.append(f"{name}（{n:,} 列、非空 {share:.1%}）")
    if dead:
        raise ValueError(
            "特徵覆蓋不足，拒絕訓練："
            + "；".join(dead)
            + f"。門檻 non-null > {min_non_null_share:.0%}。全空或近空的特徵在模型裡是白佔的欄位"
            "（Phase 1 曾以 3 個空特徵訓練並回報成功）——先補資料或從配置移除，不得靜默訓練。"
        )
    return shares


def assert_inference_features_present(
    features: Mapping[str, pl.DataFrame], required: Sequence[str], day: dt.date
) -> None:
    """推論前的特徵存在閘門：任一必要特徵在該日**完全沒有值**即 raise（T-227）。

    與 `assert_feature_coverage` 是兩個不同的問題，門檻刻意不同：
    訓練看的是「這個特徵在整段樣本裡是不是白佔欄位」，故連近空都要擋；
    推論看的是「今天這一格資料到了沒」，**稀疏是正常的**（月營收因子本來就只有
    九成多的股票有值），只有**整欄空**才代表上游斷掉。

    為什麼需要這一關：2026-09-02~09-09 期間 `index_daily` 落後 `prices_daily`
    六個交易日（指數是月頻 `ingest-index`，從未接進每日迴圈），
    於是 `price.rs_market_20d` 整欄空，而 LightGBM 把缺欄當 NaN 照樣吐分數，
    `ml score` 回報「[OK] 1961 檔」——**12 個特徵少 1 個，沒有任何一關報錯**。
    黃金規則 5：資料有問題就 raise，不得靜默降級。
    """
    empty = [
        name
        for name in required
        if (df := features.get(name)) is None
        or df.height == 0
        or int(df["value"].is_not_null().sum()) == 0
    ]
    if empty:
        raise DataError(
            "ml_score",
            day,
            f"{day} 這些必要特徵整欄無值：{'、'.join(empty)}。"
            "模型會把缺欄當 NaN 照樣吐分數，故此處拒絕推論——"
            "先補上游資料（例如 index_daily 落後會讓 price.rs_market_20d 全空）"
            "再重跑，不得以殘缺特徵發布名單。",
        )


def assemble(features: Mapping[str, pl.DataFrame], labels: pl.DataFrame) -> pl.DataFrame:
    """特徵長表（各為 (date, stock_id, value)）outer join 成寬表，label 以 left join 掛上。

    缺值一律保留 null（LightGBM 原生處理；黃金規則 5 禁止填 0）。
    輸出欄位 (date, stock_id, <特徵名...>, label)，依 (date, stock_id) 排序。
    """
    if not features:
        raise ValueError("features 為空：至少需要一個特徵")
    wide: pl.DataFrame | None = None
    for name, df in features.items():
        part = df.select("date", "stock_id", pl.col("value").alias(name))
        wide = (
            part
            if wide is None
            else wide.join(part, on=["date", "stock_id"], how="full", coalesce=True)
        )
    assert wide is not None  # features 非空已檢查
    return wide.join(
        labels.select("date", "stock_id", "label"), on=["date", "stock_id"], how="left"
    ).sort("date", "stock_id")


# ── D-074／D-075：月頻訊號日的另類標籤 ────────────────────────────────────
# 兩條事前登記的研究線換的是**預測標的**，不是特徵工程或調參（那是 D-066 乙層
# 明文禁止的）。切分與 LightGBM 參數與既有模型完全相同，故結果可比。


def month_end_signal_dates(trading_days: Sequence[dt.date]) -> list[dt.date]:
    """每個日曆月的最後一個交易日（D-074／D-075 寫定的訊號日）。

    月營收在次月約 10 日公布，所以「月底」是唯一一個對所有個股都剛好站在
    「本月已公布、下月未公布」邊界上的日子；R 系列沿用同一組日子純粹是為了讓
    兩條線的切分逐字可比。

    尾端若是不完整的月份（資料迄日落在月中），仍會產生一個訊號日——它的標籤
    必然是 null（前向視窗不足），故不影響訓練，只影響推論時的最新一列。
    """
    days = sorted(set(trading_days))
    if not days:
        raise ValueError("trading_days 為空，無法取月底訊號日")
    return [
        a
        for a, b in zip(days, [*days[1:], None], strict=True)
        if b is None or (b.year, b.month) != (a.year, a.month)
    ]


def _session_numbers(trading_days: Sequence[dt.date]) -> pl.DataFrame:
    """全市場日曆 → (_d, _n)：稠密開市日序號，供任意日期換算開市日跨距。"""
    return (
        pl.DataFrame({"_d": sorted(set(trading_days))}, schema={"_d": pl.Date})
        .sort("_d")
        .with_row_index("_n")
        .with_columns(pl.col("_n").cast(pl.Int64))
    )


# 橫斷面 OLS 需要「截距 ＋ 2 個解釋變數」共 3 個參數，再留 1 個殘差自由度。
# 這是迴歸的秩要求，不是可調的門檻——低於此值代表當日資料壞了，不是該放寬。
_MIN_REGRESSION_ROWS = 4


def _residualize_cross_section(
    frame: pl.DataFrame, y_col: str, x_cols: Sequence[str], out_col: str
) -> pl.DataFrame:
    """逐日橫斷面 OLS（含截距）殘差。

    用 numpy 而非 polars 運算式：每日最小平方解沒有對應的 polars 表達式，
    硬湊會比一個 3 欄的 lstsq 更難讀也更容易錯。輸入須已無缺值與非有限值。
    """
    parts: list[pl.DataFrame] = []
    for part in frame.partition_by("date", maintain_order=True):
        n = part.height
        if n < _MIN_REGRESSION_ROWS:
            day = part["date"][0]
            raise ValueError(
                f"{day} 的橫斷面只有 {n} 檔，不足以做 {len(x_cols)} 元迴歸"
                f"（需 ≥ {_MIN_REGRESSION_ROWS}）。真實橫斷面是上千檔，"
                "出現這種日子代表上游資料缺了一段，不得靜默略過"
            )
        design = np.column_stack([np.ones(n), *(part[c].to_numpy() for c in x_cols)])
        y = part[y_col].to_numpy()
        beta, *_ = np.linalg.lstsq(design, y, rcond=None)
        parts.append(part.with_columns(pl.Series(out_col, y - design @ beta, dtype=pl.Float64)))
    return pl.concat(parts)


def revenue_surprise_labels(
    revenue: pl.DataFrame,
    signal_dates: Sequence[dt.date],
    trading_days: Sequence[dt.date],
    *,
    max_lag_sessions: int,
) -> pl.DataFrame:
    """F 系列標籤（D-074）：下一次公布的月營收 YoY 對「本月 YoY ＋ 加速度」的橫斷面殘差。

    輸入 `revenue` 需含 (stock_id, period_end, available_at, yoy)；
    輸出 (date, stock_id, label)，label 為同日殘差的橫斷面百分位 (0, 1]。

    **為什麼是殘差而不是 YoY 本身**：直接以「下月 YoY」為標籤時，最強的預測子是
    營收加速度自己（RankIC −0.29），那不是訊號而是均值回歸——把它壓掉之後
    剩下的才是「量價籌碼領先基本面」這個假說要問的東西。

    **PIT 的定義由公布日決定，不是月份**：在訊號日 t，`yoy`／`_accel` 取
    `available_at ≤ t` 的最後一次公布，標籤取**緊接其後**的那一次公布（相鄰，
    不跳號）。該次公布距 t 超過 `max_lag_sessions` 個開市日者無標籤——那是
    遲報或缺報，不是驚奇。

    label 是前瞻構造，不受 `assert_no_lookahead` 約束（與 `excess_rank_labels` 同）。
    """
    if max_lag_sessions < 1:
        raise ValueError(f"max_lag_sessions 須 ≥ 1，得到 {max_lag_sessions}")
    for col in ("stock_id", "period_end", "available_at", "yoy"):
        if col not in revenue.columns:
            raise ValueError(f"revenue 缺欄位 {col}（需 stock_id/period_end/available_at/yoy）")
    rev = (
        revenue.select("stock_id", "period_end", "available_at", "yoy")
        .drop_nulls("yoy")
        .with_columns(_avail=pl.col("available_at").dt.date())
        # 同一天有多筆（更正重編）時取 period_end 最新的那筆
        .sort("stock_id", "_avail", "period_end")
        .unique(subset=["stock_id", "_avail"], keep="last", maintain_order=True)
        .with_columns(
            _accel=pl.col("yoy") - pl.col("yoy").shift(1).over("stock_id", order_by="_avail"),
            _yoy_next=pl.col("yoy").shift(-1).over("stock_id", order_by="_avail"),
            _avail_next=pl.col("_avail").shift(-1).over("stock_id", order_by="_avail"),
        )
        .select("stock_id", "_avail", "yoy", "_accel", "_yoy_next", "_avail_next")
        .sort("stock_id", "_avail")
    )
    spine = (
        pl.DataFrame({"date": sorted(set(signal_dates))}, schema={"date": pl.Date})
        .join(rev.select("stock_id").unique(), how="cross")
        .sort("stock_id", "date")
    )
    with warnings.catch_warnings():
        # 附 by 群組時 polars 無法驗證排序旗標；已明確 sort（同 factors/fundamental.py）
        warnings.filterwarnings("ignore", message="Sortedness of columns cannot be checked")
        joined = spine.join_asof(
            rev, left_on="date", right_on="_avail", by="stock_id", strategy="backward"
        )
    cal = _session_numbers(trading_days)
    have_next = (
        joined.filter(pl.col("_avail_next").is_not_null())
        .join(cal.rename({"_d": "date", "_n": "_n_t"}), on="date", how="inner")
        .sort("_avail_next")
    )
    have_next = have_next.join_asof(
        cal, left_on="_avail_next", right_on="_d", strategy="backward"
    ).rename({"_n": "_n_next"})
    usable = have_next.filter(
        pl.col("yoy").is_not_null()
        & pl.col("_accel").is_not_null()
        & pl.col("_yoy_next").is_not_null()
        & pl.col("_n_next").is_not_null()
        & (pl.col("_n_next") - pl.col("_n_t")).is_between(1, max_lag_sessions)
    ).select("date", "stock_id", "_yoy_next", "yoy", "_accel")
    if usable.is_empty():
        raise ValueError(
            f"營收驚奇標籤為空：{len(signal_dates)} 個訊號日中沒有任何一檔"
            f"在 {max_lag_sessions} 個開市日內有下一次公布。"
            "檢查 monthly_revenue 的 available_at 是否覆蓋該區間"
        )
    bad = usable.filter(
        ~pl.all_horizontal(pl.col(c).is_finite() for c in ("_yoy_next", "yoy", "_accel"))
    )
    if not bad.is_empty():
        # 黃金規則 5：非有限值不是缺值，drop_nulls 攔不住，而 OLS 會把它擴散到
        # 當日整個橫斷面的殘差——一列壞資料毀掉一整天的樣本
        raise ValueError(
            f"monthly_revenue.yoy 有 {bad.height} 列非有限值（例：{bad.row(0, named=True)}）。"
            "去年同月營收為 0 會產生 inf；請在清洗層修正，不得帶進標籤"
        )
    return (
        _residualize_cross_section(
            usable.sort("date", "stock_id"), "_yoy_next", ("yoy", "_accel"), "_resid"
        )
        .pipe(cross_rank_pct, "_resid", "label")
        .select("date", "stock_id", "label")
        .sort("date", "stock_id")
    )


def forward_drawdown_labels(
    prices: pl.DataFrame,
    signal_dates: Sequence[dt.date],
    *,
    horizon: int,
    adjusted: bool = False,
    max_span_sessions: int | None = None,
) -> pl.DataFrame:
    """R 系列標籤（D-075）：未來 `horizon` 個交易日內、**相對 t 日收盤**的最低點跌幅。

    輸入需含 (date, stock_id, close)，`adjusted=True` 時另需 `adj`。
    輸出 (date, stock_id, label)，label 為同日跌幅的橫斷面百分位 (0, 1]
    ——**低百分位＝跌得深**，故風控閘門剔除的是分數最低的一段。

    **標籤定義是寫死的，不得改回 run-max 版**（D-075）：原版由「t 之後的 run-max
    往下算」，與 `price.high_52w`（當下離高點多近）共用「當下價位在區間中的位置」；
    實測換成本定義後 IC 衰減 13~41%，那一段是共用資訊不是預測力。

    `adjusted=True` 走總報酬價：除息當天的價格跳空不是虧損，未還原時會被當成回撤
    （D-054 同一個失效模式）。`max_span_sessions` 見 `forward_open_returns`
    ——出場列與當前列的開市日跨距超過此值即設 null。
    """
    if horizon < 1:
        raise ValueError(f"horizon 須 ≥ 1，得到 {horizon}")
    if max_span_sessions is not None and max_span_sessions < horizon:
        raise ValueError(
            f"max_span_sessions({max_span_sessions}) 不得小於 horizon({horizon})："
            "往後 horizon 列至少跨 horizon 個開市日，設得更小會讓所有標籤變 null"
        )
    if "close" not in prices.columns:
        raise ValueError("prices 缺 close 欄（forward_drawdown_labels 以收盤價為基準）")
    if adjusted and "adj" not in prices.columns:
        raise ValueError(
            "adjusted=True 需要 adj 欄（固定過去錨的累積除權息還原因子）；"
            "缺欄時不得回退成未還原價格——除息跳空會被當成回撤（D-054）"
        )
    px = (pl.col("close") / pl.col("adj")) if adjusted else pl.col("close")
    frame = prices.sort("stock_id", "date")
    if max_span_sessions is not None:
        frame = session_index(frame).sort("stock_id", "date")
    # schema 允許 close >= 0，故 0 是合法值；除以 0 會產生 inf 而 inf 通過每一道 null 檢查
    frame = frame.with_columns(pl.when(px > 0.0).then(px).otherwise(None).alias("_p"))
    fwd_min = (
        pl.col("_p")
        .rolling_min(window_size=horizon, min_samples=horizon)
        .shift(-horizon)
        .over("stock_id", order_by="date")
    )
    if max_span_sessions is not None:
        sidx = pl.col("_sidx").cast(pl.Int64)
        span = sidx.shift(-horizon).over("stock_id", order_by="date") - sidx
        fwd_min = pl.when(span <= max_span_sessions).then(fwd_min).otherwise(None)
    keep = sorted(set(signal_dates))
    return (
        frame.with_columns((fwd_min / pl.col("_p") - 1.0).alias("_dd"))
        .filter(pl.col("date").is_in(keep))
        .select("date", "stock_id", "_dd")
        .sort("date", "stock_id")
        .pipe(cross_rank_pct, "_dd", "label")
        .select("date", "stock_id", "label")
    )


def triple_barrier_labels(
    prices: pl.DataFrame,
    *,
    horizon: int,
    up: float,
    down: float,
    adjusted: bool = False,
    max_span_sessions: int | None = None,
    vol_scaled: bool = False,
    vol_window: int | None = None,
) -> pl.DataFrame:
    """S 系列買進標籤（D-120 §三）：t+1 開盤進場後 `horizon` 個交易日內，收盤**先**碰到
    `+up`（在碰到 `−down` 之前）＝1；先碰下緣、或期滿兩邊都沒碰＝0。

    輸入需含 (date, stock_id, open, close)；`adjusted=True` 另需 `adj`（總報酬價，D-054）。
    輸出 (date, stock_id, label)，label ∈ {0.0, 1.0, null}。

    **這是絕對標籤，刻意不做橫斷面轉換**——既有標籤全是「比同日其他股票好多少」，
    所以崩盤日照樣有前 1%；這個標籤在崩盤日會整片是 0，模型才學得到「現在不是時候」。

    往後不足 `horizon` 列者為 null（沒有答案），不得記 0——那會把「資料到期」教成「沒漲」。
    同一根收盤不可能同時碰到上下緣，故不需要 tie-break。

    `vol_scaled=True`（T-260／D-127）：上下緣改為 `up × σ`／`down × σ`，
    σ＝該檔在訊號日（含）以前 `vol_window` 日報酬標準差 × √horizon（該視野的預期波動）。
    **為什麼需要**：固定百分比的邊界對高波動股天生容易碰到，模型會學成「挑波動大的」
    而不是「挑會漲的」——D-126 實測平靜日前 15 名先跌 10% 的比例（50.6%）遠高於先漲（33.4%）。
    標準化之後，碰到邊界的難度對每檔股票、每個時期一致，模型只能靠方向取勝。
    σ 算不出來或為 0（上市未滿 `vol_window` 日、或價格完全不動）→ 該列無標籤，**不補值**。
    """
    if horizon < 1:
        raise ValueError(f"horizon 須 ≥ 1，得到 {horizon}")
    if up <= 0.0 or down <= 0.0:
        raise ValueError(f"up／down 須 > 0，得到 up={up}、down={down}")
    if max_span_sessions is not None and max_span_sessions < horizon + 1:
        raise ValueError(
            f"max_span_sessions({max_span_sessions}) 不得小於 horizon+1({horizon + 1})"
        )
    if vol_scaled and (vol_window is None or vol_window < 2):
        raise ValueError(f"vol_scaled=True 需要 vol_window >= 2，得到 {vol_window}")
    need = {"open", "close"} | ({"adj"} if adjusted else set())
    missing = sorted(need - set(prices.columns))
    if missing:
        raise ValueError(f"triple_barrier_labels 缺欄位：{missing}")
    adj = pl.col("adj") if adjusted else pl.lit(1.0)
    frame = prices.sort("stock_id", "date")
    if max_span_sessions is not None:
        frame = session_index(frame).sort("stock_id", "date")

    def _fwd(col: str, k: int) -> pl.Expr:
        p = pl.col(col) / adj
        return pl.when(p > 0.0).then(p).otherwise(None).shift(-k).over("stock_id", order_by="date")

    if vol_scaled:
        window = int(cast("int", vol_window))
        px_now = pl.col("close") / adj
        daily = (px_now / px_now.shift(1).over("stock_id", order_by="date") - 1.0).over(
            "stock_id", order_by="date"
        )
        # 只用訊號日（含）以前的報酬：rolling 本身向後看，無前視
        sigma = daily.rolling_std(window, min_samples=window).over("stock_id", order_by="date") * (
            horizon**0.5
        )
        frame = frame.with_columns(
            _sigma=pl.when(sigma > 0.0).then(sigma).otherwise(None),
        )
        up_barrier, dn_barrier = pl.col("_sigma") * up, pl.col("_sigma") * down
    else:
        frame = frame.with_columns(_sigma=pl.lit(1.0))
        up_barrier, dn_barrier = pl.lit(up), pl.lit(down)

    entry = _fwd("open", 1)
    first_up = pl.lit(None, dtype=pl.Int32)
    first_dn = pl.lit(None, dtype=pl.Int32)
    for k in range(horizon, 0, -1):  # 由遠到近覆寫，最後留下的是**最早**碰到的那一天
        ret = _fwd("close", k) / entry - 1.0
        first_up = pl.when(ret >= up_barrier).then(pl.lit(k, dtype=pl.Int32)).otherwise(first_up)
        first_dn = pl.when(ret <= -dn_barrier).then(pl.lit(k, dtype=pl.Int32)).otherwise(first_dn)
    complete = (
        _fwd("close", horizon).is_not_null() & entry.is_not_null() & pl.col("_sigma").is_not_null()
    )
    if max_span_sessions is not None:
        sidx = pl.col("_sidx").cast(pl.Int64)
        span = sidx.shift(-horizon).over("stock_id", order_by="date") - sidx
        complete = complete & (span <= max_span_sessions)
    frame = frame.with_columns(_up=first_up, _dn=first_dn, _ok=complete)
    won = pl.col("_up").is_not_null() & (pl.col("_dn").is_null() | (pl.col("_up") < pl.col("_dn")))
    return (
        frame.with_columns(
            pl.when(pl.col("_ok")).then(won.cast(pl.Float64)).otherwise(None).alias("label")
        )
        .select("date", "stock_id", "label")
        .sort("date", "stock_id")
    )


def liquid_pool(
    prices: pl.DataFrame, *, adv_window: int, min_adv_pctl: float | None, min_price: float | None
) -> pl.DataFrame:
    """每日流動性合格的 (date, stock_id)（T-248）：與 `strategy.universe.universe_ids` 同定義——
    `adv_window` 日成交金額中位數在當日第 `min_adv_pctl` 百分位（linear 內插）以上、收盤 ≥ `min_price`。

    訓練樣本與回測宇宙用同一把尺：買進模型只會被拿來排這個池子裡的股票，
    拿池外的冷門股去訓練，學到的是用不到的東西，還會稀釋校準。
    **與 `universe_ids` 的已知差異**：這裡不做衛生濾網（跳價／缺口）與市場別濾網——
    那兩者剔除的是個別異常日，不改變池子的量級；差異寫明，不假裝相同。
    輸入需含 (date, stock_id, close, amount)。
    """
    frame = prices.sort("stock_id", "date").with_columns(
        _adv=pl.col("amount").rolling_median(adv_window, min_samples=adv_window).over("stock_id")
    )
    keep = pl.col("_adv").is_not_null()
    if min_adv_pctl is not None:
        frame = frame.with_columns(
            _cut=pl.col("_adv").quantile(min_adv_pctl, "linear").over("date")
        )
        keep = keep & (pl.col("_adv") >= pl.col("_cut"))
    if min_price is not None:
        keep = keep & (pl.col("close") >= min_price)
    return frame.filter(keep).select("date", "stock_id").sort("date", "stock_id")


def tail_up_labels(
    prices: pl.DataFrame,
    *,
    horizon: int,
    up: float,
    adjusted: bool = False,
    max_span_sessions: int | None = None,
) -> pl.DataFrame:
    """D-143：右尾標籤——t+1 開盤進場後 `horizon` 個交易日內，收盤相對進場價的
    **最大漲幅 ≥ `up`** 記 1，否則 0。

    **這一輪換的是問題本身。** 前八輪都在預測橫斷面「平均」報酬的排名，那等於複製
    指數再扣成本（使用者 2026-09-22：「我都找整體股市的前段班，這樣注定打不贏 ETF」）。
    右尾問的是「會不會飆」，而這兩件事在台股方向相反：`price.mom_60d` 的橫斷面平均
    RankIC 為負（D-020），但它前 10% 的飆股率是基礎率的 2.25 倍。

    與 `down_probability_labels` 的關係：**鏡像**。那個問有沒有跌到，這個問有沒有漲到。
    與 `triple_barrier_labels` 的差別：**先跌不取消後漲**——這裡只問這段期間內有沒有
    摸到 `+up`，不問路徑。會不會被中途的回檔洗掉是**出場規則**的問題（D-143 §四 的
    ratchet），不該混進標籤；混進去就變成同時在學「會漲」與「不會先跌」兩件事。

    進場價取 **t+1 開盤**（與 `execution: next_open` 及同族標籤一致），不是訊號日收盤：
    收盤到次日開盤的跳空是**買不到的**，用收盤當基準等於把那段跳空算進模型的功勞。

    往後不足 `horizon` 列者為 null（沒有答案），不得記 0——那會把「資料到期」教成「沒飆」。
    """
    if horizon < 1:
        raise ValueError(f"horizon 須 ≥ 1，得到 {horizon}")
    if up <= 0.0:
        raise ValueError(f"up 須 > 0，得到 {up}")
    need = {"open", "close"} | ({"adj"} if adjusted else set())
    missing = sorted(need - set(prices.columns))
    if missing:
        raise ValueError(f"tail_up_labels 缺欄位：{missing}")
    adj = pl.col("adj") if adjusted else pl.lit(1.0)
    frame = prices.sort("stock_id", "date")
    if max_span_sessions is not None:
        frame = session_index(frame).sort("stock_id", "date")

    def _fwd(col: str, k: int) -> pl.Expr:
        p = pl.col(col) / adj
        return pl.when(p > 0.0).then(p).otherwise(None).shift(-k).over("stock_id", order_by="date")

    entry = _fwd("open", 1)
    hit = pl.lit(False)
    for k in range(1, horizon + 1):
        hit = hit | ((_fwd("close", k) / entry - 1.0) >= up)
    complete = _fwd("close", horizon).is_not_null() & entry.is_not_null()
    if max_span_sessions is not None:
        sidx = pl.col("_sidx").cast(pl.Int64)
        complete = complete & (
            sidx.shift(-horizon).over("stock_id", order_by="date") - sidx <= max_span_sessions
        )
    return (
        frame.with_columns(
            pl.when(complete).then(hit.cast(pl.Float64)).otherwise(None).alias("label")
        )
        .select("date", "stock_id", "label")
        .sort("date", "stock_id")
    )


def down_probability_labels(
    prices: pl.DataFrame,
    *,
    horizon: int,
    drop: float,
    adjusted: bool = False,
    max_span_sessions: int | None = None,
) -> pl.DataFrame:
    """D-131：個股下跌機率的標籤——t+1 開盤進場後 `horizon` 個交易日內，
    收盤相對進場價的最大跌幅 ≤ `−drop` 記 1，否則 0。

    與 `forward_drawdown_labels` 的差別：那個回傳**橫斷面百分位**（相對，用來排序），
    這個回傳 **0/1**（絕對）——「這檔會不會跌 10%」才答得出來，
    而「相對其他股票跌得比較多」回答不了風險預警的問題。

    與 `triple_barrier_labels` 的差別：**先漲不取消後跌**。這裡只問有沒有跌到，
    不問先碰哪一邊——那才是「會不會下跌」的字面意思。

    往後不足 `horizon` 列者為 null（沒有答案），不得記 0。
    """
    if horizon < 1:
        raise ValueError(f"horizon 須 ≥ 1，得到 {horizon}")
    if drop <= 0.0:
        raise ValueError(f"drop 須 > 0，得到 {drop}")
    need = {"open", "close"} | ({"adj"} if adjusted else set())
    missing = sorted(need - set(prices.columns))
    if missing:
        raise ValueError(f"down_probability_labels 缺欄位：{missing}")
    adj = pl.col("adj") if adjusted else pl.lit(1.0)
    frame = prices.sort("stock_id", "date")
    if max_span_sessions is not None:
        frame = session_index(frame).sort("stock_id", "date")

    def _fwd(col: str, k: int) -> pl.Expr:
        p = pl.col(col) / adj
        return pl.when(p > 0.0).then(p).otherwise(None).shift(-k).over("stock_id", order_by="date")

    entry = _fwd("open", 1)
    hit = pl.lit(False)
    for k in range(1, horizon + 1):
        hit = hit | ((_fwd("close", k) / entry - 1.0) <= -drop)
    complete = _fwd("close", horizon).is_not_null() & entry.is_not_null()
    if max_span_sessions is not None:
        sidx = pl.col("_sidx").cast(pl.Int64)
        complete = complete & (
            sidx.shift(-horizon).over("stock_id", order_by="date") - sidx <= max_span_sessions
        )
    return (
        frame.with_columns(
            pl.when(complete).then(hit.cast(pl.Float64)).otherwise(None).alias("label")
        )
        .select("date", "stock_id", "label")
        .sort("date", "stock_id")
    )
