"""T-394／D-196：名單四面向評分與 AI 首選——事實句、觀點核對、首選核對與後備、接進每日 pipeline。

T-426／D-206 起分數由規則算（tests/test_pick_score.py），模型只寫觀點；本檔改成檢查「分數照規則、模型不再打分」。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from twquant.config import ServingConfig, SystemConfig, load_config
from twquant.conventions import TZ
from twquant.data.serving import schema as sch
from twquant.insight.client import InsightClient
from twquant.insight.config import LlmConfig, load_llm_config
from twquant.insight.prompts import PICK_REVIEW_PROMPT_VERSION, PICK_REVIEW_SYSTEM, PICK_TOP_SYSTEM
from twquant.insight.schemas import PickReviewItem, PickTop
from twquant.reporting.pick_blurbs import PickFacts
from twquant.reporting.pick_review import (
    NO_NEWS,
    Dimensions,
    check_reason,
    chip_facts,
    compose_line,
    fund_facts,
    news_facts,
    rule_reason,
    stock_payload,
    tech_facts,
)

_ROOT = Path(__file__).resolve().parents[1]
SYS = load_config(_ROOT / "configs" / "system.yaml", SystemConfig)
CFG, BCFG = SYS.pick_review, SYS.pick_blurbs
DAY = dt.date(2026, 10, 16)
NOW = dt.datetime(2026, 10, 16, 17, 40, tzinfo=TZ)


# ── AC-1：四個面向的事實句 ───────────────────────────────────────────────────
def test_ac1_news_facts_and_neutral_when_nothing() -> None:
    p = PickFacts(day=DAY, line="CORE", line_name="大盤精選", stock_id="2603", name="長榮", rank=1)
    assert news_facts(p) == [NO_NEWS]
    p.attention, p.events, p.announcements = True, ["10/22 除息"], ["澄清媒體報導"]
    p.disposition, p.insider, p.insider_days = ["處置中，到 10/24"], 2, 30
    assert news_facts(p) == [
        "處置中，到 10/24",
        "今天是注意股",
        "10/22 除息",
        "近 30 天有 2 筆內部人申報轉讓持股",
        "近期公告：澄清媒體報導",
    ]


def test_ac1_tech_facts_moves_averages_high_volume_and_down_risk() -> None:
    closes = [100.0] * 239 + [
        100.0 + i for i in range(1, 12)
    ]  # 250 筆，最後 11 天一路漲 → 創 52 週新高
    vols = [1000.0] * 249 + [3000.0]
    f = tech_facts(closes, vols, CFG, down_risk=True)
    assert f[0].startswith("今天漲 0.9%；近 5 日漲 4.7%；近 20 日漲 11.0%")
    assert f[1].startswith("收盤在 20 日均線之上") and "60 日均線之上" in f[1]
    assert "創 52 週新高" in f and "成交量是前 20 日平均的 3.0 倍" in f and "模型標了易跌" in f
    low = tech_facts([100.0] * 30 + [90.0], [1.0] * 31, CFG, down_risk=False)
    assert "今天是 20 日新低" in low and any(x.startswith("離 52 週高點還差 10.0%") for x in low)
    assert tech_facts([100.0], [1.0], CFG, False) == ["行情不足，算不出技術面"]


def test_ac1_chip_facts_lots_streaks_margin_and_holders() -> None:
    inst = [(-1000.0, -500.0, 0.0)] * 17 + [
        (2_000_000.0, 300_000.0, -1000.0)
    ] * 3  # 外資連買 3 天；股數換張
    margin = [1_000_000.0] * 15 + [1_100_000.0] * 5 + [1_200_000.0]
    holders = [(dt.date(2026, 10, 3), 0.40), (dt.date(2026, 10, 9), 0.404)]
    f = chip_facts(inst, margin, holders, CFG)
    assert f[0] == "外資近 5 日買超 5,998 張，近 20 日買超 5,983 張，連續買超 3 天"
    assert f[1] == "投信近 5 日買超 899 張，連續買超 3 天"
    assert f[2] == "自營商近 5 日賣超 3 張，連續賣超 3 天"
    assert f[3] == "融資近 5 日增加 100 張（9.1%）"
    assert f[4] == "千張大戶持股 40.4%（10/9 那週），比前一週多 0.4 個百分點"
    assert chip_facts([], [], [], CFG) == ["籌碼資料不足"]


def test_ac1_fund_facts_revenue_and_valuation() -> None:
    f = fund_facts([("2026-09", 0.637), ("2026-08", 0.486), ("2026-07", -0.05)], (10.5, 1.0, 0.06))
    assert f == [
        "9 月營收年增 63.7%；8 月年增 48.6%、7 月年減 5.0%",
        "本益比 10.5 倍、股價淨值比 1.0 倍、殖利率 6.0%",
    ]
    assert fund_facts([], (None, None, None)) == ["本益比無（可能虧損）"]
    assert fund_facts([], None) == ["基本面資料不足"]


# ── AC-2：分數與首選的核對、後備 ─────────────────────────────────────────────
def _dims(
    sid: str, rank: int, news: list[str] | None = None, scores: dict[str, int] | None = None
) -> Dimensions:
    return Dimensions(
        stock_id=sid,
        name=f"股{sid}",
        line="CORE",
        rank=rank,
        facts={
            "消息": news or [NO_NEWS],
            "技術": ["創 52 週新高", "成交量是前 20 日平均的 1.4 倍"],
            "籌碼": ["外資近 5 日買超 28,867 張"],
            "基本": ["9 月營收年增 63.7%"],
        },
        scores=scores or {"news": 5, "tech": 8, "chips": 7, "fund": 6},
    )


def _llm() -> LlmConfig:
    return LlmConfig.model_validate(
        {
            "models": {"m": {"id": "claude-x", "input_usd_per_1m": 1.0, "output_usd_per_1m": 1.0}},
            "tasks": {"pick_review": "m"},
        }
    )


def _item(**kw: str) -> PickReviewItem:
    base = {
        "news_view": "近期沒有重大消息",
        "tech_view": "創 52 週新高、量能放大，多頭排列",
        "chips_view": "外資買超，籌碼往法人集中",
        "fund_view": "9 月營收年增 63.7%，成長強勁",
        "stance": "偏多",
        "conclusion": "技術與籌碼同步走強，可列入觀察、逢回再評估",
    }
    base.update(kw)
    return PickReviewItem(**base)


def _run(
    tmp_path: Path, items_by_sid: dict[str, PickReviewItem], top: PickTop, items: list[Dimensions]
) -> tuple[list[Any], list[tuple[str, str]]]:
    seen: list[tuple[str, str]] = []

    def backend(model_id: str, system: str, user: str, schema: type, max_tokens: int) -> Any:
        seen.append((system, user))
        if schema is PickTop:
            return top, 4000, 80
        sid = next(k for k in items_by_sid if f"[{k} " in user)
        return items_by_sid[sid], 1300, 300

    out, _ = compose_line(
        InsightClient(_llm(), tmp_path, api_key="k", backend=backend),
        items,
        "大盤精選",
        DAY,
        NOW,
        CFG,
        BCFG,
    )
    return out, seen


def test_ac2_one_call_per_stock_then_one_for_top(tmp_path: Path) -> None:
    items = [
        _dims("2603", 1),
        _dims("2027", 2, ["今天是注意股"], {"news": 4, "tech": 8, "chips": 6, "fund": 6}),
        _dims("3010", 3),
    ]
    replies = {
        "2603": _item(),
        "2027": _item(
            news_view="注意股，留意交易限制", tech_view="技術面創新高"
        ),  # 技術觀點寫「技術面」→ 談到自己的面向可以；新高有事實
        "3010": _item(),
    }
    top = PickTop(top_sid="2603 長榮", top_reason="創 52 週新高、外資近 5 日買超 28,867 張")
    out, seen = _run(tmp_path, replies, top, items)
    assert len(seen) == 4 and seen[-1][0] == PICK_TOP_SYSTEM  # 三檔各一次＋首選一次
    assert seen[0] == (PICK_REVIEW_SYSTEM, stock_payload("大盤精選", DAY, items[0]))
    assert "【籌碼 7 分】" in seen[0][1]  # 規則分數給模型看，模型照著寫
    by = {r.stock_id: r for r in out}
    a = by["2603"]
    assert a.scores == {"news": 5, "tech": 8, "chips": 7, "fund": 6}  # 分數照規則，不是模型打的
    assert a.views == {
        "news": "近期沒有重大消息",
        "tech": "創 52 週新高、量能放大，多頭排列",
        "chips": "外資買超，籌碼往法人集中",
        "fund": "9 月營收年增 63.7%，成長強勁",
    }
    assert (
        a.stance == "偏多" and a.conclusion == "技術與籌碼同步走強，可列入觀察、逢回再評估"
    )  # 可以給建議（D-196 §二）
    assert by["2027"].scores == {"news": 4, "tech": 8, "chips": 6, "fund": 6}
    assert (
        a.is_top and a.source == "model" and a.reason == "創 52 週新高、外資近 5 日買超 28,867 張"
    )
    assert sum(r.is_top for r in out) == 1
    assert (
        "0～10" in PICK_REVIEW_SYSTEM
        and "不用打分" in PICK_REVIEW_SYSTEM
        and "目標價" in PICK_REVIEW_SYSTEM
        and PICK_REVIEW_PROMPT_VERSION.startswith("2026-")
    )


def test_ac2_views_stance_and_conclusion_checked_one_by_one(tmp_path: Path) -> None:
    items = [_dims("2603", 1, scores={"news": 5, "tech": 3, "chips": 3, "fund": 4})]
    bad = _item(
        news_view="無重大消息，技術面偏多",  # 消息觀點談到技術
        chips_view="外資連買，籌碼集中",  # 事實沒有連續買超
        fund_view="營收年增逾 60%",  # 數字不在事實
        stance="偏多",  # 總分 15（中性 20）偏多 → 不一致
        conclusion="技術面創新高，目標價上看 300 元",  # 喊價
    )
    out, _ = _run(
        tmp_path, {"2603": bad}, PickTop(top_sid="2603", top_reason="創 52 週新高"), items
    )
    r = out[0]
    assert r.scores is not None and r.views["tech"] is not None
    assert r.views["news"] is None and r.views["chips"] is None and r.views["fund"] is None
    assert r.stance is None and r.conclusion is None
    joined = "\n".join(r.checks)
    for w in ("談到別的面向", "連買", "數字不在事實裡", "不一致", "喊價或保證"):
        assert w in joined, w


def test_ac2_bad_reason_or_inconsistent_top_falls_back_to_highest_total(tmp_path: Path) -> None:
    items = [
        _dims("2603", 1, scores={"news": 5, "tech": 9, "chips": 8, "fund": 7}),
        _dims("2027", 2, scores={"news": 5, "tech": 3, "chips": 3, "fund": 4}),
    ]
    replies = {
        "2603": _item(),
        "2027": _item(stance="偏空", conclusion="技術與籌碼偏弱，宜觀望"),
    }
    # 理由有事實沒有的數字 → 後備
    out, _ = _run(tmp_path, replies, PickTop(top_sid="2603", top_reason="營收年增 80%"), items)
    top = next(r for r in out if r.is_top)
    assert top.stock_id == "2603" and top.source == "rule" and top.reason == rule_reason(top.scores)
    assert any("數字不在事實裡" in c for c in top.checks)
    # 挑了規則總分低的那檔（15 分，最高 29）→ 後備成最高分
    out2, _ = _run(tmp_path, replies, PickTop(top_sid="2027", top_reason="創 52 週新高"), items)
    top2 = next(r for r in out2 if r.is_top)
    assert (
        top2.stock_id == "2603"
        and top2.source == "rule"
        and any("不一致" in c for c in top2.checks)
    )
    assert (
        rule_reason({"news": 3, "tech": 5, "chips": 5, "fund": 5})
        == "四面向合計 18 分最高（消息 3、技術 5、籌碼 5、基本 5）"
    )


def test_ac2_reason_checks_reuse_the_blurb_rules() -> None:
    d = _dims("2603", 1)
    assert check_reason("創 52 週新高，9 月營收年增 63.7%。", d, BCFG, CFG.reason_chars) == (
        "創 52 週新高，9 月營收年增 63.7%",
        [],
    )
    assert any("中文" in x for x in check_reason("營收年增六成", d, BCFG, CFG.reason_chars)[1])
    assert any("建議" in x for x in check_reason("創新高，建議布局", d, BCFG, CFG.reason_chars)[1])
    assert any(
        "外資" not in x and "說法" in x
        for x in check_reason("股價突破", d, BCFG, CFG.reason_chars)[1]
    )


def test_ac2_model_absent_means_no_scores(tmp_path: Path) -> None:
    out, absent = compose_line(
        InsightClient(_llm(), tmp_path, api_key=None, backend=None),
        [_dims("2603", 1)],
        "大盤精選",
        DAY,
        NOW,
        CFG,
        BCFG,
    )
    assert absent and absent.startswith("模型缺席") and out[0].scores is None and not out[0].is_top


# ── AC-3：表、路由、每日 pipeline、雲端保留、拉回本機 ──────────────────────────
def test_ac3_wiring() -> None:
    spec = sch.spec_of("pick_reviews")
    assert spec.pk == ("date", "line", "stock_id") and {
        "news",
        "tech",
        "chips",
        "fund",
        "is_top",
        "top_reason",
    } <= set(spec.column_names)
    assert load_llm_config(_ROOT / "configs" / "llm.yaml").tasks["pick_review"] == "cf_qwen3"
    cli = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert (
        cli.index('PipelineStep("名單一句話"')
        < cli.index('PipelineStep("名單評分"')
        < cli.index('PipelineStep("洞悉分析"')
    )
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert (
        "名單評分" not in scfg.cloud.skip_pipeline_steps
        and scfg.cloud.windows["pick_reviews"] >= 80
    )  # D-196 要 60 天＋抱 20 天
    assert '"pick_reviews"' in (_ROOT / "scripts" / "pull_cloud_to_local.py").read_text(
        encoding="utf-8"
    )
    assert CFG.lines == ["CORE", "QUIET"]
