"""富邦DJ 券商分點買賣超轉接器（T-010 broker_trades；D-019 取代 D-001）。

免費來源：富邦 DJ 個股分點進出頁（zco.djhtm）。**每檔每日一次請求**，回傳「買超券商
top-15 + 賣超券商 top-15」的**母公司層級**彙總——無分公司(分點)、無券商代號、無成交
均價。因此 canonical broker_trades 的 `broker_id` 存**券商名稱**、`avg_price` 留 null。

單位：頁面為「張」，`rows_to_canonical` 一處轉「股」(×1000, conventions.SHARES_PER_LOT)。
`available_at` = 交易日 18:00 台北（盤後資料，比照 clean_prices/clean_institutional；
D-008 訊號截點 20:00 → 同一交易日即可見，不造成假延遲）。

畸形 HTML 以 BeautifulSoup + 內建 html.parser 解析（零 C 依賴）。解析刻意避開 legacy
（cloudDailyReport）的 Py2 bug：`x is not ''`（Py3 恆真）與 `int(x, 0)`（把 "012" 當
八進位）。所有網路呼叫經注入式 HttpClient；測試以假 transport 離線驗證（CLAUDE.md §4）。

此來源**不註冊為 daily SourceSpec**：富邦DJ 是每檔每日、全市場一天 ~1800 請求，屬批量
回補，由專屬 CLI 指令 `backfill-fubon-chip` 驅動（比照 finmind），不進每日編排迴圈。
"""

from __future__ import annotations

import datetime as dt
from typing import TYPE_CHECKING

import polars as pl

from twquant.conventions import SHARES_PER_LOT, TZ
from twquant.data import schemas

if TYPE_CHECKING:
    from twquant.data.http import HttpClient

_URL = "https://fubon-ebrokerdj.fbs.com.tw/z/zc/zco/zco.djhtm"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
_HEADERS = {"Referer": "https://fubon-ebrokerdj.fbs.com.tw/"}

# footer / 表頭重複列標記：任一半的名稱或量欄命中即停止解析資料列
_STOP_MARKERS = frozenset(
    {"買超券商", "賣超券商", "合計買超張數", "合計賣超張數", "合計買超股數", "合計賣超股數"}
)

# 欄序須與 schemas.BROKER_TRADES 一致（validate strict=True）
_CANONICAL = (
    "date",
    "stock_id",
    "broker_id",
    "buy_shares",
    "sell_shares",
    "avg_price",
    "available_at",
)

_EMPTY_SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date(),
    "stock_id": pl.String(),
    "broker_id": pl.String(),
    "buy_shares": pl.Int64(),
    "sell_shares": pl.Int64(),
    "avg_price": pl.Float64(),
    "available_at": schemas.TW_DT,
}


def browser_client(rate: object, **kwargs: object) -> HttpClient:
    """建立帶瀏覽器 headers 的 HttpClient（富邦DJ 需 UA/Referer）。"""
    from twquant.config import SourceRate
    from twquant.data.http import HttpClient

    assert isinstance(rate, SourceRate)
    return HttpClient(source="fubon", rate=rate, user_agent=_UA, headers=dict(_HEADERS), **kwargs)  # type: ignore[arg-type]


def fetch_broker_trades(client: HttpClient, stock_id: str, date: dt.date) -> bytes:
    """抓某檔某日的富邦DJ 分點頁，回傳原始 HTML bytes（GET，params a/e/f）。"""
    resp = client.get(_URL, params={"a": stock_id, "e": date.isoformat(), "f": date.isoformat()})
    return resp.content


def _cell_int(text: str) -> int:
    """券量儲存格 → int（張）。去逗號/\xa0/空白；空或 '--' → 0；base-10（避開 int(x,0) 八進位）。"""
    s = text.replace(",", "").replace("\xa0", "").strip()
    if not s or s == "--":
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def parse_broker_rows(html: bytes) -> list[dict[str, object]]:
    """解析富邦DJ zco 頁的 t01 表 → [{broker_id, buy_lots, sell_lots}, ...]（單位：張）。

    無 t01 表（非交易日/下市/被 anti-bot 擋）→ 回 []。每資料列兩半（各為該券商自身的
    買量/賣量）：買方 td[0..3]=(名, 買量, 賣量, 買賣超)、td[4] 隔欄、賣方 td[5..8]。名稱
    空白的半列略過（短尾列可能只一側有券商）。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table", class_="t01")
    if table is None:
        return []

    out: list[dict[str, object]] = []
    for row in table.find_all("tr")[8:]:  # 前 8 列為頁面表頭
        tds = row.find_all("td")
        if len(tds) < 9:  # 短列（合計/隔列）不 index crash，直接略過
            continue
        if tds[0].get_text().strip() in _STOP_MARKERS or tds[2].get_text().strip() in _STOP_MARKERS:
            break
        for name_i, buy_i, sell_i in ((0, 1, 2), (5, 6, 7)):
            name = tds[name_i].get_text().replace("\xa0", "").strip()
            if not name:
                continue
            out.append(
                {
                    "broker_id": name,
                    "buy_lots": _cell_int(tds[buy_i].get_text()),
                    "sell_lots": _cell_int(tds[sell_i].get_text()),
                }
            )
    return out


def _empty_canonical() -> pl.DataFrame:
    return schemas.validate("broker_trades", pl.DataFrame(schema=_EMPTY_SCHEMA))


def rows_to_canonical(rows: list[dict[str, object]], stock_id: str, date: dt.date) -> pl.DataFrame:
    """解析列（張）→ canonical broker_trades（股；已驗證）。空 → 空 canonical。

    date/stock_id 來自請求（非 HTML）。同名券商彙總成一列（保險：買賣兩半分列，某券商
    可能同時出現在極端情況）。買賣量 ×1000 轉股；avg_price 富邦DJ 不提供 → null。
    """
    if not rows:
        return _empty_canonical()
    at = dt.datetime(date.year, date.month, date.day, 18, 0, tzinfo=TZ)
    out = (
        pl.DataFrame(rows)
        .group_by("broker_id")
        .agg(
            pl.col("buy_lots").sum().alias("_buy_lots"),
            pl.col("sell_lots").sum().alias("_sell_lots"),
        )
        .with_columns(
            date=pl.lit(date, dtype=pl.Date),
            stock_id=pl.lit(stock_id, dtype=pl.String),
            buy_shares=(pl.col("_buy_lots") * SHARES_PER_LOT).cast(pl.Int64),
            sell_shares=(pl.col("_sell_lots") * SHARES_PER_LOT).cast(pl.Int64),
            avg_price=pl.lit(None, dtype=pl.Float64),
            available_at=pl.lit(at, dtype=schemas.TW_DT),
        )
        .select(_CANONICAL)
    )
    return schemas.validate("broker_trades", out)
