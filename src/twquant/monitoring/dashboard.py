"""Streamlit 監控儀表板（SPEC-007 的 v1）。

唯讀：讀 clean 資料湖顯示資料狀態、市場趨勢、法人流向、月營收成長。完整版
（權益曲線、漂移、因子健康度）在 T-024。
以 `streamlit run src/twquant/monitoring/dashboard.py` 啟動。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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


def main() -> None:
    st.set_page_config(page_title="twquant 監控", page_icon="📈", layout="wide")
    st.title("📈 twquant 台股量化平台")
    st.caption("資料監控儀表板 · DuckDB + Parquet 資料湖 · 唯讀")

    wh = _warehouse()

    # ── 資料狀態 KPI ────────────────────────────────────────────
    st.subheader("資料狀態")
    cols = st.columns(len(_TABLES))
    for col, (table, label) in zip(cols, _TABLES.items(), strict=True):
        d = wh.latest_date(table)
        col.metric(label=label, value=(d.isoformat() if d else "—"), delta=f"{_rows(table):,} 列")

    # ── 擷取狀態（涵蓋/缺口/失敗）─────────────────────────────────
    _ingestion_status(wh)

    price_dates = _price_dates()
    if not price_dates:
        st.info("資料湖尚無行情。先執行：`twquant backfill --from ... --to ...`")
        return

    # ── 市場趨勢（多日）──────────────────────────────────────────
    st.subheader(f"市場趨勢 · {price_dates[0]} ~ {price_dates[-1]}（{len(price_dates)} 交易日）")
    trend = _sql(
        "SELECT date, count(*) AS 檔數, round(avg(close),1) AS 平均收盤, "
        "round(sum(amount)/1e8,0) AS 成交金額_億 FROM prices_daily GROUP BY date ORDER BY date"
    ).to_pandas()
    c1, c2 = st.columns(2)
    c1.markdown("**每日成交金額（億元）**")
    c1.bar_chart(trend.set_index("date")["成交金額_億"])
    c2.markdown("**每日平均收盤價**")
    c2.line_chart(trend.set_index("date")["平均收盤"])

    latest = price_dates[-1]

    # ── 法人流向 ────────────────────────────────────────────────
    inst_date = wh.latest_date("institutional_daily")
    if inst_date is not None:
        st.subheader(f"投信買超前 15 · {inst_date}")
        flows = _sql(
            f"SELECT i.stock_id, i.trust_net, i.foreign_net, p.close "
            f"FROM institutional_daily i LEFT JOIN prices_daily p USING (date, stock_id) "
            f"WHERE i.date = DATE '{inst_date}' ORDER BY i.trust_net DESC LIMIT 15"
        )
        left, right = st.columns([2, 3])
        pdf = flows.to_pandas().set_index("stock_id")
        left.bar_chart(pdf["trust_net"])
        right.dataframe(flows.to_pandas(), width="stretch", height=420)

    # ── 月營收成長榜 ────────────────────────────────────────────
    rev_ym = _latest_revenue_month()
    if rev_ym is not None:
        st.subheader(f"月營收 YoY 成長前 15（營收 > 5 億）· {rev_ym}")
        rev = _sql(
            f"SELECT stock_id AS 股票, round(revenue/1e8,1) AS 營收_億, "
            f"round(yoy*100,1) AS YoY_pct FROM monthly_revenue "
            f"WHERE year_month = '{rev_ym}' AND revenue > 5e8 AND yoy IS NOT NULL "
            f"ORDER BY yoy DESC LIMIT 15"
        )
        left, right = st.columns([2, 3])
        left.bar_chart(rev.to_pandas().set_index("股票")["YoY_pct"])
        right.dataframe(rev.to_pandas(), width="stretch", height=420)

    # ── 因子健檢（策略層）──────────────────────────────────────────
    st.divider()
    _factor_health(wh)

    st.caption(f"最新行情日：{latest}｜資料來源：TWSE 上市 · TPEX 上櫃 · MOPS 月營收")


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
