"""Streamlit 監控儀表板（SPEC-007 的 v1）。

唯讀：讀 clean 資料湖顯示資料狀態與法人流向。完整版（權益曲線、漂移、因子健康度）
在 T-024。以 `streamlit run src/twquant/monitoring/dashboard.py` 啟動。
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import streamlit as st

from twquant.config import SystemConfig, load_config
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError

_CONFIG = Path(__file__).resolve().parents[3] / "configs" / "system.yaml"
_TABLES = ["prices_daily", "institutional_daily", "margin_daily"]


def _warehouse() -> Warehouse:
    cfg = load_config(_CONFIG, SystemConfig)
    return Warehouse(cfg.data_dir)


def main() -> None:
    st.set_page_config(page_title="twquant 監控", layout="wide")
    st.title("twquant 台股量化平台 — 資料監控")

    wh = _warehouse()

    st.header("資料狀態")
    cols = st.columns(len(_TABLES))
    latest: dict[str, str] = {}
    for col, table in zip(cols, _TABLES, strict=True):
        d = wh.latest_date(table)
        latest[table] = d.isoformat() if d else "—"
        try:
            n = wh.scan(table).select(pl.len()).collect().item()
        except DataError:
            n = 0
        col.metric(label=table, value=latest[table], delta=f"{n} 列")

    inst_date = wh.latest_date("institutional_daily")
    if inst_date is None:
        st.info("尚無法人資料。先執行：twquant ingest --date YYYY-MM-DD")
        return

    st.header(f"法人流向 — {inst_date.isoformat()}")
    try:
        flows = wh.sql(
            f"""
            SELECT i.stock_id, i.trust_net, i.foreign_net, p.close
            FROM institutional_daily i
            LEFT JOIN prices_daily p USING (date, stock_id)
            WHERE i.date = DATE '{inst_date.isoformat()}'
            ORDER BY i.trust_net DESC
            LIMIT 15
            """
        )
    except DataError as exc:
        st.warning(f"查詢失敗：{exc}")
        return

    left, right = st.columns(2)
    with left:
        st.subheader("投信買超前 15")
        pdf = flows.to_pandas().set_index("stock_id")
        st.bar_chart(pdf["trust_net"])
    with right:
        st.subheader("明細（投信/外資買賣超、收盤）")
        st.dataframe(flows.to_pandas(), width="stretch")


main()
