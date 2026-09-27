"""證交所／櫃買官方每日當沖與借券賣出（T-310／D-154 §五）：純解析器、抓取與直寫 PG 的來源。

一天一市場一個請求，全市場：

| 市場 | 表 | 端點（GET） | 日期 | 個股資料 |
|---|---|---|---|---|
| twse | daytrade_daily | `rwd/zh/dayTrading/TWTB4U?selectType=All` | 20260918 | tables[] 中含「證券代號」那張（另一張是全市場統計） |
| twse | sbl_short_daily | `rwd/zh/marginTrading/TWT93U` | 20260918 | 扁平；融券六欄與借券賣出六欄**同名**，只能靠位置 |
| tpex | daytrade_daily | `www/zh-tw/intraday/stat?type=Daily` | 2026/09/18 | 含「證券代號」那張；網站用 POST，GET 同參數回應逐字相同 |
| tpex | sbl_short_daily | `www/zh-tw/margin/sbl` | 2026/09/18 | tables[0]；同上兩段同名欄 |

單位：兩市都是**股**與**元**。2026-09-18 兩市當沖 1,728 檔、借券 1,875 檔對 FinMind 回補的歷史**逐位相同**
（D-157）。只收 `chip_daily.universe_pattern`（四碼普通股）——與歷史同一個宇宙（D-154 §二.5）；官方表另有
ETF、六碼 TDR 等，刻意不收。

休市或尚未公布：兩市都回 stat OK＋空表 → raise「無資料」，由呼叫端依同日其他表判斷是否休市（T-276）。
回應的日期與要求的不同 → raise（不把別天的數字記成這天）。

`sbl_covering`：官方兩張表都沒有這一欄 → null。FinMind 的 `SBLShortSalesShortCovering` 全史 494 萬列
都是 0，是它自己補上的欄，不是官方數字；照抄成 0 等於捏造（黃金規則 5）。
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import polars as pl

from twquant.data.http import HttpClient
from twquant.data.sources import alerts
from twquant.errors import DataError, QualityGateError

MARKETS = ("twse", "tpex")
CHIP_TABLES = ("daytrade_daily", "sbl_short_daily")

_TWSE = "https://www.twse.com.tw/rwd/zh"
_TPEX = "https://www.tpex.org.tw/www/zh-tw"
_REQUESTS: dict[tuple[str, str], tuple[str, dict[str, str]]] = {
    ("twse", "daytrade_daily"): (
        f"{_TWSE}/dayTrading/TWTB4U",
        {"selectType": "All", "response": "json"},
    ),
    ("twse", "sbl_short_daily"): (f"{_TWSE}/marginTrading/TWT93U", {"response": "json"}),
    ("tpex", "daytrade_daily"): (f"{_TPEX}/intraday/stat", {"type": "Daily", "response": "json"}),
    ("tpex", "sbl_short_daily"): (f"{_TPEX}/margin/sbl", {"response": "json"}),
}
_DATE_PARAM = {"twse": "%Y%m%d", "tpex": "%Y/%m/%d"}
_RESPONSE_DATE = "%Y%m%d"  # 兩市回應最外層的 date 都寫 20260918

# 當沖：兩市欄位相同。「暫停現股賣出後現款買進當沖註記」＝發行公司停止過戶前 5 個營業日的標記
# （D-154 §一）：證交所標 'Y'、櫃買標全形 '＊'，沒標是空字串或一個空白。其他值＝來源改了語意，raise。
_DAYTRADE_FIELDS = (
    "證券代號",
    "證券名稱",
    "暫停現股賣出後現款買進當沖註記",
    "當日沖銷交易成交股數",
    "當日沖銷交易買進成交金額",
    "當日沖銷交易賣出成交金額",
)
_SELL_FIRST_MARK = {"twse": "Y", "tpex": "＊"}

# 借券賣出在第 8~13 欄（0~1 股票、2~7 融券、14 備註）。欄名和融券那段同名，只能靠位置——所以整列欄名逐字核對，
# 改一個字就 raise，不猜位置。
_SBL_FIELDS = {
    "twse": (
        "代號",
        "名稱",
        "前日餘額",
        "賣出",
        "買進",
        "現券",
        "今日餘額",
        "次一營業日限額",
        "前日餘額",
        "當日賣出",
        "當日還券",
        "當日調整",
        "當日餘額",
        "次一營業日可限額",
        "備註",
    ),
    "tpex": (
        "股票代號",
        "股票名稱",
        "前日餘額",
        "賣出",
        "買進",
        "現券",
        "當日餘額",
        "限額",
        "前日餘額",
        "當日賣出",
        "當日還券",
        "當日調整數額",
        "當日餘額",
        "次一營業日可借券賣出限額",
        "備註",
    ),
}
_SBL_POSITIONS = {
    "sbl_prev_balance": 8,
    "sbl_sell": 9,
    "sbl_return": 10,
    "sbl_adjust": 11,  # 可為負（集中市場普通／信用／借券部位互調、錯帳申報）
    "sbl_balance": 12,
    "sbl_next_quota": 13,
}
# 證交所另附欄組（休市日是空的）；有就核對，確定第 8~13 欄真的是「借券賣出」那一組。
_TWSE_SBL_GROUPS = (("股票", 2), ("融券", 6), ("借券賣出", 6), ("", 1))

_INT = re.compile(r"-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)")
_NUMBER = re.compile(r"-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?")


def chip_request(market: str, table: str, day: dt.date) -> tuple[str, dict[str, str]]:
    """(URL, 查詢參數)。證交所日期寫 20260918、櫃買寫 2026/09/18。"""
    try:
        url, params = _REQUESTS[(market, table)]
    except KeyError as exc:
        raise ValueError(f"未知的 market／table：{market!r}／{table!r}") from exc
    return url, {**params, "date": day.strftime(_DATE_PARAM[market])}


def fetch_chip(client: HttpClient, market: str, table: str, day: dt.date) -> dict[str, Any]:
    """抓一天全市場（一個請求）→ JSON 物件；HTTP 非 200、非 JSON、stat 不是 ok、回應日期不對 → DataError。"""
    url, params = chip_request(market, table, day)
    resp = client.get(url, params=params)
    if resp.status_code != 200:
        raise DataError(
            table,
            day,
            f"{market} HTTP {resp.status_code}（anti_bot_suspected？）：{resp.text[:120]!r}",
        )
    try:
        payload = json.loads(resp.text)
    except json.JSONDecodeError as exc:
        raise DataError(
            table, day, f"{market} 回應不是 JSON（anti_bot_suspected）：{resp.text[:120]!r}"
        ) from exc
    if not isinstance(payload, dict):
        raise DataError(table, day, f"{market} 回應 JSON 不是物件：{type(payload).__name__}")
    stat = payload.get("stat")
    if not isinstance(stat, str) or stat.lower() != "ok":
        raise DataError(table, day, f"{market} 回應 stat={stat!r}（休市或尚未公布？）")
    got = payload.get("date")
    if got != day.strftime(_RESPONSE_DATE):
        raise DataError(
            table,
            day,
            f"{market} 回應的日期 {got!r} ≠ 要求的 {day:%Y%m%d}——不把別天的數字記成這一天。",
        )
    return payload


def _stock_table(
    market: str,
    table: str,
    payload: Mapping[str, Any],
    day: dt.date,
    expected: Sequence[str],
) -> list[list[Any]]:
    """挑出個股資料表並核對：休市（空表）→「無資料」；欄位逐字相符；筆數與 total／totalCount 相符。"""
    id_field = expected[0]
    raw_tables = payload.get("tables")
    candidates: list[Mapping[str, Any]]
    if isinstance(raw_tables, list):
        candidates = [
            t for t in raw_tables if isinstance(t, Mapping) and id_field in (t.get("fields") or [])
        ]
    else:
        candidates = [payload] if id_field in (payload.get("fields") or []) else []
    if len(candidates) != 1:
        raise DataError(
            table,
            day,
            f"{market} 回應中含「{id_field}」的資料表有 {len(candidates)} 張（預期 1 張）——來源改版？",
        )
    found = candidates[0]
    data = found.get("data")
    if not isinstance(data, list):
        raise DataError(table, day, f"{market} 個股資料表缺 data 陣列——來源改版？")
    if not data:
        raise DataError(table, day, f"{market} 個股資料表是空的（休市或尚未公布）")
    fields = list(found.get("fields") or [])
    if fields != list(expected):
        raise DataError(
            table, day, f"{market} 欄位與預期不同——來源改版？得到 {fields}，預期 {list(expected)}"
        )
    declared = found.get("total", found.get("totalCount"))
    if declared is not None and int(declared) != len(data):
        raise DataError(
            table, day, f"{market} 回應標示 {declared} 筆，實際 {len(data)} 列——回應被截斷？"
        )
    return data


# 證交所 TWT93U 最後一列是「合計」（代號空白、名稱「合計」，算在 total 裡）；只有這個樣子的列可以略過。
_TOTAL_ROW_NAME = "合計"


def _is_total_row(values: Sequence[Any]) -> bool:
    code, name = values[0], values[1]
    return (
        isinstance(code, str)
        and not code.strip()
        and isinstance(name, str)
        and name.strip() == _TOTAL_ROW_NAME
    )


def _code(values: Sequence[Any], table: str, day: dt.date, market: str) -> str:
    raw = values[0]
    sid = raw.strip() if isinstance(raw, str) else ""
    if not sid:
        raise DataError(
            table,
            day,
            f"{market} 有一列證券代號空白、又不是「合計」列——丟掉它就是丟掉一檔：{list(values)!r}",
        )
    return sid


def _int(value: Any, what: str, table: str, day: dt.date, sid: str) -> int:
    text = value.strip() if isinstance(value, str) else None
    if text is None or not _INT.fullmatch(text):
        raise DataError(table, day, f"{sid}：{what} 應為整數（股），得到 {value!r}——缺值不得當 0。")
    return int(text.replace(",", ""))


def _number(value: Any, what: str, table: str, day: dt.date, sid: str) -> float:
    text = value.strip() if isinstance(value, str) else None
    if text is None or not _NUMBER.fullmatch(text):
        raise DataError(table, day, f"{sid}：{what} 應為數字（元），得到 {value!r}——缺值不得當 0。")
    return float(text.replace(",", ""))


def _check_row(values: Any, n_fields: int, table: str, day: dt.date, market: str) -> list[Any]:
    if not isinstance(values, list) or len(values) != n_fields:
        raise DataError(table, day, f"{market} 資料列長度與欄位數 {n_fields} 不符：{values!r}")
    return values


def daytrade_rows(
    market: str, payload: Mapping[str, Any], day: dt.date, universe: re.Pattern[str]
) -> list[dict[str, Any]]:
    """官方當沖日報 → `daytrade_daily` 列（股、元；宇宙外略過）。與 FinMind `TaiwanStockDayTrading` 同欄同單位。"""
    table = "daytrade_daily"
    data = _stock_table(market, table, payload, day, _DAYTRADE_FIELDS)
    mark = _SELL_FIRST_MARK[market]
    out: list[dict[str, Any]] = []
    for raw in data:
        values = _check_row(raw, len(_DAYTRADE_FIELDS), table, day, market)
        if _is_total_row(values):
            continue
        sid = _code(values, table, day, market)
        if not universe.fullmatch(sid):
            continue
        flag = values[2].strip() if isinstance(values[2], str) else None
        if flag not in ("", mark):
            raise DataError(
                table,
                day,
                f"{market} {sid}：暫停先賣後買註記出現未知值 {values[2]!r}（只認空白與 {mark!r}）——來源改了語意，不猜。",
            )
        out.append(
            {
                "date": day,
                "stock_id": sid,
                "daytrade_shares": _int(values[3], "當沖成交股數", table, day, sid),
                "daytrade_buy_amount": _number(values[4], "當沖買進金額", table, day, sid),
                "daytrade_sell_amount": _number(values[5], "當沖賣出金額", table, day, sid),
                "sell_first_suspended": flag == mark,
            }
        )
    if not out:
        raise DataError(table, day, f"{market} 回應 {len(data)} 列、宇宙內 0 列——代號格式改了？")
    return out


def sbl_rows(
    market: str, payload: Mapping[str, Any], day: dt.date, universe: re.Pattern[str]
) -> list[dict[str, Any]]:
    """官方信用額度總量管制餘額表 → `sbl_short_daily` 列（借券賣出那六欄，股；宇宙外略過）。"""
    table = "sbl_short_daily"
    expected = _SBL_FIELDS[market]
    data = _stock_table(market, table, payload, day, expected)
    groups = payload.get("groups")
    if market == "twse" and groups:
        got = tuple((g.get("title"), g.get("span")) for g in groups if isinstance(g, Mapping))
        if got != _TWSE_SBL_GROUPS:
            raise DataError(
                table, day, f"twse 欄組與預期不同——來源改版？得到 {got}，預期 {_TWSE_SBL_GROUPS}"
            )
    out: list[dict[str, Any]] = []
    for raw in data:
        values = _check_row(raw, len(expected), table, day, market)
        if _is_total_row(values):
            continue
        sid = _code(values, table, day, market)
        if not universe.fullmatch(sid):
            continue
        row: dict[str, Any] = {"date": day, "stock_id": sid}
        for col, pos in _SBL_POSITIONS.items():
            row[col] = _int(values[pos], f"{col}（{expected[pos]}）", table, day, sid)
        row["sbl_covering"] = None  # 官方沒有這一欄（見模組說明）
        out.append(row)
    if not out:
        raise DataError(table, day, f"{market} 回應 {len(data)} 列、宇宙內 0 列——代號格式改了？")
    return out


_PARSERS: dict[
    str,
    Callable[[str, Mapping[str, Any], dt.date, re.Pattern[str]], list[dict[str, Any]]],
] = {
    "daytrade_daily": daytrade_rows,
    "sbl_short_daily": sbl_rows,
}


@dataclass(frozen=True)
class OfficialChipSource:
    """官方當沖／借券的 SourcePort（直寫 PG）：load＝抓一天 → 解析 → `alerts.build_frame`（同日 21:00）。"""

    market: str
    client: HttpClient
    universe: re.Pattern[str]

    @property
    def name(self) -> str:
        """來源標籤。"""
        return f"{self.market}_chip"

    @property
    def tables(self) -> list[str]:
        """供應的表。"""
        return list(CHIP_TABLES)

    def load(self, table: str, period: dt.date) -> pl.DataFrame:
        """抓 period 當天全市場 → 驗證過的 frame；任何資料問題都是 DataError（只算這張表失敗）。"""
        if table not in _PARSERS:
            raise DataError(table, period, f"{self.name} 不供應 {table}")
        payload = fetch_chip(self.client, self.market, table, period)
        rows = _PARSERS[table](self.market, payload, period, self.universe)
        try:
            df, _dups = alerts.build_frame(table, rows)
        except QualityGateError as exc:
            raise DataError(table, period, f"{self.market}：{exc}") from exc
        return df
