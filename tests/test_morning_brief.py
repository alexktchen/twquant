"""T-377／D-188：晨報——收資料（假 PG、假行情）、規則一句話、模型版、表與排程。不連網、不連 DB。"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from twquant.config import MorningBriefConfig, SystemConfig, load_config
from twquant.conventions import TZ
from twquant.data.serving import schema as sch
from twquant.data.sources.global_quotes import NightFuture, Quote
from twquant.data.sources.news_headlines import Headline
from twquant.insight.client import InsightClient
from twquant.insight.config import LlmConfig, load_llm_config
from twquant.insight.prompts import MORNING_BRIEF_PROMPT_VERSION, MORNING_BRIEF_SYSTEM
from twquant.insight.schemas import MorningBrief
from twquant.reporting.morning_brief import (
    MorningInputs,
    check_brief,
    classify,
    compose,
    facts,
    gather,
    reversed_directions,
    rule_text,
    signals,
    strip_self_reference,
    user_payload,
)

_ROOT = Path(__file__).resolve().parent.parent
DAY = dt.date(2026, 10, 17)
MDATE = dt.date(2026, 10, 16)
NOW = dt.datetime(2026, 10, 17, 6, 30, tzinfo=TZ)
CATS = json.loads((_ROOT / "configs" / "news_categories.json").read_text(encoding="utf-8"))[
    "categories"
]
CFG = load_config(_ROOT / "configs" / "system.yaml", SystemConfig).morning_brief


class _Cursor:
    def __init__(self, answers: list[tuple[str, list[str], list[tuple[Any, ...]]]]) -> None:
        self.answers = answers
        self.description: list[tuple[str]] = [("x",)]
        self._rows: list[tuple[Any, ...]] = []
        self.seen: list[str] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.seen.append(sql)
        for needle, cols, rows in self.answers:
            if needle in sql:
                self.description = [(c,) for c in cols]
                self._rows = rows
                return
        self.description, self._rows = [("x",)], []

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows


class _Conn:
    def __init__(self, answers: list[tuple[str, list[str], list[tuple[Any, ...]]]]) -> None:
        self.cur = _Cursor(answers)

    def cursor(self) -> _Cursor:
        return self.cur


def _market_row() -> tuple[list[str], tuple[Any, ...]]:
    cols = list(sch.MARKET_DAILY_SPEC.column_names)
    vals: dict[str, Any] = {c: None for c in cols}
    vals.update(
        date=MDATE,
        taiex_close=27000.0,
        taiex_chg=-0.0123,
        up=543,
        down=1238,
        flat=100,
        limit_up=40,
        limit_down=2,
        amount=5.1e11,
        amount_avg=4.0e11,
        amount_ratio=1.275,
        foreign_net=-2.5e10,
        trust_net=3e9,
        dealer_net=0.0,
        foreign_streak=-4,
    )
    return cols, tuple(vals[c] for c in cols)


def _conn(with_market: bool = True) -> _Conn:
    mcols, mrow = _market_row()
    tcols = list(sch.TAIFEX_DAILY_SPEC.column_names)
    trow = (MDATE, 3197, 82464, -93675, 9812, 103487, 88.75, 67.6, 55093, 81497, None)
    trow_prev = (
        dt.date(2026, 10, 15),
        1000,
        80000,
        -89768,
        9000,
        98768,
        74.99,
        60.84,
        77287,
        127036,
        None,
    )
    return _Conn(
        [
            ("to_regclass", ["t"], [("ok",)]),
            ('"market_daily"', mcols, [mrow] if with_market else []),
            ('"taifex_daily"', tcols, [trow, trow_prev]),
            (
                '"group_flow_daily"',
                ["grp", "share", "share_avg", "ret"],
                [
                    ("半導體", 0.30, 0.25, 0.01),
                    ("金融", 0.05, 0.08, -0.01),
                    ("航運", 0.03, 0.02, 0.02),
                ],
            ),
            ("WHERE date < %s)", ["line", "stock_id"], [("CORE", "2330"), ("QUIET", "1101")]),
            (
                '"daily_picks" WHERE date = %s',
                ["line", "stock_id"],
                [("CORE", "2330"), ("CORE", "2317"), ("QUIET", "8084")],
            ),
            (
                '"mops_announcements"',
                ["stock_id", "name", "subject", "available_at"],
                [
                    (
                        "8084",
                        "巨虹",
                        "本公司接獲通知列為變更交易方法",
                        dt.datetime(2026, 10, 16, 23, 4, tzinfo=TZ),
                    ),
                    (
                        "1234",
                        "黑松",
                        "公告本公司115年8月自結合併財務報告之負債比率",
                        dt.datetime(2026, 10, 16, 15, 0, tzinfo=TZ),
                    ),
                    (
                        "9999",
                        "某某",
                        "本公司董事會決議辦理現金增資",
                        dt.datetime(2026, 10, 16, 18, 0, tzinfo=TZ),
                    ),
                ],
            ),
            ('"prices_daily"', ["close"], [(1000.0,)]),
            (
                '"conference_calendar"',
                ["stock_id", "name", "conf_time", "summary"],
                [("2408", "南亞科", "15:00", "第 3 季營運")],
            ),
            ('"corporate_events"', ["stock_id"], [("2882",)]),
        ]
    )


def _quote(symbol: str, name: str) -> Quote:
    table = {
        "^GSPC": 0.01,
        "^IXIC": 0.02,
        "^DJI": 0.0,
        "^SOX": 0.03,
        "^VIX": 0.1,
        "^TNX": 0.0,
        "TWD=X": 0.0,
        "TSM": -0.02,
        "DX-Y.NYB": 0.0,
    }
    if symbol not in table:
        raise RuntimeError("沒有這個代號")
    last = {"TWD=X": 32.0, "TSM": 200.0}.get(symbol, 100.0)
    return Quote(
        symbol=symbol,
        name=name,
        date="2026-10-16",
        last=last,
        prev=last / (1 + table[symbol]),
        chg=table[symbol],
        open=False,
        time="05:00",
    )


def _night() -> NightFuture:
    return NightFuture(
        symbol="TXFJ6",
        date="2026-10-16",
        time="04:59:57",
        last=26500.0,
        ref=27100.0,
        chg=26500 / 27100 - 1,
        spot_close=27000.0,
        gap_pts=-500.0,
        volume=40941,
    )


# ── AC-1：收資料——昨天那一列、族群流向、名單新增、公告只留名單股與重要類別、ADR 折溢價 ─────
def test_ac1_gather_builds_inputs_from_pg_and_quotes() -> None:
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    assert inp.market_date == MDATE and inp.market is not None and inp.market["up"] == 543
    assert inp.taifex is not None and inp.taifex["fut_foreign_net_oi"] == -93675
    assert [m["grp"] for m in inp.inflow] == ["半導體", "航運"] and [
        m["grp"] for m in inp.outflow
    ] == ["金融"]
    assert sorted((p["name"], p["n"], p["new"]) for p in inp.picks) == [
        ("大盤精選", 2, 1),
        ("潛伏飆股", 1, 1),
    ]
    # 8084 在名單上（任何公告都留）、9999 命中全市場重要類別（現金增資）、黑松的自結比率兩者皆非 → 不留
    assert [(n["stock_id"], n["tags"]) for n in inp.news] == [
        ("8084", ["名單", "變更交易／下市"]),
        ("9999", ["減資／增資／私募"]),
    ]
    assert len(inp.us) == 4 and len(inp.extras) == 5 and inp.errors == []
    assert inp.night is not None and inp.night.gap_pts == -500.0
    assert inp.tsmc_close == 1000.0 and inp.adr_premium == (200.0 * 32.0 / 5 / 1000.0 - 1)
    assert inp.conferences_today[0]["stock_id"] == "2408" and inp.dividends_today == ["2882"]
    assert inp.revenue_deadline_in is None  # 10/17 距 11/10 太遠


def test_ac1_quote_failures_are_recorded_not_raised() -> None:
    def flaky(symbol: str, name: str) -> Quote:
        if symbol == "^SOX":
            raise RuntimeError("HTTP 429")
        return _quote(symbol, name)

    def no_night() -> NightFuture:
        raise RuntimeError("期交所沒回應")

    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=flaky, fetch_night=no_night)
    assert len(inp.us) == 3 and inp.night is None
    assert any("費半" in e and "429" in e for e in inp.errors) and any(
        "夜盤" in e for e in inp.errors
    )


def test_ac1_no_market_row_yet_means_empty_sections_not_errors() -> None:
    inp = gather(
        _conn(with_market=False), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night
    )
    assert inp.market is None and inp.picks == [] and inp.news == [] and inp.inflow == []
    assert rule_text(inp).startswith("美股")


# ── AC-2：規則一句話——每個數字都對得回輸入，缺的不說 ──────────────────────────────
def test_ac2_rule_text_cites_inputs_and_skips_missing() -> None:
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    text = rule_text(inp)
    # 2026-10-17 使用者：「太白話了」→ 改券商晨報口吻；數字與涵蓋的項目不變
    assert "美股收紅，四大指數均幅 +1.5%、費半 +3.0%" in text
    assert "台指期夜盤 26,500（-2.2%），逆價差 500 點" in text
    assert "前一交易日加權 -1.23%，漲跌家數 543：1238，漲停 40、跌停 2，量能 1.27 倍均量" in text
    assert "外資賣超 250 億（連 4 日賣超）" in text
    assert "外資期貨淨空單 93,675 口，P/C 未平倉比 68%" in text
    assert "資金流入 半導體、航運" in text and "今日名單 大盤精選 2 檔（新增 1）" in text
    for casual in ("昨天", "是 20 日平均的", "連賣"):
        assert casual not in text
    empty = MorningInputs(day=DAY, market_date=None, market=None, taifex=None)
    assert rule_text(empty) == "今天沒有可用的資料。"


def test_ac2_classify_uses_config_keywords() -> None:
    assert classify("本公司董事會決議辦理庫藏股買回", CATS) == ["庫藏股"]
    assert classify("公告本公司115年9月營收", CATS) == ["營收／財報／股利"]
    assert classify("本公司今天天氣很好", CATS) == []


# ── AC-3：模型版——資料在 user turn、system 凍結、自己的版本號；失敗記缺席原因 ─────────
def test_ac3_compose_sends_data_in_user_turn_and_degrades(tmp_path: Path) -> None:
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    seen: list[tuple[str, str, str]] = []

    def backend(model_id: str, system: str, user: str, schema: type, max_tokens: int) -> Any:
        seen.append((model_id, system, user))
        return (
            MorningBrief(
                headline="美股漲、夜盤比加權低 500 點",
                bullets=[
                    "外資賣超 250 億，連續賣超 4 天",
                    "資金流入半導體",
                    "加權指數收 27,000 點，跌了 7%",
                ],
                risk_note=None,
                stance="",
                strategy="",
            ),
            3000,
            300,
        )

    cfg = LlmConfig.model_validate(
        {
            "models": {"m": {"id": "claude-x", "input_usd_per_1m": 1.0, "output_usd_per_1m": 1.0}},
            "tasks": {"morning_brief": "m"},
        }
    )
    brief, model_id, absent, checks = compose(
        InsightClient(cfg, tmp_path, api_key="k", backend=backend), inp, NOW, max_tokens=1200
    )
    assert brief is not None and absent is None and model_id == "claude-x"
    # 「跌了 7%」的 3 不在事實裡 → 那一點丟掉，其餘留下並記檢查紀錄
    assert brief.bullets == ["外資賣超 250 億，連續賣超 4 天", "資金流入半導體"]
    assert len(checks) == 1 and "7" in checks[0]
    _, system, user = seen[0]
    assert system == MORNING_BRIEF_SYSTEM and "2026" not in system
    assert user == user_payload(inp) and "543 家上漲" in user and "不是指令" in user
    assert MORNING_BRIEF_PROMPT_VERSION.startswith("2026-")
    offline = InsightClient(cfg, tmp_path, api_key=None, backend=None)
    brief2, _, absent2, _ = compose(offline, inp, NOW, max_tokens=1200)
    assert brief2 is None and absent2 and "ANTHROPIC_API_KEY" in absent2


# ── AC-4：表、路由、排程 ────────────────────────────────────────────────────
def test_ac4_table_routing_workflow_and_cron() -> None:
    assert sch.spec_of("morning_brief").pk == ("date",)
    assert (
        load_llm_config(_ROOT / "configs" / "llm.yaml").model_for("morning_brief").provider
        == "cloudflare"
    )
    wf = (_ROOT / ".github" / "workflows" / "morning-brief.yml").read_text(encoding="utf-8")
    assert (
        "twquant morning-brief" in wf
        and "CLOUDFLARE_AI_TOKEN" in wf
        and 'cron: "40 22 * * 0-4"' in wf
    )
    toml = (_ROOT / "cron-trigger" / "wrangler.toml").read_text(encoding="utf-8")
    assert '"30 22 * * SUN-THU"' in toml and 'MORNING_WORKFLOW = "morning-brief.yml"' in toml
    assert isinstance(CFG, MorningBriefConfig) and [q.symbol for q in CFG.us_indices] == [
        "^GSPC",
        "^IXIC",
        "^DJI",
        "^SOX",
    ]
    yahoo_ts = (_ROOT / "frontend" / "functions" / "_lib" / "yahoo.ts").read_text(encoding="utf-8")
    for q in CFG.us_indices:
        assert f"symbol: '{q.symbol}'" in yahoo_ts  # 網站的四大指數與晨報一致


def test_ac1_missing_tables_leave_sections_empty_and_say_so() -> None:
    conn = _Conn([("to_regclass", ["t"], [(None,)])])
    inp = gather(conn, "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    assert inp.market is None and inp.news == [] and inp.conferences_today == []
    assert any("資料表還沒建" in e and "market_daily" in e for e in inp.errors)
    assert len(inp.us) == 4  # 行情照抓


# ── AC-5：事實句——數字由程式格式化（百分比、億、方向字），模型只能用這些數字 ─────────────
def test_ac5_facts_are_formatted_with_units_and_direction() -> None:
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    f = facts(inp)
    assert "2026-10-16 加權指數 -1.23%，收 27,000 點" in f
    assert "2026-10-16 外資估算賣超 250 億，連續賣超 4 天" in f
    assert "2026-10-16 投信估算買超 30 億" in f
    assert "外資台指期貨未平倉淨空單 93,675 口" in f
    assert any(
        x.startswith("台指期夜盤（近月）收 26,500 點") and "比加權收盤低 500 點" in x for x in f
    )
    assert any(x.startswith("台積電 ADR 對台股收盤溢價 28.0%") for x in f)
    assert any("重大訊息 8084" in x and "（名單、變更交易／下市）" in x for x in f)


def test_ac5_check_brief_rejects_wrong_headline_and_null_risk() -> None:
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    bad = MorningBrief(
        headline="外資逾 140 億賣超",
        bullets=["外資賣超 250 億"],
        risk_note=None,
        stance="",
        strategy="",
    )
    out, notes = check_brief(bad, inp)
    assert out is None and "140" in notes[0]
    ok = MorningBrief(
        headline="外資連續賣超 4 天",
        bullets=["外資賣超 250 億"],
        risk_note="null",
        stance="",
        strategy="",
    )
    out2, notes2 = check_brief(ok, inp)
    assert out2 is not None and out2.risk_note is None and notes2 == []


def test_ac5_prompt_asks_for_analyst_judgement() -> None:
    # 使用者 2026-10-17：「太白話了」→ v3 盤前快訊；「多一點分析師的角度」→ v4 先研判、再依據、最後觀察重點；數字規則照舊
    # 同日：「也加上昨晚國際金融大事、新聞頭條」「不用加上『我以分析師角度』」→ v5 加【大事】、禁止自報身分
    # 2026-10-18：「首頁的今日一句話也是要有推薦的方向」→ v6 加 stance＋strategy（今天的操作方向；不准喊點位或保證）
    assert MORNING_BRIEF_PROMPT_VERSION == "2026-10-18.7"
    for word in ("stance", "strategy", "持股水位", "偏多", "偏空", "不保證漲跌"):
        assert word in MORNING_BRIEF_SYSTEM
    for word in (
        "盤勢分析師",
        "【研判】",
        "【國際】",
        "【籌碼】",
        "【觀察】",
        "【大事】",
        "新聞頭條",
        "不要提到自己的身分",
        "研判素材",
        "逆價差",
        "不超過 30 字",
        "2% 以上才寫「大漲／重挫」",
        "不給目標價",
    ):
        assert word in MORNING_BRIEF_SYSTEM
    assert "白話" not in MORNING_BRIEF_SYSTEM and "散戶" not in MORNING_BRIEF_SYSTEM
    assert "只能使用事實句、研判素材或新聞頭條裡出現的數字" in MORNING_BRIEF_SYSTEM
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    assert any("比加權收盤低 500 點（逆價差）" in x for x in facts(inp))


# ── AC-6：研判素材——規則算出的背離、一致與部位增減（數字都在這裡算好，檢查照樣成立） ─────────
def test_ac6_signals_spell_out_divergences_and_position_changes() -> None:
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    sig = signals(inp)
    assert "研判素材：夜盤 -2.21%，延續日盤方向且幅度擴大" in sig
    assert "研判素材：費半 +3.00%，台股半導體與 AI 供應鏈權值股開盤可望受惠" in sig
    assert "研判素材：外資估算賣超、投信估算買超，法人看法分歧" in sig
    assert "研判素材：指數下跌且下跌家數較多，賣壓普遍" in sig
    assert "研判素材：成交量明顯高於 20 日平均，量能放大" in sig
    assert "研判素材：外資台指期淨空單比前一日增加 3,907 口（避險／看空部位加碼）" in sig
    assert "研判素材：資金由 金融 轉向 半導體、航運" in sig
    assert not any("連續" in x for x in sig)  # 外資連賣 4 天 < 門檻 5
    assert set(sig) <= set(facts(inp))  # 研判素材也在事實清單裡，模型引用的數字可檢查


def test_ac6_day_night_divergence_is_called_out() -> None:
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    assert inp.market is not None
    inp.market = {**inp.market, "taiex_chg": 0.003, "up": 1223, "down": 533}
    sig = signals(inp)
    assert "研判素材：加權指數前一交易日 +0.30%，夜盤卻 -2.21%，日夜盤方向相反" in sig
    assert "研判素材：指數上漲且上漲家數較多，漲勢普及" in sig


# ── AC-7：昨晚國際大事・新聞頭條（使用者 2026-10-17）──────────────────────────────
def _head(nid: int, hour: int, title: str, day: int = 16) -> Headline:
    return Headline(
        news_id=nid,
        title=title,
        summary=None,
        published_at=dt.datetime(2026, 10, day, hour, 0, tzinfo=TZ),
        category="國際政經",
        url=f"https://news.cnyes.com/news/id/{nid}",
        source="鉅亨網",
    )


def _news(category: str, limit: int, label: str) -> list[Headline]:
    if category == "headline":
        return [
            _head(1, 22, "Fed 官員暗示 12 月降息"),
            _head(2, 13, "台股午盤震盪"),  # 前一天 14:00 以前 → 不列
            _head(3, 5, "美股四大指數收黑", day=17),
        ]
    return [_head(1, 22, "Fed 官員暗示 12 月降息"), _head(4, 23, "油價跳漲 4%")]


def test_ac7_gather_keeps_headlines_after_previous_close() -> None:
    inp = gather(
        _conn(),
        "serving",
        DAY,
        CFG,
        CATS,
        fetch_quote=_quote,
        fetch_night=_night,
        fetch_news=_news,
        now=NOW,
    )
    assert [h.news_id for h in inp.headlines] == [3, 4, 1]  # 新到舊、去重、13:00 那則不列
    f = facts(inp)
    assert "新聞頭條（鉅亨網 10/17 05:00）：美股四大指數收黑" in f
    assert "新聞頭條（鉅亨網 10/16 23:00）：油價跳漲 4%" in f
    assert not any("台股午盤震盪" in x for x in f)
    # 頭條裡的數字可以被引用：逐句檢查不會把「4%」當成編造
    ok = MorningBrief(
        headline="油價跳漲 4%，台股開盤恐承壓",
        bullets=["【大事】油價跳漲 4%"],
        risk_note="",
        stance="",
        strategy="",
    )
    out, notes = check_brief(ok, inp)
    assert out is not None and notes == []


def test_ac7_news_failure_is_recorded_not_raised() -> None:
    def broken(category: str, limit: int, label: str) -> list[Headline]:
        raise RuntimeError("HTTP 503")

    inp = gather(
        _conn(),
        "serving",
        DAY,
        CFG,
        CATS,
        fetch_quote=_quote,
        fetch_night=_night,
        fetch_news=broken,
        now=NOW,
    )
    assert inp.headlines == []
    assert any(e.startswith("新聞 頭條") and "503" in e for e in inp.errors)


def test_ac7_self_reference_is_stripped() -> None:
    # 使用者 2026-10-17：「不用加上『我以分析師角度』」——模型寫了也刪掉
    assert strip_self_reference("我以分析師角度來看，台股開盤恐承壓") == "台股開盤恐承壓"
    assert (
        strip_self_reference("【研判】從分析師的觀點而言，外資賣壓未歇") == "【研判】外資賣壓未歇"
    )
    assert strip_self_reference("站在分析師立場：量能萎縮") == "量能萎縮"
    assert strip_self_reference("外資賣超 250 億") == "外資賣超 250 億"
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    brief = MorningBrief(
        headline="以分析師角度來看，外資連續賣超 4 天",
        bullets=["我以分析師角度分析，外資賣超 250 億"],
        risk_note="",
        stance="",
        strategy="",
    )
    out, _ = check_brief(brief, inp)
    assert out is not None
    assert out.headline == "外資連續賣超 4 天" and out.bullets == ["外資賣超 250 億"]


def test_ac7_flipped_news_direction_is_dropped() -> None:
    # 2026-10-17 實見：頭條「AI賣壓燒向債市！美債殖利率急跌」，模型寫成「美債殖利率急升」
    def news(category: str, limit: int, label: str) -> list[Headline]:
        return [
            _head(5, 22, "AI賣壓燒向債市！美債殖利率急跌 華爾街緊盯科技巨頭財報"),
            _head(6, 23, "油價跳漲 4%"),
            _head(7, 21, "金價上漲"),
            _head(8, 20, "金價回落"),  # 頭條之間方向不一 → 金價不檢查
        ]

    inp = gather(
        _conn(),
        "serving",
        DAY,
        CFG,
        CATS,
        fetch_quote=_quote,
        fetch_night=_night,
        fetch_news=news,
        now=NOW,
    )
    assert reversed_directions("美債殖利率急升反映避險情緒", inp) == ["殖利率"]
    assert reversed_directions("美債殖利率下降、油價走揚", inp) == []
    assert reversed_directions("油價下跌", inp) == ["油價"]
    assert reversed_directions("金價下跌", inp) == []
    brief = MorningBrief(
        headline="外資連續賣超 4 天，台股開盤恐承壓",
        bullets=["【大事】美債殖利率急升反映避險情緒", "外資賣超 250 億"],
        risk_note="",
        stance="",
        strategy="",
    )
    out, notes = check_brief(brief, inp)
    assert out is not None and out.bullets == ["外資賣超 250 億"]
    assert any("殖利率" in n and "方向" in n for n in notes)
    bad_head = MorningBrief(
        headline="油價重挫，台股承壓",
        bullets=["外資賣超 250 億"],
        risk_note="",
        stance="",
        strategy="",
    )
    out2, notes2 = check_brief(bad_head, inp)
    assert out2 is None and "方向寫反" in notes2[0]


# ── AC-8：今天的操作方向（T-396）——立場三選一、數字出自事實、不喊價；過了放在第一點【方向】 ─────────
def test_ac8_direction_is_checked_and_put_first() -> None:
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    assert inp.stances == ["偏多", "中性", "偏空"] and "目標價" in inp.forbidden_terms

    def brief(stance: str, strategy: str) -> MorningBrief:
        return MorningBrief(
            headline="外資連續賣超 4 天",
            bullets=["外資賣超 250 億"],
            risk_note="",
            stance=stance,
            strategy=strategy,
        )

    out, notes = check_brief(brief("偏空", "開盤不追高、持股降低，留意資金流入的半導體。"), inp)
    assert (
        out is not None
        and out.bullets[0] == "【方向】偏空｜開盤不追高、持股降低，留意資金流入的半導體"
        and notes == []
    )
    for stance, text, why in (
        ("看多", "逢低布局", "立場不是"),
        ("偏多", "目標價看 30000 點", "喊價或保證"),
        ("偏多", "外資賣超 999 億仍可布局", "不在事實裡"),
        ("偏多", "開盤" * 40, "太長"),
        ("偏空", "持股維持五成以下", "中文"),
        ("偏空", "持股提高、逢低進場", "矛盾"),
        ("偏空", "持股〈降低〉、開盤不追高", "佔位"),
    ):
        out2, notes2 = check_brief(brief(stance, text), inp)
        assert out2 is not None and not out2.bullets[0].startswith("【方向】"), why
        assert any(why in n for n in notes2), (why, notes2)
    out3, notes3 = check_brief(brief("", ""), inp)  # 舊版模型沒寫方向：不加這一點、不算錯
    assert out3 is not None and out3.bullets == ["外資賣超 250 億"] and notes3 == []
