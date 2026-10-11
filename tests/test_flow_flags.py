"""SPEC-016 T-341：看板資金流旗標——缺資料是 null／「無資料」不是 0（AC-24）、主動式 ETF 標「監控、未回測」（AC-25）。"""

from __future__ import annotations

import datetime as dt

import polars as pl

from twquant.reporting import flow_flags as ff

D1, D2 = dt.date(2026, 10, 7), dt.date(2026, 10, 8)


def _h(rows: list[tuple[dt.date, str, str, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "etf_id": pl.String, "stock_id": pl.String, "shares": pl.Int64},
        orient="row",
    )


def test_ac25_active_changes_last_two_snapshots_only() -> None:
    h = _h(
        [
            (D1, "00981A", "2330", 10_000),
            (D1, "00981A", "2383", 5_000),
            (D2, "00981A", "2330", 12_000),
            (D2, "00981A", "3037", 1_000),
            (D2, "0050", "2330", 99),  # 指數型不算
            (D2, "00403A", "2330", 1_000),  # 只有一份 → 沒有可比
        ]
    )
    c = ff.active_changes(h, ["00981A", "00403A"])
    got = {(e, s): d for e, s, d, _, _ in c.iter_rows()}
    assert got == {("00981A", "2330"): 2_000, ("00981A", "2383"): -5_000, ("00981A", "3037"): 1_000}


def test_ac24_missing_inputs_are_none_not_zero() -> None:
    pressure = pl.DataFrame({"date": [D2], "stock_id": ["2330"], "value": [0.012]})
    flags = ff.build_flow_flags(
        ["2330", "1101"], D2, pressure=pressure, streak=None, events=None, changes=None
    )
    assert flags["2330"]["pressure"] == 0.012
    assert flags["1101"]["pressure"] is None  # 有資料表但這檔沒值
    assert flags["2330"]["disc_streak"] is None and flags["2330"]["events"] is None
    text = ff.flags_text(flags["1101"])
    assert "無資料" in text and " 0" not in text and "+0.0%" not in text
    assert ff.flags_text(None) == "無資料"


def test_ac24_events_and_changes_render() -> None:
    events = pl.DataFrame({"event": ["E5", "E3", "E3"], "stock_id": ["2330", "2330", "2330"]})
    changes = ff.active_changes(
        _h([(D1, "00981A", "2330", 10_000), (D2, "00981A", "2330", 12_000)]), ["00981A"]
    )
    flags = ff.build_flow_flags(
        ["2330", "2317"],
        D2,
        pressure=pl.DataFrame({"date": [D2], "stock_id": ["2330"], "value": [-0.02]}),
        streak=pl.DataFrame({"date": [D2], "stock_id": ["2330"], "value": [3.0]}),
        events=events,
        changes=changes,
    )
    assert flags["2330"]["events"] == ["E3", "E5"]
    assert flags["2317"]["events"] == [] and flags["2317"]["active_etf"] == []  # 有資料、沒事件
    t = ff.flags_text(flags["2330"])
    assert "ETF 壓力 -2.0%" in t and "連買 3 日" in t and "E3、E5" in t and "00981A +2 張" in t
    assert ff.NOTE == "監控、未回測（SPEC-016）"


def test_ac24_board_html_flow_column() -> None:
    from twquant.reporting.pick_board_html import _Flow

    old = _Flow({})
    assert old.th() == "" and old.td("2330") == ""  # 舊看板沒有這欄
    none = _Flow({"flow_flags": None, "flow_flags_missing": "no_tables"})
    assert "無資料" in none.td("2330") and "監控、未回測" in none.th()
    flags = ff.build_flow_flags(["2330"], D2, pressure=None, streak=None, events=None, changes=None)
    some = _Flow({"flow_flags": {"note": ff.NOTE, "as_of": str(D2), "stocks": flags}})
    assert "無資料" in some.td("2330") and "無資料" in some.td("9999")
