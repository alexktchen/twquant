"""SPEC-016 T-337：由指數型 ETF 持股增刪推導換股事件（AC-10）、與元大換股紀錄核對（AC-11）。

fixture：`yuanta_0050_members_rebalance_2026.json`＝0050 在 2026-06、2026-09 兩次換股前後各基準日的成分；
`yuanta_rebalance_1066.json`＝元大官網的 0050 換股紀錄（日期、增刪檔數）。
換股是兩步：先買進新成分（持股暫時 51 檔），下一個基準日才賣掉被剔除的。
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import fund_flow_units as ffu
from twquant.data import schemas

_FIX = Path(__file__).parent / "fixtures" / "http" / "etf_pcf"


def _holdings(members: dict[str, list[str]], etf_id: str = "0050") -> pl.DataFrame:
    rows = [
        (
            dt.date.fromisoformat(d),
            etf_id,
            sid,
            dt.datetime.combine(dt.date.fromisoformat(d), dt.time(18), TZ),
        )
        for d, ids in members.items()
        for sid in ids
    ]
    return pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "etf_id": pl.String,
            "stock_id": pl.String,
            "available_at": schemas.TW_DT,
        },
        orient="row",
    )


def _members() -> dict[str, list[str]]:
    data: dict[str, list[str]] = json.loads(
        (_FIX / "yuanta_0050_members_rebalance_2026.json").read_text(encoding="utf-8")
    )
    return data


def test_ac10_two_step_rebalance_events() -> None:
    ev = ffu.rebalance_events(_holdings(_members()), {"0050"})
    got = {(r["effective_date"], r["action"], r["stock_id"]) for r in ev.iter_rows(named=True)}
    assert (dt.date(2026, 9, 18), "add", "6446") in got
    assert (dt.date(2026, 9, 21), "remove", "3661") in got
    assert {s for d, a, s in got if d == dt.date(2026, 6, 18) and a == "add"} == {
        "3443",
        "3665",
        "4958",
        "8046",
    }
    row = ev.filter(pl.col("stock_id") == "6446").row(0, named=True)
    assert row["prev_base_date"] == dt.date(2026, 9, 17)
    assert row["announce_date"] is None and row["source"] == "holdings_diff"
    assert ev.select("etf_id", "effective_date", "stock_id", "action").is_unique().all()


def test_ac10_active_etf_and_share_changes_make_no_events() -> None:
    m = _members()
    assert ffu.rebalance_events(_holdings(m, "00981A"), {"0050"}).height == 0
    same = {"2026-01-02": ["2330", "2317"], "2026-01-05": ["2317", "2330"]}
    assert ffu.rebalance_events(_holdings(same), {"0050"}).height == 0


def test_ac10_actions_are_validated() -> None:
    ev = ffu.rebalance_events(_holdings(_members()), {"0050"})
    bad = ev.with_columns(pl.lit("swap").alias("action"))
    with pytest.raises(Exception, match="index_rebalance_events"):
        schemas.validate("index_rebalance_events", bad)


@pytest.mark.parametrize(
    ("after", "upto", "create_date"),
    [
        (dt.date(2026, 6, 16), dt.date(2026, 6, 22), "20260622"),
        (dt.date(2026, 9, 16), dt.date(2026, 9, 21), "20260921"),
    ],
)
def test_ac11_net_changes_match_yuanta_rebalance_record(
    after: dt.date, upto: dt.date, create_date: str
) -> None:
    ev = ffu.rebalance_events(_holdings(_members()), {"0050"})
    added, removed = ffu.net_changes(ev, "0050", after, upto)
    rec = {
        r["createDate"]: r
        for r in json.loads((_FIX / "yuanta_rebalance_1066.json").read_text(encoding="utf-8"))[
            "Data"
        ]
    }[create_date]
    assert (len(added), len(removed)) == (rec["addCount"], rec["delCount"])


def test_ac11_net_changes_cancel_transient_holdings() -> None:
    m = {
        "2026-03-19": ["A", "B"],
        "2026-03-20": ["A", "B", "C"],  # 暫時多 C
        "2026-03-23": ["A", "B"],  # C 又不見
    }
    ev = ffu.rebalance_events(_holdings(m), {"0050"})
    assert ev.height == 2
    assert ffu.net_changes(ev, "0050", dt.date(2026, 3, 19), dt.date(2026, 3, 23)) == (set(), set())
