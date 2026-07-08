"""T-010 / D-017：TDCC 集保戶股權分散來源與清洗（離線，內嵌真實 CSV 精簡 fixture）。

fixture 由 opendata getOD.ashx?id=1-5 實抓 2026-07-03 精簡而來（006208 + 00400A 各 17
分級；utf-8-sig）——涵蓋前導零、字尾字母代號、16=差異數/17=合計 剔除等案例。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import polars as pl
import pytest

from twquant.config import SourceRate
from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.data.sources.tdcc import TdccDispersionSource
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError

_RATE = SourceRate(max_requests=3, per_seconds=5.0)
_FIXTURE = Path(__file__).parent / "fixtures" / "http" / "tdcc" / "dispersion.csv"
_TABLE = "shareholding_dispersion"


@dataclass
class FakeResponse:
    status_code: int
    text: str
    content: bytes


def _source(body: bytes | None = None) -> TdccDispersionSource:
    data = body if body is not None else _FIXTURE.read_bytes()

    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        return FakeResponse(200, "", data)

    return TdccDispersionSource(client=HttpClient(source="tdcc", rate=_RATE, transport=transport))


def test_fetch_returns_raw_columns() -> None:
    raw = _source().fetch(_TABLE, dt.date(2026, 7, 3))
    assert {"資料日期", "證券代號", "持股分級", "人數", "股數"} <= set(raw.columns)
    assert raw.height == 34  # 2 檔 × 17 分級（含 16/17）


def test_fetch_rejects_other_table() -> None:
    with pytest.raises(DataError):
        _source().fetch("prices_daily", dt.date(2026, 7, 3))


def test_fetch_empty_response_raises() -> None:
    with pytest.raises(DataError):
        _source(body=b"resonly_header\n").fetch(_TABLE, dt.date(2026, 7, 3))


def test_load_filters_levels_and_preserves_ids() -> None:
    df = _source().load(_TABLE, dt.date(2026, 7, 3))
    assert df.height == 30  # 2 檔 × 15 分級（16=差異數、17=合計 剔除）
    assert sorted(df["level"].unique().to_list()) == list(range(1, 16))
    assert set(df["stock_id"].unique().to_list()) == {"006208", "00400A"}  # 前導零/字尾字母保留
    assert df.schema["level"] == pl.Int32
    assert df.schema["holders"] == pl.Int64
    assert df.schema["shares"] == pl.Int64


def test_available_at_is_publish_saturday_noon() -> None:
    # 基準日 2026-07-03（週五）→ 公布日週六 2026-07-04 12:00（前視關鍵值，off-by-one 即前視 bug）
    df = _source().load(_TABLE, dt.date(2026, 7, 3))
    assert df["available_at"][0] == dt.datetime(2026, 7, 4, 12, 0, tzinfo=TZ)


def test_pit_visibility_at_saturday_cutoff(tmp_path: Path) -> None:
    # D-008：date 型 as_of coerce 到 20:00。available_at=週六12:00 → 週五(基準日)看不到、週六看得到。
    wh = Warehouse(tmp_path)
    wh.write("clean", _TABLE, _source().load(_TABLE, dt.date(2026, 7, 3)), source="tdcc")
    fri = wh.view(dt.date(2026, 7, 3)).table(_TABLE).collect()
    sat = wh.view(dt.date(2026, 7, 4)).table(_TABLE).collect()
    assert fri.height == 0  # 基準日當日（週五 20:00 cutoff）尚未公布 → 看不到
    assert sat.height == 30  # 公布日（週六 20:00 cutoff）→ 看得到
