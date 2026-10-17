"""上櫃三大法人與融資融券（2026-10-17）：櫃買新網站兩個吃日期的端點接進每日擷取。

fixture（`tests/fixtures/http/tpex/`）是實測回應裁成幾檔：2026-10-16 與 2026-09-04。
`finmind_expected_20260904.json` 是同一天同幾檔在 PG 裡的 FinMind 歷史值——官方每日數字要與它**逐位相同**，
否則表會在接上每日增量那天出現斷階（同上市的 D-157 原則）。測試不連網。
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.config import SourceRate
from twquant.conventions import TZ
from twquant.data.clean import TPEX_CLEANERS, clean_tpex_institutional, clean_tpex_margin
from twquant.data.http import HttpClient
from twquant.data.sources import raw_frame
from twquant.data.sources.tpex import TpexSource
from twquant.errors import DataError

_FIX = Path(__file__).parent / "fixtures" / "http" / "tpex"
D16 = dt.date(2026, 10, 16)
D04 = dt.date(2026, 9, 4)


def _load(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((_FIX / name).read_text(encoding="utf-8"))
    return payload


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


@dataclass
class _Resp:
    status_code: int
    text: str

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")


def _source(pages: dict[str, str], calls: list[tuple[str, dict[str, str] | None]]) -> TpexSource:
    def transport(method: str, url: str, *, params: dict[str, str] | None, timeout: float) -> _Resp:
        calls.append((url, params))
        for key, body in pages.items():
            if url.endswith(key):
                return _Resp(200, body)
        raise AssertionError(f"沒錄到 {url}")

    client = HttpClient(
        source="test",
        rate=SourceRate(max_requests=10, per_seconds=1.0),
        clock=_Clock(),
        transport=transport,
    )
    return TpexSource(client)


def _clean(table: str, name: str, day: dt.date) -> pl.DataFrame:
    calls: list[tuple[str, dict[str, str] | None]] = []
    page = (_FIX / name).read_text(encoding="utf-8")
    src = _source({"insti/dailyTrade": page, "margin/balance": page}, calls)
    raw = src.fetch(table, day)
    return TPEX_CLEANERS[table](raw, day)


# ── AC-1：兩個端點接進櫃買轉接器；參數帶日期與上櫃股票別 ─────────────────────────
def test_ac1_adapter_fetches_institutional_and_margin_with_dates() -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []
    pages = {
        "insti/dailyTrade": (_FIX / "institutional_daily.json").read_text(encoding="utf-8"),
        "margin/balance": (_FIX / "margin_daily.json").read_text(encoding="utf-8"),
    }
    src = _source(pages, calls)
    assert {"institutional_daily", "margin_daily"} <= set(src.tables)
    src.fetch("institutional_daily", D16)
    src.fetch("margin_daily", D16)
    (u1, p1), (u2, p2) = calls
    assert u1 == "https://www.tpex.org.tw/www/zh-tw/insti/dailyTrade"
    assert p1 == {"type": "Daily", "sect": "EW", "date": "2026/10/16", "response": "json"}
    assert u2 == "https://www.tpex.org.tw/www/zh-tw/margin/balance"
    assert p2 == {"date": "2026/10/16", "response": "json"}


def test_ac1_cleaned_rows_have_the_listed_definitions() -> None:
    ins = _clean("institutional_daily", "institutional_daily.json", D16)
    r = ins.filter(pl.col("stock_id") == "3105").row(0, named=True)
    # 外資＝外資及陸資合計；投信；自營商合計（2026-10-16 穩懋實際值）
    assert (r["foreign_net"], r["trust_net"], r["dealer_net"]) == (98529, -289000, -407228)
    assert r["available_at"] == dt.datetime(2026, 10, 16, 18, 0, tzinfo=TZ)
    mar = _clean("margin_daily", "margin_daily.json", D16)
    m = mar.filter(pl.col("stock_id") == "3105").row(0, named=True)
    assert (m["margin_balance"], m["short_balance"]) == (43943 * 1000, 682 * 1000)  # 張 → 股
    assert m["available_at"] == dt.datetime(2026, 10, 16, 21, 0, tzinfo=TZ)
    assert all(isinstance(s, str) for s in ins["stock_id"])


# ── AC-2：官方每日值與 FinMind 歷史逐位相同（不會在接上那天斷階） ─────────────────────
def test_ac2_official_values_equal_the_finmind_history_digit_for_digit() -> None:
    expected = _load("finmind_expected_20260904.json")
    ins = _clean("institutional_daily", "institutional_daily_20260904.json", D04)
    mar = _clean("margin_daily", "margin_daily_20260904.json", D04)
    got_i = {
        r["stock_id"]: {k: r[k] for k in ("foreign_net", "trust_net", "dealer_net")}
        for r in ins.iter_rows(named=True)
    }
    got_m = {
        r["stock_id"]: {k: r[k] for k in ("margin_balance", "short_balance")}
        for r in mar.iter_rows(named=True)
    }
    assert expected["institutional_daily"] and expected["margin_daily"]
    for sid, row in expected["institutional_daily"].items():
        assert got_i[sid] == row, sid
    for sid, row in expected["margin_daily"].items():
        assert got_m[sid] == row, sid


# ── AC-3：欄位位移或缺欄 → 整天 raise，不給出錯的數字 ───────────────────────────────
def _raw(name: str) -> pl.DataFrame:
    tb = _load(name)["tables"][0]
    return raw_frame(tb["fields"], tb["data"])


def test_ac3_shifted_or_missing_institutional_columns_raise() -> None:
    raw = _raw("institutional_daily.json")
    shifted = raw.with_columns(
        pl.col("買賣超股數_4").alias("買賣超股數_7")
    )  # 投信那組被當成自營商合計
    with pytest.raises(DataError, match="加總不符"):
        clean_tpex_institutional(shifted, D16)
    with pytest.raises(DataError, match="缺欄位"):
        clean_tpex_institutional(raw.drop("三大法人買賣超股數合計"), D16)


def test_ac3_bad_margin_values_raise() -> None:
    raw = _raw("margin_daily.json")
    with pytest.raises(DataError, match="不是數字"):
        clean_tpex_margin(raw.with_columns(pl.lit("--").alias("資餘額")), D16)
    with pytest.raises(DataError, match="缺欄位"):
        clean_tpex_margin(raw.drop("券餘額"), D16)
