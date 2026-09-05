"""宇宙過濾（SPEC-003 §2）。

以 PIT 視圖為基礎，回傳 signal_date 當日合格的股票集合。過濾在因子計算「之前」執行
（避免處置股污染橫斷面標準化）。

第一版可實作：20 日成交金額中位數（ADV，中位數抗漲停無量）、最低價、四碼普通股
（排除權證/ETF）。**待接資料**：處置/全額交割（disposition_stocks）、上市日
（stock_meta.list_date）、市場別（stock_meta.market）——相關旗標暫為 no-op 並在
STRATEGY/TASKS 標註，接上後於此啟用。
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView
    from twquant.strategy.config import UniverseConfig

_COMMON_STOCK = r"^[1-9][0-9]{3}$"  # 四碼普通股，排除 00xx ETF 與 5-6 碼權證


def universe_ids(view: PitView, signal_date: dt.date, cfg: UniverseConfig) -> list[str]:
    """回傳 signal_date 合格股票代號（已套用可實作的過濾條件）。"""
    lf = (
        view.table("prices_daily")
        .select("date", "stock_id", "close", "amount")
        .filter(pl.col("date") <= signal_date)
        .sort(["stock_id", "date"])
    )
    # 20 日成交金額中位數（截至 signal_date）；不足 20 日 → null → 淘汰（流動性歷史不足）
    lf = lf.with_columns(_adv=pl.col("amount").rolling_median(20).over("stock_id"))
    # D-029 衛生濾網：近 hygiene_lookback 列內出現非漲跌停可解釋的跳價或交易日缺口
    # （停牌復牌/減資/資料缺段）→ 排除，避免買進即將/剛剛經歷資料事件的股票。
    # D-037：缺口一律以「漏掉的開市日數」判斷。全市場休市（農曆年封關可達 12 曆日）
    # 不漏任何開市日，故不算缺口；市場日曆＝prices_daily 的日期聯集，與
    # data/calendar.py 的交易日定義同源（該處亦註明禁用 weekday() 推算）。
    if cfg.max_jump_ratio is not None or cfg.max_gap_sessions is not None:
        market = lf.select("date").unique().sort("date").with_row_index("_sidx")
        lf = lf.join(market, on="date", how="left").sort(["stock_id", "date"])
        prev_c = pl.col("close").shift(1).over("stock_id")
        sidx = pl.col("_sidx").cast(pl.Int64)
        bad = pl.lit(False)
        if cfg.max_jump_ratio is not None:
            bad = bad | ((pl.col("close") / prev_c - 1.0).abs() > cfg.max_jump_ratio)
        if cfg.max_gap_sessions is not None:
            missed = sidx - sidx.shift(1).over("stock_id") - 1
            bad = bad | (missed > cfg.max_gap_sessions)
        lf = lf.with_columns(_cum=bad.cast(pl.Int32).fill_null(0).cum_sum().over("stock_id"))
        lf = lf.with_columns(
            _badw=(
                pl.col("_cum")
                - pl.col("_cum").shift(cfg.hygiene_lookback).over("stock_id").fill_null(0)
            )
            > 0
        )
    else:
        lf = lf.with_columns(_badw=pl.lit(False))
    at = lf.filter(pl.col("date") == signal_date)

    at = at.filter(pl.col("stock_id").str.contains(_COMMON_STOCK))
    at = at.filter(~pl.col("_badw"))
    if cfg.min_adv_twd is not None:
        at = at.filter(pl.col("_adv") >= cfg.min_adv_twd)
    if cfg.min_price is not None:
        at = at.filter(pl.col("close") >= cfg.min_price)
    ids = sorted(at.select("stock_id").collect()["stock_id"].to_list())
    if cfg.mktcap_rank_max is not None or cfg.mktcap_rank_min is not None:
        ids = _mktcap_gate(
            view,
            signal_date,
            ids,
            rank_max=cfg.mktcap_rank_max,
            rank_min=cfg.mktcap_rank_min,
        )
    if (
        cfg.min_revenue_yoy is not None
        or cfg.max_revenue_yoy is not None
        or cfg.min_revenue_streak is not None
    ):
        ids = _revenue_gate(
            view,
            ids,
            min_yoy=cfg.min_revenue_yoy,
            max_yoy=cfg.max_revenue_yoy,
            min_streak=cfg.min_revenue_streak,
        )
    if cfg.min_roe is not None:
        ids = _quality_gate(view, signal_date, ids, cfg.min_roe)
    return ids


def _revenue_gate(
    view: PitView,
    ids: list[str],
    *,
    min_yoy: float | None,
    max_yoy: float | None,
    min_streak: int | None,
) -> list[str]:
    """T-122：依月營收年增與連續正成長月數過濾，**無營收資料者一律排除**。

    PIT 由 `view.table` 負責（依 available_at 截斷），故這裡**不再**依 `date` 過濾——
    月營收的 `date` 是所屬月月底，不是公布日，拿它當截點會放行尚未公告的月份。

    「連續」以 **year_month 的跨距**驗證，不是「有幾筆是正的」：`drop_nulls` 之後
    缺月的兩筆會變成相鄰，只數筆數會把 10 月與 12 月誤判為連續兩個月。
    """
    rev = (
        view.table("monthly_revenue")
        .select("stock_id", "year_month", "yoy")
        .drop_nulls("yoy")
        # year_month 為 "YYYY-MM"，字典序即時序
        .sort(["stock_id", "year_month"])
        .collect()
    )
    if rev.is_empty():
        return []
    # 由新到舊的序號：1 = 最新已公布月
    rev = rev.with_columns(
        _from_end=pl.len().over("stock_id") - pl.int_range(pl.len()).over("stock_id")
    )

    keep: set[str] | None = None
    if min_yoy is not None or max_yoy is not None:
        latest = rev.filter(pl.col("_from_end") == 1)
        if min_yoy is not None:
            latest = latest.filter(pl.col("yoy") >= min_yoy)
        if max_yoy is not None:
            latest = latest.filter(pl.col("yoy") <= max_yoy)
        keep = set(latest["stock_id"].to_list())

    if min_streak is not None:
        # 月份序數：year × 12 + month，用來驗證跨距而非筆數
        mo = pl.col("year_month").str.slice(0, 4).cast(pl.Int32) * 12 + pl.col(
            "year_month"
        ).str.slice(5, 2).cast(pl.Int32)
        win = rev.filter(pl.col("_from_end") <= min_streak).with_columns(_mo=mo)
        agg = win.group_by("stock_id").agg(
            _n=pl.len(),
            _all_pos=(pl.col("yoy") > 0).all(),
            _span=pl.col("_mo").max() - pl.col("_mo").min(),
        )
        ok = agg.filter(
            (pl.col("_n") == min_streak)  # 歷史不足即不通過（不得用較短的鏈充數）
            & pl.col("_all_pos")
            & (pl.col("_span") == min_streak - 1)  # 月份必須真的連續
        )
        streak_ids = set(ok["stock_id"].to_list())
        keep = streak_ids if keep is None else (keep & streak_ids)

    assert keep is not None  # 呼叫端已確認至少一個門檻有值
    return [s for s in ids if s in keep]


def _mktcap_gate(
    view: PitView,
    signal_date: dt.date,
    ids: list[str],
    *,
    rank_max: int | None,
    rank_min: int | None,
) -> list[str]:
    """T-121：依 PIT 市值排名過濾（排名 1 = 最大），**市值為 null 一律排除**。

    排名的母體是「當日有市值的四碼普通股全體」，而**不是** ids（流動性濾網之後的殘存集合）。
    這是刻意的：「市值前 100 大」是市場事實，若母體隨 min_adv_twd 變動，T-120 的
    絕對門檻漂移問題就會經由排名滲進來，長歷史上「前 100 大」會年年代表不同的東西。

    為什麼 null 要排除：「不知道它多大」不等於「它符合規模條件」。把缺值當成通過，
    等於讓沒有財報的新股整批繞過濾網（黃金規則 5 的精神，與 D-059 的 min_roe 同一規則）。

    走與因子同一條 compute（因而同一條 PitView），故 PIT 語意一致、不另開讀取路徑。
    """
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發因子註冊
    from twquant.factors.compute import compute

    # log 是單調遞增，故以 ln(市值) 排序等同以市值排序；不需還原成線性值。
    mc = compute(["fundamental.log_market_cap"], view, dates=[signal_date])
    ranked = (
        mc.filter(pl.col("value").is_not_null())
        .filter(pl.col("stock_id").str.contains(_COMMON_STOCK))
        # 次序鍵 stock_id：市值同值時決定性排名（避免跨程序順序影響宇宙）
        .sort(["value", "stock_id"], descending=[True, False])
        .with_row_index("_rank", offset=1)
    )
    if rank_max is not None:
        ranked = ranked.filter(pl.col("_rank") <= rank_max)
    if rank_min is not None:
        ranked = ranked.filter(pl.col("_rank") >= rank_min)
    keep = set(ranked["stock_id"].to_list())
    return [s for s in ids if s in keep]


def _quality_gate(view: PitView, signal_date: dt.date, ids: list[str], min_roe: float) -> list[str]:
    """D-059／T-119：TTM ROE 低於門檻者移出宇宙，**null 一律排除**。

    走與因子同一條 `compute`（因而同一條 PitView），故 PIT 語意一致、不另開讀取路徑。
    濾網必須在因子的橫斷面標準化**之前**作用——否則被剔除的股票仍會影響 z 分數。

    為什麼 null 要排除：「不知道它是不是品質股」不等於「它是品質股」。把缺值當成通過，
    等於讓沒有財報的新股與非標準公司整批繞過濾網，是靜默放行（黃金規則 5 的精神）。
    """
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發因子註冊
    from twquant.factors.compute import compute

    roe = compute(["fundamental.roe"], view, dates=[signal_date])
    keep = set(
        roe.filter(pl.col("value").is_not_null() & (pl.col("value") >= min_roe))[
            "stock_id"
        ].to_list()
    )
    return [s for s in ids if s in keep]
