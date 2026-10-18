"""T-393／D-195：名單每檔一句話——事實句、模型濃縮、逐句檢查、規則句後備、接進每日 pipeline。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from twquant.config import ServingConfig, SystemConfig, load_config
from twquant.conventions import TZ
from twquant.data.serving import schema as sch
from twquant.errors import DataError
from twquant.insight.client import InsightClient
from twquant.insight.config import LlmConfig, load_llm_config
from twquant.insight.prompts import PICK_BLURB_PROMPT_VERSION, PICK_BLURB_SYSTEM
from twquant.insight.schemas import PickBlurbItem, PickBlurbs
from twquant.reporting.pick_blurbs import (
    PickFacts,
    check_text,
    compose_line,
    facts,
    gather,
    grouped_facts,
    rule_text,
    user_payload,
    value_count,
)

_ROOT = Path(__file__).resolve().parents[1]
CFG = load_config(_ROOT / "configs" / "system.yaml", SystemConfig).pick_blurbs
NAMES = {"CORE": "大盤精選", "QUIET": "潛伏飆股", "QUIETV": "高分爆量"}
DAY = dt.date(2026, 10, 16)
NOW = dt.datetime(2026, 10, 16, 17, 40, tzinfo=TZ)
D = dt.date


class _Cur:
    """依 SQL 裡的表名回假資料；information_schema 查表在不在。"""

    def __init__(self, tables: dict[str, list[tuple[Any, ...]]]) -> None:
        self.tables = tables
        self.result: list[tuple[Any, ...]] = []

    def __enter__(self) -> _Cur:
        return self

    def __exit__(self, *a: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        if "information_schema" in sql:
            self.result = [(1,)] if params[1] in self.tables else []
            return
        if "SELECT line, rank, stock_id FROM" in sql:
            self.result = [(r[0], r[2], r[3]) for r in self.tables["daily_picks"] if r[1] == DAY]
            return
        for name, rows in self.tables.items():
            if f".{name} " in sql:
                self.result = rows
                return
        raise AssertionError(f"沒預期的查詢：{sql}")

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.result

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.result[0] if self.result else None


class _Conn:
    def __init__(self, tables: dict[str, list[tuple[Any, ...]]]) -> None:
        self.tables = tables

    def cursor(self) -> _Cur:
        return _Cur(self.tables)


def _tables() -> dict[str, list[tuple[Any, ...]]]:
    # daily_picks：(line, date, rank, stock_id)；只有今天那份會被讀
    picks = [
        ("CORE", D(2026, 10, 14), 1, "2305"),
        ("CORE", D(2026, 10, 15), 1, "2305"),
        ("CORE", D(2026, 10, 16), 1, "2305"),
        ("CORE", D(2026, 10, 15), 2, "1303"),
        ("CORE", D(2026, 10, 16), 2, "6209"),
    ]
    closes = [
        (sid, D(2026, 10, d), c)
        for sid, base in (("2305", 50.0), ("6209", 100.0))
        for d, c in zip(range(9, 17), [base + i for i in range(8)], strict=True)
    ]
    return {
        "daily_picks": picks,
        "stock_meta": [("2305", "全友"), ("6209", "今國光")],
        "prices_daily": closes,
        "pick_reasons": [
            (
                "CORE",
                "2305",
                json.dumps(
                    [
                        {"f": "price.mom_60d", "s": 1, "t": "近 60 日漲 57%，全市場前 3%"},
                        {"f": "fundamental.earnings_yield", "s": -1, "t": "本益比 6.5 倍"},
                    ],
                    ensure_ascii=False,
                ),
            ),
        ],
        "pick_down_risk": [("CORE", "6209")],
        "disposition_stocks": [
            ("6209", D(2026, 10, 10), D(2026, 10, 24)),
            ("2305", D(2026, 10, 20), D(2026, 11, 2)),
        ],
        "attention_stocks": [("6209",)],
        "dividend_calendar": [("2305", D(2026, 10, 22), "cash")],
        "conference_calendar": [("2305", D(2026, 10, 16))],
        "insider_transfer_notices": [("6209", 3)],
        "mops_announcements": [
            ("2305", NOW, "公告本公司115年第3季合併自結損益"),
            ("2305", NOW, "公告本公司115年第3季合併自結損益"),
            ("2305", NOW, "澄清媒體報導" + "很長" * 30),
        ],
    }


# ── AC-1：從 PG 讀名單與每檔事實 ─────────────────────────────────────────────
def test_ac1_gather_builds_facts_per_pick() -> None:
    items, errors = gather(_Conn(_tables()), "serving", DAY, CFG, NAMES)
    assert errors == []
    by = {p.stock_id: p for p in items}
    a, b = by["2305"], by["6209"]
    assert (a.line_name, a.rank, a.name) == ("大盤精選", 1, "全友") and (b.rank, b.name) == (
        2,
        "今國光",
    )
    assert (
        a.close == 57.0
        and a.chg == pytest.approx(57 / 56 - 1)
        and a.move == pytest.approx(57 / 52 - 1)
    )
    assert [r["t"] for r in a.reasons] == ["近 60 日漲 57%，全市場前 3%", "本益比 6.5 倍"]
    assert b.down_risk and not a.down_risk
    assert b.disposition == ["處置中，到 10/24"] and a.disposition == ["10/20 起處置"]
    assert b.attention and not a.attention
    assert a.events == ["今天法說會", "10/22 除息"]  # 依日期排
    assert b.insider == 3
    assert (
        a.announcements[0] == "公告本公司115年第3季合併自結損益" and len(a.announcements) == 2
    )  # 同主旨只留一則
    assert len(a.announcements[1]) == CFG.announcement_chars


def test_ac1_missing_optional_tables_are_reported_and_required_ones_raise() -> None:
    t = _tables()
    del t["mops_announcements"], t["pick_down_risk"]
    items, errors = gather(_Conn(t), "serving", DAY, CFG, NAMES)
    assert len(items) == 2 and sorted(errors) == [
        "缺 mops_announcements，少了這類事實",
        "缺 pick_down_risk，少了這類事實",
    ]
    t2 = _tables()
    del t2["prices_daily"]
    with pytest.raises(DataError, match="prices_daily"):
        gather(_Conn(t2), "serving", DAY, CFG, NAMES)


# ── AC-2：事實句與規則句 ────────────────────────────────────────────────────
def _p(**kw: Any) -> PickFacts:
    base: dict[str, Any] = dict(
        day=DAY,
        line="CORE",
        line_name="大盤精選",
        stock_id="2305",
        name="全友",
        rank=3,
        close=59.3,
        chg=0.021,
        move=0.123,
        move_days=5,
        reasons=[{"t": "近 60 日漲 57%，全市場前 3%", "s": 1}, {"t": "本益比 6.5 倍", "s": -1}],
    )
    base.update(kw)
    return PickFacts(**base)


def test_ac2_facts_are_grouped_plain_sentences_with_units() -> None:
    p = _p(
        events=["10/22 除息"],
        insider=2,
        insider_days=30,
        announcements=["澄清媒體報導"],
        reasons=[
            {"t": "近 60 日漲 57%，全市場前 3%", "s": 1},
            {"t": "本益比 6.5 倍", "s": -1},
            {"t": "排序模型分數在全市場前 3%", "s": 0},
        ],
    )
    g = grouped_facts(p, CFG.drop_alert)
    assert g["看點"] == ["近 60 日漲 57%，全市場前 3%"]
    assert g["提醒"] == [
        "10/22 除息",
        "近 30 天有 2 筆內部人申報轉讓持股",
        "近期公告：澄清媒體報導",
        "本益比 6.5 倍",
    ]
    assert g["行情"] == ["今天收盤 59.30 元，漲 2.1%", "近 5 日漲 12.3%"]
    f = facts(p, CFG.drop_alert)
    assert f == [*g["看點"], *g["提醒"], *g["行情"]]
    # 「貼近一年高點」要有「高點附近」的事實；「還差 59%」不算
    far = _p(reasons=[{"t": "離 52 週高點還差 59%", "s": -1}])
    assert any("貼近" in x for x in check_text("股價貼近一年高點", far, CFG)[1])
    near = _p(reasons=[{"t": "離 52 週高點還差 3%，全市場前 6%", "s": 1}])
    assert "股價在 52 週高點附近" in grouped_facts(near, CFG.drop_alert, CFG.near_high)["看點"]
    assert check_text("股價貼近一年高點", near, CFG)[1] == []
    assert "股價在 52 週高點附近" not in grouped_facts(far, CFG.drop_alert, CFG.near_high)["看點"]
    # 上榜前提（每檔都有）不給模型；名次與上榜資訊也不給（網站那一列已經有）
    assert not any("排序模型" in x for x in f) and not any("上榜" in x or "第 3 名" in x for x in f)
    assert any("新上榜" in x for x in check_text("新上榜，動能強", _p(), CFG)[1])
    # 近幾天跌深 → 算提醒；小跌只是行情
    assert "近 5 日跌 8.0%" in grouped_facts(_p(move=-0.08), CFG.drop_alert)["提醒"]
    assert "近 5 日跌 2.0%" in grouped_facts(_p(move=-0.02), CFG.drop_alert)["行情"]
    assert grouped_facts(
        _p(down_risk=True, attention=True, disposition=["處置中，到 10/24"]), CFG.drop_alert
    )["提醒"] == ["模型標了易跌", "處置中，到 10/24", "今天是注意股", "本益比 6.5 倍"]


def test_ac2_rule_text_puts_risk_first_then_top_reason() -> None:
    a = CFG.drop_alert
    assert rule_text(_p(), a) == "近 60 日漲 57%，但本益比 6.5 倍"  # 「全市場前 3%」的排名尾巴不帶
    assert (
        rule_text(_p(disposition=["處置中，到 10/24"], down_risk=True), a)
        == "近 60 日漲 57%，但模型標了易跌"
    )
    assert rule_text(_p(move=-0.08), a) == "近 60 日漲 57%，但近 5 日跌 8.0%"  # 跌深排在扣分理由前
    assert rule_text(_p(announcements=["澄清媒體報導"]), a) == "近 60 日漲 57%，但近期有重大訊息"
    assert (
        rule_text(_p(reasons=[{"t": "近 60 日漲 57%，全市場前 3%", "s": 1}]), a) == "近 60 日漲 57%"
    )
    assert rule_text(_p(reasons=[]), a) == "大盤精選第 3 名"
    assert rule_text(_p(reasons=[{"t": "高低價振幅在全市場前 31%", "s": 1}]), a) == "高低價振幅"


def test_ac2_reasons_most_of_the_line_share_are_not_given_to_the_model() -> None:
    vol = {"f": "price.atr_14", "s": 1, "t": "每日真實波幅約 5.0%"}
    rev = {"f": "fundamental.revenue_yoy", "s": 1, "t": "月營收年增 536%"}
    p = _p(reasons=[vol, rev], common=frozenset({"price.atr_14"}))
    assert grouped_facts(p, CFG.drop_alert)["看點"] == ["月營收年增 536%"]
    # 拿掉後一個都不剩（大盤精選人人都是同四個因子）→ 全留
    q = _p(reasons=[vol], common=frozenset({"price.atr_14"}))
    assert grouped_facts(q, CFG.drop_alert)["看點"] == ["每日真實波幅約 5.0%"]


def test_ac1_gather_marks_reasons_shared_by_most_of_the_line() -> None:
    t = _tables()
    r = [{"f": "price.mom_60d", "s": 1, "t": "近 60 日漲 57%"}]
    t["pick_reasons"] = [
        (
            "CORE",
            "2305",
            json.dumps([*r, {"f": "x.own", "s": 1, "t": "自己的"}], ensure_ascii=False),
        ),
        ("CORE", "6209", json.dumps(r, ensure_ascii=False)),
    ]
    items, _ = gather(_Conn(t), "serving", DAY, CFG, NAMES)
    assert all(p.common == frozenset({"price.mom_60d"}) for p in items)


# ── AC-3：逐句檢查 ──────────────────────────────────────────────────────────
def test_ac3_check_rejects_made_up_numbers_units_terms_advice_and_long_text() -> None:
    p = _p(events=["10/22 除息"])
    ok, problems = check_text("兩個月來漲勢強，但 10/22 除息。", p, CFG)
    assert problems == [] and ok == "兩個月來漲勢強，但 10/22 除息"
    assert check_text("近 60 日漲勢強，但本益比 6.5 倍", p, CFG)[1] == []  # 60 日是期間、不算數字
    assert check_text("漲勢強，但 10/22 除息。", p, CFG) == ("漲勢強，但 10/22 除息", [])
    assert check_text("近 60 日漲 57%，但 10/22 除息", p, CFG)[1] == []  # 兩個數字可以
    assert any("數字" in x for x in check_text("近 60 日漲 58%", p, CFG)[1])
    # 數字在事實裡、但單位換了（前 3% 寫成前 3 名、60 日寫成 60%）
    assert any("單位" in x and "3名" in x for x in check_text("動能全市場前3名", p, CFG)[1])
    assert any(
        "事實沒有的說法" in x and "處置" in x for x in check_text("處置中但動能強", p, CFG)[1]
    )
    assert any("建議" in x for x in check_text("動能強，建議逢低布局", p, CFG)[1])
    assert any("太長" in x for x in check_text("動能強" * 20, p, CFG)[1])
    assert check_text("以分析師角度來看，動能強", p, CFG) == ("動能強", [])
    # 2026-10-18 實測模型自己編的：歷史高點、股價反轉、七成
    assert any("歷史" in x for x in check_text("股價接近歷史高點", p, CFG)[1])
    assert any("反轉" in x for x in check_text("量能放大，但近期股價反轉", p, CFG)[1])
    assert any("中文" in x for x in check_text("兩個月漲七成", p, CFG)[1])
    assert any("重複" in x for x in check_text("漲勢強勁，漲勢強勁，但 10/22 除息", p, CFG)[1])
    assert any("術語" in x for x in check_text("振幅全市前 47%", p, CFG)[1])
    assert any("中文" in x for x in check_text("兩個月漲一倍二", p, CFG)[1])
    # 白話說法要有對應的事實：沒有 52 週高點的事實就不能說「一年高點」；有成交量的事實才能說「量能」
    assert any("根據" in x and "一年高點" in x for x in check_text("股價貼近一年高點", p, CFG)[1])
    vol = _p(
        reasons=[
            {"t": "成交量高於 20 日平均（3.0 個標準差）", "s": 1},
            {"t": "在 52 週高點附近", "s": 1},
        ]
    )
    assert check_text("股價貼近一年高點、量能明顯放大", vol, CFG) == (
        "股價貼近一年高點、量能明顯放大",
        [],
    )
    assert any(
        "不只一個" in x for x in check_text("動能強但本益比 6.5 倍，但 10/22 除息", p, CFG)[1]
    )
    clean = _p(reasons=[{"t": "近 60 日漲 57%，全市場前 3%", "s": 1}])
    assert any("沒有提醒" in x for x in check_text("動能強，但漲多", clean, CFG)[1])
    assert check_text("近 60 日漲 57%、動能強", clean, CFG)[1] == []
    amp = _p(reasons=[{"t": "高低價振幅在全市場前 31%", "s": 1}])
    assert check_text("股價波動大", amp, CFG)[1] == []  # 波動的根據可以是「振幅」
    assert any(
        "數字太多（3 個" in x
        for x in check_text("近 60 日漲 57%、全市場前 3%，本益比 6.5 倍", _p(), CFG)[1]
    )
    assert any("建議" in x and "空間" in x for x in check_text("離一年高點仍有空間", _p(), CFG)[1])
    assert any("中文" in x for x in check_text("近六旬漲勢強", _p(), CFG)[1])
    assert any("中文" in x for x in check_text("離高點僅差三個百分比", _p(), CFG)[1])
    assert value_count("離 52 週高點還差 12%、近 60 日漲 57%，10/22 除息") == 3
    assert check_text("動能強 但 10/22 除息", p, CFG) == (
        "動能強，但 10/22 除息",
        [],
    )  # 日期算一個數字；「但」前補逗號


# ── AC-4：一條線一次呼叫；沒過或缺席的改用規則句 ──────────────────────────────
def _llm() -> LlmConfig:
    return LlmConfig.model_validate(
        {
            "models": {"m": {"id": "claude-x", "input_usd_per_1m": 1.0, "output_usd_per_1m": 1.0}},
            "tasks": {"pick_blurb": "m"},
        }
    )


def test_ac4_compose_checks_each_pick_and_falls_back(tmp_path: Path) -> None:
    items = [
        _p(stock_id="2305"),
        _p(stock_id="6209", name="今國光", rank=4),
        _p(stock_id="3016", rank=5),
    ]
    seen: list[tuple[str, str]] = []

    def backend(model_id: str, system: str, user: str, schema: type, max_tokens: int) -> Any:
        seen.append((system, user))
        return (
            PickBlurbs(
                items=[
                    PickBlurbItem(sid="2305 全友", text="動能強，但本益比 6.5 倍"),
                    PickBlurbItem(sid="6209", text="漲 80%"),  # 80 不在事實裡
                ]
            ),
            2500,
            600,
        )

    out = compose_line(
        InsightClient(_llm(), tmp_path, api_key="k", backend=backend), items, NOW, CFG
    )
    by = {b.stock_id: b for b in out}
    assert (
        by["2305"].source == "model"
        and by["2305"].text == "動能強，但本益比 6.5 倍"
        and by["2305"].checks == []
    )
    assert (
        by["6209"].source == "rule"
        and by["6209"].text == rule_text(items[1], CFG.drop_alert)
        and "原句：漲 80%" in by["6209"].checks
    )
    assert by["3016"].source == "rule" and by["3016"].checks == [
        "模型漏了這檔（回了：['2305', '6209']）"
    ]
    system, user = seen[0]
    assert system == PICK_BLURB_SYSTEM and user == user_payload(
        "大盤精選", DAY, items, CFG.drop_alert, CFG.near_high
    )
    assert (
        "[2305 全友]\n【看點】近 60 日漲 57%，全市場前 3%\n【提醒】本益比 6.5 倍\n【行情】" in user
    )
    assert (
        "不給建議" in PICK_BLURB_SYSTEM
        and "不要寫股名" in PICK_BLURB_SYSTEM
        and "【提醒】" in PICK_BLURB_SYSTEM
        and PICK_BLURB_PROMPT_VERSION.startswith("2026-")
    )
    offline = compose_line(
        InsightClient(_llm(), tmp_path, api_key=None, backend=None), items[:1], NOW, CFG
    )
    assert offline[0].source == "rule" and offline[0].checks[0].startswith("模型缺席：")
    assert (
        compose_line(InsightClient(_llm(), tmp_path, api_key="k", backend=backend), [], NOW, CFG)
        == []
    )


# ── AC-5：表、路由、每日 pipeline 地端雲端都跑、雲端保留窗、拉回本機 ─────────────
def test_ac5_wiring() -> None:
    spec = sch.spec_of("pick_blurbs")
    assert spec.pk == ("date", "line", "stock_id") and "source" in spec.column_names
    assert load_llm_config(_ROOT / "configs" / "llm.yaml").tasks["pick_blurb"] == "cf_qwen3"
    cli = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert (
        cli.index('PipelineStep("內部人申讓"')
        < cli.index('PipelineStep("名單一句話"')
        < cli.index('PipelineStep("洞悉分析"')
    )
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "名單一句話" not in scfg.cloud.skip_pipeline_steps
    assert scfg.cloud.windows["pick_blurbs"] > 0
    pull = (_ROOT / "scripts" / "pull_cloud_to_local.py").read_text(encoding="utf-8")
    assert '"pick_blurbs"' in pull
    assert (
        CFG.lines == ["CORE", "QUIET", "QUIETV"]
        and "建議" in CFG.advice_terms
        and "處置" in CFG.state_terms
    )
