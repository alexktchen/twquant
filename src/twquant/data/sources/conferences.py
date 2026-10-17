"""MOPS 法人說明會月表（T-376／D-188）：舊站 `ajax_t100sb02_1` 逐月、上市（sii）／上櫃（otc）各一頁。

表格欄：公司代號、公司名稱、召開日期（民國）、時間、地點、擇要訊息、簡報、公司網站、影音、其他、歷年、中文檔、英文檔。
只存前六欄＋市場。首頁行事曆要的是「接下來幾天誰開法說」，所以每天抓當月與下個月。

`available_at`＝抓到的時間：這一頁不給公告時點，老實記「我們什麼時候知道」，不假裝是公司公告那一刻
（本機另一張研究用的 `investor_conferences` 以 row_key 為鍵、含檔名欄位，是 2026-09-28 的快照，與這張 `conference_calendar` 分開）。
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from html.parser import HTMLParser

import polars as pl

from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.errors import DataError

TABLE = "conference_calendar"
URL = "https://mopsov.twse.com.tw/mops/web/ajax_t100sb02_1"
MARKETS = ("sii", "otc")
_PAGE_MARK = "召開法人說明會日期"
_SPACES = re.compile(r"\s+")
_CODE = re.compile(r"^[0-9A-Z]{4,6}$")
_TIME = re.compile(r"^\d{1,2}:\d{2}$")

SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date(),
    "stock_id": pl.Utf8(),
    "conf_time": pl.Utf8(),
    "market": pl.Utf8(),
    "name": pl.Utf8(),
    "location": pl.Utf8(),
    "summary": pl.Utf8(),
    "available_at": pl.Datetime("us", "Asia/Taipei"),
}


def query_params(market: str, year: int, month: int) -> dict[str, str]:
    """POST／GET 參數（民國年）。"""
    if market not in MARKETS:
        raise ValueError(f"market 必須是 {MARKETS}：{market!r}")
    return {
        "encodeURIComponent": "1",
        "step": "1",
        "firstin": "1",
        "off": "1",
        "TYPEK": market,
        "year": str(year - 1911),
        "month": f"{month:02d}",
    }


class _Table(HTMLParser):
    """收每個 <tr> 的儲存格文字。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(
                _SPACES.sub(" ", unicodedata.normalize("NFKC", "".join(self._cell))).strip()
            )
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _roc(text: str, market: str, sid: str) -> dt.date:
    """民國 yyy/mm/dd；多日活動寫「115/10/13 至 115/10/14」，取起日。"""
    first = text.split("至")[0].strip()
    try:
        y, m, d = (int(x) for x in first.split("/"))
        return dt.date(y + 1911, m, d)
    except ValueError as exc:
        raise DataError(
            TABLE, None, f"{market} {sid} 的召開日期不是民國 yyy/mm/dd：{text!r}"
        ) from exc


def parse_month(page: str, market: str, fetched_at: dt.datetime) -> pl.DataFrame:
    """解析一個市場一個月的法說會表。不是法說會頁 → DataError；表頭之後沒有列＝這個月還沒有公告（空表，不是錯）。"""
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at 必須帶時區")
    if _PAGE_MARK not in page:
        raise DataError(
            TABLE, None, f"MOPS 回應不是法說會月表（{len(page)} 字元，前段 {page[:80]!r}）"
        )
    parser = _Table()
    parser.feed(page)
    header_at = next((i for i, r in enumerate(parser.rows) if _PAGE_MARK in "".join(r)), None)
    if header_at is None:
        raise DataError(TABLE, None, "法說會月表找不到表頭列")
    out: list[dict[str, object]] = []
    for cells in parser.rows[header_at + 1 :]:
        if len(cells) < 6:
            continue  # 表尾的說明列
        sid = cells[0]
        if not _CODE.match(sid):
            raise DataError(TABLE, None, f"{market} 法說會表有一列的公司代號不像代號：{cells[:3]}")
        time = cells[3]
        if not _TIME.match(time):
            raise DataError(TABLE, None, f"{market} {sid} 的召開時間不是 HH:MM：{time!r}")
        out.append(
            {
                "date": _roc(cells[2], market, sid),
                "stock_id": sid,
                "conf_time": time.zfill(5),
                "market": market,
                "name": cells[1] or None,
                "location": cells[4] or None,
                "summary": cells[5] or None,
                "available_at": fetched_at.astimezone(TZ),
            }
        )
    # MOPS 偶爾把同一場列兩次（2026-10 康那香 9919：兩列每個欄位都一樣）——一模一樣的只留一列；
    # 同一個鍵內容不同才是真的衝突，照樣 raise
    df = pl.DataFrame(out, schema=SCHEMA, orient="row").unique(maintain_order=True)
    dup = df.filter(pl.struct("date", "stock_id", "conf_time").is_duplicated())
    if not dup.is_empty():
        raise DataError(
            TABLE,
            None,
            f"(日期, 代號, 時間) 重複：{dup.select('date', 'stock_id', 'conf_time').rows()[:5]}",
        )
    return df


def fetch_month(
    client: HttpClient, market: str, year: int, month: int, fetched_at: dt.datetime
) -> pl.DataFrame:
    """抓一個市場一個月並解析（頁面 UTF-8）。"""
    resp = client.get(URL, params=query_params(market, year, month))
    if resp.status_code != 200:
        raise DataError(TABLE, None, f"MOPS HTTP {resp.status_code}（{market} {year}-{month:02d}）")
    return parse_month(resp.content.decode("utf-8"), market, fetched_at)


def months_to_fetch(today: dt.date, months_ahead: int) -> list[tuple[int, int]]:
    """當月起往後 `months_ahead` 個月（含當月）。"""
    out = []
    y, m = today.year, today.month
    for _ in range(months_ahead + 1):
        out.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out
