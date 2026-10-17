"""MOPS 重大訊息（T-371／D-187）：舊站 `ajax_t05st02` 逐日全市場清單的純解析器與抓取。

一頁＝查詢日 D 的重大訊息（頁首註明「包含前一日 17:30 以後之訊息」），上市／上櫃／興櫃／公開發行
四種市場（sii／otc／rotc／pub）全在同一頁，週末也有列。每列另有一個隱藏表單（名稱 `<市場>_fm<序>`），
欄位名是 `h<列序><欄>`、欄是個位數——列序會長到三位數（h1500），所以一律取名稱**最後一碼**當欄位：

| 欄 | 內容 | 例 |
|---|---|---|
| 0 | 公司名稱 | 愛山林 |
| 1 | 公司代號 | 2540 |
| 2 | 發言日期（西元） | 20261015 |
| 3 | 發言時間 HHMMSS（早上可能只有五碼） | 173537 |
| 4 | 主旨 | 本公司董事會通過… |
| 5 | MOPS 自己的編號（意義未公開；**不是**當日序號：同公司同日不同時間的公告都可能是 1，同主旨再發一次是 2） | 1 |
| 6 | 符合條款（款次） | 20 |
| 7 | 事實發生日（可空） | 20261015 |
| 8 | 說明全文 | 存進 `content`（超過 system.yaml `material_news.content_max_chars` 截斷並註明） |

**存託憑證（91xx，例：9105 泰金寶-DR）沒有隱藏表單**：明細按鈕改走 `t59sb01_form`。這些列從看得到的
儲存格取日期（民國）、時間、代號、名稱、主旨；市場記 `dr`，`mops_seq`／款次／事實發生日／內文留空。

一則公告的身分＝(代號, 發言時間到秒, 主旨)：MOPS 的編號不唯一（2026-10-16 實測 1558 兩則不同公告都是 1），
同一頁出現兩列身分相同就 raise。隔天的頁再列出同一則時身分不變，upsert 覆寫成同一列。

內文（2026-10-17 使用者：「昨日消息點開要可以看到消息內文」）：一天約 300 KB，雲端留 25 個交易日約 10 MB；
洞悉層的證據仍只用主旨。`content` 放在最後一欄，舊表 `ALTER TABLE … ADD COLUMN` 就對齊。
表名 `mops_announcements`：本機 PG 另有 2026-09-28 事件研究（D-166／D-168）留下的 `material_news`
（52 萬則全文、到 2026-09-28 為止的凍結快照、主鍵含 content_md5），這條每日主旨流不碰它、也不與它合併。
available_at＝發言時間本身（到秒，Asia/Taipei）：公告上了 MOPS 那一刻就公開，PIT 精確。
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

TABLE = "mops_announcements"
URL = "https://mopsov.twse.com.tw/mops/web/ajax_t05st02"
MARKETS = ("sii", "otc", "rotc", "pub")
# 頁首標題：沒有它就不是清單頁（錯誤頁、查詢過頻的提示頁），不能當成「當天沒有公告」
_PAGE_MARK = "公司當日重大訊息之詳細內容"
_FORM_NAME = re.compile(rf"({'|'.join(MARKETS)})_fm\d+")
_FIELD_NAME = re.compile(r"h\d{2,}")
_DR_FORM = "t59sb01_form"
DR_MARKET = "dr"
_SPACES = re.compile(r"\s+")

SCHEMA: dict[str, pl.DataType] = {
    "date": pl.Date(),
    "stock_id": pl.Utf8(),
    "market": pl.Utf8(),
    "name": pl.Utf8(),
    "subject": pl.Utf8(),
    "clause": pl.Utf8(),
    "fact_date": pl.Date(),
    "mops_seq": pl.Int64(),
    "available_at": pl.Datetime("us", "Asia/Taipei"),
    "content": pl.Utf8(),
}


def query_params(day: dt.date) -> dict[str, str]:
    """GET 參數（民國年）；舊站 POST 與 GET 回同一頁。"""
    return {
        "encodeURIComponent": "1",
        "step": "0",
        "firstin": "true",
        "off": "1",
        "TYPEK": "all",
        "year": str(day.year - 1911),
        "month": f"{day.month:02d}",
        "day": f"{day.day:02d}",
    }


class _Rows(HTMLParser):
    """逐列收集清單：看得到的儲存格文字，以及（有的話）該列 `<市場>_fm<序>` 表單的隱藏欄位。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[_Raw] = []
        self._row: _Raw | None = None
        self._cell: list[str] | None = None
        self._fields: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "tr" and a.get("class") in ("even", "odd"):
            self._row = _Raw()
            self.rows.append(self._row)
        elif self._row is None:
            return
        elif tag == "td":
            self._cell = []
        elif tag == "form":
            m = _FORM_NAME.fullmatch(a.get("name", ""))
            if m:
                self._row.market = m.group(1)
                self._fields = self._row.fields
        elif tag == "input":
            name = a.get("name", "")
            if self._fields is not None and _FIELD_NAME.fullmatch(name):
                self._fields[name[-1]] = a.get("value", "")
            if _DR_FORM in a.get("onclick", ""):
                self._row.dr = True

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self._row is None:
            return
        if tag == "td" and self._cell is not None:
            self._row.cells.append("".join(self._cell).replace(" ", " ").strip())
            self._cell = None
        elif tag == "form":
            self._fields = None
        elif tag == "tr":
            self._row = None


class _Raw:
    """One listing row as scraped, before validation."""

    def __init__(self) -> None:
        self.cells: list[str] = []
        self.market: str | None = None
        self.fields: dict[str, str] = {}
        self.dr = False


def _date(raw: str, day: dt.date, sid: str, what: str) -> dt.date:
    try:
        return dt.datetime.strptime(raw.strip(), "%Y%m%d").date()
    except ValueError as exc:
        raise DataError(TABLE, day, f"{sid} 的{what}不是 YYYYMMDD：{raw!r}") from exc


def _clock(raw: str, day: dt.date, sid: str, *, visible: bool = False) -> dt.time:
    """隱藏欄位是 5～6 位數字（早上少一碼）；看得到的儲存格是 HH:MM:SS。其他寫法一律 raise。"""
    text = raw.strip()
    try:
        if visible:
            return dt.datetime.strptime(text, "%H:%M:%S").time()
        if not (text.isdigit() and len(text) <= 6):  # 前導零會被吃掉：658＝00:06:58
            raise ValueError(text)
        return dt.datetime.strptime(text.zfill(6), "%H%M%S").time()
    except ValueError as exc:
        raise DataError(TABLE, day, f"{sid} 的發言時間不是 HHMMSS：{raw!r}") from exc


def _subject(raw: str) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", raw)).strip()


_BLANKS = re.compile(r"\n{3,}")
TRUNCATED = "\n……（內文過長，以下略；全文見公開資訊觀測站）"


def clean_content(raw: str, max_chars: int | None) -> str | None:
    """說明全文：NFKC、換行統一、每行去尾空白、三個以上空行收成一個；超過 max_chars 截斷並註明。空的 → None。"""
    text = unicodedata.normalize("NFKC", raw).replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    text = _BLANKS.sub("\n\n", "\n".join(lines)).strip()
    if not text:
        return None
    if max_chars is not None and len(text) > max_chars:
        return text[:max_chars].rstrip() + TRUNCATED
    return text


def _row(raw: _Raw, day: dt.date, content_max: int | None) -> dict[str, object]:
    if raw.market is not None:
        return _form_row(raw.market, raw.fields, day, content_max)
    if raw.dr:
        return _dr_row(raw.cells, day)
    raise DataError(
        TABLE, day, f"一列既沒有隱藏表單也不是存託憑證，頁面格式可能改了：{raw.cells[:5]}"
    )


def _dr_row(cells: list[str], day: dt.date) -> dict[str, object]:
    if len(cells) < 5:
        raise DataError(TABLE, day, f"存託憑證列的儲存格不足 5 個：{cells}")
    sid = cells[2] or "?"
    try:
        y, m, d = (int(x) for x in cells[0].split("/"))
        date = dt.date(y + 1911, m, d)
    except ValueError as exc:
        raise DataError(TABLE, day, f"{sid} 的發言日期不是民國 yyy/mm/dd：{cells[0]!r}") from exc
    t = _clock(cells[1], day, sid, visible=True)
    subject = _subject(cells[4])
    if sid == "?" or not subject:
        raise DataError(TABLE, day, f"存託憑證列缺代號或主旨：{cells[:5]}")
    return {
        "date": date,
        "stock_id": sid,
        "market": DR_MARKET,
        "name": cells[3] or None,
        "subject": subject,
        "clause": None,
        "fact_date": None,
        "mops_seq": None,
        "available_at": dt.datetime.combine(date, t, tzinfo=TZ),
        "content": None,
    }


def _form_row(
    market: str, f: dict[str, str], day: dt.date, content_max: int | None
) -> dict[str, object]:
    missing = [k for k in "012345" if k not in f]
    sid = f.get("1", "").strip() or "?"
    if missing:
        raise DataError(TABLE, day, f"{sid} 的隱藏欄位缺 h?{missing}")
    if sid == "?":
        raise DataError(TABLE, day, f"有一列沒有公司代號：{f.get('4', '')[:40]!r}")
    date = _date(f["2"], day, sid, "發言日期")
    t = _clock(f["3"], day, sid)
    try:
        seq = int(f["5"].strip())
    except ValueError as exc:
        raise DataError(TABLE, day, f"{sid} 的 MOPS 編號不是整數：{f['5']!r}") from exc
    subject = _subject(f["4"])
    if not subject:
        raise DataError(TABLE, day, f"{sid} {date} {t} 的公告沒有主旨")
    fact = f.get("7", "").strip()
    return {
        "date": date,
        "stock_id": sid,
        "market": market,
        "name": f.get("0", "").strip() or None,
        "subject": subject,
        "clause": f.get("6", "").strip() or None,
        "fact_date": _date(fact, day, sid, "事實發生日") if fact else None,
        "mops_seq": seq,
        "available_at": dt.datetime.combine(date, t, tzinfo=TZ),
        "content": clean_content(f.get("8", ""), content_max),
    }


def parse_day(page: str, day: dt.date, content_max: int | None = None) -> pl.DataFrame:
    """解析查詢日 `day` 的清單頁。不是清單頁、認不出的列、鍵重複 → DataError（不丟列繼續）。
    `content_max`：內文最多幾個字（system.yaml），None＝不截。"""
    if _PAGE_MARK not in page:
        raise DataError(
            TABLE, day, f"MOPS 回應不是重大訊息清單（{len(page)} 字元，前段 {page[:80]!r}）"
        )
    parser = _Rows()
    parser.feed(page)
    rows = [_row(raw, day, content_max) for raw in parser.rows]
    df = pl.DataFrame(rows, schema=SCHEMA, orient="row")
    key = ["stock_id", "available_at", "subject"]
    dup = df.filter(pl.struct(*key).is_duplicated())
    if not dup.is_empty():
        raise DataError(
            TABLE,
            day,
            f"(代號, 發言時間, 主旨) 重複：{dup.select('stock_id', 'available_at').rows()[:5]}",
        )
    return df


def fetch_day(client: HttpClient, day: dt.date, content_max: int | None = None) -> pl.DataFrame:
    """抓查詢日 `day` 的清單並解析。頁面是 UTF-8；解不開就 raise，不猜編碼。"""
    resp = client.get(URL, params=query_params(day))
    if resp.status_code != 200:
        raise DataError(TABLE, day, f"MOPS HTTP {resp.status_code}")
    return parse_day(resp.content.decode("utf-8"), day, content_max)


def fetch_plan(day: dt.date, lookback_days: int, present: set[dt.date]) -> list[dt.date]:
    """要抓哪些查詢日：當天與前一天一律重抓（前一天 17:30 後的公告要隔天的頁才有），
    再加上回看窗內 PG 一列都沒有的日曆日（排程停擺後自己補回來）。由舊到新。"""
    window = [day - dt.timedelta(days=k) for k in range(lookback_days + 1)]
    always = {day, day - dt.timedelta(days=1)}
    return sorted(d for d in window if d in always or d not in present)
