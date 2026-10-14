"""TPEX（上櫃）轉接器（SPEC-001 / DECISIONS D-014）。

第一版聚焦上櫃「日行情」（afterTrading/dailyQuotes，支援歷史日期，且附次日漲跌停價）；
T-313 加上個股本益比／殖利率／淨值比（afterTrading/peQryDate，依日期查詢）。
TPEX 有 anti-bot，需帶完整瀏覽器 headers。上櫃法人/融資券的歷史資料改由 FinMind
（T-010，統一 TWSE+TPEX）提供，避免與 TPEX 僅回最新日的 OpenAPI 產生日期不符前視。
"""

from __future__ import annotations

import datetime as dt
import json

import polars as pl

from twquant.data.http import HttpClient
from twquant.data.sources import raw_frame
from twquant.errors import DataError

_BASE = "https://www.tpex.org.tw/www/zh-tw/afterTrading"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
_HEADERS = {"Accept": "application/json", "Referer": "https://www.tpex.org.tw/"}

# 表 → (端點, 個股資料表必含的欄位)。兩個端點都是 tables[] 結構、日期寫 2026/09/18。
_ENDPOINTS: dict[str, tuple[str, str]] = {
    "prices_daily": ("dailyQuotes", "代號"),
    # T-313：個股本益比、殖利率及股價淨值比（依日期查詢）；網頁 mainboard/trading/info/daily-pe.html
    "stock_valuation": ("peQryDate", "股票代號"),
}


def browser_client(rate: object, **kwargs: object) -> HttpClient:
    """建立帶瀏覽器 headers 的 HttpClient（繞過 TPEX anti-bot）。"""
    from twquant.config import SourceRate

    assert isinstance(rate, SourceRate)
    return HttpClient(source="tpex", rate=rate, user_agent=_UA, headers=dict(_HEADERS), **kwargs)  # type: ignore[arg-type]


class TpexSource:
    """TPEX 上櫃來源轉接器。"""

    name = "tpex"

    def __init__(self, client: HttpClient) -> None:
        self.client = client

    @property
    def tables(self) -> list[str]:
        return list(_ENDPOINTS)

    def fetch(self, table: str, date: dt.date) -> pl.DataFrame:
        if table not in _ENDPOINTS:
            raise DataError(
                table,
                date,
                f"tpex 只支援 {list(_ENDPOINTS)}（{table} 走 FinMind，T-010）",
            )
        path, id_field = _ENDPOINTS[table]
        params = {"date": date.strftime("%Y/%m/%d"), "response": "json"}
        resp = self.client.get(f"{_BASE}/{path}", params=params)
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise DataError(table, date, f"回應非 JSON（可能被 anti-bot 擋）：{exc}") from exc

        for tb in payload.get("tables", []):
            fields = tb.get("fields", [])
            if id_field in fields:
                rows = tb.get("data", [])
                if not rows:
                    raise DataError(table, date, "上櫃資料表為空（非交易日或尚未公布）")
                return raw_frame(fields, rows)
        raise DataError(table, date, f"回應中找不到含「{id_field}」的上櫃資料表")


# ── 上櫃三大法人（D-177 修訂三：族群法人象限圖）────────────────────────────────
# 刻意**不**放進 `_ENDPOINTS`：放進去每日擷取就會把上櫃法人寫進 `institutional_daily`，
# 而模型的籌碼特徵讀那張表——上櫃股的特徵會在沒有登記的情況下改變（上櫃法人補源是 T-037，另案）。
# 這裡只給族群法人用：呼叫端抓、算成族群合計，不落地個股列。
INSTI_URL = "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
# 欄位依序：代號、名稱，再來七組（買進、賣出、買賣超）股數，最後三大法人買賣超合計。
# 七組：外資及陸資(不含外資自營商)、外資自營商、外資及陸資合計、投信、自營商(自行買賣)、自營商(避險)、自營商合計。
# 欄名重複（每組都叫買進股數…），只能照位置讀；位置錯了由下面的恆等式擋下。
_INSTI_FIELDS = (
    ["代號", "名稱"] + ["買進股數", "賣出股數", "買賣超股數"] * 7 + ["三大法人買賣超股數合計"]
)


def fetch_institutional(client: HttpClient, day: dt.date) -> str:
    """櫃買中心「三大法人買賣明細」某一天（全部上櫃，含 ETF）的原始 JSON 文字。"""
    params = {"type": "Daily", "sect": "EW", "date": day.strftime("%Y/%m/%d"), "response": "json"}
    return client.get(INSTI_URL, params=params).text


def _shares(cell: object, sid: str, day: dt.date) -> int:
    text = str(cell).replace(",", "").strip()
    try:
        return int(text)
    except ValueError as exc:
        raise DataError("tpex_institutional", day, f"{sid} 股數不是整數：{cell!r}") from exc


def parse_institutional(text: str, day: dt.date) -> pl.DataFrame:
    """三大法人買賣明細 → date、stock_id、foreign_net、trust_net、dealer_net（股）。

    口徑同上市（`clean.clean_institutional`）：外資＝外資及陸資(不含外資自營商)＋外資自營商。
    回的日期不是 `day`、欄位不對、沒有資料、或任一列的買賣超對不起來（買進−賣出、各組加總）即 raise。
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DataError(
            "tpex_institutional", day, f"回應非 JSON（可能被 anti-bot 擋）：{exc}"
        ) from exc
    if payload.get("date") != day.strftime("%Y%m%d"):
        raise DataError(
            "tpex_institutional", day, f"回的是 {payload.get('date')!r} 的資料，不是 {day}"
        )
    tables = [t for t in payload.get("tables", []) if t.get("fields")]
    if len(tables) != 1 or tables[0]["fields"] != _INSTI_FIELDS:
        got = [t.get("fields") for t in tables]
        raise DataError("tpex_institutional", day, f"欄位跟預期不同，櫃買可能改版：{got}")
    rows = tables[0].get("data") or []
    if not rows:
        raise DataError("tpex_institutional", day, "三大法人買賣明細是空的（非交易日或尚未公布）")
    out: list[tuple[str, int, int, int]] = []
    for row in rows:
        sid = str(row[0]).strip()
        if len(row) != len(_INSTI_FIELDS):
            raise DataError(
                "tpex_institutional", day, f"{sid} 有 {len(row)} 欄，應為 {len(_INSTI_FIELDS)}"
            )
        v = [_shares(c, sid, day) for c in row[2:]]
        sums_ok = (
            all(v[i] - v[i + 1] == v[i + 2] for i in range(0, 21, 3))
            and v[8] == v[2] + v[5]  # 外資合計＝不含自營商＋外資自營商
            and v[20] == v[14] + v[17]  # 自營商合計＝自行買賣＋避險
            and v[21] == v[8] + v[11] + v[20]  # 三大法人合計
        )
        if not sums_ok:
            raise DataError(
                "tpex_institutional", day, f"{sid} 的買賣超對不起來（欄位可能位移）：{row}"
            )
        out.append((sid, v[8], v[11], v[20]))
    frame = pl.DataFrame(
        out,
        schema={
            "stock_id": pl.String,
            "foreign_net": pl.Int64,
            "trust_net": pl.Int64,
            "dealer_net": pl.Int64,
        },
        orient="row",
    )
    dup = frame.filter(pl.col("stock_id").is_duplicated())
    if dup.height:
        raise DataError("tpex_institutional", day, f"同一檔出現兩次：{dup.head(3).to_dicts()}")
    return frame.select(pl.lit(day, dtype=pl.Date).alias("date"), pl.all())
