"""T-009 / SPEC-001：MOPS 月營收轉接器與清洗（離線，內嵌 Big5 HTML）。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import polars as pl
import pytest

from twquant.config import SourceRate
from twquant.conventions import TZ
from twquant.data.clean import clean_monthly_revenue
from twquant.data.http import HttpClient
from twquant.data.sources.mops import MopsSource
from twquant.errors import DataError

_RATE = SourceRate(max_requests=3, per_seconds=5.0)

# 最小 t21sc03 片段：表頭列（會被略過）+ 台泥資料列
_HTML = """<table>
<tr><td>公司代號</td><td>公司名稱</td><td>當月營收</td><td>上月營收</td><td>去年當月營收</td>
<td>上月比較增減(%)</td><td>去年同月增減(%)</td><td>當月累計營收</td><td>去年累計營收</td><td>前期比較增減(%)</td></tr>
<tr><td>1101</td><td>台泥</td><td>12,612,013</td><td>12,213,195</td><td>12,619,495</td>
<td>3.26</td><td>-0.06</td><td>58,084,626</td><td>60,273,039</td><td>-3.63</td></tr>
</table>"""


@dataclass
class FakeResponse:
    status_code: int
    text: str
    content: bytes


def _source() -> MopsSource:
    body = _HTML.encode("big5")

    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        return FakeResponse(200, "", body)

    return MopsSource(client=HttpClient(source="mops", rate=_RATE, transport=transport))


def test_mops_fetch_parses_row() -> None:
    df = _source().fetch_month(115, 5, "sii")
    assert df.columns[0] == "公司代號"
    row = df.filter(df["公司代號"] == "1101").to_dicts()[0]
    assert row["當月營收"] == "12,612,013"
    assert row["去年同月增減"] == "-0.06"


def test_mops_clean_revenue() -> None:
    raw = _source().fetch_month(115, 5, "sii")
    clean = clean_monthly_revenue(raw, 115, 5)
    row = clean.filter(pl.col("stock_id") == "1101").to_dicts()[0]
    assert row["revenue"] == 12_612_013 * 1000.0  # 千元 → 元
    # yoy 自算 = 當月/去年當月 − 1 = 12,612,013 / 12,619,495 − 1
    assert row["yoy"] == pytest.approx(12_612_013 / 12_619_495 - 1.0)
    assert row["year_month"] == "2026-05"
    assert row["period_end"] == dt.date(2026, 5, 31)
    assert row["date"] == dt.date(2026, 5, 31)
    # available_at 估算為次月 10 日 22:00
    assert row["available_at"] == dt.datetime(2026, 6, 10, 22, 0, tzinfo=TZ)
    assert row["available_at_estimated"] is True


def test_mops_empty_raises() -> None:
    body = "<table><tr><td>無資料</td></tr></table>".encode("big5")

    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        return FakeResponse(200, "", body)

    src = MopsSource(client=HttpClient(source="mops", rate=_RATE, transport=transport))
    with pytest.raises(DataError, match="無月營收"):
        src.fetch_month(115, 5, "sii")
