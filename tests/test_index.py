"""index_daily：官方指數清洗（加權 TAIEX / 櫃買 TPEX）與監控快照查詢測試。

清洗以位置取欄（c0=日期,c1..c4=開高低收；TPEx 收盤在 c4）、民國年轉西元、丟總計列；
market_indices 供市場頁的官方指數序列。純函數 + 小 fixture 精確斷言。
"""

from __future__ import annotations

from pathlib import Path

from twquant.data.clean import clean_index_tpex, clean_index_twse
from twquant.data.sources.indices import _positional_frame
from twquant.data.warehouse import Warehouse
from twquant.monitoring import snapshot as snap

_TWSE_ROWS = [
    ["115/07/01", "46,234.70", "47,293.10", "46,234.70", "47,018.99"],
    ["115/07/02", "46,833.89", "46,910.31", "45,981.89", "46,744.16"],
    ["合計", "", "", "", ""],  # 總計列 → date 解析失敗 → 丟棄
]
# TPEx「日成交量值指數」：c0 日期、c1 張數、c2 金額、c3 筆數、c4 收盤指數、c5 漲跌
_TPEX_ROWS = [
    ["115/07/01", "1,369,067", "248,218,714", "1,273,652", "431.23", "4.26"],
    ["115/07/02", "1,340,448", "242,505,188", "1,280,213", "439.51", "8.28"],
]


def test_clean_index_twse_ohlc_and_roc_date() -> None:
    out = clean_index_twse(_positional_frame(_TWSE_ROWS))
    assert out.height == 2  # 總計列被丟
    rows = out.sort("date").to_dicts()
    assert str(rows[0]["date"]) == "2026-07-01"  # 民國 115 → 2026
    assert rows[0]["index_id"] == "TAIEX"
    assert rows[0]["open"] == 46234.70  # 去千分位逗號後轉 float
    assert rows[0]["close"] == 47018.99
    assert str(rows[0]["available_at"]).startswith("2026-07-01 18:00")


def test_clean_index_tpex_close_only() -> None:
    out = clean_index_tpex(_positional_frame(_TPEX_ROWS))
    assert out.height == 2
    rows = out.sort("date").to_dicts()
    assert rows[0]["index_id"] == "TPEX"
    assert rows[0]["close"] == 431.23  # 收盤指數在 c4
    assert rows[0]["open"] is None  # 櫃買僅收盤，OHLC 留 null


def test_market_indices_snapshot(tmp_path: Path) -> None:
    wh = Warehouse(tmp_path)
    wh.write("clean", "index_daily", clean_index_twse(_positional_frame(_TWSE_ROWS)), source="twse")
    wh.write("clean", "index_daily", clean_index_tpex(_positional_frame(_TPEX_ROWS)), source="tpex")
    idx = snap.market_indices(wh)
    taiex = idx["taiex"]
    tpex = idx["tpex"]
    assert isinstance(taiex, list)
    assert isinstance(tpex, list)
    assert len(taiex) == 2
    assert len(tpex) == 2
    assert taiex[0]["c"] == 47018.99
    assert "o" in taiex[0]  # 加權有 OHLC
    assert tpex[0]["c"] == 431.23
    assert set(tpex[0]) == {"d", "c"}  # 櫃買僅日期+收盤


def test_market_indices_empty_when_absent(tmp_path: Path) -> None:
    # 尚未 backfill-index → 空序列、不報錯（前端顯示提示）
    idx = snap.market_indices(Warehouse(tmp_path))
    assert idx == {"taiex": [], "tpex": []}


def test_index_gap_command_is_month_based() -> None:
    import datetime as dt

    run = [dt.date(2026, 3, 2), dt.date(2026, 3, 3)]
    entry = snap._daily_run_entry("index_daily", "twse", "加權指數", run, "missing", {})
    assert entry["command"] == "twquant ingest-index --month 2026-03"  # 同月 → ingest-index
    cross = snap._daily_run_entry(
        "index_daily", "twse", "加權指數", [dt.date(2026, 1, 5), dt.date(2026, 3, 3)], "missing", {}
    )
    assert cross["command"] == "twquant backfill-index --from 2026-01 --to 2026-03"
