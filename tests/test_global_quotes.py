"""T-377／D-188：晨報的全球行情解析（Yahoo 日線、期交所夜盤）。錄下的回應，不連網。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from twquant.data.sources import global_quotes as gq
from twquant.errors import DataError

_FIX = Path(__file__).resolve().parent / "fixtures" / "http"


def _yahoo(name: str) -> dict:
    return json.loads((_FIX / "yahoo" / f"{name}_5d.json").read_text(encoding="utf-8"))


# ── AC-1：Yahoo 日線 → 最新一根對前一根；台積電 ADR、匯率也走同一條 ───────────────
def test_ac1_yahoo_latest_bar_versus_previous() -> None:
    q = gq.parse_yahoo(_yahoo("gspc"), "^GSPC", "S&P 500")
    assert q.symbol == "^GSPC" and q.name == "S&P 500"
    assert q.last == pytest.approx(7659.44, abs=0.01) and q.prev == pytest.approx(7744.06, abs=0.01)
    assert q.chg == pytest.approx(7659.44 / 7744.06 - 1, abs=1e-4)
    assert q.date == "2026-10-16" and q.time is not None and q.open is False
    fx = gq.parse_yahoo(_yahoo("twdx"), "TWD=X", "美元／台幣")
    assert fx.last == pytest.approx(32.428, abs=0.001)
    adr = gq.parse_yahoo(_yahoo("tsm"), "TSM", "台積電 ADR")
    assert adr.last == pytest.approx(412.61, abs=0.01) and adr.chg < 0


def test_ac1_yahoo_bad_shapes_raise() -> None:
    with pytest.raises(DataError, match="回應異常"):
        gq.parse_yahoo(
            {"chart": {"result": None, "error": {"description": "Not Found"}}}, "^XXX", "x"
        )
    one = _yahoo("gspc")
    one["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [None, None, None, None, 1.0]
    with pytest.raises(DataError, match="不到兩根"):
        gq.parse_yahoo(one, "^GSPC", "S&P 500")
    assert gq.yahoo_url("TWD=X").endswith("/TWD%3DX?range=5d&interval=1d")


# ── AC-2：期交所夜盤 → 近月、對結算價漲跌、對加權收盤的價差 ───────────────────────
def test_ac2_taifex_night_near_month_and_gap() -> None:
    body = json.loads(
        (_FIX / "taifex" / "getQuoteList_night_20261016.json").read_text(encoding="utf-8")
    )
    n = gq.parse_taifex_night(body)
    assert n.symbol == "TXFJ6" and n.date == "2026-10-16" and n.time == "04:59:57"
    assert n.last == 46140.0 and n.ref == 47284.0 and n.chg == pytest.approx(46140 / 47284 - 1)
    assert n.spot_close == pytest.approx(47111.26) and n.gap_pts == pytest.approx(46140 - 47111.26)
    assert n.volume == 40941


def test_ac2_taifex_bad_code_raises_and_body_is_night() -> None:
    with pytest.raises(DataError, match="RtCode"):
        gq.parse_taifex_night({"RtCode": "1", "RtMsg": "busy"})
    assert (
        gq.taifex_body("night")["MarketType"] == "1" and gq.taifex_body("day")["MarketType"] == "0"
    )
