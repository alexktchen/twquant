"""T-426／D-206 §二：名單四面向 10 分制的 20 條規則。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from twquant.config import SystemConfig, load_config
from twquant.reporting.pick_score import (
    NO_DATA,
    Item,
    Market,
    chip_items,
    flow_ratio,
    fund_items,
    industry_medians,
    items_json,
    news_items,
    percentile,
    price_stats,
    tech_items,
    total,
)

_ROOT = Path(__file__).resolve().parents[1]
CFG = load_config(_ROOT / "configs" / "system.yaml", SystemConfig).pick_review
R = CFG.rules


def _m(**kw: object) -> Market:
    base: dict[str, object] = {
        "mom": {},
        "vol": {},
        "foreign": {},
        "main_force": {},
        "dy": {},
        "industry": {},
        "per_median": {},
        "pbr_median": {},
    }
    base.update(kw)
    return Market(**base)  # type: ignore[arg-type]


def _pts(items: list[Item]) -> dict[str, int]:
    return {i.name: i.points for i in items}


def test_t426_ac1_score_is_base_plus_items_clamped() -> None:
    assert R.base == 5
    up = [Item(str(i), 1, "") for i in range(5)]
    assert total(up, R) == 10 and total([Item("a", -1, "")] * 5, R) == 0
    assert total([Item("a", 1, ""), Item("b", -1, ""), Item("c", 0, "")], R) == 5
    assert total([Item("a", 1, "")] * 9, R) == 10  # 夾在 0～10


def test_t426_ac2_tech_rules() -> None:
    closes = [100.0] * 239 + [100.0 + i for i in range(1, 12)]  # 一路漲到創新高
    vols = [1000.0] * 249 + [3000.0]
    m = _m(mom={"2330": (0.11, 0.9)}, vol={"2330": (0.01, 0.1)})
    got = _pts(tech_items("2330", closes, vols, m, R, CFG.high_days))
    assert got == {"均線": 1, "漲幅": 1, "高低點": 1, "量價": 1, "波動": 1}
    down = [100.0] * 60 + [90.0]
    m2 = _m(mom={"1101": (-0.1, 0.1)}, vol={"1101": (0.05, 0.95)})
    got2 = _pts(tech_items("1101", down, [1.0] * 60 + [5.0], m2, R, CFG.high_days))
    assert got2 == {"均線": -1, "漲幅": -1, "高低點": -1, "量價": -1, "波動": -1}
    none = tech_items("9999", [100.0], [1.0], _m(), R, CFG.high_days)
    assert all(i.points == 0 and i.text == NO_DATA for i in none)


def test_t426_ac3_chip_rules_need_sign_and_percentile() -> None:
    m = _m(
        foreign={"2330": (5_000_000.0, 0.05, 0.9)},
        main_force={"2330": (-3_000_000.0, -0.03, 0.1)},
    )
    trust = [1.0, 1.0, 1.0, 1.0]
    margin = [1_000_000.0] * 6 + [900_000.0]
    closes = [100.0] * 6 + [110.0]
    holders = [(dt.date(2026, 10, 3), 0.40), (dt.date(2026, 10, 9), 0.41)]
    items = chip_items("2330", trust, margin, closes, holders, m, R)
    # D-207 起多了借券、當沖兩項（這裡沒給資料 → 0）；原本五項的規則不變
    assert _pts(items) == {
        "外資": 1,
        "投信": 1,
        "主力": -1,
        "融資": 1,
        "借券": 0,
        "當沖": 0,
        "千張大戶": 1,
    }
    mf = next(i for i in items if i.name == "主力")
    assert "賣超 3,000 張" in mf.text and "前 15 大券商" in mf.text
    # 百分位高但其實是賣超 → 不加分（D-206：要同號）
    m2 = _m(foreign={"2330": (-1.0, -0.0001, 0.85)})
    assert _pts(chip_items("2330", [], [], [], [], m2, R))["外資"] == 0
    # 融資大增且下跌 → −1；投信連賣 3 天 → −1
    up = chip_items(
        "2330", [-1.0] * 3, [1_000_000.0] * 6 + [1_050_000.0], [100.0] * 6 + [95.0], [], _m(), R
    )
    assert _pts(up)["融資"] == -1 and _pts(up)["投信"] == -1 and _pts(up)["主力"] == 0


def test_t426_ac4_fund_rules_against_industry() -> None:
    m = _m(
        industry={"2330": "半導體"},
        per_median={"半導體": 20.0},
        pbr_median={"半導體": 3.0},
        dy={"2330": (0.05, 0.9)},
    )
    rev: list[tuple[str, float | None]] = [("2026-09", 0.3), ("2026-08", 0.2), ("2026-07", 0.1)]
    assert _pts(fund_items("2330", rev, (10.0, 1.5, 0.05), m, R)) == {
        "月營收": 1,
        "營收趨勢": 1,
        "本益比": 1,
        "股價淨值比": 1,
        "殖利率": 1,
    }
    bad: list[tuple[str, float | None]] = [("2026-09", -0.2), ("2026-08", -0.1), ("2026-07", -0.3)]
    got = _pts(fund_items("2330", bad, (None, 6.0, 0.0), m, R))
    assert got == {"月營收": -1, "營收趨勢": -1, "本益比": -1, "股價淨值比": -1, "殖利率": -1}
    # 沒有產業中位數 → 不比
    lone = fund_items("2330", [], (10.0, 1.0, 0.01), _m(), R)
    assert _pts(lone) == {"月營收": 0, "營收趨勢": 0, "本益比": 0, "股價淨值比": 0, "殖利率": 0}


def test_t426_ac5_news_rules_and_calendar_not_scored() -> None:
    quiet = news_items([], False, 0, 30, [], [], R)
    assert total(quiet, R) == 5
    loud = news_items(
        ["處置中"], True, 2, 30, ["董事會決議買回庫藏股", "自結虧損"], ["10/22 法說會"], R
    )
    assert _pts(loud) == {
        "處置／注意": -1,
        "內部人": -1,
        "重大訊息利多": 1,
        "重大訊息利空": -1,
        "行事曆": 0,
    }
    assert next(i for i in loud if i.name == "行事曆").text == "10/22 法說會"


def test_t426_ac6_market_helpers() -> None:
    assert percentile({"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}) == {
        "a": 0.25,
        "b": 0.5,
        "c": 0.75,
        "d": 1.0,
    }
    days = [dt.date(2026, 10, d) for d in (15, 16, 17)]
    flows = pl.DataFrame(
        {
            "date": days + days[:2],
            "stock_id": ["A"] * 3 + ["B"] * 2,
            "net": [10.0, 20.0, 30.0, 5.0, 5.0],
        }
    )
    vol = pl.DataFrame({"date": days * 2, "stock_id": ["A"] * 3 + ["B"] * 3, "volume": [100.0] * 6})
    assert flow_ratio(flows, vol, days) == {"A": (60.0, 0.2)}  # B 缺一天 → 不算，不補 0
    px = pl.DataFrame(
        {
            "stock_id": ["A"] * 22,
            "date": [dt.date(2026, 9, 1) + dt.timedelta(days=i) for i in range(22)],
            "close": [100.0] * 21 + [110.0],
        }
    )
    mom, vol_ = price_stats(px, R)
    assert round(mom["A"], 4) == 0.1 and vol_["A"] > 0
    per, pbr = industry_medians(
        {str(i): (float(i + 1), None, None) for i in range(5)},
        {str(i): "X" for i in range(5)},
        R.min_peers,
    )
    assert per == {"X": 3.0} and pbr == {}
    assert industry_medians({"0": (1.0, None, None)}, {"0": "X"}, R.min_peers) == ({}, {})


def test_t426_ac7_items_json_shape() -> None:
    js = items_json({"籌碼": [Item("主力", -1, "主力賣超")]})
    # D-207 §二.4：多一欄 weight（比重 1）；其餘形狀不變
    assert js == [{"dim": "籌碼", "name": "主力", "points": -1, "text": "主力賣超", "weight": 1.0}]


# ── T-429／D-207 §二：籌碼七項加權、當沖與借券、對稱進位 ─────────────────────────


def test_t429_ac1_chip_weights_sum_to_base_and_rounding_is_symmetric() -> None:
    from twquant.config import PickScoreRules

    assert set(R.chip_weights) == {"外資", "投信", "主力", "融資", "借券", "當沖", "千張大戶"}
    assert abs(sum(R.chip_weights.values()) - R.base) < 1e-9
    assert R.chip_weights["外資"] > R.chip_weights["投信"] > R.chip_weights["主力"]
    # 半分往離 5 分的方向進位：+0.5 → 6、−0.5 → 4（不能一邊 6 一邊 5）
    assert total([Item("當沖", 1, "", 0.5)], R) == 6
    assert total([Item("當沖", -1, "", 0.5)], R) == 4
    assert (
        total([Item("外資", 1, "", 1.5), Item("投信", 1, "", 1.0), Item("主力", -1, "", 0.5)], R)
        == 7
    )
    assert total([Item("a", 1, "", 1.5), Item("b", -1, "", 1.5)], R) == 5
    # 七項全 +1＝10、全 −1＝0；比重 1 時與 D-206 逐位相同
    seven = [Item(k, 1, "", w) for k, w in R.chip_weights.items()]
    assert total(seven, R) == 10 and total([Item(i.name, -1, "", i.weight) for i in seven], R) == 0
    assert total([Item("a", 1, "")] * 3, R) == 8
    # 鍵不對或合計不等於 base → 設定檔就擋下
    base = R.model_dump()
    bad = dict(R.chip_weights)
    bad["外資"] += 0.5
    with pytest.raises(ValueError):
        PickScoreRules(**{**base, "chip_weights": bad})
    with pytest.raises(ValueError):
        PickScoreRules(
            **{**base, "chip_weights": {k: v for k, v in R.chip_weights.items() if k != "當沖"}}
        )


def test_t429_ac2_seven_chip_items_daytrade_only_minus_sbl_both_ways() -> None:
    m = _m(
        foreign={"2330": (5_000_000.0, 0.05, 0.9)},
        main_force={"2330": (-3_000_000.0, -0.03, 0.1)},
        daytrade={"2330": (40_000_000.0, 0.45, 0.95)},
        sbl={"2330": (-2_000_000.0, -0.02, 0.1)},
    )
    trust = [1.0, 1.0, 1.0, 1.0]
    margin = [1_000_000.0] * 6 + [900_000.0]
    closes = [100.0] * 6 + [110.0]
    holders = [(dt.date(2026, 10, 3), 0.40), (dt.date(2026, 10, 9), 0.41)]
    items = chip_items("2330", trust, margin, closes, holders, m, R)
    assert [i.name for i in items] == ["外資", "投信", "主力", "融資", "借券", "當沖", "千張大戶"]
    assert _pts(items) == {
        "外資": 1,
        "投信": 1,
        "主力": -1,
        "融資": 1,
        "借券": 1,
        "當沖": -1,
        "千張大戶": 1,
    }
    assert {i.name: i.weight for i in items} == R.chip_weights
    assert total(items, R) == 8  # 5 + (1.5 + 1 − 0.5 + 0.5 + 0.5 − 0.5 + 0.5) = 8.0
    dt_ = next(i for i in items if i.name == "當沖")
    assert "當沖" in dt_.text and "45.0%" in dt_.text and "比全市場 95%" in dt_.text
    sbl = next(i for i in items if i.name == "借券")
    assert "借券賣出餘額" in sbl.text and "減少 2,000 張" in sbl.text
    # 當沖低也不加分（只扣不加）；借券增加在前 20% → −1、同號才算
    low = _m(daytrade={"2330": (1_000.0, 0.01, 0.05)}, sbl={"2330": (3_000_000.0, 0.03, 0.9)})
    got = _pts(chip_items("2330", [], [], [], [], low, R))
    assert got["當沖"] == 0 and got["借券"] == -1
    odd = _m(sbl={"2330": (-1.0, -0.0001, 0.85)})
    assert _pts(chip_items("2330", [], [], [], [], odd, R))["借券"] == 0
    # 缺資料 → 0 分、資料不足
    none = chip_items("9999", [], [], [], [], _m(), R)
    assert all(i.points == 0 and i.text == NO_DATA for i in none) and len(none) == 7


def test_t429_ac3_balance_change_ratio_needs_every_day() -> None:
    from twquant.reporting.pick_score import balance_change_ratio

    days = [dt.date(2026, 10, d) for d in (15, 16, 19, 20, 21, 22)]  # 第一天是基準，後五天是窗
    bal = pl.DataFrame(
        {
            "date": days + days[1:],
            "stock_id": ["A"] * 6 + ["B"] * 5,
            "balance": [100.0, 110.0, 120.0, 130.0, 140.0, 160.0] + [50.0] * 5,
        }
    )
    vol = pl.DataFrame(
        {"date": days[1:] * 2, "stock_id": ["A"] * 5 + ["B"] * 5, "volume": [200.0] * 10}
    )
    assert balance_change_ratio(bal, vol, days) == {"A": (60.0, 0.06)}  # B 缺基準日 → 不算


def test_t429_ac4_items_json_carries_weight() -> None:
    js = items_json({"籌碼": [Item("當沖", -1, "當沖佔成交量 45.0%", 0.5)]})
    assert js == [
        {"dim": "籌碼", "name": "當沖", "points": -1, "text": "當沖佔成交量 45.0%", "weight": 0.5}
    ]
