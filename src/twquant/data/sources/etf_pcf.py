"""ETF 每日持股（PCF）的來源轉接器（SPEC-016 §2.2，T-336）：元大、統一。

| 投信 | 端點 | 查詢日 → 持股基準日 | 公布時點 |
|---|---|---|---|
| 元大 | `etfapi.yuantaetfs.com/ectranslation/api/bridge?APIType=ETFAPI&FuncId=PCF/Daily&ticker=<代號>&date=<YYYYMMDD>` | 查詢日的前一交易日（`PCF.trandate`）；不帶 date＝最新 | `PCF.upddate`（台北時間字串） |
| 統一 | `www.ezmoney.com.tw/ETF/Transaction/GetPCF`（POST JSON；先 GET 清單頁取 cookie 與基金代碼） | 同上（`asset[ST].Details[].TranDate`） | `Details[].EditTime` 最大值 |

T-334／T-336 實測：兩家的公布時間都在持股基準日當天 15:50～16:55。查詢日必須是交易日或未來日；
週末查詢回空。權重一律換成小數（來源是百分比）。解析函數是純函數（測試不連網），抓取函數很薄。
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.errors import DataError

TABLE = "etf_holdings_daily"
YUANTA_BRIDGE = "https://etfapi.yuantaetfs.com/ectranslation/api/bridge"
EZMONEY = "https://www.ezmoney.com.tw"
_PCT = 100.0
_STOCK_ID = re.compile(r"^[0-9A-Z]{4,6}$")
_MS_DATE = re.compile(r"^/Date\((-?\d+)\)/$")


class PcfEmpty(DataError):
    """查詢日拿不到清單（非交易日、尚未公布）。只有這種錯誤可以換下一個查詢日重試。"""


class PcfMissingBase(DataError):
    """來源沒有某個基準日的清單（查到的是別天，或試完所有查詢日都空）。回補時只報不停；每日模式照樣 raise。"""


@dataclass(frozen=True)
class PcfSnapshot:
    """一份持股清單：持股基準日、來源公布時點、逐檔持股（股、權重小數）。"""

    etf_id: str
    base_date: dt.date
    published_at: dt.datetime
    rows: tuple[tuple[str, int, float], ...]  # (stock_id, shares, weight)


def _shares(raw: Any, etf_id: str, day: dt.date | None, sid: str) -> int:
    ok = isinstance(raw, int | float) and not isinstance(raw, bool)
    if not ok or not float(raw).is_integer() or raw < 0:
        raise DataError(TABLE, day, f"{etf_id} {sid}：持股股數須為非負整數，得到 {raw!r}")
    return int(raw)


def _weight(raw: Any, etf_id: str, day: dt.date | None, sid: str) -> float:
    ok = isinstance(raw, int | float) and not isinstance(raw, bool)
    if not ok or not 0.0 <= float(raw) <= _PCT:
        raise DataError(TABLE, day, f"{etf_id} {sid}：權重須在 0～100%，得到 {raw!r}")
    return float(raw) / _PCT


def _sid(raw: Any, etf_id: str, day: dt.date | None) -> str:
    sid = str(raw or "").strip()
    if not _STOCK_ID.match(sid):
        raise DataError(TABLE, day, f"{etf_id}：持股代號格式不對：{raw!r}")
    return sid


def _check_unique(etf_id: str, day: dt.date, rows: list[tuple[str, int, float]]) -> None:
    ids = [r[0] for r in rows]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise DataError(TABLE, day, f"{etf_id}：同一份清單代號重複 {dup}")
    if not rows:
        raise DataError(TABLE, day, f"{etf_id}：清單沒有任何股票")


def yuanta_snapshot(payload: Mapping[str, Any], etf_id: str) -> PcfSnapshot:
    """元大 PCF/Daily JSON → PcfSnapshot。"""
    pcf = payload.get("PCF") or {}
    if not pcf.get("trandate"):
        raise PcfEmpty(TABLE, None, f"{etf_id}：元大這個查詢日沒有清單（非交易日或尚未公布）")
    if pcf.get("markcd") not in (None, etf_id) and str(pcf.get("markcd")).strip() != etf_id:
        raise DataError(TABLE, None, f"要 {etf_id}，元大回的是 {pcf.get('markcd')!r}")
    try:
        base = dt.datetime.strptime(str(pcf["trandate"]), "%Y%m%d").date()
        pub = dt.datetime.strptime(str(pcf["upddate"]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    except (KeyError, ValueError) as exc:
        raise DataError(TABLE, None, f"{etf_id}：元大 PCF 缺 trandate／upddate：{exc}") from exc
    weights = (payload.get("FundWeights") or {}).get("StockWeights")
    if not isinstance(weights, list):
        raise DataError(TABLE, base, f"{etf_id}：元大 PCF 缺 FundWeights.StockWeights")
    rows = [
        (
            sid := _sid(w.get("code"), etf_id, base),
            _shares(w.get("qty"), etf_id, base, sid),
            _weight(w.get("weights"), etf_id, base, sid),
        )
        for w in weights
    ]
    _check_unique(etf_id, base, rows)
    return PcfSnapshot(etf_id, base, pub, tuple(sorted(rows)))


def _ez_time(raw: Any, what: str, etf_id: str) -> dt.datetime:
    """ezmoney 兩種時間格式：'/Date(毫秒)/'（UTC 毫秒）或 '2025-08-29T00:00:00'（台北時間）。"""
    text = str(raw or "")
    m = _MS_DATE.match(text)
    if m:
        return dt.datetime.fromtimestamp(int(m.group(1)) / 1000, tz=TZ)
    try:
        return dt.datetime.fromisoformat(text).replace(tzinfo=TZ)
    except ValueError as exc:
        raise DataError(TABLE, None, f"{etf_id}：統一 {what} 格式不明：{raw!r}") from exc


def ezmoney_snapshot(payload: Mapping[str, Any], etf_id: str) -> PcfSnapshot:
    """統一 GetPCF JSON → PcfSnapshot（只取股票 ST；期貨與現金不是個股持股）。"""
    stocks = [a for a in payload.get("asset") or [] if a.get("AssetCode") == "ST"]
    if not stocks:  # 假日查詢回空資產清單（2026-10-09 補假實測）
        raise PcfEmpty(TABLE, None, f"{etf_id}：統一這個查詢日沒有清單（非交易日或尚未公布）")
    if len(stocks) != 1:
        raise DataError(TABLE, None, f"{etf_id}：統一 PCF 應有一個股票資產段，得到 {len(stocks)}")
    details = stocks[0].get("Details") or []
    if not details:
        raise PcfEmpty(TABLE, None, f"{etf_id}：統一這個查詢日沒有清單（非交易日或尚未公布）")
    bases = {_ez_time(d.get("TranDate"), "TranDate", etf_id).date() for d in details}
    if len(bases) != 1:
        raise DataError(TABLE, None, f"{etf_id}：同一份清單有多個基準日 {sorted(bases)}")
    base = bases.pop()
    pub = max(_ez_time(d.get("EditTime"), "EditTime", etf_id) for d in details)
    rows = [
        (
            sid := _sid(d.get("DetailCode"), etf_id, base),
            _shares(d.get("Share"), etf_id, base, sid),
            _weight(d.get("NavRate"), etf_id, base, sid),
        )
        for d in details
    ]
    _check_unique(etf_id, base, rows)
    return PcfSnapshot(etf_id, base, pub, tuple(sorted(rows)))


def ezmoney_fund_codes(page: str) -> dict[str, str]:
    """統一申購買回清單頁的 DataFundList → {ETF 代號: 統一內部基金代碼}。"""
    m = re.search(r'<div id="DataFundList" data-content="(.*?)" style', page, re.S)
    if not m:
        raise DataError(TABLE, None, "統一清單頁找不到 DataFundList（改版？）")
    funds = json.loads(html.unescape(m.group(1)))
    out = {
        str(f["sStockNo"]).strip(): str(f["sFundCode"]).strip() for f in funds if f.get("sFundCode")
    }
    if not out:
        raise DataError(TABLE, None, "統一 DataFundList 為空")
    return out


def roc(day: dt.date) -> str:
    """date → 民國 'YYY/MM/DD'（統一 GetPCF 的查詢日格式）。"""
    return f"{day.year - 1911}/{day:%m/%d}"


def fetch_yuanta(client: HttpClient, etf_id: str, query_date: dt.date | None) -> PcfSnapshot:
    """抓元大一份清單；query_date＝None 取最新。"""
    params = {"APIType": "ETFAPI", "FuncId": "PCF/Daily", "ticker": etf_id}
    if query_date is not None:
        params["date"] = f"{query_date:%Y%m%d}"
    resp = client.get(YUANTA_BRIDGE, params=params)
    try:
        payload = json.loads(resp.text)
    except json.JSONDecodeError as exc:
        raise DataError(TABLE, query_date, f"{etf_id}：元大回應非 JSON：{exc}") from exc
    return yuanta_snapshot(payload, etf_id)


class EzmoneySession:
    """統一 GetPCF 需要同一個 session 的 cookie；本類別只做 GET 清單頁一次、之後 POST。"""

    def __init__(self, pace_seconds: float, timeout: float = 30.0) -> None:
        import requests  # 延遲 import：純解析測試不需要

        self._s = requests.Session()
        self._s.headers["User-Agent"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) twquant"
        self._pace = pace_seconds
        self._timeout = timeout
        self._last = 0.0
        self._codes: dict[str, str] | None = None

    def _wait(self) -> None:
        gap = self._pace - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()

    def codes(self) -> dict[str, str]:
        """ETF 代號 → 統一內部基金代碼（第一次呼叫時抓清單頁）。"""
        if self._codes is None:
            self._wait()
            r = self._s.get(f"{EZMONEY}/ETF/Transaction/PCF", timeout=self._timeout)
            r.raise_for_status()
            self._codes = ezmoney_fund_codes(r.text)
        return self._codes

    def fetch(self, etf_id: str, query_date: dt.date | None) -> PcfSnapshot:
        """抓統一一份清單（查詢日必填：統一沒有「最新」模式，給 None 即 raise）。"""
        if query_date is None:
            raise DataError(TABLE, None, f"{etf_id}：統一 GetPCF 必須給查詢日")
        code = self.codes().get(etf_id)
        if code is None:
            raise DataError(TABLE, query_date, f"統一清單頁沒有 {etf_id}")
        self._wait()
        r = self._s.post(
            f"{EZMONEY}/ETF/Transaction/GetPCF",
            json={"fundCode": code, "date": roc(query_date), "specificDate": True},
            timeout=self._timeout,
        )
        if r.status_code != 200:
            raise DataError(TABLE, query_date, f"{etf_id}：統一 HTTP {r.status_code}")
        return ezmoney_snapshot(r.json(), etf_id)
