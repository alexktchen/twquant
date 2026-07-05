"""Streamlit 監控儀表板（SPEC-007 的 v1）。

唯讀：讀 clean 資料湖顯示資料狀態、市場趨勢、法人流向、月營收成長。完整版
（權益曲線、漂移、因子健康度）在 T-024。
以 `streamlit run src/twquant/monitoring/dashboard.py` 啟動。
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import streamlit as st

from twquant.config import SystemConfig, load_config
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError

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


def _rows(wh: Warehouse, table: str) -> int:
    try:
        return int(wh.scan(table).select(pl.len()).collect().item())
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
        col.metric(
            label=label, value=(d.isoformat() if d else "—"), delta=f"{_rows(wh, table):,} 列"
        )

    price_dates = _price_dates(wh)
    if not price_dates:
        st.info("資料湖尚無行情。先執行：`twquant backfill --from ... --to ...`")
        return

    # ── 市場趨勢（多日）──────────────────────────────────────────
    st.subheader(f"市場趨勢 · {price_dates[0]} ~ {price_dates[-1]}（{len(price_dates)} 交易日）")
    trend = wh.sql(
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
        flows = wh.sql(
            f"SELECT i.stock_id, i.trust_net, i.foreign_net, p.close "
            f"FROM institutional_daily i LEFT JOIN prices_daily p USING (date, stock_id) "
            f"WHERE i.date = DATE '{inst_date}' ORDER BY i.trust_net DESC LIMIT 15"
        )
        left, right = st.columns([2, 3])
        pdf = flows.to_pandas().set_index("stock_id")
        left.bar_chart(pdf["trust_net"])
        right.dataframe(flows.to_pandas(), width="stretch", height=420)

    # ── 月營收成長榜 ────────────────────────────────────────────
    rev_ym = _latest_revenue_month(wh)
    if rev_ym is not None:
        st.subheader(f"月營收 YoY 成長前 15（營收 > 5 億）· {rev_ym}")
        rev = wh.sql(
            f"SELECT stock_id AS 股票, round(revenue/1e8,1) AS 營收_億, "
            f"round(yoy*100,1) AS YoY_pct FROM monthly_revenue "
            f"WHERE year_month = '{rev_ym}' AND revenue > 5e8 AND yoy IS NOT NULL "
            f"ORDER BY yoy DESC LIMIT 15"
        )
        left, right = st.columns([2, 3])
        left.bar_chart(rev.to_pandas().set_index("股票")["YoY_pct"])
        right.dataframe(rev.to_pandas(), width="stretch", height=420)

    st.caption(f"最新行情日：{latest}｜資料來源：TWSE 上市 · TPEX 上櫃 · MOPS 月營收")


def _price_dates(wh: Warehouse) -> list[str]:
    try:
        df = wh.sql("SELECT DISTINCT date FROM prices_daily ORDER BY date")
        return [str(x) for x in df["date"].to_list()]
    except DataError:
        return []


def _latest_revenue_month(wh: Warehouse) -> str | None:
    try:
        df = wh.sql("SELECT max(year_month) AS ym FROM monthly_revenue")
        val = df["ym"][0]
        return str(val) if val is not None else None
    except DataError:
        return None


main()
