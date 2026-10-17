"""T-371／D-187：MOPS 重大訊息來源（解析、抓取計畫、PG 表、每日 pipeline 步驟）。不連網。"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import pytest

from twquant.config import ServingConfig, SourceRate, SystemConfig, load_config
from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.data.serving import schema as sch
from twquant.data.sources import material_news as mn
from twquant.errors import DataError

_ROOT = Path(__file__).resolve().parent.parent
_FIX = _ROOT / "tests" / "fixtures" / "http" / "mops"
_DAY = dt.date(2026, 10, 16)


def _page(name: str = "t05st02_20261016.html") -> str:
    return (_FIX / name).read_text(encoding="utf-8")


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


def _client(body: str, calls: list[dict[str, str] | None], status: int = 200) -> HttpClient:
    def transport(method: str, url: str, *, params: dict[str, str] | None, timeout: float) -> _Resp:
        assert url == mn.URL
        calls.append(params)
        return _Resp(status, body)

    return HttpClient(
        source="test",
        rate=SourceRate(max_requests=10, per_seconds=1.0),
        clock=_Clock(),
        transport=transport,
    )


# ── AC-1：實際頁面（錄下的 2026-10-16，裁成 6 列）解析正確 ─────────────────────
def test_ac1_parses_every_market_with_exact_timestamps() -> None:
    df = mn.parse_day(_page(), _DAY)
    assert df.height == 6
    assert set(df["market"]) == {"sii", "otc", "rotc", "pub"}
    assert list(df.columns) == list(mn.SCHEMA)
    first = df.row(0, named=True)
    assert first == {
        "date": dt.date(2026, 10, 15),
        "stock_id": "2540",
        "market": "sii",
        "name": "愛山林",
        "subject": "本公司董事會通過基隆市土地合建開發案",
        "clause": "20",
        "fact_date": dt.date(2026, 10, 15),
        "mops_seq": 1,
        "available_at": dt.datetime(2026, 10, 15, 17, 35, 37, tzinfo=TZ),
    }
    # 同一公司同一天同主旨再發一次：發言時間區分（MOPS 編號 2），時間各自精確
    second = df.row(1, named=True)
    assert (second["stock_id"], second["subject"], second["mops_seq"]) == (
        "2540",
        first["subject"],
        2,
    )
    assert second["available_at"] == dt.datetime(2026, 10, 15, 17, 36, 47, tzinfo=TZ)
    # 全形括號經 NFKC 轉半形；HTML 實體已解開；代號保留字串
    otc = df.filter(df["market"] == "otc").row(0, named=True)
    assert otc["subject"].startswith("補充(或更正)") and "&" not in otc["subject"]
    assert otc["fact_date"] == dt.date(2026, 8, 5)  # 事實發生日早於發言日：補充公告
    assert all(isinstance(s, str) for s in df["stock_id"])


def test_ac1_page_without_rows_is_an_empty_day_not_an_error() -> None:
    df = mn.parse_day(_page("t05st02_norows.html"), _DAY)
    assert df.is_empty() and list(df.columns) == list(mn.SCHEMA)


# ── AC-2：壞頁面一律 raise，不丟列繼續 ────────────────────────────────────────
def test_ac2_non_list_page_raises() -> None:
    with pytest.raises(DataError, match="不是重大訊息清單"):
        mn.parse_day("<html>查詢過於頻繁，請稍後再試</html>", _DAY)


def test_ac2_row_and_form_count_mismatch_raises() -> None:
    page = _page()
    broken = page.replace("name='sii_fm0'", "name='xxx_fm0'", 1)  # 一列的表單認不出來
    with pytest.raises(DataError, match="頁面格式可能改了"):
        mn.parse_day(broken, _DAY)


def test_ac2_duplicate_key_and_bad_fields_raise() -> None:
    page = _page()
    # 第二列（同主旨）改成同一秒 → 身分 (代號, 發言時間, 主旨) 相同
    dup = page.replace("name='h13' value='173647'", "name='h13' value='173537'", 1)
    with pytest.raises(DataError, match="重複"):
        mn.parse_day(dup, _DAY)
    bad_time = page.replace("name='h03' value='173537'", "name='h03' value='17:35'", 1)
    with pytest.raises(DataError, match="HHMMSS"):
        mn.parse_day(bad_time, _DAY)


def test_ac2_mops_seq_is_not_a_key_and_short_times_keep_their_zeros() -> None:
    # 2026-10-16 實測：同公司同日兩則不同公告的 MOPS 編號都是 1；時間的前導零會被吃掉（658＝00:06:58）
    page = _page().replace("name='h15' value='2'", "name='h15' value='1'", 1)
    page = page.replace("name='h03' value='173537'", "name='h03' value='658'", 1)
    df = mn.parse_day(page, _DAY)
    assert df.filter(df["stock_id"] == "2540")["mops_seq"].to_list() == [1, 1]
    assert df.row(0, named=True)["available_at"] == dt.datetime(2026, 10, 15, 0, 6, 58, tzinfo=TZ)


def test_ac2_fetch_uses_roc_date_and_rejects_http_errors() -> None:
    calls: list[dict[str, str] | None] = []
    df = mn.fetch_day(_client(_page(), calls), _DAY)
    assert df.height == 6
    assert calls[0] is not None
    assert (calls[0]["year"], calls[0]["month"], calls[0]["day"]) == ("115", "10", "16")
    with pytest.raises(DataError, match="HTTP 503"):
        mn.fetch_day(_client("", [], status=503), _DAY)


# ── AC-3：抓取計畫＝當天、前一天，加回看窗內的缺日 ────────────────────────────
def test_ac3_fetch_plan_always_refetches_today_and_yesterday_and_fills_gaps() -> None:
    day = dt.date(2026, 10, 17)
    window = {day - dt.timedelta(days=k) for k in range(6)}
    present = window - {dt.date(2026, 10, 13)}
    assert mn.fetch_plan(day, 5, present) == [
        dt.date(2026, 10, 13),
        dt.date(2026, 10, 16),
        dt.date(2026, 10, 17),
    ]
    first_run = mn.fetch_plan(day, 5, set())
    assert first_run == sorted(window) and len(first_run) == 6


# ── AC-4：PG 表與解析器欄位一致；雲端有滾動窗 ──────────────────────────────────
def test_ac4_pg_spec_matches_parser_schema_and_cloud_prunes_it() -> None:
    spec = sch.spec_of("mops_announcements")
    assert spec is sch.MOPS_ANNOUNCEMENTS_SPEC
    assert spec.column_names == tuple(mn.SCHEMA)
    assert spec.pk == ("stock_id", "available_at", "subject")
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert scfg.cloud.windows["mops_announcements"] >= 22  # 證據包讀近 30 個日曆日
    cfg = load_config(_ROOT / "configs" / "system.yaml", SystemConfig)
    assert cfg.material_news.lookback_days >= 30


# ── AC-5：每日 pipeline 地端與雲端都跑「重大訊息」，排在洞悉分析之前 ─────────────
def test_ac5_pipeline_runs_news_step_before_insights_in_cloud_too() -> None:
    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert source.index('PipelineStep("重大訊息"') < source.index('PipelineStep("洞悉分析"')
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "重大訊息" not in scfg.cloud.skip_pipeline_steps


def test_ac1_depositary_receipts_without_forms_are_read_from_visible_cells() -> None:
    # 2026-09-15 實際頁：9105／9110 存託憑證的明細走 t59sb01_form、沒有隱藏欄位
    df = mn.parse_day(_page("t05st02_20260915_dr.html"), dt.date(2026, 9, 15))
    dr = df.filter(df["market"] == mn.DR_MARKET).sort("stock_id")
    assert dr["stock_id"].to_list() == ["9105", "9110"]
    first = dr.row(0, named=True)
    assert first["date"] == dt.date(2026, 9, 15)
    assert first["available_at"] == dt.datetime(2026, 9, 15, 17, 30, 10, tzinfo=TZ)
    assert first["mops_seq"] is None  # MOPS 不給存託憑證編號
    assert first["subject"].startswith("(匯率確認版)泰金寶")
    assert first["clause"] is None and first["fact_date"] is None
    assert df.filter(df["market"] != mn.DR_MARKET).height == 1
