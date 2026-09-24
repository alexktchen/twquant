"""處置股、注意股公告（T-309／D-154）：證交所＋櫃買四個端點的純解析器、抓取小工具與第 1 批 frame builder。

四個端點都接受整年的日期區間——一年一個請求，2014~2026 共約 52 個，遠低於 TWSE WAF 的封鎖門檻
（約 120~390 個請求，D-015／D-044）：

| 市場 | 種類 | 端點 | 日期參數 | 回應的特徵 |
|---|---|---|---|---|
| twse | 處置 | `rwd/zh/announcement/punish` | 20180101 | 扁平 fields/data；期間用全形「～」；撮合分鐘是中文數字 |
| twse | 注意 | `rwd/zh/announcement/notice` | 20180101 | 扁平；日期 `107.12.28`；款次包在 `<font>` 裡 |
| tpex | 處置 | `www/zh-tw/bulletin/disposal` | 2018/01/01 | tables[]；有「本日無處置資料」佔位列；分鐘是阿拉伯數字 |
| tpex | 注意 | `www/zh-tw/bulletin/attention` | 2018/01/01 | tables[]；有「本日無公布注意交易資訊」佔位列；款次用半形括號、`<br>` 分段 |

以上格式對 2014~2026 全部 52 份回應逐年核過（D-154 §一）。

處置的查詢是依「處置期間」與區間重疊，所以前一年底公告、跨年執行的處置兩個年度都會出現
（內容相同，upsert 冪等）。

清洗規則（SPEC-001 §3）：民國年 → 西元、全形 → 半形（NFKC）、去 HTML 與櫃買的連結尾巴、
數字欄的 "--"／"N/A" → null（不是 0）。

來源的「累計／累計次數」**刻意不存**：證交所的是查詢區間內該檔的總次數（2018 全年 2,335 列
全部等於該檔在區間內的列數——含之後才公告的，是前視值）；櫃買的是當天公告了幾檔（同一天
各檔數字相同）。兩者都不是這一列的事實；要算某檔先前被注意幾次，由因子層經 PitView 自己數。
"""

from __future__ import annotations

import datetime as dt
import html
import json
import math
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import polars as pl

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.http import HttpClient
from twquant.errors import DataError

MARKETS = ("twse", "tpex")
KINDS = ("disposition", "attention")
TABLE_OF_KIND = {"disposition": "disposition_stocks", "attention": "attention_stocks"}

_ENDPOINTS = {
    ("twse", "disposition"): "https://www.twse.com.tw/rwd/zh/announcement/punish",
    ("twse", "attention"): "https://www.twse.com.tw/rwd/zh/announcement/notice",
    ("tpex", "disposition"): "https://www.tpex.org.tw/www/zh-tw/bulletin/disposal",
    ("tpex", "attention"): "https://www.tpex.org.tw/www/zh-tw/bulletin/attention",
}
_DATE_PARAM_FORMAT = {"twse": "%Y%m%d", "tpex": "%Y/%m/%d"}

# available_at（SPEC-001 §1）：處置／注意＝公告日 18:00；當沖／借券＝同日 21:00
# （比照 margin_daily：20:00 訊號截點看不到，以 T-1 進當日訊號，D-008）。
_AVAILABLE_HOUR = {
    "disposition_stocks": 18,
    "attention_stocks": 18,
    "daytrade_daily": 21,
    "sbl_short_daily": 21,
}
_KEY = ["stock_id", "date"]
_ROC_YEAR_OFFSET = 1911


@dataclass(frozen=True)
class Parsed:
    """一個回應解析出的列（對應 schema 欄位、尚無 available_at），與略過的佔位列數。"""

    rows: list[dict[str, Any]]
    placeholders: int = 0


# ── 文字與數字的小工具（純函數；格式不符一律 ValueError，由列解析器補上表名／日期／代號）──────


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


_ROC_DATE = re.compile(r"(\d{2,3})[/.](\d{1,2})[/.](\d{1,2})")


def roc_date(text: str) -> dt.date:
    """民國日期 `107/11/05` 或 `107.12.28` → 西元 date；格式不符或日期不存在 → ValueError。"""
    m = _ROC_DATE.fullmatch(_nfkc(text).strip())
    if m is None:
        raise ValueError(f"不是民國日期（107/11/05 或 107.12.28）：{text!r}")
    year, month, day = (int(g) for g in m.groups())
    return dt.date(year + _ROC_YEAR_OFFSET, month, day)


def roc_period(text: str) -> tuple[dt.date, dt.date]:
    """處置期間 `107/11/06～107/11/19`（證交所全形）或 `106/12/22~107/01/05`（櫃買半形）→ (起, 迄)。"""
    parts = _nfkc(text).split("~")
    if len(parts) != 2:
        raise ValueError(f"處置期間應為「起~迄」兩段：{text!r}")
    start, end = roc_date(parts[0]), roc_date(parts[1])
    if start > end:
        raise ValueError(f"處置期間起日晚於迄日：{text!r}")
    return start, end


_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_ARABIC = re.compile(r"[0-9]+")
_CHINESE = re.compile(r"[一二三四五六七八九十]+")
_NUMERAL = r"([0-9]+|[一二三四五六七八九十]+)"


def chinese_number(text: str) -> int:
    """正整數：中文數字限 1~99（五、十、二十五、四十五）；阿拉伯數字（含全形）不設上限；其他 → ValueError。

    阿拉伯數字不設上限是實測來的：櫃買 2014 有「約每270分鐘撮合一次」（270 分鐘＝整個交易時段，
    即一天只撮合一次），是合法值。
    """
    t = _nfkc(text).strip()
    if _ARABIC.fullmatch(t):
        n = int(t)
        if n < 1:
            raise ValueError(f"須為正整數：{text!r}")
        return n
    if not _CHINESE.fullmatch(t):
        raise ValueError(f"不是數字：{text!r}")
    tens, ten, ones = t.partition("十")
    if not ten:
        if len(t) != 1:
            raise ValueError(f"無法解讀的中文數字（只支援 1~99）：{text!r}")
        return _CN_DIGITS[t]
    if "十" in ones or len(tens) > 1 or len(ones) > 1:
        raise ValueError(f"無法解讀的中文數字（只支援 1~99）：{text!r}")
    return (_CN_DIGITS[tens] if tens else 1) * 10 + (_CN_DIGITS[ones] if ones else 0)


_MINUTES = re.compile(r"每\s*" + _NUMERAL + r"\s*分鐘")


def match_minutes(text: str) -> int | None:
    """處置內容裡「約每 N 分鐘撮合一次」的 N；沒寫 → None。

    同一則寫了**多個**間隔時取最長的（最嚴格的撮合）。實測 2014~2026 有 26 則如此，兩種成因：
    (a) 2026-08-10 處置新制過渡——期間跨過那天的，原本每 5／20 分鐘、當天起改每 2 分鐘；
    (b) 櫃買可轉債併同標的股的處置——自身每 5 分鐘，與標的重疊的那段「仍應」每 20 分鐘。
    完整文字留在 measure 欄，這一欄只是摘要。
    """
    found = {chinese_number(m) for m in _MINUTES.findall(_nfkc(text))}
    return max(found) if found else None


_CLAUSE = re.compile(r"第" + _NUMERAL + r"款")


def clause_list(text: str) -> str | None:
    """注意交易資訊的款次（`﹝第九款﹞`、`(第七款)`）→ 遞增、不重複的逗號串（如 "1,9"）；沒有 → None。"""
    nums = sorted({chinese_number(m) for m in _CLAUSE.findall(_nfkc(text))})
    return ",".join(str(n) for n in nums) if nums else None


_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")
_LINK = re.compile(r"\((?:\.{1,2}/|https?://)[^()]*\)")


def strip_html(text: str) -> str:
    """`<br>` → 換行、其餘標籤去掉、HTML 實體還原。"""
    return html.unescape(_TAG.sub("", _BR.sub("\n", text)))


def strip_links(text: str) -> str:
    """去掉櫃買接在名稱／原因後的連結尾巴，如 `倚強(../../mainboard/…)`、`第一款(./attention.html)`。"""
    return _LINK.sub("", text)


def clean_text(raw: str) -> str | None:
    """存進表的文字：去 HTML → 全形轉半形（NFKC）→ 去連結尾巴 → 去頭尾空白；空的 → None。"""
    text = strip_links(_nfkc(strip_html(raw))).strip()
    return text or None


_NULL_TOKENS = frozenset({"", "-", "--", "---", "----", "-----", "N/A", "NA"})


def number_or_none(raw: object) -> float | None:
    """數字欄：'12.60'、'1,234.5' → float；''、'--'、'-----'、'N/A'、null → None（不是 0）；其他 → ValueError。"""
    if raw is None:
        return None
    if isinstance(raw, bool):
        raise ValueError(f"數字欄不應是布林：{raw!r}")
    if isinstance(raw, int | float):
        value = float(raw)
    elif isinstance(raw, str):
        text = _nfkc(raw).strip().replace(",", "")
        if text in _NULL_TOKENS:
            return None
        value = float(text)
    else:
        raise ValueError(f"數字欄型別不明：{raw!r}")
    if not math.isfinite(value):
        raise ValueError(f"數字欄不是有限數值：{raw!r}")
    return value


# ── 回應結構 ────────────────────────────────────────────────────────────────


def _field_index(fields: Sequence[Any], required: Sequence[str], table: str) -> dict[str, int]:
    names = list(fields)
    out: dict[str, int] = {}
    for name in required:
        n = names.count(name)
        if n != 1:
            raise DataError(
                table, None, f"回應欄位 {name!r} 出現 {n} 次（預期 1 次）——來源改版？欄位：{names}"
            )
        out[name] = names.index(name)
    return out


def _twse_data(payload: Mapping[str, Any], table: str) -> tuple[list[Any], list[Any]]:
    stat = payload.get("stat")
    if stat != "OK":
        raise DataError(table, None, f"證交所回應 stat={stat!r}（預期 'OK'）")
    fields, data = payload.get("fields"), payload.get("data")
    if not isinstance(fields, list) or not isinstance(data, list):
        raise DataError(table, None, "證交所回應缺 fields／data 陣列——來源改版？")
    total = payload.get("total")
    if total is not None and int(total) != len(data):
        raise DataError(
            table, None, f"證交所回應 total={total}，data 卻有 {len(data)} 列——回應被截斷？"
        )
    return fields, data


_TPEX_TOTAL = re.compile(r"總累計次數\s*:\s*(\d+)")


def _tpex_data(
    payload: Mapping[str, Any], table: str, required: Sequence[str]
) -> tuple[list[Any], list[Any], int | None]:
    stat = payload.get("stat")
    if not isinstance(stat, str) or stat.lower() != "ok":
        raise DataError(table, None, f"櫃買回應 stat={stat!r}（預期 'ok'）")
    tables = payload.get("tables")
    if not isinstance(tables, list):
        raise DataError(table, None, "櫃買回應缺 tables 陣列——來源改版？")
    hits = [
        t
        for t in tables
        if isinstance(t, Mapping)
        and isinstance(t.get("fields"), list)
        and all(f in t["fields"] for f in required)
    ]
    if len(hits) != 1:
        raise DataError(
            table, None, f"櫃買回應含欄位 {list(required)} 的資料表有 {len(hits)} 張（預期 1 張）"
        )
    found = hits[0]
    data = found.get("data")
    if not isinstance(data, list):
        raise DataError(table, None, "櫃買資料表缺 data 陣列——來源改版？")
    m = _TPEX_TOTAL.search(_nfkc(str(found.get("subtitle") or "")))
    return found["fields"], data, int(m.group(1)) if m else None


def _check_declared(declared: int | None, counted: int, table: str) -> None:
    """櫃買 subtitle 的「總累計次數: N」與實際列數核對；對不上＝截斷或佔位列規則錯了。

    兩個端點數法不同（實測 2014~2026 每一年都如此）：處置的 N **不含**佔位列（＝公告列數），
    注意的 N **含**佔位列（佔位列自帶「累計 1」）——呼叫端依端點給對應的列數。
    """
    if declared is not None and declared != counted:
        raise DataError(
            table,
            None,
            f"櫃買回應標示總累計次數 {declared}，實際 {counted} 列——回應被截斷，或佔位列規則錯了。",
        )


def _wrap[T](fn: Callable[[Any], T], arg: Any, table: str, day: dt.date, sid: str, what: str) -> T:
    try:
        return fn(arg)
    except ValueError as exc:
        raise DataError(table, day, f"{sid}：{what}解析失敗——{exc}") from exc


@dataclass(frozen=True)
class _Row:
    """一列原始資料的取值器；錯誤訊息帶表名、公告日、代號（SPEC-000 §錯誤處理）。"""

    values: Sequence[Any]
    fields: Sequence[Any]
    ix: Mapping[str, int]
    table: str

    def __post_init__(self) -> None:
        if not isinstance(self.values, list) or len(self.values) != len(self.fields):
            raise DataError(
                self.table, None, f"資料列長度與欄位數 {len(self.fields)} 不符：{self.values!r}"
            )

    def raw(self, name: str) -> Any:
        return self.values[self.ix[name]]

    def text(self, name: str, day: dt.date | None, sid: str = "?") -> str:
        value = self.raw(name)
        if not isinstance(value, str):
            raise DataError(self.table, day, f"{sid}：欄位 {name} 應為字串，得到 {value!r}")
        return value

    def blank(self, name: str) -> bool:
        value = self.raw(name)
        return isinstance(value, str) and not value.strip()

    def date(self, name: str) -> dt.date:
        raw = self.text(name, None)
        try:
            return roc_date(raw)
        except ValueError as exc:
            raise DataError(self.table, None, f"欄位 {name}：{exc}") from exc

    def code(self, name: str, day: dt.date) -> str:
        sid = _nfkc(self.text(name, day)).strip()
        if not sid:
            raise DataError(
                self.table,
                day,
                f"證券代號空白、又不是「本日無…」佔位列——丟掉它就是丟掉一筆公告：{self.values!r}",
            )
        return sid


# 櫃買的「今天沒有公告」佔位列。實測 2014~2026 只有兩種樣子，代號與名稱都空白：
#   處置：處置內容「本日無處置資料」，其餘欄空白（1,432 列）；
#   注意：注意交易資訊「本日無公布注意交易資訊」，另帶 累計 1、收盤價 0.00、本益比 N/A、
#         link「…company-detail.html?code=」這些填充值（10 列）。
_PLACEHOLDER = re.compile(r"本日無\S*(?:資料|資訊)")


def _is_placeholder(row: _Row, content: str) -> bool:
    """櫃買佔位列：代號、名稱都空白，且內容欄寫的是「本日無…資料／資訊」。其餘欄是填充值，不看。"""
    if not (row.blank("證券代號") and row.blank("證券名稱")):
        return False
    text = row.raw(content)
    cleaned = clean_text(text) if isinstance(text, str) else None
    return cleaned is not None and _PLACEHOLDER.fullmatch(cleaned) is not None


# ── 四個解析器 ────────────────────────────────────────────────────────────


_TWSE_DISPOSITION = ("公布日期", "證券代號", "處置條件", "處置起迄時間", "處置措施", "處置內容")
# 證交所「處置措施」實測 2014~2026 只有這四種（不重複公告 1,291／834／195／73 則）。「人工管制撮合」
# （全額交割股等）與「督導會報決議」（監視業務督導會報另案決議）沒有第幾次的分別，level 留 null；
# 未知的值＝來源改了分類，raise 不猜。
_TWSE_LEVEL: dict[str, int | None] = {
    "第一次處置": 1,
    "第二次處置": 2,
    "人工管制撮合": None,
    "督導會報決議": None,
}


def twse_disposition_rows(payload: Mapping[str, Any]) -> Parsed:
    """證交所處置公告 → `disposition_stocks` 列（market='twse'，含權證等全部有價證券）。"""
    table = TABLE_OF_KIND["disposition"]
    fields, data = _twse_data(payload, table)
    ix = _field_index(fields, _TWSE_DISPOSITION, table)
    out: list[dict[str, Any]] = []
    for values in data:
        row = _Row(values, fields, ix, table)
        day = row.date("公布日期")
        sid = row.code("證券代號", day)
        kind = _nfkc(row.text("處置措施", day, sid)).strip()
        if kind not in _TWSE_LEVEL:
            raise DataError(
                table,
                day,
                f"{sid}：未知的處置措施 {kind!r}（已知：{list(_TWSE_LEVEL)}）——來源改了分類，不猜 level。",
            )
        start, end = _wrap(
            roc_period, row.text("處置起迄時間", day, sid), table, day, sid, "處置期間"
        )
        content = row.text("處置內容", day, sid)
        out.append(
            {
                "date": day,
                "stock_id": sid,
                "market": "twse",
                "effective_start": start,
                "effective_end": end,
                "level": _TWSE_LEVEL[kind],
                "match_minutes": _wrap(match_minutes, content, table, day, sid, "撮合分鐘"),
                "reason": clean_text(row.text("處置條件", day, sid)),
                "measure": clean_text(content),
            }
        )
    return Parsed(out)


_TPEX_DISPOSITION = ("公布日期", "證券代號", "證券名稱", "處置起訖時間", "處置原因", "處置內容")
# 櫃買沒有「第幾次」欄位：內容寫「最近30個營業日內曾發布處置」＝第二次（實測 2018：193 筆中 64 筆）。
_TPEX_REPEAT_MARK = "曾發布處置"
# 處置原因寫「監視業務督導會報」＝督導會報另案決議（實測 47 則，多在 2014），同證交所「督導會報決議」
# 沒有第幾次的分別 → level null。
_TPEX_SUPERVISORY_MARK = "督導會報"


def _tpex_level(reason: str | None, content: str) -> int | None:
    if reason is not None and _TPEX_SUPERVISORY_MARK in reason:
        return None
    return 2 if _TPEX_REPEAT_MARK in _nfkc(content) else 1


def tpex_disposition_rows(payload: Mapping[str, Any]) -> Parsed:
    """櫃買處置公告 → `disposition_stocks` 列（market='tpex'）；略過「本日無處置資料」佔位列。"""
    table = TABLE_OF_KIND["disposition"]
    fields, data, declared = _tpex_data(payload, table, _TPEX_DISPOSITION)
    ix = _field_index(fields, _TPEX_DISPOSITION, table)
    out: list[dict[str, Any]] = []
    placeholders = 0
    for values in data:
        row = _Row(values, fields, ix, table)
        if _is_placeholder(row, "處置內容"):
            placeholders += 1
            continue
        day = row.date("公布日期")
        sid = row.code("證券代號", day)
        start, end = _wrap(
            roc_period, row.text("處置起訖時間", day, sid), table, day, sid, "處置期間"
        )
        content = row.text("處置內容", day, sid)
        reason = clean_text(row.text("處置原因", day, sid))
        out.append(
            {
                "date": day,
                "stock_id": sid,
                "market": "tpex",
                "effective_start": start,
                "effective_end": end,
                "level": _tpex_level(reason, content),
                "match_minutes": _wrap(match_minutes, content, table, day, sid, "撮合分鐘"),
                "reason": reason,
                "measure": clean_text(content),
            }
        )
    _check_declared(declared, len(out), table)  # 處置：N 不含佔位列
    return Parsed(out, placeholders)


def _attention_row(row: _Row, day: dt.date, sid: str, market: str) -> dict[str, Any]:
    table = row.table
    raw_info = row.text("注意交易資訊", day, sid)
    info = clean_text(raw_info)
    if info is None:
        raise DataError(
            table, day, f"{sid}：注意交易資訊是空的——注意股公告不會沒有理由，來源改版？"
        )
    return {
        "date": day,
        "stock_id": sid,
        "market": market,
        "clauses": _wrap(clause_list, raw_info, table, day, sid, "款次"),
        "info": info,
        "close": _wrap(number_or_none, row.raw("收盤價"), table, day, sid, "收盤價"),
    }


_TWSE_ATTENTION = ("證券代號", "注意交易資訊", "日期", "收盤價")


def twse_attention_rows(payload: Mapping[str, Any]) -> Parsed:
    """證交所注意股公告 → `attention_stocks` 列（market='twse'，含權證／ETN 等全部有價證券）。"""
    table = TABLE_OF_KIND["attention"]
    fields, data = _twse_data(payload, table)
    ix = _field_index(fields, _TWSE_ATTENTION, table)
    out: list[dict[str, Any]] = []
    for values in data:
        row = _Row(values, fields, ix, table)
        day = row.date("日期")
        out.append(_attention_row(row, day, row.code("證券代號", day), "twse"))
    return Parsed(out)


_TPEX_ATTENTION = ("證券代號", "證券名稱", "注意交易資訊", "公告日期", "收盤價")


def tpex_attention_rows(payload: Mapping[str, Any]) -> Parsed:
    """櫃買注意股公告 → `attention_stocks` 列（market='tpex'）；略過「本日無公布注意交易資訊」佔位列。"""
    table = TABLE_OF_KIND["attention"]
    fields, data, declared = _tpex_data(payload, table, _TPEX_ATTENTION)
    ix = _field_index(fields, _TPEX_ATTENTION, table)
    out: list[dict[str, Any]] = []
    placeholders = 0
    for values in data:
        row = _Row(values, fields, ix, table)
        if _is_placeholder(row, "注意交易資訊"):
            placeholders += 1
            continue
        day = row.date("公告日期")
        out.append(_attention_row(row, day, row.code("證券代號", day), "tpex"))
    _check_declared(declared, len(out) + placeholders, table)  # 注意：N 含佔位列
    return Parsed(out, placeholders)


_PARSERS: dict[tuple[str, str], Callable[[Mapping[str, Any]], Parsed]] = {
    ("twse", "disposition"): twse_disposition_rows,
    ("twse", "attention"): twse_attention_rows,
    ("tpex", "disposition"): tpex_disposition_rows,
    ("tpex", "attention"): tpex_attention_rows,
}


def parse_alerts(market: str, kind: str, payload: Mapping[str, Any]) -> Parsed:
    """依市場與種類分派到對應的解析器。"""
    try:
        parser = _PARSERS[(market, kind)]
    except KeyError as exc:
        raise ValueError(f"未知的 market／kind：{market!r}／{kind!r}") from exc
    return parser(payload)


# ── 抓取（呼叫端給 HttpClient；限流與重試由它負責）──────────────────────────────


def alert_request(
    market: str, kind: str, start: dt.date, end: dt.date
) -> tuple[str, dict[str, str]]:
    """(URL, 查詢參數)。證交所日期寫 20180101、櫃買寫 2018/01/01；區間兩端都含。"""
    try:
        url = _ENDPOINTS[(market, kind)]
    except KeyError as exc:
        raise ValueError(f"未知的 market／kind：{market!r}／{kind!r}") from exc
    fmt = _DATE_PARAM_FORMAT[market]
    return url, {"startDate": start.strftime(fmt), "endDate": end.strftime(fmt), "response": "json"}


def fetch_alerts(
    client: HttpClient, market: str, kind: str, start: dt.date, end: dt.date
) -> dict[str, Any]:
    """抓 [start, end] 的處置／注意公告（一個請求），回傳 JSON 物件。

    HTTP 非 200 或回應不是 JSON（WAF 挑戰頁）→ raise DataError 並標 `anti_bot_suspected`
    （SPEC-001 §2）。呼叫端見到就停，不在迴圈裡重試——那只會讓封鎖更久。
    """
    table = TABLE_OF_KIND[kind]
    url, params = alert_request(market, kind, start, end)
    resp = client.get(url, params=params)
    if resp.status_code != 200:
        raise DataError(
            table,
            end,
            f"{market} {kind} HTTP {resp.status_code}（anti_bot_suspected：WAF 挑戰頁？）：{resp.text[:120]!r}",
        )
    try:
        payload = json.loads(resp.text)
    except json.JSONDecodeError as exc:
        raise DataError(
            table,
            end,
            f"{market} {kind} 回應不是 JSON（anti_bot_suspected）：{resp.text[:120]!r}",
        ) from exc
    if not isinstance(payload, dict):
        raise DataError(table, end, f"{market} {kind} 回應 JSON 不是物件：{type(payload).__name__}")
    return payload


# ── 第 1 批四張表的 frame builder ─────────────────────────────────────────────


def build_frame(table: str, rows: Sequence[Mapping[str, Any]]) -> tuple[pl.DataFrame, int]:
    """第 1 批四張表的列 → 驗證過的 DataFrame；回傳 (frame, 合併掉的完全重複列數)。

    - 列的欄位必須與 schema（去掉 available_at）完全一致：多欄、少欄都 raise——少的欄
      polars 會靜默補 null。
    - 加 available_at：處置／注意＝公告日 18:00，當沖／借券＝同日 21:00。
    - 同一 (stock_id, date) 出現**內容不同**的兩列 → raise（`upsert` 的去重只會默默留下最後一列）；
      內容完全相同的重複列合併成一列，並回報筆數。
    - 最後跑 `schemas.validate`（pandera），不過 → QualityGateError。
    """
    if table not in _AVAILABLE_HOUR:
        raise ValueError(f"build_frame 只處理第 1 批的表 {sorted(_AVAILABLE_HOUR)}，得到 {table!r}")
    schema = schemas.get_schema(table)
    cols = list(schema.columns)
    data_cols = [c for c in cols if c != "available_at"]
    expected = set(data_cols)
    for row in rows:
        keys = set(row)
        if keys != expected:
            raise ValueError(
                f"{table}：列的欄位與 schema 不符——多了 {sorted(keys - expected)}、"
                f"少了 {sorted(expected - keys)}"
            )
    dtypes = {c: schema.columns[c].dtype.type for c in data_cols}
    df = (
        pl.DataFrame([dict(r) for r in rows], schema=dtypes)
        .with_columns(
            (pl.col("date").cast(pl.Datetime("us")) + pl.duration(hours=_AVAILABLE_HOUR[table]))
            .dt.replace_time_zone(TZ.key)
            .alias("available_at")
        )
        .select(cols)
    )
    exact = 0
    duplicated = df.select(pl.struct(_KEY).is_duplicated()).to_series()
    if duplicated.any():
        conflicts = (
            df.filter(duplicated).unique().group_by(_KEY).len().filter(pl.col("len") > 1).sort(_KEY)
        )
        if conflicts.height:
            first = conflicts.row(0, named=True)
            raise DataError(
                table,
                first["date"],
                f"同一 (stock_id, date) 出現內容不同的列 {conflicts.height} 組（例：{first['stock_id']}）"
                "——upsert 的去重只會默默留下最後一列，故中止。",
            )
        before = df.height
        df = df.unique(maintain_order=True)
        exact = before - df.height
    return schemas.validate(table, df.sort(["date", "stock_id"])), exact
