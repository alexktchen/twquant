"""除權除息預告（2026-10-17）：證交所 TWT48U＋櫃買 tpex_exright_prepost。錄下的回應，不連網。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from twquant.config import ServingConfig, load_config
from twquant.conventions import TZ
from twquant.data.serving import schema as sch
from twquant.data.sources import dividend_calendar as dc
from twquant.errors import DataError

_ROOT = Path(__file__).resolve().parent.parent
_FIX = _ROOT / "tests" / "fixtures" / "http" / "dividend"
AT = dt.datetime(2026, 10, 17, 17, 40, tzinfo=TZ)


def _twse() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((_FIX / "twse_TWT48U.json").read_text(encoding="utf-8"))
    return payload


def _tpex() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads(
        (_FIX / "tpex_exright_prepost.json").read_text(encoding="utf-8")
    )
    return rows


# ── AC-1：兩份預告解析成同一種列；民國日期、種類、金額 ───────────────────────────
def test_ac1_twse_forecast_rows() -> None:
    rows = {r["stock_id"]: r for r in dc.parse_twse(_twse(), AT)}
    r = rows["1463"]
    assert (r["date"], r["market"], r["kind"], r["cash_dividend"]) == (
        dt.date(2026, 10, 15),
        "TWSE",
        "cash",
        0.1273,
    )
    # 現金增資造成的「權」：無償配股率 0、現增配股率與認購價有值
    w = rows["1711"]
    assert (w["kind"], w["stock_ratio"], w["rights_ratio"], w["rights_price"]) == (
        "stock",
        0.0,
        0.09128214,
        34.0,
    )
    assert rows["00401A"]["kind"] == "cash" and r["available_at"] == AT


def test_ac1_tpex_forecast_rows() -> None:
    rows = {r["stock_id"]: r for r in dc.parse_tpex(_tpex(), AT)}
    assert (rows["2947"]["date"], rows["2947"]["kind"], rows["2947"]["cash_dividend"]) == (
        dt.date(2026, 10, 7),
        "cash",
        1.0,
    )
    both = rows["2063"]
    assert (both["kind"], both["date"]) == ("both", dt.date(2026, 10, 28))
    assert both["cash_dividend"] == pytest.approx(0.5000208) and both[
        "stock_ratio"
    ] == pytest.approx(0.09999999)
    assert {r["market"] for r in rows.values()} == {"TPEX"}


def test_ac1_combined_frame_matches_the_pg_spec() -> None:
    df = dc.to_frame(dc.parse_twse(_twse(), AT) + dc.parse_tpex(_tpex(), AT))
    assert list(df.columns) == list(dc.SCHEMA) == list(sch.DIVIDEND_CALENDAR_SPEC.column_names)
    assert sch.DIVIDEND_CALENDAR_SPEC.pk == ("date", "stock_id")
    assert df.height == len(_twse()["data"]) + len(_tpex())


# ── AC-2：壞回應 raise，不丟列 ───────────────────────────────────────────────
def test_ac2_bad_responses_raise() -> None:
    with pytest.raises(DataError, match="stat"):
        dc.parse_twse({"stat": "很抱歉，沒有符合條件的資料!"}, AT)
    bad = _twse()
    bad["fields"] = [f for f in bad["fields"] if f != "現金股利"]
    with pytest.raises(DataError, match="缺欄位"):
        dc.parse_twse(bad, AT)
    odd = _tpex()
    odd[0] = {**odd[0], "ExRrightsExDividend": "除權除息"}
    with pytest.raises(DataError, match="種類不認得"):
        dc.parse_tpex(odd, AT)
    nan = _tpex()
    nan[0] = {**nan[0], "CashDividend": "N/A"}
    with pytest.raises(DataError, match="不是數字"):
        dc.parse_tpex(nan, AT)
    dup = dc.parse_tpex(_tpex(), AT)
    with pytest.raises(DataError, match="重複"):
        dc.to_frame(dup + dup[:1])


# ── AC-3：每日 pipeline 地端與雲端都跑「除權息預告」 ─────────────────────────────
def test_ac3_pipeline_runs_the_forecast_step_in_cloud_too() -> None:
    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert 'PipelineStep("除權息預告"' in source
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "除權息預告" not in scfg.cloud.skip_pipeline_steps
    # 網站的「往後除權息」讀預告表、不讀事後才有的 corporate_events。T-431 起首頁沒有行事曆，
    # 預告改標在名單股與追蹤股上（_lib/risks.ts）；個股頁行事曆另見 stocks/[sid]/calendar.ts。
    api = (_ROOT / "frontend" / "functions" / "_lib" / "risks.ts").read_text(encoding="utf-8")
    assert "serving.dividend_calendar" in api and "serving.corporate_events" not in api


def test_ac2_amount_to_be_announced_is_none_not_an_error() -> None:
    # 證交所 2026-10-17 實見：ETF 現金股利欄是 HTML 包著的「待公告實際收益分配金額」
    payload = _twse()
    row = list(payload["data"][0])
    row[payload["fields"].index("現金股利")] = (
        "<p style= text-align:center;>待公告實際收益分配金額</p>"
    )
    payload["data"] = [row]
    (r,) = dc.parse_twse(payload, AT)
    assert r["cash_dividend"] is None and r["kind"] == "cash"


@pytest.mark.parametrize("raw", ["尚未公告", "註4", "<p>待公告實際收益分配金額</p>", ""])
def test_ac2_known_no_number_placeholders(raw: str) -> None:
    assert dc._num(raw, "x") is None


def test_ac2_unknown_text_still_raises() -> None:
    with pytest.raises(DataError, match="不是數字"):
        dc._num("約 1 元", "x")
