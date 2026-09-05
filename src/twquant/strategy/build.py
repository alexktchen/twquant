"""策略執行：再平衡排程 + 目標權重產生（SPEC-003 §1/§5）。

build_targets 對單一 signal_date 產出 (date, stock_id, target_weight)：
宇宙過濾 → 因子計算（限宇宙）→ 組合 → 選股 → 權重 → overlay → 單股上限。
signal_dates 用交易日曆（T-003）把 weekly/monthly 排程對到真實開市日（AC-3）。
純運算：無網路、無寫檔。
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

from twquant.errors import ConfigError, DataError
from twquant.strategy.banding import banded_selection
from twquant.strategy.combine import combined_score
from twquant.strategy.overlays import get_overlay
from twquant.strategy.universe import universe_ids
from twquant.strategy.weighting import (
    cap_and_renormalize,
    fixed_slot_weights,
    mktcap_weights,
    shifted_power_weights,
)

if TYPE_CHECKING:
    from twquant.data.calendar import TradingCalendar
    from twquant.data.warehouse import TableSource
    from twquant.strategy.config import RebalanceConfig, StrategyConfig

_WEEKDAY = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4}

_OUT_SCHEMA = {"date": pl.Date, "stock_id": pl.String, "target_weight": pl.Float64}


def signal_dates(
    rb: RebalanceConfig, calendar: TradingCalendar, start: dt.date, end: dt.date
) -> list[dt.date]:
    """依排程回傳 [start, end] 內的訊號日（皆為開市日）。"""
    sessions = calendar.between(start, end)
    if rb.schedule == "daily":
        return sessions
    if rb.schedule in ("weekly", "biweekly"):
        target = _WEEKDAY[rb.day]
        picked: dict[tuple[int, int], dt.date] = {}
        for d in sessions:
            key = d.isocalendar()[:2]  # (ISO 年, ISO 週)
            if d.weekday() >= target and key not in picked:
                picked[key] = d  # 該週第一個「星期>=目標」的開市日（週一休市則順延）
        if rb.schedule == "biweekly":
            # 取 ISO 偶數週而非「從 start 起每隔一週」：讓不同起日的回測共用同一套再平衡
            # 日曆，否則兩次回測的差異會混進日曆相位（D-046 候選 2、4）。
            picked = {k: v for k, v in picked.items() if k[1] % 2 == 0}
        return sorted(picked.values())
    if rb.schedule == "monthly":
        dom = rb.day_of_month or 1
        picked_m: dict[tuple[int, int], dt.date] = {}
        for d in sessions:
            key = (d.year, d.month)
            if d.day >= dom and key not in picked_m:
                picked_m[key] = d
        return sorted(picked_m.values())
    raise ValueError(f"未知排程：{rb.schedule}")


def _empty() -> pl.DataFrame:
    return pl.DataFrame(schema=_OUT_SCHEMA)


def build_targets(
    cfg: StrategyConfig,
    wh: TableSource,
    signal_date: dt.date,
    held: frozenset[str] = frozenset(),
) -> pl.DataFrame:
    """對 signal_date 產出目標權重 (date, stock_id, target_weight)。空手日回空表。

    held 為呼叫端當下的持股（SPEC-012）：只有 selection.method=banded_top_n 會用到，
    其餘方法忽略之，故既有策略的結果不受影響。本函數仍為純運算。
    """
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.factors.compute import compute
    from twquant.factors.registry import get

    view = wh.view(signal_date)
    uni = universe_ids(view, signal_date, cfg.universe)
    if not uni:
        # D-037/黃金規則 5：宇宙全空是資料或配置問題，不是「空手日」。靜默回空表會讓
        # 回測輸出 0 成交/0 報酬卻 exit 0，還把無效試驗寫進 research_log 污染 DSR trials。
        u = cfg.universe
        raise DataError(
            "prices_daily",
            signal_date,
            f"策略「{cfg.name}」宇宙過濾後為 0 檔——期望 ≥1 檔。"
            f"檢查該訊號日行情是否缺漏，或濾網是否過嚴："
            f"min_adv_twd={u.min_adv_twd}、min_price={u.min_price}、"
            f"max_jump_ratio={u.max_jump_ratio}、max_gap_sessions={u.max_gap_sessions}、"
            f"hygiene_lookback={u.hygiene_lookback}",
        )

    names = [s.factor for s in cfg.signals]
    params = {s.factor: dict(s.params) for s in cfg.signals}
    fdf = compute(names, view, dates=[signal_date], params=params).filter(
        pl.col("stock_id").is_in(uni)
    )
    if fdf.is_empty():
        return _empty()

    if cfg.require_all_factors:
        # D-061／R12：`rank_avg` 對存在的列求和，缺負向因子者拿不到負分、分數虛高。
        # 在**組合分數之前**剔除，否則它們會先污染各因子的橫斷面百分位。
        complete = (
            fdf.group_by("stock_id")
            .agg(_k=pl.col("factor").n_unique())
            .filter(pl.col("_k") == len(names))["stock_id"]
            .to_list()
        )
        fdf = fdf.filter(pl.col("stock_id").is_in(complete))
        if fdf.is_empty():
            return _empty()

    # 方向併入權重：direction=-1 的因子以負權重併入，使高分一致代表看多
    signed = {s.factor: s.weight * get(s.factor).direction for s in cfg.signals}
    score = combined_score(fdf, signed, cfg.combine).drop_nulls("score")
    if score.is_empty():
        return _empty()

    picked = _select(score, cfg, held=held)
    if picked.is_empty():
        return _empty()

    # 平移基準取**全宇宙**最低分（非入選名單），見 SPEC-013 §2.2.1(a)
    score_min = float(score["score"].min())  # type: ignore[arg-type]
    log_mktcap: dict[str, float] = {}
    if cfg.weighting == "mktcap":
        mc = compute(["fundamental.log_market_cap"], view, dates=[signal_date])
        log_mktcap = dict(zip(mc["stock_id"].to_list(), mc["value"].to_list(), strict=True))

    # 截頂與重分配在 `_weight` 內完成，**overlay 之後不再歸一**（SPEC-013 §2.2.1(c)）：
    # 在最後歸一會把 breadth 這類 overlay 刻意壓低的曝險補回去，改變既有策略行為。
    weights = _weight(picked, cfg, score_min=score_min, log_mktcap=log_mktcap)
    for name in cfg.overlays:
        weights = get_overlay(name)(signal_date, weights, view, cfg.overlay_params.get(name, {}))

    return weights.select("date", "stock_id", "target_weight").sort("stock_id")


def _select(
    score: pl.DataFrame, cfg: StrategyConfig, *, held: frozenset[str] = frozenset()
) -> pl.DataFrame:
    sel = cfg.selection
    if sel.method == "banded_top_n":
        # 門檻的存在與大小關係已由 SelectionConfig 驗證（SPEC-012 AC-2）
        assert sel.entry_rank_pct is not None and sel.exit_rank_pct is not None
        return banded_selection(
            score,
            held=held,
            top_n=sel.top_n or 40,
            entry_rank_pct=sel.entry_rank_pct,
            exit_rank_pct=sel.exit_rank_pct,
        )
    if sel.method == "top_n":
        n = sel.top_n or 40
        # 次序鍵 stock_id：分數同分時決定性挑選（避免跨程序 group_by 順序影響選股）
        return score.sort(["score", "stock_id"], descending=[True, False]).head(n)
    if sel.method == "threshold":
        thr = sel.threshold if sel.threshold is not None else 0.0
        return score.filter(pl.col("score") >= thr)
    raise ValueError(f"未知選股方法：{sel.method}")


def _weight(
    picked: pl.DataFrame,
    cfg: StrategyConfig,
    *,
    score_min: float,
    log_mktcap: dict[str, float],
) -> pl.DataFrame:
    """依配置算目標權重，並在此完成截頂與重分配（SPEC-013 §2.2.1）。

    `picked` 的列序由 `_select` 決定且穩定；下面以位置對應寫回權重，不得重排。
    """
    ids = picked["stock_id"].to_list()
    n = len(ids)
    if cfg.weighting == "equal":
        raw = [1.0 / n] * n
    elif cfg.weighting == "fixed_slot":
        # 固定格位：每檔 1/top_n，不足額的部分留現金。**直接回傳、不過 cap_and_renormalize**
        # ——那個函式會把省下的資金重分配回去，正好抵銷「少投一點」的意圖。
        slots = cfg.selection.top_n
        if slots is None:
            raise ConfigError(
                "weighting=fixed_slot 需要 selection.top_n 決定格位數；"
                "threshold 選股沒有固定格位，語意未定義。"
            )
        return picked.with_columns(
            pl.Series(
                "target_weight",
                fixed_slot_weights(n, slots=slots, cap=cfg.max_weight_per_stock),
            )
        )
    elif cfg.weighting in ("score_prop", "score_pow"):
        p = 1.0 if cfg.weighting == "score_prop" else cfg.weighting_power
        raw = shifted_power_weights(picked["score"].to_list(), p=p, score_min=score_min)
    elif cfg.weighting == "mktcap":
        missing = [s for s in ids if s not in log_mktcap]
        if missing:
            # 靜默補等權會讓「市值加權」名不副實；缺市值是資料問題（黃金規則 5）
            raise DataError(
                "financial_statements",
                None,
                f"weighting=mktcap 但 {len(missing)} 檔入選股無市值："
                f"{', '.join(missing[:8])}。宇宙層應以 mktcap_rank_* 先排除無市值者。",
            )
        raw = mktcap_weights([log_mktcap[s] for s in ids])
    else:  # inv_vol
        raise NotImplementedError(
            f"weighting={cfg.weighting} 尚未實作（見 SPEC-013 §2.2.1）。"
            "本規格用不到它，不得為了順手而實作（CLAUDE.md §7 禁止範圍外重構）。"
        )
    w = cap_and_renormalize(raw, cfg.max_weight_per_stock)
    return picked.with_columns(pl.Series("target_weight", w))
