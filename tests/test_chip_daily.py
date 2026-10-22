"""T-310／T-313：官方每日當沖、借券賣出、處置、注意（直寫 PG）與個股估值（資料湖 → 鏡射）。

fixture（`tests/fixtures/http/chip_daily/`）是 2026-09-25 實測回應的裁切：結構原樣，只留挑過的列，
「筆數」欄（證交所 `total`、櫃買 `totalCount`）改成裁切後的列數——解析器拿它核對有沒有被截斷。
`pg_history_expected.json` 是同一 (日, 檔) 在 PG 裡的 FinMind 歷史值，錄一次；測試不連網。

最重要的一件事：官方每日數字要與 FinMind 歷史**逐位相同**（同欄、同單位），否則表會在接上每日增量
那天出現斷階。唯一的刻意差異是 `sbl_covering`（官方沒有這一欄 → null；FinMind 全史補 0），見 D-157。
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.config import ChipDailyConfig, SourceRate, SystemConfig, load_config
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.clean import (
    CLEANERS,
    TPEX_CLEANERS,
    clean_tpex_valuation,
    clean_valuation,
)
from twquant.data.http import HttpClient, SlidingWindowLimiter, shared_limiter
from twquant.data.ingest.orchestrator import IngestOutcome, trading_evidence
from twquant.data.ingest.ports import FetchCleanSource
from twquant.data.serving import direct_ingest as di
from twquant.data.serving import schema as sch
from twquant.data.sources import alerts, official_chip
from twquant.data.sources.tpex import TpexSource
from twquant.data.sources.twse_rwd import TwseRwdSource
from twquant.errors import DataError

_FIX = Path(__file__).parent / "fixtures" / "http" / "chip_daily"
_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "system.yaml"
_D = dt.date(2026, 9, 18)
_UNIVERSE = re.compile("^[1-9][0-9]{3}$")


def _load(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((_FIX / name).read_text(encoding="utf-8"))
    return payload


def _text(name: str) -> str:
    return (_FIX / name).read_text(encoding="utf-8")


def _expected(table: str, day: dt.date | None = None) -> dict[str, dict[str, Any]]:
    """PG 的 FinMind 歷史值（錄下的），{stock_id: 列}；date 轉回 dt.date。"""
    out: dict[str, dict[str, Any]] = {}
    for row in _load("pg_history_expected.json")[table]:
        r = dict(row)
        r["date"] = dt.date.fromisoformat(r["date"])
        if day is None or r["date"] == day:
            out[r["stock_id"]] = r
    return out


# ── 不連網的 HttpClient ────────────────────────────────────────────────────


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


def _client(
    body: str | Callable[[str, dict[str, str] | None], str],
    calls: list[tuple[str, dict[str, str] | None]] | None = None,
    status: int = 200,
) -> HttpClient:
    def transport(method: str, url: str, *, params: dict[str, str] | None, timeout: float) -> _Resp:
        if calls is not None:
            calls.append((url, params))
        text = body(url, params) if callable(body) else body
        return _Resp(status, text)

    return HttpClient(
        source="test",
        rate=SourceRate(max_requests=10, per_seconds=1.0),
        clock=_Clock(),
        transport=transport,
    )


def _chip_source(market: str, body: str, calls: list[Any] | None = None) -> Any:
    return official_chip.OfficialChipSource(market, _client(body, calls), _UNIVERSE)


# ── AC-310-1 當沖：官方 → 與 FinMind 歷史逐位相同 ─────────────────────────────


def test_ac310_1_twse_daytrade_equals_finmind_history() -> None:
    """證交所 TWTB4U 2026-09-18：四碼股逐欄等於 PG 的 FinMind 值；ETF、0050、六碼 TDR 不收。"""
    rows = official_chip.daytrade_rows("twse", _load("twse_twtb4u_20260918.json"), _D, _UNIVERSE)
    got = {r["stock_id"]: r for r in rows}
    assert set(got) == {"1233", "1439", "2330", "9103"}, "00400A、0050、911868 在宇宙外"
    expected = _expected("daytrade_daily", _D)
    for sid, row in got.items():
        assert row == expected[sid], sid
    assert got["1439"]["sell_first_suspended"] is True, "證交所標 'Y'"
    assert got["1233"]["daytrade_shares"] == 0, "0 股是真的 0，不是缺值"


def test_ac310_1_tpex_daytrade_equals_finmind_history() -> None:
    """櫃買 intraday/stat 2026-09-18：同上；全形「＊」＝暫停先賣後買。"""
    rows = official_chip.daytrade_rows("tpex", _load("tpex_intraday_20260918.json"), _D, _UNIVERSE)
    got = {r["stock_id"]: r for r in rows}
    assert set(got) == {"1240", "1595", "6488"}, "00411A、006201 在宇宙外"
    expected = _expected("daytrade_daily", _D)
    for sid, row in got.items():
        assert row == expected[sid], sid
    assert got["1595"]["sell_first_suspended"] is True
    avg = got["6488"]["daytrade_buy_amount"] / got["6488"]["daytrade_shares"]
    assert 900 < avg < 950, "股與元：均價落在當日股價附近；把股當張會差 1,000 倍"


def test_ac310_1_frame_is_valid_and_available_at_21() -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []
    df = _chip_source("twse", _text("twse_twtb4u_20260918.json"), calls).load("daytrade_daily", _D)
    assert df.columns == list(schemas.get_schema("daytrade_daily").columns)
    assert df["available_at"].to_list()[0] == dt.datetime(2026, 9, 18, 21, 0, tzinfo=TZ)
    assert calls == [
        (
            "https://www.twse.com.tw/rwd/zh/dayTrading/TWTB4U",
            {"selectType": "All", "response": "json", "date": "20260918"},
        )
    ]


def test_ac310_1_tpex_daytrade_uses_get_with_site_params() -> None:
    """網站用 POST；GET 同參數實測回應逐字相同，所以不必替 HttpClient 加 POST。"""
    calls: list[tuple[str, dict[str, str] | None]] = []
    _chip_source("tpex", _text("tpex_intraday_20260918.json"), calls).load("daytrade_daily", _D)
    assert calls == [
        (
            "https://www.tpex.org.tw/www/zh-tw/intraday/stat",
            {"type": "Daily", "response": "json", "date": "2026/09/18"},
        )
    ]


@pytest.mark.parametrize(("market", "bad"), [("twse", "＊"), ("tpex", "Y"), ("twse", "N")])
def test_ac310_1_unknown_flag_raises(market: str, bad: str) -> None:
    """只認各市場自己的標記：證交所 'Y'、櫃買 '＊'；對方的標記也算未知。"""
    name = "twse_twtb4u_20260918.json" if market == "twse" else "tpex_intraday_20260918.json"
    payload = _load(name)
    table = next(t for t in payload["tables"] if "證券代號" in t.get("fields", []))
    row = next(r for r in table["data"] if r[0] in ("2330", "6488"))
    row[2] = bad
    with pytest.raises(DataError, match="未知值"):
        official_chip.daytrade_rows(market, payload, _D, _UNIVERSE)


@pytest.mark.parametrize("bad", ["", "--", "4,805,000.5", "4.805.000"])
def test_ac310_1_non_integer_shares_raise_not_zero(bad: str) -> None:
    payload = _load("twse_twtb4u_20260918.json")
    row = next(r for r in payload["tables"][1]["data"] if r[0] == "2330")
    row[3] = bad
    with pytest.raises(DataError, match="整數"):
        official_chip.daytrade_rows("twse", payload, _D, _UNIVERSE)


@pytest.mark.parametrize(
    ("market", "table", "name"),
    [
        ("twse", "daytrade_daily", "twse_twtb4u_20260925_holiday.json"),
        ("tpex", "daytrade_daily", "tpex_intraday_20260925_holiday.json"),
        ("twse", "sbl_short_daily", "twse_twt93u_20260925_holiday.json"),
        ("tpex", "sbl_short_daily", "tpex_sbl_20260925_holiday.json"),
    ],
)
def test_ac310_1_holiday_is_no_data_not_empty_success(market: str, table: str, name: str) -> None:
    """2026-09-25（中秋）：四個端點都回 stat OK＋空表——必須 raise，不能當「成功 0 列」。"""
    with pytest.raises(DataError, match="空的"):
        _chip_source(market, _text(name)).load(table, dt.date(2026, 9, 25))


def test_ac310_1_response_for_another_day_raises() -> None:
    with pytest.raises(DataError, match="不把別天"):
        _chip_source("twse", _text("twse_twtb4u_20260918.json")).load(
            "daytrade_daily", dt.date(2026, 9, 17)
        )


def test_ac310_1_truncated_or_changed_response_raises() -> None:
    payload = _load("tpex_intraday_20260918.json")
    table = next(t for t in payload["tables"] if "證券代號" in t.get("fields", []))
    table["totalCount"] += 1
    with pytest.raises(DataError, match="截斷"):
        official_chip.daytrade_rows("tpex", payload, _D, _UNIVERSE)
    payload = _load("tpex_intraday_20260918.json")
    table = next(t for t in payload["tables"] if "證券代號" in t.get("fields", []))
    table["fields"][3] = "當日沖銷交易成交張數"
    with pytest.raises(DataError, match="欄位與預期不同"):
        official_chip.daytrade_rows("tpex", payload, _D, _UNIVERSE)


@pytest.mark.parametrize(
    ("status", "text"), [(200, "<html>Please enable JavaScript</html>"), (428, "<html>x</html>")]
)
def test_ac310_1_waf_page_raises(status: int, text: str) -> None:
    src = official_chip.OfficialChipSource("twse", _client(text, status=status), _UNIVERSE)
    with pytest.raises(DataError, match="anti_bot_suspected"):
        src.load("daytrade_daily", _D)


def test_ac310_1_no_universe_rows_raises() -> None:
    payload = _load("twse_twtb4u_20260918.json")
    with pytest.raises(DataError, match="宇宙內 0 列"):
        official_chip.daytrade_rows("twse", payload, _D, re.compile("^Z{9}$"))


# ── AC-310-2 借券：官方 → 與 FinMind 歷史逐位相同（股，不 ×1000）─────────────────


_SBL_CORE = (
    "sbl_prev_balance",
    "sbl_sell",
    "sbl_return",
    "sbl_adjust",
    "sbl_balance",
    "sbl_next_quota",
)


@pytest.mark.parametrize(
    ("market", "name", "ids"),
    [
        ("twse", "twse_twt93u_20260918.json", {"1213", "2330", "2887", "9103"}),
        ("tpex", "tpex_sbl_20260918.json", {"1799", "5904", "6488"}),
    ],
)
def test_ac310_2_sbl_equals_finmind_history(market: str, name: str, ids: set[str]) -> None:
    """借券賣出六欄（前日、賣出、還券、調整、當日餘額、次一營業日可限額）逐位等於 FinMind；ETF 不收。"""
    rows = official_chip.sbl_rows(market, _load(name), _D, _UNIVERSE)
    got = {r["stock_id"]: r for r in rows}
    assert set(got) == ids
    expected = _expected("sbl_short_daily", _D)
    for sid, row in got.items():
        for col in ("date", "stock_id", *_SBL_CORE):
            assert row[col] == expected[sid][col], (sid, col)
        # 唯一的刻意差異（D-157）：官方沒有這一欄 → null；FinMind 全史 494 萬列都補 0。
        assert row["sbl_covering"] is None
        assert expected[sid]["sbl_covering"] == 0
    # 不是融券那一段：2330 融券今日餘額 15,000（股）、借券當日餘額 15,672,514
    if market == "twse":
        assert got["2330"]["sbl_balance"] == 15_672_514


def test_ac310_2_request_params() -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []
    _chip_source("twse", _text("twse_twt93u_20260918.json"), calls).load("sbl_short_daily", _D)
    _chip_source("tpex", _text("tpex_sbl_20260918.json"), calls).load("sbl_short_daily", _D)
    assert calls == [
        (
            "https://www.twse.com.tw/rwd/zh/marginTrading/TWT93U",
            {"response": "json", "date": "20260918"},
        ),
        (
            "https://www.tpex.org.tw/www/zh-tw/margin/sbl",
            {"response": "json", "date": "2026/09/18"},
        ),
    ]


def test_ac310_2_adjust_may_be_negative_and_frame_validates() -> None:
    payload = _load("twse_twt93u_20260918.json")
    row = next(r for r in payload["data"] if r[0] == "2330")
    row[11] = "-1,000"
    row[12] = "15,671,514"
    rows = official_chip.sbl_rows("twse", payload, _D, _UNIVERSE)
    df, _ = alerts.build_frame("sbl_short_daily", rows)
    assert df.filter(pl.col("stock_id") == "2330")["sbl_adjust"].item() == -1_000
    assert df["sbl_covering"].null_count() == df.height


@pytest.mark.parametrize("market", ["twse", "tpex"])
def test_ac310_2_same_named_columns_are_checked_by_position(market: str) -> None:
    """融券與借券兩段同名，只能靠位置——欄名改一個字（或兩段對調）就 raise，不猜。"""
    name = "twse_twt93u_20260918.json" if market == "twse" else "tpex_sbl_20260918.json"
    payload = _load(name)
    table = payload if market == "twse" else payload["tables"][0]
    table["fields"][9] = "賣出"
    with pytest.raises(DataError, match="欄位與預期不同"):
        official_chip.sbl_rows(market, payload, _D, _UNIVERSE)


def test_ac310_2_twse_total_row_is_skipped_other_blank_codes_raise() -> None:
    """TWT93U 最後一列是「合計」（代號空白，算在 total 裡）→ 略過；其他代號空白的列 → raise（丟掉就是丟掉一檔）。"""
    payload = _load("twse_twt93u_20260918.json")
    assert payload["data"][-1][:2] == ["", "合計"]
    rows = official_chip.sbl_rows("twse", payload, _D, _UNIVERSE)
    assert all(r["stock_id"] for r in rows)
    payload["data"][-1][1] = "小計"
    with pytest.raises(DataError, match="不是「合計」"):
        official_chip.sbl_rows("twse", payload, _D, _UNIVERSE)


def test_ac310_2_twse_groups_are_checked() -> None:
    payload = _load("twse_twt93u_20260918.json")
    payload["groups"][1], payload["groups"][2] = payload["groups"][2], payload["groups"][1]
    with pytest.raises(DataError, match="欄組"):
        official_chip.sbl_rows("twse", payload, _D, _UNIVERSE)


# ── AC-310-3 公告每日查詢窗 ───────────────────────────────────────────────────


def test_ac310_3_alert_window() -> None:
    d = dt.date(2026, 9, 24)
    assert alerts.alert_window("attention", d, 14, 14) == (dt.date(2026, 9, 10), d)
    assert alerts.alert_window("disposition", d, 14, 14) == (
        dt.date(2026, 9, 10),
        dt.date(2026, 10, 8),
    )
    with pytest.raises(ValueError):
        alerts.alert_window("punish", d, 14, 14)


def test_ac310_3_same_day_window_misses_todays_disposition() -> None:
    """處置依「處置期間」查：3443 於 09-24 公告、09-25 起處置——查 [09-24, 09-24] 抓不到它。"""
    same_day = alerts.twse_disposition_rows(_load("twse_punish_20260924_same_day.json")).rows
    ahead = alerts.twse_disposition_rows(_load("twse_punish_20260924_lookahead.json")).rows
    d = dt.date(2026, 9, 24)
    assert not [r for r in same_day if r["date"] == d]
    todays = [r for r in ahead if r["date"] == d]
    assert [r["stock_id"] for r in todays] == ["3443"]
    assert todays[0]["effective_start"] > d
    tpex = alerts.tpex_disposition_rows(_load("tpex_disposal_20260924_lookahead.json")).rows
    assert len([r for r in tpex if r["date"] == d]) == 6


def test_ac310_3_alert_source_requests_the_window() -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []

    def body(url: str, params: dict[str, str] | None) -> str:
        return _text(
            "twse_punish_20260924_lookahead.json"
            if url.endswith("punish")
            else "twse_notice_20260924.json"
        )

    src = alerts.AlertSource("twse", _client(body, calls), 14, 14)
    d = dt.date(2026, 9, 24)
    disp = src.load("disposition_stocks", d)
    attn = src.load("attention_stocks", d)
    assert calls == [
        (
            "https://www.twse.com.tw/rwd/zh/announcement/punish",
            {"startDate": "20260910", "endDate": "20261008", "response": "json"},
        ),
        (
            "https://www.twse.com.tw/rwd/zh/announcement/notice",
            {"startDate": "20260910", "endDate": "20260924", "response": "json"},
        ),
    ]
    assert disp.height == 8 and "3443" in disp["stock_id"].to_list()
    assert attn.height == 18
    assert disp.columns == list(schemas.get_schema("disposition_stocks").columns)


# ── AC-310-4 註冊、直寫、分辨休市 ─────────────────────────────────────────────


def test_ac310_4_registry_routes_direct_tables_to_pg() -> None:
    import twquant.data.ingest.builtins as b
    from twquant.data.ingest.registry import get

    for market, tag in b.CHIP_SOURCES.items():
        spec = get(tag)
        assert (spec.store, spec.proves_trading, spec.cadence) == ("pg", True, "daily")
        assert spec.build(load_config(_CONFIG, SystemConfig)).tables == list(
            official_chip.CHIP_TABLES
        ), market
    for tag in b.ALERT_SOURCES.values():
        spec = get(tag)
        assert (spec.store, spec.proves_trading) == ("pg", False)
    assert get("twse").store == "lake" and get("tpex").store == "lake"
    cfg = load_config(_CONFIG, SystemConfig)
    produced = {t for tag in b.CHIP_SOURCES.values() for t in get(tag).build(cfg).tables}
    produced |= {t for tag in b.ALERT_SOURCES.values() for t in get(tag).build(cfg).tables}
    assert produced == set(sch.DIRECT_PG_TABLES), "四張直寫表每一張都有每日來源"


def test_ac310_4_announcements_do_not_prove_a_trading_day() -> None:
    """休市日：行情全失敗、公告照樣查得到 → 不是「這天有交易卻沒抓到」（T-276 的分辨不能被公告騙過）。"""
    proves = {"twse": True, "tpex": True, "twse_alerts": False, "tpex_alerts": False}
    holiday = [
        IngestOutcome("twse", "prices_daily", "failed", 0, "stat='很抱歉，沒有符合條件的資料!'"),
        IngestOutcome("tpex", "prices_daily", "failed", 0, "上櫃資料表為空"),
        IngestOutcome("twse_alerts", "disposition_stocks", "success", 7, ""),
        IngestOutcome("tpex_alerts", "attention_stocks", "success", 20, ""),
    ]
    assert trading_evidence(holiday, proves) == []
    trading_day = [*holiday, IngestOutcome("twse", "margin_daily", "success", 1297, "")]
    assert [o.table for o in trading_evidence(trading_day, proves)] == ["margin_daily"]
    with pytest.raises(KeyError):
        trading_evidence([IngestOutcome("nope", "x", "success", 1, "")], proves)


def test_ac310_4_sink_rejects_non_direct_tables() -> None:
    sink = di.PgDirectSink(conn=None, schema="serving")
    with pytest.raises(DataError, match="不是直寫"):
        sink.write("clean", "prices_daily", pl.DataFrame())


# ── AC-310-5 每日補抓計畫（當天不抓；T−1 首次、T−2／T−3 重抓；缺日補） ────────────


_S = [dt.date(2026, 9, d) for d in (24, 23, 22, 21, 18, 17, 16, 15, 14, 11, 10, 9)]  # 新到舊


def _full_counts(
    skip: set[tuple[str, dt.date, str]] | None = None,
) -> dict[str, dict[tuple[dt.date, str], int]]:
    """每張表、每個交易日、兩個市場都有 900 列；skip 裡的 (表, 日, 市場) 為 0（缺日）。"""
    gone = skip or set()
    return {
        t: {(d, m): 900 for d in _S for m in ("TWSE", "TPEX") if (t, d, m) not in gone}
        for t in ("daytrade_daily", "sbl_short_daily")
    }


def test_ac310_5_offsets_and_first_publication() -> None:
    plan = di.plan_from(_S, _full_counts(), ChipDailyConfig(), live=True)
    got = {(f.table, f.market, f.day, f.first, f.reason) for f in plan}
    t1, t2, t3 = _S[0], _S[1], _S[2]
    for m in ("twse", "tpex"):
        assert ("daytrade_daily", m, t1, True, "T-1") in got, "T+1 那次是首次值"
        assert ("daytrade_daily", m, t2, False, "T-2") in got, "T+2 定案（D-154 §三）"
        assert ("daytrade_daily", m, t3, False, "T-3") in got
        assert ("sbl_short_daily", m, t1, False, "T-1") in got, "22:30 第二次更新之後"
    assert len(plan) == 8, "沒有缺日時：當沖 3 天＋借券 1 天，兩市"
    assert all(f.day < dt.date(2026, 9, 25) for f in plan), "當天不抓"


def test_ac310_5_gap_days_are_filled_per_market() -> None:
    skip = {("sbl_short_daily", _S[4], "TPEX"), ("daytrade_daily", _S[6], "TWSE")}
    plan = di.plan_from(_S, _full_counts(skip), ChipDailyConfig(), live=True)
    gaps = {(f.table, f.market, f.day) for f in plan if f.reason == "缺日"}
    assert gaps == {("sbl_short_daily", "tpex", _S[4]), ("daytrade_daily", "twse", _S[6])}
    assert not any(f.first for f in plan if f.reason == "缺日"), (
        "缺日補抓不當首次值（可能已是定案值）"
    )


def test_ac310_5_rerunning_a_past_day_does_not_record_first_publication() -> None:
    """手動重跑過去的日子（live=False）照樣抓、照樣覆寫，但不存首次值——那時抓到的多半已是定案值。"""
    plan = di.plan_from(_S, _full_counts(), ChipDailyConfig(), live=False)
    assert len(plan) == 8
    assert not any(f.first for f in plan)


def test_ac310_5_gap_window_is_bounded() -> None:
    cfg = ChipDailyConfig(catchup_trading_days=5)
    plan = di.plan_from(_S, _full_counts({("daytrade_daily", _S[8], "TPEX")}), cfg, live=True)
    assert not [f for f in plan if f.reason == "缺日"], "第 9 個交易日前的缺口超出補抓窗"


def test_t432_same_day_fetch_after_evening_cutoff_without_first_publication() -> None:
    """D-210 §二.3：21:00 以後那班把當天的當沖、借券也抓進來（T-0），但首次值仍只在隔天 T-1 那次存。"""
    today = dt.date(2026, 9, 25)
    plan = di.plan_from(_S, _full_counts(), ChipDailyConfig(), live=True, today=today)
    got = {(f.table, f.market, f.day, f.first, f.reason) for f in plan}
    for m in ("twse", "tpex"):
        assert ("daytrade_daily", m, today, False, "T-0") in got
        assert ("sbl_short_daily", m, today, False, "T-0") in got
        assert ("daytrade_daily", m, _S[0], True, "T-1") in got, "首次值還是 T+1 那次"
    assert len(plan) == 12
    assert di.plan_from(_S, _full_counts(), ChipDailyConfig(), live=True) == di.plan_from(
        _S, _full_counts(), ChipDailyConfig(), live=True, today=None
    )
    cfg = ChipDailyConfig()
    assert cfg.same_day_after == dt.time(21, 0)
    assert ChipDailyConfig(same_day_after="20:45").same_day_after == dt.time(20, 45)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad",
    [
        {"refetch_offsets": {"daytrade_daily": (0, 2), "sbl_short_daily": (1,)}},
        {"refetch_offsets": {"daytrade_daily": (1,)}},
        {"refetch_offsets": {"daytrade_daily": (), "sbl_short_daily": (1,)}},
        {"universe_pattern": "^[1-9"},
        {"catchup_trading_days": 0},
    ],
)
def test_ac310_5_config_rejects_bad_values(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ChipDailyConfig(**bad)


def test_ac310_5_system_yaml_matches_defaults() -> None:
    """system.yaml 是權威值；與 pydantic 預設相同（改一邊忘了另一邊會在這裡被抓到）。"""
    assert load_config(_CONFIG, SystemConfig).chip_daily == ChipDailyConfig()


# ── AC-310-6 PG 往返（scratch schema；未設 TWQUANT_PG_DSN 即跳過）──────────────────


def _pg_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        return psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")


def _daytrade_frame(market: str) -> pl.DataFrame:
    name = "twse_twtb4u_20260918.json" if market == "twse" else "tpex_intraday_20260918.json"
    df, _ = alerts.build_frame(
        "daytrade_daily", official_chip.daytrade_rows(market, _load(name), _D, _UNIVERSE)
    )
    return df


def test_ac310_6_pg_direct_sink_first_publication_and_revision() -> None:
    conn = _pg_or_skip()
    test_schema = "serving_test_t310"
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        sch.ensure_tables(conn, sch.DIRECT_INGEST_SPECS, test_schema)
        sch.ensure_tables(
            conn, (sch.spec_of("stock_meta"), sch.spec_of("prices_daily")), test_schema
        )
        with conn.cursor() as cur:
            cur.executemany(
                f'INSERT INTO "{test_schema}".stock_meta (stock_id, market) VALUES (%s, %s)',
                [("1233", "TWSE"), ("1439", "TWSE"), ("2330", "TWSE"), ("9103", "TWSE")],
            )
        conn.commit()

        twse = _daytrade_frame("twse")
        # T+1：首次抓 → daytrade_daily 與 daytrade_first_pub 各 4 列
        di.PgDirectSink(conn, test_schema, keep_first=True, market="TWSE").write(
            "clean", "daytrade_daily", twse, source="twse_chip"
        )
        # T+2：定案值與首次不同（2330 多 1,000 股）→ 只改 daytrade_daily
        revised = twse.with_columns(
            pl.when(pl.col("stock_id") == "2330")
            .then(pl.col("daytrade_shares") + 1_000)
            .otherwise(pl.col("daytrade_shares"))
            .alias("daytrade_shares")
        )
        sink = di.PgDirectSink(conn, test_schema, market="TWSE")
        sink.write("clean", "daytrade_daily", revised, source="twse_chip")
        sink.write("clean", "daytrade_daily", revised, source="twse_chip")  # 冪等
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{test_schema}".daytrade_daily')
            assert cur.fetchone()[0] == 4
            cur.execute(
                f'SELECT daytrade_shares, fetched_at IS NOT NULL FROM "{test_schema}".daytrade_first_pub '
                "WHERE stock_id = '2330'"
            )
            assert cur.fetchall() == [(4_805_000, True)], "首次值不被重抓覆寫"
            rev = di.daytrade_revision(cur, test_schema, _D)
        assert (rev.compared, rev.changed, rev.shares_abs_diff) == (4, 1, 1_000)
        assert rev.examples == ["2330 4,805,000→4,806,000"]

        # 重抓少了一檔：不刪，記一筆 note
        sink = di.PgDirectSink(conn, test_schema, market="TWSE")
        sink.write("clean", "daytrade_daily", revised.filter(pl.col("stock_id") != "9103"))
        assert sink.notes and "9103" in sink.notes[0]
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{test_schema}".daytrade_daily')
            assert cur.fetchone()[0] == 4
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        conn.close()


def test_ac310_6_changed_announcement_is_refused_and_nothing_written() -> None:
    """D-154 §二.6：窗內舊公告與 PG 內容不同 → raise、整批不寫；連線仍可用（rollback）。"""
    conn = _pg_or_skip()
    test_schema = "serving_test_t310b"
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        sch.ensure_tables(conn, sch.DIRECT_INGEST_SPECS, test_schema)
        rows = alerts.twse_disposition_rows(_load("twse_punish_20260924_lookahead.json")).rows
        df, _ = alerts.build_frame("disposition_stocks", rows)
        sink = di.PgDirectSink(conn, test_schema)
        sink.write("clean", "disposition_stocks", df)
        sink.write("clean", "disposition_stocks", df)  # 同內容：冪等
        changed = df.with_columns(
            pl.when(pl.col("stock_id") == "3443")
            .then(pl.lit("改過的內容"))
            .otherwise(pl.col("measure"))
            .alias("measure")
        )
        with pytest.raises(DataError, match="內容不同"):
            sink.write("clean", "disposition_stocks", changed)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT measure FROM \"{test_schema}\".disposition_stocks WHERE stock_id = '3443'"
            )
            assert cur.fetchone()[0] != "改過的內容"
            cur.execute(f'SELECT count(*) FROM "{test_schema}".disposition_stocks')
            assert cur.fetchone()[0] == df.height
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        conn.close()


def test_ac310_6_plan_reads_sessions_and_market_counts_from_pg() -> None:
    conn = _pg_or_skip()
    test_schema = "serving_test_t310c"
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        sch.ensure_tables(conn, sch.DIRECT_INGEST_SPECS, test_schema)
        sch.ensure_tables(
            conn, (sch.spec_of("stock_meta"), sch.spec_of("prices_daily")), test_schema
        )
        days = sorted(_S[:4])  # 09-21~09-24
        with conn.cursor() as cur:
            cur.executemany(
                f'INSERT INTO "{test_schema}".stock_meta (stock_id, market) VALUES (%s, %s)',
                [("2330", "TWSE"), ("6488", "TPEX")],
            )
            cur.executemany(
                f'INSERT INTO "{test_schema}".prices_daily (date, stock_id) VALUES (%s, %s)',
                [(d, s) for d in [*days, dt.date(2026, 9, 25)] for s in ("2330", "6488")],
            )
            # 當沖：09-21 兩市都有、09-22 只有上市；借券：全部都有
            cur.executemany(
                f'INSERT INTO "{test_schema}".daytrade_daily (date, stock_id, daytrade_shares, '
                "daytrade_buy_amount, daytrade_sell_amount, sell_first_suspended) "
                "VALUES (%s, %s, 0, 0, 0, false)",
                [(days[0], "2330"), (days[0], "6488"), (days[1], "2330")],
            )
            cur.executemany(
                f'INSERT INTO "{test_schema}".sbl_short_daily (date, stock_id, sbl_prev_balance, '
                "sbl_sell, sbl_return, sbl_adjust, sbl_balance) VALUES (%s, %s, 0, 0, 0, 0, 0)",
                [(d, s) for d in days for s in ("2330", "6488")],
            )
            cfg = ChipDailyConfig(catchup_trading_days=4)
            plan = di.plan_chip_fetches(cur, test_schema, dt.date(2026, 9, 25), cfg, live=True)
        conn.commit()
        got = {(f.table, f.market, f.day, f.reason) for f in plan}
        # 09-25 本身是「今天」：不抓；09-22 上櫃當沖缺 → 在 T-3 的例行重抓裡；09-24、09-23 當沖本來就要抓
        assert ("daytrade_daily", "tpex", days[1], "T-3") in got
        assert ("daytrade_daily", "twse", days[0], "缺日") not in got
        assert not [f for f in plan if f.day == dt.date(2026, 9, 25)]
        assert {f.day for f in plan if f.table == "sbl_short_daily"} == {days[3]}
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        conn.close()


def test_ac310_6_first_pub_is_bookkeeping_not_a_data_table() -> None:
    """首次值表不在 DIRECT_PG_SPECS（T-309 的四張資料表）也不在鏡射清單；每日直寫會建它。"""
    names = {s.name for s in sch.DIRECT_INGEST_SPECS}
    assert names == {*sch.DIRECT_PG_TABLES, "daytrade_first_pub"}
    assert "daytrade_first_pub" not in sch.MIRROR_TABLES
    assert "daytrade_first_pub" in sch.ALL_TABLES
    first = sch.DAYTRADE_FIRST_PUB_SPEC.column_names
    assert first[:-1] == sch.spec_of("daytrade_daily").column_names, (
        "欄位與 daytrade_daily 同名同序"
    )
    assert first[-1] == "fetched_at"


# ── AC-310-7 同一交易所共用限流器 ─────────────────────────────────────────────


def test_ac310_7_two_clients_share_one_window() -> None:
    clock = _Clock()
    limiter = SlidingWindowLimiter(3, 5.0, clock)
    times: list[float] = []

    def transport(method: str, url: str, *, params: dict[str, str] | None, timeout: float) -> _Resp:
        times.append(clock.t)
        return _Resp(200, "{}")

    rate = SourceRate(max_requests=3, per_seconds=5.0)
    a = HttpClient(source="twse_rwd", rate=rate, clock=clock, transport=transport, limiter=limiter)
    b = HttpClient(source="twse_rwd", rate=rate, clock=clock, transport=transport, limiter=limiter)
    for c in (a, b, a, b):
        c.get("https://example.invalid")
    assert times == [0.0, 0.0, 0.0, 5.0], "第 4 個請求（換了 client）仍要等窗"


def test_ac310_7_builtins_share_the_limiter_per_exchange() -> None:
    rate = SourceRate(max_requests=3, per_seconds=5.0)
    assert shared_limiter("twse_rwd", rate) is shared_limiter("twse_rwd", rate)
    assert shared_limiter("twse_rwd", rate) is not shared_limiter("tpex", rate)


# ── AC-313-1 估值：官方 → 與 FinMind 歷史逐位相同；「不計算」→ 0.0 ───────────────


def _twse_val(name: str, day: dt.date) -> pl.DataFrame:
    src = TwseRwdSource(client=_client(_text(name)))
    return clean_valuation(src.fetch("stock_valuation", day), day)


def _tpex_val(name: str, day: dt.date) -> pl.DataFrame:
    src = TpexSource(client=_client(_text(name)))
    return clean_tpex_valuation(src.fetch("stock_valuation", day), day)


@pytest.mark.parametrize(
    ("loader", "name", "day", "ids"),
    [
        (_twse_val, "twse_bwibbu_20260918.json", _D, {"1101", "1102", "2330"}),
        (_tpex_val, "tpex_pe_20260918.json", _D, {"1240", "1595", "6488"}),
        (_twse_val, "twse_bwibbu_20260724.json", dt.date(2026, 7, 24), {"2330", "7835"}),
        (_tpex_val, "tpex_pe_20250620.json", dt.date(2025, 6, 20), {"6287", "6488"}),
    ],
)
def test_ac313_1_valuation_equals_finmind_history_exactly(
    loader: Callable[[str, dt.date], pl.DataFrame], name: str, day: dt.date, ids: set[str]
) -> None:
    """per／pbr／殖利率（÷100）與 FinMind **逐位**相同、沒有容忍度：兩邊都是同一個官方數字。

    2330 @ 2026-07-24 的殖利率 0.94% 是會抓到「除法寫法不同」的那一列：Python 0.94/100 ＝
    0.009399999999999999，polars 除以純量（乘倒數）＝0.0094——差最後一位，同一個數字存成兩個值。
    """
    df = loader(name, day)
    expected = _expected("stock_valuation", day)
    assert set(df["stock_id"]) == ids and ids <= set(expected)
    for row in df.iter_rows(named=True):
        want = expected[row["stock_id"]]
        for col in ("date", "per", "pbr", "dividend_yield"):
            assert row[col] == want[col], (row["stock_id"], col)
        assert row["available_at"] == dt.datetime(day.year, day.month, day.day, 18, 0, tzinfo=TZ)


def test_ac313_1_not_computed_markers_become_zero() -> None:
    """證交所 '-'、櫃買 'N/A'＝不計算（虧損、新上市無財報）→ 0.0，與 FinMind 歷史同一個慣例。"""
    tw = _twse_val("twse_bwibbu_20260918.json", _D).filter(pl.col("stock_id") == "1101")
    assert tw["per"].item() == 0.0 and tw["pbr"].item() == 0.79
    new = _twse_val("twse_bwibbu_20260724.json", dt.date(2026, 7, 24))
    row = new.filter(pl.col("stock_id") == "7835").row(0, named=True)
    assert (row["per"], row["pbr"], row["dividend_yield"]) == (0.0, 0.0, 0.0)
    tp = _tpex_val("tpex_pe_20250620.json", dt.date(2025, 6, 20))
    row = tp.filter(pl.col("stock_id") == "6287").row(0, named=True)
    assert (row["per"], row["pbr"]) == (0.0, 0.0)
    assert (
        _tpex_val("tpex_pe_20260918.json", _D).filter(pl.col("stock_id") == "1595")["per"].item()
        == 0.0
    )


@pytest.mark.parametrize(
    ("market", "col", "bad"),
    [
        ("twse", 5, "N/A"),  # 櫃買的標記出現在證交所 → 不認
        ("twse", 5, "--"),
        ("twse", 6, ""),
        ("twse", 3, "-"),  # 殖利率沒有「不計算」標記
        ("tpex", 2, "-"),
        ("tpex", 6, "abc"),
        ("tpex", 5, "N/A"),
    ],
)
def test_ac313_1_other_tokens_raise(market: str, col: int, bad: str) -> None:
    if market == "twse":
        payload = _load("twse_bwibbu_20260918.json")
        payload["data"][1][col] = bad
        src: Any = TwseRwdSource(client=_client(json.dumps(payload, ensure_ascii=False)))
        cleaner = clean_valuation
    else:
        payload = _load("tpex_pe_20260918.json")
        payload["tables"][0]["data"][0][col] = bad
        src = TpexSource(client=_client(json.dumps(payload, ensure_ascii=False)))
        cleaner = clean_tpex_valuation
    with pytest.raises(DataError, match="不是數字"):
        cleaner(src.fetch("stock_valuation", _D), _D)


def test_ac313_1_schema_failure_is_a_data_error_for_this_table_only() -> None:
    """新表驗證失敗只算這一張表失敗（run_ingest 只接 DataError）；重複代號也擋。"""
    payload = _load("twse_bwibbu_20260918.json")
    payload["data"].append(list(payload["data"][0]))
    payload["total"] += 1
    src = TwseRwdSource(client=_client(json.dumps(payload, ensure_ascii=False)))
    with pytest.raises(DataError, match="重複"):
        clean_valuation(src.fetch("stock_valuation", _D), _D)


# ── AC-313-2 接進每日擷取（twse／tpex 兩個既有來源多供應一張表） ────────────────


def test_ac313_2_daily_sources_supply_valuation() -> None:
    assert "stock_valuation" in CLEANERS and "stock_valuation" in TPEX_CLEANERS
    calls: list[tuple[str, dict[str, str] | None]] = []

    def body(url: str, params: dict[str, str] | None) -> str:
        return _text("twse_bwibbu_20260918.json" if "twse" in url else "tpex_pe_20260918.json")

    twse = FetchCleanSource("twse", TwseRwdSource(client=_client(body, calls)), CLEANERS)
    tpex = FetchCleanSource("tpex", TpexSource(client=_client(body, calls)), TPEX_CLEANERS)
    assert "stock_valuation" in twse.tables and "stock_valuation" in tpex.tables
    both = pl.concat([twse.load("stock_valuation", _D), tpex.load("stock_valuation", _D)])
    assert both.height == 6
    assert calls == [
        (
            "https://www.twse.com.tw/rwd/zh/afterTrading/BWIBBU_d",
            {"selectType": "ALL", "response": "json", "date": "20260918"},
        ),
        (
            "https://www.tpex.org.tw/www/zh-tw/afterTrading/peQryDate",
            {"date": "2026/09/18", "response": "json"},
        ),
    ]
    schemas.validate("stock_valuation", both)


@pytest.mark.parametrize(
    ("loader", "name", "match"),
    [
        (_twse_val, "twse_bwibbu_20260925_holiday.json", "非交易日"),
        (_tpex_val, "tpex_pe_20260925_holiday.json", "為空"),
    ],
)
def test_ac313_2_holiday_raises(
    loader: Callable[[str, dt.date], pl.DataFrame], name: str, match: str
) -> None:
    """休市日（2026-09-25）兩個端點都 raise——同日其他表一起失敗，T-276 才會判成休市。"""
    with pytest.raises(DataError, match=match):
        loader(name, dt.date(2026, 9, 25))


# ── AC-327-2 資料湖表缺日補抓（融資融券常晚於 19:00 公布）───────────────────────


def test_ac327_2_missing_days_are_oldest_first_and_only_absent_ones() -> None:
    present = {_S[0], _S[3], _S[4]}
    assert di.missing_days(_S[:5], present) == [_S[2], _S[1]], "舊到新、只列 PG 沒有的交易日"
    assert di.missing_days([], set()) == []


def test_ac327_2_margin_is_a_default_lake_catchup_table() -> None:
    assert ChipDailyConfig().lake_catchup_tables == ("margin_daily",)
    assert load_config(_CONFIG, SystemConfig).chip_daily.lake_catchup_tables == ("margin_daily",)


def test_ac327_2_direct_pg_tables_are_refused() -> None:
    from twquant import cli
    from twquant.errors import ConfigError

    with pytest.raises(ConfigError, match="daytrade_daily"):
        cli._catchup_lake_days({"daytrade_daily": [_S[0]]}, lambda d: lambda tag, table: None)


def test_ac327_2_catchup_fetches_each_gap_into_the_lake_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from twquant import cli
    from twquant.data.ingest import orchestrator

    calls: list[tuple[dt.date, list[str] | None, str]] = []

    def fake_run_ingest(specs: Any, cfg: Any, sink: Any, period: dt.date, **kw: Any) -> Any:
        calls.append((period, kw["tables"], type(sink).__name__))
        if period == _S[1]:
            return [IngestOutcome("twse", "margin_daily", "failed", 0, "沒有符合條件的資料")]
        return [IngestOutcome("twse", "margin_daily", "success", 1297, "")]

    monkeypatch.setattr(orchestrator, "run_ingest", fake_run_ingest)
    parts, failures = cli._catchup_lake_days(
        {"margin_daily": [_S[2], _S[1]]}, lambda d: lambda tag, table: None
    )
    assert calls == [
        (_S[2], ["margin_daily"], "Warehouse"),
        (_S[1], ["margin_daily"], "Warehouse"),
    ], "逐日、只抓那張表、落資料湖（供應層再鏡射）"
    assert parts == [f"margin_daily 缺日補 1 天 1,297 列（{_S[2]}）"]
    assert failures == [f"margin_daily {_S[1]}（缺日）：沒有符合條件的資料"]


def test_ac327_2_lake_gap_days_reads_sessions_and_rows_from_pg() -> None:
    conn = _pg_or_skip()
    test_schema = "serving_test_t327"
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
            cur.execute(f'CREATE SCHEMA "{test_schema}"')
            cur.execute(f'CREATE TABLE "{test_schema}".prices_daily (date date, stock_id text)')
            cur.execute(f'CREATE TABLE "{test_schema}".margin_daily (date date, stock_id text)')
            days = sorted(_S[:5])  # 09-18~09-24
            today = dt.date(2026, 9, 25)
            cur.executemany(
                f'INSERT INTO "{test_schema}".prices_daily VALUES (%s, %s)',
                [(d, "2330") for d in [*days, today]],
            )
            # 融資：09-21、09-23 缺；今天（09-25）本來就不在補抓範圍
            cur.executemany(
                f'INSERT INTO "{test_schema}".margin_daily VALUES (%s, %s)',
                [(d, "2330") for d in days if d not in (_S[1], _S[3])],
            )
            got = di.lake_gap_days(cur, test_schema, "margin_daily", today, 4)
            assert got == [_S[3], _S[1]], "窗內缺的兩天，舊到新"
            assert di.lake_gap_days(cur, test_schema, "margin_daily", today, 1) == [], "窗外不補"
        conn.commit()
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        conn.close()
