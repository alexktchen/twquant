"""投信／ETF 資金流因子（SPEC-016 §3，T-338）。

**刻意不在 `factors.builtins`**：builtins 的因子每天在地端與雲端物化，雲端沒有 ETF 三張表（D-170）。
本模組只在研究腳本（T-339）與測試裡 `import twquant.factors.fund_flow` 時註冊；要進每日物化另立裁決。

定義（SPEC-016 §3；窗口預設值與 configs/fund_flow.yaml 的 factors 同值，呼叫端以 params 覆寫）：
- 機械流量 `flow_twd_i(t)`＝Σ_e units_delta_e(t) × close_e(t) × w_{e,i}(基準日 < t 的最近一份持股)。
  close 是 ETF 收盤價（淨值歷史沒有來源，T-334）。
- 某 ETF 當天 units_delta 為 null（分割窗、缺日）而它持有該股 → 該股當天流量 null（未知，不是 0）。
  沒有任何已知持股的 ETF 持有它 → 0（結構性零）。
- 單位數 21:00 才公布（晚於 20:00 截點）：流量算完後把日期前移到下一個交易日（D-008，同融資）。
- 持股超過 `max_stale` 個交易日沒更新的 ETF 不算「已知持股」（覆蓋率因子會反映）。

限制（報告必列）：只有元大、統一有持股（T-334：今天規模的 59%）；實物申購時買股票的是參與券商、
不是投信，這部分不會出現在投信買賣超裡——扣除會扣過頭。單位數變動與實際買賣可能差 1～2 日。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView


def _int(params: dict[str, object], key: str, default: int) -> int:
    raw = params.get(key, default)
    return int(raw) if isinstance(raw, int | float | str) else default


def _sessions(view: PitView) -> pl.LazyFrame:
    """交易日序列與位置（PitView 內的 prices_daily，已按截點過濾）。"""
    return (
        view.table("prices_daily")
        .select(pl.col("date"))
        .unique()
        .sort("date")
        .with_row_index("_pos")
        .with_columns(pl.col("_pos").cast(pl.Int64))
    )


def _etf_flows(view: PitView) -> pl.LazyFrame:
    """(date, etf_id, flow_twd)：每檔追蹤中 ETF 每天的申贖金額；units_delta null → flow null。"""
    units = view.table("etf_units_daily").select("date", "etf_id", "units_delta")
    px = view.table("prices_daily").select(
        "date", pl.col("stock_id").alias("etf_id"), pl.col("close").alias("_etf_close")
    )
    return units.join(px, on=["date", "etf_id"], how="left").with_columns(
        flow_twd=pl.col("units_delta").cast(pl.Float64) * pl.col("_etf_close")
    )


def _stock_flows(view: PitView, max_stale: int) -> pl.LazyFrame:
    """(date, stock_id, flow_twd, _held)：個股當天的機械流量（日期仍是單位數的日期，尚未前移）。"""
    sess = _sessions(view)
    flows = _etf_flows(view).join(sess, on="date", how="inner")
    hold = (
        view.table("etf_holdings_daily")
        .select(pl.col("date").alias("_base"), "etf_id", "stock_id", "weight")
        .join(sess.rename({"date": "_base", "_pos": "_base_pos"}), on="_base", how="inner")
    )
    snaps = hold.select("etf_id", "_base", "_base_pos").unique().sort("_base_pos")
    # 每個 (日, ETF) 配上「基準日 < 當日」的最近一份清單
    picked = (
        flows.sort("_pos")
        .with_columns((pl.col("_pos") - 1).alias("_want"))
        .join_asof(
            snaps.rename({"_base_pos": "_want"}),
            on="_want",
            by="etf_id",
            strategy="backward",
        )
        .join(snaps.select("etf_id", "_base", "_base_pos"), on=["etf_id", "_base"], how="left")
        .filter(pl.col("_base").is_not_null() & (pl.col("_pos") - pl.col("_base_pos") <= max_stale))
    )
    return (
        picked.join(hold.select("_base", "etf_id", "stock_id", "weight"), on=["_base", "etf_id"])
        .with_columns(_part=pl.col("flow_twd") * pl.col("weight"))
        .group_by("date", "stock_id")
        .agg(
            flow_twd=pl.when(pl.col("_part").null_count() > 0)
            .then(None)
            .otherwise(pl.col("_part").sum()),
            _held=pl.lit(True),
        )
    )


def _grid(view: PitView, max_stale: int) -> pl.LazyFrame:
    """(date, stock_id, close, volume, amount, flow_twd, _pos)：有單位數的日子 × 當天有行情的股票。

    沒被任何已知持股的 ETF 持有 → flow 0（結構性零）；持有但某 ETF 當天差分未知 → null。
    """
    sess = _sessions(view)
    days = view.table("etf_units_daily").select("date").unique()
    px = view.table("prices_daily").select("date", "stock_id", "close", "volume", "amount")
    return (
        px.join(days, on="date", how="semi")
        .join(sess, on="date", how="inner")
        .join(_stock_flows(view, max_stale), on=["date", "stock_id"], how="left")
        .with_columns(
            flow_twd=pl.when(pl.col("_held").is_null())
            .then(pl.lit(0.0))
            .otherwise(pl.col("flow_twd"))
        )
        .drop("_held")
    )


def _to_next_session(df: pl.LazyFrame, view: PitView) -> pl.LazyFrame:
    """date → 下一個交易日（21:00 才公布的資料，D-008）。沒有下一個交易日的列丟掉（任何 as_of 都還不能用）。"""
    sess = _sessions(view).select(pl.col("date").alias("_next"), pl.col("_pos").alias("_npos"))
    return (
        df.with_columns((pl.col("_pos") + 1).alias("_npos"))
        .join(sess, on="_npos", how="inner")
        .drop("date", "_pos")
        .rename({"_next": "date", "_npos": "_pos"})
    )


def _wsum(col: str, window: int, *, group: str | None = "stock_id", poison: bool = True) -> pl.Expr:
    """以交易日位置（不是列數）為窗口的滾動和，**逐格、固定順序相加**（呼叫端須先依 group、_pos 排序）。

    不用 polars 的增量滾動和：它「加進來、減出去」逐步算，值離開窗口後會留下 1e-18 級的殘差——
    過去 5 日全是 0 的股票算出 −7e-18，被當成「< 0」的贖回潮（D-172 驗證時抓到）；而且裁切下界
    會改變殘差，使 `trim_lookback` 前後不相等。逐格相加：全 0 就是 0、同一組列永遠同一個結果。
    `poison`：窗口內有 null → null（不得當 0）。
    """

    def over(e: pl.Expr) -> pl.Expr:
        return e.over(group) if group else e

    total: pl.Expr = pl.lit(0.0)
    n_null: pl.Expr = pl.lit(0)
    for k in range(window):
        v = over(pl.col(col).shift(k)).cast(pl.Float64)
        inside = over(pl.col("_pos").shift(k)) > pl.col("_pos") - window
        total = total + pl.when(inside).then(v.fill_null(0.0)).otherwise(0.0)
        n_null = n_null + pl.when(inside & v.is_null()).then(1).otherwise(0)
    return pl.when(n_null > 0).then(None).otherwise(total) if poison else total


def _rolling_sum(col: str, window: int) -> pl.Expr:
    """個股的滾動和（見 `_wsum`）；窗口內有 null → null。"""
    return _wsum(col, window)


@factor(
    name="chip.etf_flow_pressure_5d",
    category="chip",
    direction=-1,
    tables=["etf_units_daily", "etf_holdings_daily", "prices_daily"],
    lookback_days=60,
    params={"window": 5, "adv_window": 20, "max_stale": 10},
)
def etf_flow_pressure_5d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """N 日 ETF 機械流量（TWD）÷ ADV（20 日成交金額中位數）。先驗負向：被申購推高的之後回吐（SPEC-016 §1）。"""
    window, adv_w = _int(params, "window", 5), _int(params, "adv_window", 20)
    g = _to_next_session(_grid(view, _int(params, "max_stale", 10)), view).sort("stock_id", "_pos")
    adv = (
        view.table("prices_daily")
        .select("date", "stock_id", "amount")
        .join(_sessions(view), on="date")
        .sort("stock_id", "_pos")
        .with_columns(
            _adv=pl.col("amount")
            .rolling_median_by("_pos", window_size=f"{adv_w}i")
            .over("stock_id")
        )
        .select("date", "stock_id", "_adv")
    )
    return (
        g.with_columns(_sum=_rolling_sum("flow_twd", window))
        .join(adv, on=["date", "stock_id"], how="inner")
        .with_columns(
            value=pl.when(pl.col("_adv") > 0).then(pl.col("_sum") / pl.col("_adv")).otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .sort("date", "stock_id")
        .collect()
    )


def _disc(view: PitView, max_stale: int) -> pl.LazyFrame:
    """(date, stock_id, _pos, disc, volume)：投信買賣超扣掉 ETF 機械流量（股），日期已前移。"""
    inst = view.table("institutional_daily").select("date", "stock_id", "trust_net")
    return _to_next_session(
        _grid(view, max_stale)
        .join(inst, on=["date", "stock_id"], how="inner")
        .with_columns(
            disc=pl.col("trust_net").cast(pl.Float64)
            - pl.when(pl.col("close") > 0)
            .then(pl.col("flow_twd") / pl.col("close"))
            .otherwise(None)
        ),
        view,
    )


@factor(
    name="chip.trust_disc_net_10d",
    category="chip",
    direction=1,
    tables=["institutional_daily", "etf_units_daily", "etf_holdings_daily", "prices_daily"],
    lookback_days=30,
    params={"window": 10, "max_stale": 10},
)
def trust_disc_net_10d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """N 日投信裁量買超（投信買賣超 − ETF 機械流量，股）÷ N 日成交量（股）。"""
    window = _int(params, "window", 10)
    return (
        _disc(view, _int(params, "max_stale", 10))
        .sort("stock_id", "_pos")
        .with_columns(
            _net=_rolling_sum("disc", window),
            _vol=_wsum("volume", window, poison=False),
        )
        .with_columns(
            value=pl.when(pl.col("_vol") > 0).then(pl.col("_net") / pl.col("_vol")).otherwise(None)
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .sort("date", "stock_id")
        .collect()
    )


@factor(
    name="chip.trust_disc_streak",
    category="chip",
    direction=1,
    tables=["institutional_daily", "etf_units_daily", "etf_holdings_daily", "prices_daily"],
    lookback_days=40,
    params={"max_stale": 10},
)
def trust_disc_streak(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """投信裁量連買天數（disc > 0 連續的交易日數；disc 未知的那天中斷並記 null）。"""
    return (
        _disc(view, _int(params, "max_stale", 10))
        .sort("stock_id", "_pos")
        .with_columns(
            _buy=(pl.col("disc") > 0),
            # 交易日位置不連續（該股缺日）也要中斷
            _gap=(pl.col("_pos").diff().over("stock_id").fill_null(1) != 1),
        )
        .with_columns(
            _blk=(~pl.col("_buy").fill_null(False) | pl.col("_gap") | pl.col("disc").is_null())
            .cast(pl.Int32)
            .cum_sum()
            .over("stock_id")
        )
        .with_columns(
            value=pl.when(pl.col("disc").is_null())
            .then(None)
            .otherwise(pl.col("_buy").cast(pl.Float64).cum_sum().over(["stock_id", "_blk"]))
        )
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .sort("date", "stock_id")
        .collect()
    )


def _market_daily(view: PitView, max_stale: int) -> pl.LazyFrame:
    """(date, _pos, flow_all, aum_all, aum_known, mkt_amount)：全體追蹤中 ETF 的申贖金額、規模與已知持股的規模。"""
    sess = _sessions(view)
    flows = _etf_flows(view).join(sess, on="date", how="inner")
    units = view.table("etf_units_daily").select("date", "etf_id")
    hold = (
        view.table("etf_holdings_daily")
        .select(pl.col("date").alias("_base"), "etf_id")
        .unique()
        .join(sess.rename({"date": "_base", "_pos": "_bpos"}), on="_base")
        .sort("_bpos")
    )
    known = (
        flows.select("date", "etf_id", "_pos")
        .sort("_pos")
        .with_columns((pl.col("_pos") - 1).alias("_bpos"))
        .join_asof(
            hold.select("etf_id", "_bpos").with_columns(_has=pl.col("_bpos")),
            on="_bpos",
            by="etf_id",
        )
        .with_columns(
            _known=pl.col("_has").is_not_null() & (pl.col("_pos") - pl.col("_has") <= max_stale)
        )
        .select("date", "etf_id", "_known")
    )
    aum = (
        view.table("etf_units_daily")
        .select("date", "etf_id", pl.col("units").cast(pl.Float64))
        .join(units, on=["date", "etf_id"], how="semi")
        .join(
            view.table("prices_daily").select("date", pl.col("stock_id").alias("etf_id"), "close"),
            on=["date", "etf_id"],
        )
        .with_columns(_aum=pl.col("units") * pl.col("close"))
    )
    mkt = (
        view.table("prices_daily")
        .filter(pl.col("stock_id").str.contains(r"^[1-9][0-9]{3}$"))
        .group_by("date")
        .agg(mkt_amount=pl.col("amount").sum())
    )
    return (
        flows.group_by("date", "_pos")
        .agg(
            flow_all=pl.when(pl.col("flow_twd").null_count() > 0)
            .then(None)
            .otherwise(pl.col("flow_twd").sum())
        )
        .join(
            aum.join(known, on=["date", "etf_id"], how="left")
            .group_by("date")
            .agg(
                aum_all=pl.col("_aum").sum(),
                aum_known=pl.col("_aum").filter(pl.col("_known").fill_null(False)).sum(),
            ),
            on="date",
        )
        .join(mkt, on="date")
    )


def _broadcast(daily: pl.LazyFrame, view: PitView) -> pl.DataFrame:
    """大盤層級的 (date, value) 展開到當天有行情的每一檔股票（SPEC-002 的 (date, stock_id, value) 介面）。"""
    stocks = view.table("prices_daily").select("date", "stock_id")
    return (
        stocks.join(daily.select("date", "value"), on="date", how="inner")
        .drop_nulls("value")
        .sort("date", "stock_id")
        .collect()
    )


@factor(
    name="market.etf_flow_total_5d",
    category="market",
    direction=-1,
    tables=["etf_units_daily", "etf_holdings_daily", "prices_daily"],
    lookback_days=20,
    params={"window": 5, "max_stale": 10},
)
def etf_flow_total_5d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """全體追蹤中國內股票型 ETF 的 N 日申贖金額 ÷ 全市場 N 日普通股成交金額（大盤層級，分層用）。"""
    window = _int(params, "window", 5)
    daily = _to_next_session(_market_daily(view, _int(params, "max_stale", 10)), view).sort("_pos")
    out = daily.with_columns(
        _f=_wsum("flow_all", window, group=None),
        _m=_wsum("mkt_amount", window, group=None, poison=False),
    ).with_columns(
        value=pl.when(pl.col("_f").is_not_null() & (pl.col("_m") > 0)).then(
            pl.col("_f") / pl.col("_m")
        )
    )
    return _broadcast(out, view)


@factor(
    name="market.etf_holdings_coverage",
    category="market",
    direction=1,
    tables=["etf_units_daily", "etf_holdings_daily", "prices_daily"],
    lookback_days=20,
    params={"max_stale": 10},
)
def etf_holdings_coverage(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """已知持股的 ETF 規模 ÷ 全體追蹤中 ETF 規模（SPEC-016 的 is_decomposed：0＝完全沒拆，1＝全拆）。"""
    daily = _to_next_session(_market_daily(view, _int(params, "max_stale", 10)), view).with_columns(
        value=pl.when(pl.col("aum_all") > 0).then(pl.col("aum_known") / pl.col("aum_all"))
    )
    return _broadcast(daily, view)


def daily_disc(view: PitView, max_stale: int) -> pl.DataFrame:
    """(date, stock_id, disc)：每日投信裁量買賣超（股；日期已前移到下一交易日）。T-339 的 E5／E6 用。"""
    return (
        _disc(view, max_stale).select("date", "stock_id", "disc").sort("date", "stock_id").collect()
    )


def sbl_sell_ratio(view: PitView, window: int) -> pl.DataFrame:
    """N 日借券賣出（股）÷ N 日成交量（股），借券 21:00 公布 → 前移到下一交易日（D-008）。E8 用。

    **刻意不註冊成因子**：它吃的 `sbl_short_daily` 在雲端只留 5 天（`cloud.windows`），註冊了就會被
    雲端窗口測試當成雲端要算的因子。只給 T-339 研究腳本與看板旗標用（同 `daily_disc`）。
    """
    sess = _sessions(view)
    sbl = view.table("sbl_short_daily").select("date", "stock_id", "sbl_sell")
    px = view.table("prices_daily").select("date", "stock_id", "volume")
    g = (
        px.join(sbl, on=["date", "stock_id"], how="inner")
        .join(sess, on="date", how="inner")
        .sort("stock_id", "_pos")
        .with_columns(
            _s=_wsum("sbl_sell", window, poison=False),
            _v=_wsum("volume", window, poison=False),
        )
        .with_columns(value=pl.when(pl.col("_v") > 0).then(pl.col("_s") / pl.col("_v")))
        .select("date", "stock_id", "_pos", "value")
    )
    return (
        _to_next_session(g, view)
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .sort("date", "stock_id")
        .collect()
    )


@factor(
    name="chip.etf_flow_wave",
    category="chip",
    direction=-1,
    tables=["etf_units_daily", "etf_holdings_daily", "prices_daily"],
    lookback_days=60,
    params={"window": 5, "adv_window": 20, "max_stale": 10, "hi_pctl": 0.95, "lo_pctl": 0.05},
)
def etf_flow_wave(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """ETF 申購潮／贖回潮狀態（SPEC-016 E3／E4 的定義）：1＝當天處於狀態，0＝不處於。

    `chip.etf_flow_pressure_5d` 在**四碼普通股**當日橫斷面 ≥ hi_pctl 分位數且 > 0，或 ≤ lo_pctl 且 < 0 → 1。
    壓力 null（差分未知）的股票沒有列。D-172 的進場排除規則用它（`slots.exclude`）。
    """
    hi = float(params.get("hi_pctl", 0.95))  # type: ignore[arg-type]
    lo = float(params.get("lo_pctl", 0.05))  # type: ignore[arg-type]
    p = etf_flow_pressure_5d(view, params).filter(
        pl.col("stock_id").str.contains(r"^[1-9][0-9]{3}$")
    )
    return (
        p.with_columns(
            _hi=pl.col("value").quantile(hi, "linear").over("date"),
            _lo=pl.col("value").quantile(lo, "linear").over("date"),
        )
        .with_columns(
            value=(
                ((pl.col("value") >= pl.col("_hi")) & (pl.col("value") > 0))
                | ((pl.col("value") <= pl.col("_lo")) & (pl.col("value") < 0))
            ).cast(pl.Float64)
        )
        .select("date", "stock_id", "value")
        .sort("date", "stock_id")
    )
