"""T-375／D-188：期交所盤後統計（三大法人期貨未平倉、買賣權比）。不連網。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from twquant.config import SystemConfig, load_config
from twquant.conventions import TZ
from twquant.data.serving import schema as sch
from twquant.data.sources import taifex_daily as tx
from twquant.errors import DataError

_ROOT = Path(__file__).resolve().parent.parent
_FIX = _ROOT / "tests" / "fixtures" / "http" / "taifex"
AT = dt.time(15, 30)


def _raw(name: str) -> bytes:
    return (_FIX / name).read_bytes()


# ── AC-1：實際 CSV（2026-10-14～16）解析正確 ─────────────────────────────────
def test_ac1_parses_institutional_futures_oi() -> None:
    fut = tx.parse_fut_oi(_raw("futContractsDate_20261014_16.csv"))
    assert sorted(fut) == [dt.date(2026, 10, 14), dt.date(2026, 10, 15), dt.date(2026, 10, 16)]
    d16 = fut[dt.date(2026, 10, 16)]
    assert d16 == {
        "dealer_net": 3197,
        "trust_net": 82464,
        "foreign_net": -93675,
        "foreign_long": 9812,
        "foreign_short": 103487,
    }


def test_ac1_parses_put_call_ratio() -> None:
    pc = tx.parse_pc_ratio(_raw("pcRatio_20261010_16.csv"))
    assert dt.date(2026, 10, 16) in pc and len(pc) == 5
    assert pc[dt.date(2026, 10, 16)] == {
        "pc_vol_ratio": 88.75,
        "put_oi": 55093.0,
        "call_oi": 81497.0,
        "pc_oi_ratio": 67.6,
    }


def test_ac1_combined_row_has_both_sides_and_publish_time() -> None:
    df = tx.combine(
        tx.parse_fut_oi(_raw("futContractsDate_20261014_16.csv")),
        tx.parse_pc_ratio(_raw("pcRatio_20261010_16.csv")),
        AT,
    )
    assert list(df.columns) == list(tx.SCHEMA)
    row = df.filter(df["date"] == dt.date(2026, 10, 16)).row(0, named=True)
    assert (row["fut_foreign_net_oi"], row["pc_oi_ratio"], row["put_oi"]) == (-93675, 67.6, 55093)
    assert row["available_at"] == dt.datetime(2026, 10, 16, 15, 30, tzinfo=TZ)
    # 10-13 只有買賣權比（期貨那份從 10-14 起）→ 期貨欄留空，不補 0
    only_pc = df.filter(df["date"] == dt.date(2026, 10, 13)).row(0, named=True)
    assert only_pc["fut_foreign_net_oi"] is None and only_pc["pc_oi_ratio"] == 68.95


# ── AC-2：查無資料的 HTML 頁＝沒有列；其他非 CSV raise ─────────────────────────
def test_ac2_no_data_page_is_empty_not_an_error() -> None:
    assert tx.parse_fut_oi(_raw("futContractsDate_20261011_empty.csv")) == {}
    df = tx.combine({}, {}, AT)
    assert df.is_empty() and list(df.columns) == list(tx.SCHEMA)


def test_ac2_other_html_raises() -> None:
    with pytest.raises(DataError, match="不是 CSV"):
        tx.parse_fut_oi("<html><body>Access denied</body></html>".encode("cp950"))
    bad = _raw("pcRatio_20261010_16.csv").decode("cp950").replace("88.75", "n/a", 1).encode("cp950")
    with pytest.raises(DataError, match="不是數字"):
        tx.parse_pc_ratio(bad)


# ── AC-3：PG 表欄位同名同序；參數在 configs ──────────────────────────────────
def test_ac3_spec_and_config() -> None:
    spec = sch.spec_of("taifex_daily")
    assert spec.column_names == tuple(tx.SCHEMA) and spec.pk == ("date",)
    cfg = load_config(_ROOT / "configs" / "system.yaml", SystemConfig)
    assert cfg.taifex_daily.lookback_days >= 7
    assert cfg.taifex_daily.available_time == dt.time(15, 30)
    assert tx._range(dt.date(2026, 10, 1), dt.date(2026, 10, 16)) == {
        "queryStartDate": "2026/10/01",
        "queryEndDate": "2026/10/16",
    }


def test_ac2_date_error_page_raises_its_own_error() -> None:
    # 2026-10-17 實測：迄日超過最新公布日 → 「日期時間錯誤 DateTime error」，連前面有資料的日子也不給
    with pytest.raises(tx.TaifexDateError, match="DateTime error"):
        tx.parse_fut_oi(_raw("futContractsDate_dateerror.html"))


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


class _Resp:
    def __init__(self, body: bytes) -> None:
        self.status_code = 200
        self.content = body
        self.text = ""


def test_ac2_fetch_steps_the_end_date_back_until_published() -> None:
    from twquant.config import SourceRate
    from twquant.data.http import HttpClient

    asked: list[tuple[str, str]] = []

    def transport(method: str, url: str, *, params: dict[str, str] | None, timeout: float) -> _Resp:
        assert params is not None
        if url == tx.FUT_URL:
            asked.append((params["queryStartDate"], params["queryEndDate"]))
            ok = params["queryEndDate"] <= "2026/10/16"
            return _Resp(
                _raw(
                    "futContractsDate_20261014_16.csv" if ok else "futContractsDate_dateerror.html"
                )
            )
        return _Resp(_raw("pcRatio_20261010_16.csv"))

    client = HttpClient(
        source="t",
        rate=SourceRate(max_requests=10, per_seconds=1.0),
        clock=_Clock(),
        transport=transport,
    )
    df = tx.fetch_range(client, dt.date(2026, 10, 14), dt.date(2026, 10, 18), AT)
    assert asked == [
        ("2026/10/14", "2026/10/18"),
        ("2026/10/14", "2026/10/17"),
        ("2026/10/14", "2026/10/16"),
    ]
    assert df.filter(df["date"] == dt.date(2026, 10, 16))["fut_foreign_net_oi"].to_list() == [
        -93675
    ]
