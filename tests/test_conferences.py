"""T-376／D-188：MOPS 法說會月表解析與行事曆表。不連網。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twquant.conventions import TZ
from twquant.data.serving import schema as sch
from twquant.data.sources import conferences as cf
from twquant.errors import DataError

_FIX = Path(__file__).resolve().parent / "fixtures" / "http" / "mops"
AT = dt.datetime(2026, 10, 17, 18, 0, tzinfo=TZ)


def _page() -> str:
    return (_FIX / "t100sb02_1_sii_202610.html").read_text(encoding="utf-8")


# ── AC-1：實際頁（2026-10 上市，裁成 4 列）解析正確；available_at＝抓到的時間 ───────
def test_ac1_parses_rows_with_roc_dates_and_fetch_time() -> None:
    df = cf.parse_month(_page(), "sii", AT)
    assert df.height == 4 and list(df.columns) == list(cf.SCHEMA)
    first = df.row(0, named=True)
    assert first["stock_id"] == "1203" and first["name"] == "味王"
    assert first["date"] == dt.date(2026, 10, 14) and first["conf_time"] == "14:30"
    assert first["market"] == "sii" and first["available_at"] == AT
    assert first["location"] is not None and first["summary"] == "公司概況、財務報告及營運概況。"
    assert all(isinstance(s, str) for s in df["stock_id"])


def test_ac1_header_only_page_is_an_empty_month_not_an_error() -> None:
    page = _page()
    i = page.index("<tr") if "<tr" in page else 0
    # 砍掉表頭之後的列：留表頭
    head_end = page.index("</tr>", page.index(cf._PAGE_MARK)) + len("</tr>")
    trimmed = page[:head_end] + "</table></body></html>"
    df = cf.parse_month(trimmed, "sii", AT)
    assert df.is_empty() and list(df.columns) == list(cf.SCHEMA) and i >= 0


# ── AC-2：壞頁面 raise，不丟列 ─────────────────────────────────────────────
def test_ac2_bad_pages_raise() -> None:
    with pytest.raises(DataError, match="不是法說會月表"):
        cf.parse_month("<html>查詢過於頻繁</html>", "sii", AT)
    bad = _page().replace("115/10/14", "2026-10-14", 1)
    with pytest.raises(DataError, match="民國"):
        cf.parse_month(bad, "sii", AT)
    with pytest.raises(ValueError, match="時區"):
        cf.parse_month(_page(), "sii", AT.replace(tzinfo=None))


# ── AC-3：查詢參數與月份規劃 ────────────────────────────────────────────────
def test_ac3_query_params_and_month_plan() -> None:
    assert cf.query_params("otc", 2026, 10)["TYPEK"] == "otc"
    assert (
        cf.query_params("sii", 2026, 1)["year"] == "115"
        and cf.query_params("sii", 2026, 1)["month"] == "01"
    )
    with pytest.raises(ValueError):
        cf.query_params("rotc", 2026, 10)
    assert cf.months_to_fetch(dt.date(2026, 11, 20), 1) == [(2026, 11), (2026, 12)]
    assert cf.months_to_fetch(dt.date(2026, 12, 3), 2) == [(2026, 12), (2027, 1), (2027, 2)]


# ── AC-4：PG 表欄位與解析器同名同序 ───────────────────────────────────────────
def test_ac4_spec_matches_parser() -> None:
    spec = sch.spec_of("conference_calendar")
    assert spec.column_names == tuple(cf.SCHEMA)
    assert spec.pk == ("date", "stock_id", "conf_time")


def test_ac1_multi_day_conference_takes_the_first_day() -> None:
    page = _page().replace("115/10/14", "115/10/14 至 115/10/15", 1)
    df = cf.parse_month(page, "sii", AT)
    assert df.row(0, named=True)["date"] == dt.date(2026, 10, 14)


def test_ac2_identical_duplicate_rows_collapse_but_conflicts_raise() -> None:
    page = _page()
    i = page.rindex("<tr", 0, page.index(">1203<"))  # 1203 那一列的開頭
    j = page.index("</tr>", i) + len("</tr>")
    doubled = page[:j] + page[i:j] + page[j:]
    assert cf.parse_month(doubled, "sii", AT).height == 4  # 完全相同的那列只算一次
    conflict = doubled.replace("公司概況、財務報告及營運概況。", "另一場的擇要", 1)
    with pytest.raises(DataError, match="重複"):
        cf.parse_month(conflict, "sii", AT)
