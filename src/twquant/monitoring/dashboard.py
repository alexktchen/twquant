"""Streamlit 監控儀表板（SPEC-007）。

側邊選單分頁：總覽（資料/擷取狀態）、市場 K線（等權指數）、個股 K線、法人流向、
月營收、因子健檢、策略回測（權益曲線）。唯讀讀 clean 資料湖；重讀均 @st.cache_data
快取（Windows bind-mount IO 慢）。以 `streamlit run src/twquant/monitoring/dashboard.py` 啟動。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import altair as alt
import polars as pl
import streamlit as st

from twquant.config import SystemConfig, load_config
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError

if TYPE_CHECKING:
    from twquant.factors.ic import IcReport

_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "system.yaml"
_TABLES = {
    "prices_daily": "日行情",
    "institutional_daily": "三大法人",
    "margin_daily": "融資融券",
    "monthly_revenue": "月營收",
}


def _warehouse() -> Warehouse:
    cfg = load_config(_CONFIG, SystemConfig)
    # data_dir 以 repo 根為基準（與 CWD 無關，讓儀表板在任何目錄啟動都找得到資料湖）
    data_path = Path(cfg.data_dir)
    if not data_path.is_absolute():
        data_path = _CONFIG.parents[1] / data_path
    return Warehouse(data_path)


@st.cache_data(ttl=300, show_spinner=False)
def _sql(query: str) -> pl.DataFrame:
    """對資料湖跑 SQL 並快取 5 分鐘——避免每次 Streamlit 重跑都重掃慢速掛載磁碟。"""
    return _warehouse().sql(query)


@st.cache_data(ttl=300, show_spinner=False)
def _rows(table: str) -> int:
    try:
        return int(_warehouse().scan(table).select(pl.len()).collect().item())
    except DataError:
        return 0


_PAGES = ["總覽", "市場 K線", "個股 K線", "法人流向", "月營收", "因子健檢", "策略回測"]


def main() -> None:
    st.set_page_config(page_title="twquant 監控", page_icon="📈", layout="wide")
    st.sidebar.title("📈 twquant")
    st.sidebar.caption("台股量化平台 · 唯讀")
    page = st.sidebar.radio("選單", _PAGES, label_visibility="collapsed")

    wh = _warehouse()
    latest = wh.latest_date("prices_daily")
    st.sidebar.divider()
    st.sidebar.caption(f"最新行情日：{latest or '—'}")
    st.sidebar.caption("來源：TWSE 上市 · TPEX 上櫃 · MOPS 月營收")

    if page == "總覽":
        _page_overview(wh)
        return
    if not _price_dates():
        st.info("資料湖尚無行情。先執行：`twquant backfill --from ... --to ...`")
        return

    if page == "市場 K線":
        _page_market()
    elif page == "個股 K線":
        _page_stock()
    elif page == "法人流向":
        _page_institutional(wh)
    elif page == "月營收":
        _page_revenue()
    elif page == "因子健檢":
        _factor_health(wh)
    elif page == "策略回測":
        _backtest_panel()


def _page_overview(wh: Warehouse) -> None:
    st.header("總覽")
    st.subheader("資料狀態")
    cols = st.columns(len(_TABLES))
    for col, (table, label) in zip(cols, _TABLES.items(), strict=True):
        d = wh.latest_date(table)
        col.metric(label=label, value=(d.isoformat() if d else "—"), delta=f"{_rows(table):,} 列")
    _ingestion_status(wh)


def _candlestick(pdf: Any, title: str) -> Any:
    """由含 date/open/high/low/close 的 pandas DataFrame 畫 Altair K 線（漲綠跌紅）。"""
    up = "datum.open <= datum.close"
    color = alt.condition(up, alt.value("#26a69a"), alt.value("#ef5350"))
    base = alt.Chart(pdf).encode(x=alt.X("date:T", title=None), color=color)
    wick = base.mark_rule().encode(
        y=alt.Y("low:Q", title="價", scale=alt.Scale(zero=False)), y2="high:Q"
    )
    body = base.mark_bar().encode(y="open:Q", y2="close:Q")
    return (wick + body).properties(title=title, height=400)


@st.cache_data(ttl=300, show_spinner=False)
def _market_index() -> pl.DataFrame:
    """等權價格指數（基期 1000，四碼普通股）的日 OHLC + 成交金額。"""
    return _warehouse().sql(
        "WITH base AS (SELECT stock_id, arg_min(close, date) b FROM prices_daily "
        "WHERE regexp_matches(stock_id, '^[1-9][0-9]{3}$') AND close > 0 GROUP BY stock_id) "
        'SELECT p.date, round(1000*avg(p.open/b.b),2) "open", round(1000*avg(p.high/b.b),2) "high", '
        'round(1000*avg(p.low/b.b),2) "low", round(1000*avg(p.close/b.b),2) "close", '
        'round(sum(p.amount)/1e8,0) "amount" FROM prices_daily p JOIN base b USING (stock_id) '
        "WHERE regexp_matches(p.stock_id, '^[1-9][0-9]{3}$') GROUP BY p.date ORDER BY p.date"
    )


def _page_market() -> None:
    st.header("市場 K線")
    idx = _market_index().to_pandas()
    if idx.empty:
        st.info("無行情資料。")
        return
    st.caption(f"等權價格指數（基期 1000，四碼普通股）· {idx['date'].min()} ~ {idx['date'].max()}")
    st.altair_chart(_candlestick(idx, "等權市場指數"), width="stretch")
    st.markdown("**每日成交金額（億元）**")
    st.bar_chart(idx.set_index("date")["amount"])


@st.cache_data(ttl=300, show_spinner=False)
def _liquid_stocks() -> list[str]:
    df = _warehouse().sql(
        "SELECT stock_id FROM prices_daily WHERE regexp_matches(stock_id, '^[1-9][0-9]{3}$') "
        "GROUP BY stock_id ORDER BY avg(amount) DESC LIMIT 150"
    )
    return [str(x) for x in df["stock_id"].to_list()]


@st.cache_data(ttl=300, show_spinner=False)
def _stock_ohlc(sid: str) -> pl.DataFrame:
    return _warehouse().sql(
        "SELECT date, open, high, low, close, volume FROM prices_daily "
        f"WHERE stock_id = '{sid}' ORDER BY date"
    )


def _page_stock() -> None:
    st.header("個股 K線")
    stocks = _liquid_stocks()
    if not stocks:
        st.info("無行情資料。")
        return
    sid = st.selectbox("股票代號（成交值前 150）", stocks)
    df = _stock_ohlc(str(sid)).to_pandas()
    if df.empty:
        st.info("此股票無資料。")
        return
    st.altair_chart(_candlestick(df, f"{sid} 日K"), width="stretch")
    st.markdown("**成交量（股）**")
    st.bar_chart(df.set_index("date")["volume"])


def _page_institutional(wh: Warehouse) -> None:
    st.header("法人流向")
    inst_date = wh.latest_date("institutional_daily")
    if inst_date is None:
        st.info("無三大法人資料。")
        return
    st.subheader(f"投信買超前 15 · {inst_date}")
    flows = _sql(
        "SELECT i.stock_id, i.trust_net, i.foreign_net, p.close "
        "FROM institutional_daily i LEFT JOIN prices_daily p USING (date, stock_id) "
        f"WHERE i.date = DATE '{inst_date}' ORDER BY i.trust_net DESC LIMIT 15"
    )
    left, right = st.columns([2, 3])
    left.bar_chart(flows.to_pandas().set_index("stock_id")["trust_net"])
    right.dataframe(flows.to_pandas(), width="stretch", height=420)


def _page_revenue() -> None:
    st.header("月營收")
    rev_ym = _latest_revenue_month()
    if rev_ym is None:
        st.info("無月營收資料。")
        return
    st.subheader(f"月營收 YoY 成長前 15（營收 > 5 億）· {rev_ym}")
    rev = _sql(
        "SELECT stock_id AS 股票, round(revenue/1e8,1) AS 營收_億, round(yoy*100,1) AS YoY_pct "
        f"FROM monthly_revenue WHERE year_month = '{rev_ym}' AND revenue > 5e8 AND yoy IS NOT NULL "
        "ORDER BY yoy DESC LIMIT 15"
    )
    left, right = st.columns([2, 3])
    left.bar_chart(rev.to_pandas().set_index("股票")["YoY_pct"])
    right.dataframe(rev.to_pandas(), width="stretch", height=420)


def _backtest_panel() -> None:
    """策略回測：權益曲線 + 成本後績效指標（純量化，不需 LLM）。"""
    import datetime as dt

    st.subheader("策略回測")
    strat_path = _CONFIG.parent / "strategies" / "sample_revenue_trust.yaml"
    if not strat_path.exists():
        st.info("無範例策略配置。")
        return
    c1, c2 = st.columns(2)
    start = c1.date_input("起", dt.date(2025, 8, 1))
    end = c2.date_input("迄", dt.date(2025, 12, 31))
    if not isinstance(start, dt.date) or not isinstance(end, dt.date):
        return
    try:
        res = _bt_cached(str(strat_path), start.isoformat(), end.isoformat())
    except Exception as exc:  # noqa: BLE001  # 顯示訊息而非讓儀表板崩潰
        st.warning(f"回測失敗：{exc}")
        return

    m = cast("dict[str, float]", res["metrics"])
    equity = cast("list[tuple[dt.date, float]]", res["equity"])
    total_ret = cast("float", res["total_ret"])
    n_trades = cast("int", res["n_trades"])
    cols = st.columns(5)
    cols[0].metric("Sharpe", f"{m['sharpe']:.2f}")
    cols[1].metric("CAGR", f"{m['cagr'] * 100:.1f}%")
    cols[2].metric("最大回撤", f"{m['mdd'] * 100:.1f}%")
    cols[3].metric("勝率", f"{m['win_rate'] * 100:.0f}%")
    cols[4].metric("年化周轉", f"{m['turnover'] * 100:.0f}%")

    eq = pl.DataFrame({"date": [d for d, _ in equity], "權益": [v for _, v in equity]}).to_pandas()
    st.markdown(f"**權益曲線**（總報酬 {total_ret * 100:+.2f}%　成交 {n_trades} 筆）")
    st.line_chart(eq.set_index("date")["權益"])
    st.caption(
        "成本後淨值（含手續費/證交稅/滑價）· sample 策略（月營收 YoY + 投信連買）· "
        "預設連續期避開 TWSE 缺口"
    )


@st.cache_data(ttl=1800, show_spinner="跑回測…")
def _bt_cached(strat_path: str, start_iso: str, end_iso: str) -> dict[str, object]:
    import datetime as dt

    from twquant.backtest.costs import CostConfig
    from twquant.backtest.engine import run_backtest
    from twquant.config import load_config
    from twquant.strategy.config import load_strategy

    wh = _warehouse()
    strat = load_strategy(strat_path)
    cost = load_config(_CONFIG.parent / "costs.yaml", CostConfig)
    res = run_backtest(
        strat, cost, dt.date.fromisoformat(start_iso), dt.date.fromisoformat(end_iso), wh
    )
    return {
        "equity": [(e.date, e.equity) for e in res.equity],
        "metrics": {
            "sharpe": res.metrics.sharpe,
            "cagr": res.metrics.cagr,
            "mdd": res.metrics.mdd,
            "win_rate": res.metrics.win_rate,
            "turnover": res.metrics.turnover,
        },
        "n_trades": len(res.ledger),
        "n_unfilled": len(res.unfilled),
        "total_ret": res.final_equity / res.initial_capital - 1.0,
    }


def _factor_health(wh: Warehouse) -> None:
    """因子健檢：RankIC t 值與多空價差，判斷因子在台股是否有預測力（純量化，不需 LLM）。"""
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.errors import TwquantError
    from twquant.factors.registry import list_factors

    st.subheader("因子健檢")
    factors = list_factors()
    as_of = wh.latest_date("prices_daily")
    if not factors or as_of is None:
        st.info("尚無已註冊因子或行情資料，無法檢驗。")
        return

    c1, c2 = st.columns([2, 3])
    name = c1.selectbox("因子", factors, index=0)
    horizons = c2.multiselect("前向報酬視窗（交易日）", [1, 5, 10, 20, 60], default=[5, 10, 20])
    if not horizons:
        st.info("請至少選一個前向視窗。")
        return

    try:
        report = _ic_report(str(name), tuple(sorted(horizons)), as_of.isoformat())
    except (TwquantError, DataError) as exc:
        st.warning(f"因子檢驗失敗：{exc}")
        return

    rows = [
        {
            "視窗": f"{h.horizon}日",
            "RankIC均值": round(h.ic_mean, 4),
            "t值": round(h.t_stat, 2),
            "IR": round(h.ic_ir, 3),
            "多空價差": round(h.ls_spread, 4),
            "樣本日": h.n_days,
        }
        for h in report.horizons
    ]
    left, right = st.columns([3, 2])
    left.dataframe(rows, width="stretch", hide_index=True)
    chart = (
        pl.DataFrame(
            {
                "視窗": [f"{h.horizon}日" for h in report.horizons],
                "t值": [h.t_stat for h in report.horizons],
            }
        )
        .to_pandas()
        .set_index("視窗")
    )
    right.markdown("**RankIC t 值**")
    right.bar_chart(chart)

    def _verdict(t: float) -> str:
        if t > 2:
            return "顯著正向（動能）"
        if t < -2:
            return "顯著反轉"
        return "無顯著預測力"

    notes = "　·　".join(f"{h.horizon}日 {_verdict(h.t_stat)}" for h in report.horizons)
    st.caption(f"判讀（|t|>2 為 5% 顯著）：{notes}")
    st.caption(
        f"宇宙：四碼普通股（排除權證）· as_of {as_of} · 前向報酬假設連續，"
        f"TWSE 於 2026-01 後有缺口會使長視窗 IC 失真"
    )


@st.cache_data(ttl=600, show_spinner="計算因子 IC…")
def _ic_report(name: str, horizons: tuple[int, ...], as_of_iso: str) -> IcReport:
    """對單一因子在最新 as_of 上跑多視窗 IC（與 CLI ic-report 同一路徑）。快取避免重掃。"""
    import datetime as dt

    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.factors.compute import compute
    from twquant.factors.ic import compute_ic_report

    universe = r"^[1-9][0-9]{3}$"  # 四碼普通股，排除權證/ETF 尾碼
    wh = _warehouse()
    view = wh.view(dt.date.fromisoformat(as_of_iso))
    factor_df = compute([name], view).filter(pl.col("stock_id").str.contains(universe))
    prices = (
        view.table("prices_daily")
        .select("date", "stock_id", "close")
        .filter(pl.col("stock_id").str.contains(universe))
        .collect()
    )
    return compute_ic_report(name, factor_df, prices, list(horizons))


def _ingestion_status(wh: Warehouse) -> None:
    """擷取涵蓋/缺口/最近失敗——讓人（非 LLM）一眼看資料抓取狀態。"""
    from twquant.data import calendar as cal
    from twquant.reporting.ingest_log import last_run, read_ingest_log, recent_failures

    st.subheader("擷取狀態")
    calendar = cal.load(wh)  # 交易日曆：缺口以開市日為準（假日不算缺口）
    plan = [
        ("prices_daily", "twse", "上市行情"),
        ("prices_daily", "tpex", "上櫃行情"),
        ("institutional_daily", "twse", "三大法人"),
        ("margin_daily", "twse", "融資融券"),
    ]
    rows = []
    for table, source, label in plan:
        dates = wh.source_dates(table, source)
        if not dates:
            rows.append({"資料": label, "區間": "—", "交易日": 0, "缺口": "—"})
            continue
        gaps = calendar.missing(dates, dates[0], dates[-1])
        rows.append(
            {
                "資料": label,
                "區間": f"{dates[0]} ~ {dates[-1]}",
                "交易日": len(dates),
                "缺口": f"{len(gaps)} 交易日" if gaps else "無",
            }
        )
    st.dataframe(rows, width="stretch", hide_index=True)

    cols = st.columns(2)
    cols[0].caption(f"最後擷取時點：{last_run(_warehouse_dir()) or '（無紀錄）'}")
    fails = recent_failures(_warehouse_dir(), limit=8)
    log = read_ingest_log(_warehouse_dir())
    if not log.is_empty():
        nfail = log.filter(log["status"] == "failed").height
        cols[1].caption(f"擷取紀錄 {log.height} 筆 · 失敗 {nfail} 筆")
    if not fails.is_empty():
        with st.expander(f"最近失敗 {fails.height} 筆"):
            st.dataframe(
                fails.select("attempt_ts", "source", "table", "date", "detail").to_pandas(),
                width="stretch",
                hide_index=True,
            )


def _warehouse_dir() -> str:
    cfg = load_config(_CONFIG, SystemConfig)
    p = Path(cfg.data_dir)
    if not p.is_absolute():
        p = _CONFIG.parents[1] / p
    return str(p)


def _price_dates() -> list[str]:
    try:
        df = _sql("SELECT DISTINCT date FROM prices_daily ORDER BY date")
        return [str(x) for x in df["date"].to_list()]
    except DataError:
        return []


def _latest_revenue_month() -> str | None:
    try:
        df = _sql("SELECT max(year_month) AS ym FROM monthly_revenue")
        val = df["ym"][0]
        return str(val) if val is not None else None
    except DataError:
        return None


main()
