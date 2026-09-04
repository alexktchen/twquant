"""T-011 / SPEC-001 §3：清洗管線。"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

from twquant.config import SourceRate
from twquant.conventions import SHARES_PER_LOT, TZ
from twquant.data.clean import clean_institutional, clean_margin, clean_prices
from twquant.data.http import HttpClient
from twquant.data.sources.twse_rwd import TwseRwdSource

_FIX = Path(__file__).resolve().parent / "fixtures" / "http" / "twse_rwd"
_DATE = dt.date(2026, 6, 30)


@dataclass
class FakeResponse:
    status_code: int
    text: str
    content: bytes = b""


def _raw(table: str) -> pl.DataFrame:
    text = (_FIX / f"{table}.json").read_text(encoding="utf-8")

    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        return FakeResponse(200, text)

    src = TwseRwdSource(
        client=HttpClient(
            source="twse_rwd", rate=SourceRate(max_requests=3, per_seconds=5.0), transport=transport
        )
    )
    return src.fetch(table, _DATE)


def test_clean_prices_types_and_values() -> None:
    df = clean_prices(_raw("prices_daily"), _DATE)
    assert df.schema["close"] == pl.Float64
    assert df.schema["volume"] == pl.Int64
    row = df.filter(pl.col("stock_id") == "2330").to_dicts()[0]
    assert row["close"] == 2410.0
    assert row["volume"] == 49_540_227
    assert row["available_at"] == dt.datetime(2026, 6, 30, 18, 0, tzinfo=TZ)


def test_clean_institutional_foreign_sum_and_trust() -> None:
    df = clean_institutional(_raw("institutional_daily"), _DATE)
    row = df.filter(pl.col("stock_id") == "2330").to_dicts()[0]
    assert row["trust_net"] == -89_595
    # 外資 = 外陸資(不含自營) + 外資自營商 = -1,425,774 + 0
    assert row["foreign_net"] == -1_425_774


def test_clean_margin_lots_to_shares() -> None:
    df = clean_margin(_raw("margin_daily"), _DATE)
    row = df.filter(pl.col("stock_id") == "2330").to_dicts()[0]
    assert row["margin_balance"] == 33_305 * SHARES_PER_LOT  # 張→股
    assert row["short_balance"] == 22 * SHARES_PER_LOT
    assert row["available_at"] == dt.datetime(2026, 6, 30, 21, 0, tzinfo=TZ)


# ── T-102：T86 欄位錯位的自我一致性閘門 ──────────────────────────────
#
# 背景：`institutional_daily` 有 508 列（2022-01~2026-09、41 個分散的日子）與 TWSE 原始
# JSON 不符，簽名是「欄位整體位移 3 格」。2026-09-05 重抓 2026-08-31 的原始回應並以現行
# 程式碼剖析，與 FinMind **1,331 檔逐列相符、0 差異**——**剖析器本身是對的**，出事的是
# 上游當時給了 fields 與 data 不對應的回應。既然不能靠修剖析器，防線只能是自我一致性檢查。

_T86_FIX = _FIX / "institutional_20260831.json"


def _t86_payload() -> dict[str, Any]:
    import json

    data: dict[str, Any] = json.loads(_T86_FIX.read_text(encoding="utf-8"))
    return data


def _t86_raw(payload: Mapping[str, Any]) -> pl.DataFrame:
    from twquant.data.sources import raw_frame

    return raw_frame(list(payload["fields"]), list(payload["data"]))


def test_ac2_t86_fixture_parses_to_source_values() -> None:
    """錄下的原始回應逐列剖析正確（AC-2）。2330 的值直接取自 TWSE 該日 JSON。"""
    p = _t86_payload()
    df = clean_institutional(_t86_raw(p), dt.date(2026, 8, 31))
    ix = {n: i for i, n in enumerate(p["fields"])}

    def num(row: list[str], name: str) -> int:
        v = row[ix[name]].replace(",", "").strip()
        return int(v) if v not in ("", "--") else 0

    for row in p["data"]:
        sid = row[0].strip()
        got = df.filter(pl.col("stock_id") == sid).to_dicts()
        if not got:
            continue
        r = got[0]
        assert r["foreign_net"] == num(row, "外陸資買賣超股數(不含外資自營商)") + num(
            row, "外資自營商買賣超股數"
        )
        assert r["trust_net"] == num(row, "投信買賣超股數")
        assert r["dealer_net"] == num(row, "自營商買賣超股數")


def test_ac4_t86_identities_hold_on_good_data() -> None:
    """實測 2026-08-31 全 1,335 列滿足七條恆等式；閘門不得對正常資料誤報。"""
    clean_institutional(_t86_raw(_t86_payload()), dt.date(2026, 8, 31))  # 不得 raise


def test_ac4_t86_column_shift_is_detected(caplog: object) -> None:
    """欄位位移 3 格必須 raise，且訊息要指出是哪幾檔。

    重建真實簽名：**欄名正確**（所以完全靜默）而 data 的值自 index 5 起整體前移 3 格。
    驗證重建無誤——湖當時的三欄正好是：
      foreign_net = 外陸資 + 投信、trust_net = 自營商賣出(自行)、dealer_net = 自行買賣超。
    """
    import pytest

    from twquant.errors import DataError

    p = _t86_payload()
    shifted = {
        **p,
        "data": [row[:5] + row[8:] + ["0", "0", "0"] for row in p["data"]],
    }
    ix = {n: i for i, n in enumerate(p["fields"])}
    r0 = p["data"][0]
    s0 = shifted["data"][0]
    # 重建正確性：位移後「外資自營商買賣超」的槽位裝的是「投信買賣超」的值
    assert s0[ix["外資自營商買賣超股數"]] == r0[ix["投信買賣超股數"]]
    assert s0[ix["投信買賣超股數"]] == r0[ix["自營商賣出股數(自行買賣)"]]
    assert s0[ix["自營商買賣超股數"]] == r0[ix["自營商買賣超股數(自行買賣)"]]

    with pytest.raises(DataError, match="內部恆等式"):
        clean_institutional(_t86_raw(shifted), dt.date(2026, 8, 31))


def test_ac4_t86_missing_identity_columns_raises() -> None:
    """連一條恆等式都驗不了的回應必須 raise，不得靜默放行。

    否則上游若改欄名，閘門會安靜地什麼都不檢查——那比沒有閘門更危險，
    因為它讓人以為有防護（黃金規則 5）。
    """
    import pytest

    from twquant.errors import DataError

    p = _t86_payload()
    keep = {
        "證券代號",
        "外陸資買賣超股數(不含外資自營商)",
        "外資自營商買賣超股數",
        "投信買賣超股數",
        "自營商買賣超股數",
    }
    idx = [i for i, f in enumerate(p["fields"]) if f in keep]
    thin = {
        "fields": [p["fields"][i] for i in idx],
        "data": [[row[i] for i in idx] for row in p["data"]],
    }
    with pytest.raises(DataError, match="恆等式"):
        clean_institutional(_t86_raw(thin), dt.date(2026, 8, 31))
