"""TWSE 官方開休市日曆（market_holidays）：來源解析、清洗、與官方交易日曆建構/核對測試。

覆蓋民國封裝日期解析（"1150101"→2026-01-01）、closed 分類（「開始/最後交易日」註記非休市）、
缺欄大聲失敗、由官方休市日建前瞻交易日曆（平日−休市）、官方 vs 觀測缺口核對。純函數 +
錄製的 HTTP fixture（tests/fixtures/http/twse_holiday/），不連網。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.config import SourceRate
from twquant.conventions import TZ
from twquant.data import calendar as cal
from twquant.data import schemas
from twquant.data.clean import clean_market_holidays
from twquant.data.http import HttpClient, Response
from twquant.data.sources import raw_frame
from twquant.data.sources.twse_holiday import TwseHolidaySource
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError
from twquant.monitoring import api
from twquant.monitoring import snapshot as snap

_FIXTURE = Path(__file__).parent / "fixtures" / "http" / "twse_holiday" / "holiday_schedule.json"
_FIELDS = ["Name", "Date", "Weekday", "Description"]


class _Resp:
    """最小 Response：HttpClient 只用 status_code / text / content。"""

    def __init__(self, text: str, status: int = 200) -> None:
        self._text = text
        self.status_code = status

    @property
    def text(self) -> str:
        return self._text

    @property
    def content(self) -> bytes:
        return self._text.encode("utf-8")


def _client(text: str) -> HttpClient:
    def _transport(
        method: str, url: str, *, params: dict[str, str] | None, timeout: float
    ) -> Response:
        return _Resp(text)

    return HttpClient(
        source="twse_holiday",
        rate=SourceRate(max_requests=3, per_seconds=1.0),
        transport=_transport,
    )


# ── 清洗：民國日期 + closed 分類 ─────────────────────────────────────
def test_clean_roc_date_and_closed_classification() -> None:
    raw = raw_frame(
        _FIELDS,
        [
            ["中華民國開國紀念日", "1150101", "四", "依規定放假1日。"],
            ["國曆新年開始交易日", "1150102", "五", "國曆新年開始交易。"],
            ["農曆春節前最後交易日", "1150211", "三", "農曆春節前最後交易。<br>"],
            ["和平紀念日", "1150227", "五", "和平紀念日為2月28日適逢星期六，於2月27日補假。"],
            ["和平紀念日", "1150228", "六", "依規定放假1日。"],
        ],
    )
    out = clean_market_holidays(raw)
    assert out.height == 5
    by_date = {str(r["holiday_date"]): r for r in out.to_dicts()}
    assert by_date["2026-01-01"]["closed"] is True  # 民國 115 → 2026
    assert by_date["2026-01-01"]["year"] == 2026
    assert by_date["2026-01-01"]["weekday"] == "四"
    # 「開始交易 / 最後交易」註記＝交易日，不得標成休市（否則誤刪真實交易日）
    assert by_date["2026-01-02"]["closed"] is False
    assert by_date["2026-02-11"]["closed"] is False
    assert by_date["2026-02-27"]["closed"] is True  # 補假（週五）→ 休市
    assert by_date["2026-02-28"]["closed"] is True


def test_clean_rejects_bad_date_format() -> None:
    raw = raw_frame(_FIELDS, [["某節", "2026-01-01", "四", ""]])  # 非 7 碼民國碼
    with pytest.raises(DataError, match="民國格式"):
        clean_market_holidays(raw)


def test_clean_rejects_out_of_range_date() -> None:
    # 7 碼但月/日超範圍（月=13）：須走結構化 DataError（不得讓 pl.date 拋 ComputeError 繞過）。
    raw = raw_frame(_FIELDS, [["某節", "1151301", "日", ""]])
    with pytest.raises(DataError, match="無法解析"):
        clean_market_holidays(raw)


# ── 來源：fixture 解析 + 缺欄/空回應大聲失敗 ─────────────────────────
def test_source_parses_recorded_fixture() -> None:
    text = _FIXTURE.read_text(encoding="utf-8")
    raw = TwseHolidaySource(_client(text)).fetch()
    assert set(raw.columns) == set(_FIELDS)
    assert raw.height == 27
    clean = clean_market_holidays(raw)
    assert clean.height == 27
    # 錄製回應含 3 筆交易日註記（開始/最後交易日）
    assert clean.filter(~pl.col("closed")).height == 3


def test_source_raises_on_missing_field_or_empty() -> None:
    with pytest.raises(DataError, match="缺必要欄位"):
        TwseHolidaySource(_client('[{"Name":"x","Date":"1150101"}]')).fetch()
    with pytest.raises(DataError, match="回空"):
        TwseHolidaySource(_client("[]")).fetch()
    with pytest.raises(DataError, match="回空"):
        TwseHolidaySource(_client('{"stat":"error"}')).fetch()


# ── 官方交易日曆：平日 − 休市（前瞻，不需先有行情）─────────────────
def _holiday_wh(tmp_path: Path) -> Warehouse:
    wh = Warehouse(tmp_path)
    clean = clean_market_holidays(TwseHolidaySource(_client(_FIXTURE.read_text("utf-8"))).fetch())
    wh.write("clean", "market_holidays", clean, source="twse")
    return wh


def test_official_calendar_sessions(tmp_path: Path) -> None:
    tc = cal.official_calendar(_holiday_wh(tmp_path))
    assert not tc.is_session(dt.date(2026, 1, 1))  # 開國紀念日休市
    assert tc.is_session(dt.date(2026, 1, 2))  # 交易日註記 → 開市（週五）
    assert tc.is_session(dt.date(2026, 2, 11))  # 春節前最後交易日 → 開市（週三）
    assert not tc.is_session(dt.date(2026, 2, 27))  # 和平紀念日補假（週五）休市
    assert not tc.is_session(dt.date(2026, 2, 28))  # 週六
    assert not tc.is_session(dt.date(2026, 1, 3))  # 週六
    assert tc.is_session(dt.date(2026, 7, 13))  # 一般週一 → 開市
    assert tc.is_session(dt.date(2026, 6, 18)) and not tc.is_session(dt.date(2026, 6, 19))  # 端午
    assert tc.sessions[0].year == 2026 and tc.sessions[-1].year == 2026  # 整年（含未來）


def test_official_calendar_requires_ingest(tmp_path: Path) -> None:
    with pytest.raises(DataError, match="ingest-holidays"):
        cal.official_calendar(Warehouse(tmp_path))


def test_official_calendar_multi_year(tmp_path: Path) -> None:
    # 官方日曆涵蓋兩個年度（ROC 115=2026、116=2027）→ 前瞻日曆跨年建構。
    raw = raw_frame(
        _FIELDS,
        [
            ["開國紀念日", "1150101", "四", "放假"],  # 2026-01-01 休市
            ["開國紀念日", "1160101", "五", "放假"],  # 2027-01-01 休市
        ],
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "market_holidays", clean_market_holidays(raw), source="twse")
    tc = cal.official_calendar(wh)
    assert {d.year for d in tc.sessions} == {2026, 2027}
    assert not tc.is_session(dt.date(2026, 1, 1)) and not tc.is_session(dt.date(2027, 1, 1))
    assert tc.is_session(dt.date(2026, 1, 2)) and tc.is_session(dt.date(2027, 1, 4))


def test_official_holidays_none_when_only_tmp(tmp_path: Path) -> None:
    # 中斷寫入只留 .parquet.tmp：official_holidays 須回 None（而非讓 SQL 拋 → endpoint 500）。
    wh = Warehouse(tmp_path)
    part = wh.root / "clean" / "market_holidays" / "date=_all"
    part.mkdir(parents=True)
    (part / "part-twse.parquet.tmp").write_bytes(b"partial")
    assert cal.official_holidays(wh) is None
    assert snap.holiday_calendar(wh) == {"available": False}


# ── 官方 vs 觀測核對 ─────────────────────────────────────────────────
def _write_prices(wh: Warehouse, dates: list[dt.date]) -> None:
    n = len(dates)
    df = pl.DataFrame(
        {
            "date": dates,
            "stock_id": ["2330"] * n,
            "open": [100.0] * n,
            "high": [100.0] * n,
            "low": [100.0] * n,
            "close": [100.0] * n,
            "volume": [1000] * n,
            "amount": [1e8] * n,
            "limit_up_price": pl.Series([None] * n, dtype=pl.Float64),
            "limit_down_price": pl.Series([None] * n, dtype=pl.Float64),
            "available_at": pl.Series(
                [dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ) for d in dates],
                dtype=schemas.TW_DT,
            ),
        }
    )
    wh.write("clean", "prices_daily", df, source="twse")


def test_cross_check_flags_missing_open_day(tmp_path: Path) -> None:
    wh = _holiday_wh(tmp_path)
    # 觀測 07-13(一)、07-15(三)，故意漏 07-14(二，官方開市) → missing 含 07-14
    _write_prices(wh, [dt.date(2026, 7, 13), dt.date(2026, 7, 15)])
    chk = cal.cross_check_official(wh)
    assert dt.date(2026, 7, 14) in chk["missing"]  # type: ignore[operator]
    assert chk["unexpected"] == []
    assert chk["years"] == [2026]


def test_cross_check_flags_unexpected_trading_on_holiday(tmp_path: Path) -> None:
    wh = _holiday_wh(tmp_path)
    # 觀測含 01-01（官方休市）→ unexpected 含 01-01
    _write_prices(wh, [dt.date(2026, 1, 1), dt.date(2026, 1, 2)])
    chk = cal.cross_check_official(wh)
    assert dt.date(2026, 1, 1) in chk["unexpected"]  # type: ignore[operator]


def test_cross_check_empty_without_prices(tmp_path: Path) -> None:
    chk = cal.cross_check_official(_holiday_wh(tmp_path))
    assert chk["range"] is None
    assert chk["years"] == [2026]


def test_cross_check_ignores_out_of_scope_year(tmp_path: Path) -> None:
    # 官方只涵蓋 2026，但觀測含 2025 交易日 → 只在涵蓋年度內比對，不把 2025 誤標 unexpected。
    wh = _holiday_wh(tmp_path)
    _write_prices(wh, [dt.date(2025, 7, 14), dt.date(2026, 7, 8)])
    chk = cal.cross_check_official(wh)
    assert chk["years"] == [2026]
    assert chk["unexpected"] == []  # 移除涵蓋年度守衛則 2025-07-14 會出現在此


# ── 監控快照 / API 端點 ─────────────────────────────────────────────
def test_snapshot_holiday_calendar_and_endpoint(tmp_path: Path) -> None:
    wh = _holiday_wh(tmp_path)
    _write_prices(wh, [dt.date(2026, 7, 8)])  # 一個 2026 開市日
    hc = snap.holiday_calendar(wh)
    assert hc["available"] is True
    assert hc["sessions"] == 243  # 2026 台股開市日數（fixture 對應）
    assert hc["first"] == "2026-01-02" and hc["last"] == "2026-12-31"
    closed = hc["closed"]
    assert isinstance(closed, list) and len(closed) == 24
    assert {"date", "weekday", "name"} <= set(closed[0])
    cc = hc["cross_check"]
    assert isinstance(cc, dict)
    assert cc["missing"] == [] and cc["unexpected"] == []  # 2026-07-08 官方亦為開市日
    assert api.get_holiday_calendar(wh)["available"] is True  # 端點薄包同一結果


def test_snapshot_holiday_calendar_absent(tmp_path: Path) -> None:
    assert snap.holiday_calendar(Warehouse(tmp_path)) == {"available": False}
