"""T-377／D-188：晨報——收資料（假 PG、假行情）、規則一句話、模型版、表與排程。不連網、不連 DB。
T-409（2026-10-19，使用者：「這邊台灣晚上時間也要跑一次」）：盤後快訊——同一套流程 edition="evening"。"""

from __future__ import annotations

import dataclasses
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
from twquant.insight.prompts import (
    EVENING_BRIEF_PROMPT_VERSION,
    EVENING_BRIEF_SYSTEM,
    MORNING_BRIEF_PROMPT_VERSION,
    MORNING_BRIEF_SYSTEM,
)
from twquant.insight.schemas import MorningBrief
from twquant.reporting.morning_brief import (
    MorningInputs,
    check_brief,
    check_prev_evening,
    classify,
    compose,
    facts,
    gather,
    reversed_directions,
    rule_text,
    signals,
    strip_self_reference,
    user_payload,
    write_brief,
)
from twquant.reporting.news_events import NewsEvent

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
        self.params: list[tuple[Any, ...]] = []
        self.copied: list[tuple[Any, ...]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        self.seen.append(sql)
        self.params.append(params)
        for needle, cols, rows in self.answers:
            if needle in sql:
                self.description = [(c,) for c in cols]
                self._rows = rows
                return
        self.description, self._rows = [("x",)], []

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def copy(self, sql: str) -> _Cursor:  # upsert 的 COPY … FROM STDIN
        self.seen.append(sql)
        return self

    def write_row(self, row: tuple[Any, ...]) -> None:
        self.copied.append(row)


class _Conn:
    def __init__(self, answers: list[tuple[str, list[str], list[tuple[Any, ...]]]]) -> None:
        self.cur = _Cursor(answers)
        self.commits = 0

    def cursor(self) -> _Cursor:
        return self.cur

    def commit(self) -> None:
        self.commits += 1


def _market_row(date: dt.date = MDATE) -> tuple[list[str], tuple[Any, ...]]:
    cols = list(sch.MARKET_DAILY_SPEC.column_names)
    vals: dict[str, Any] = {c: None for c in cols}
    vals.update(
        date=date,
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


def _conn(with_market: bool = True, market_date: dt.date = MDATE) -> _Conn:
    mcols, mrow = _market_row(market_date)
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
                [("CORE", "2330"), ("CORE", "2317"), ("QUIET", "8084"), ("QUIET", "1234")],
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


# ── AC-1：收資料——昨天那一列、族群流向、公告只留全市場重要類別、ADR 折溢價 ─────────────────
# T-435／D-212：晨報每個人共用，名單只給 admin → 晨報不讀名單（資料庫裡有名單也一樣）
def test_ac1_gather_builds_inputs_from_pg_and_quotes() -> None:
    inp = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    assert inp.market_date == MDATE and inp.market is not None and inp.market["up"] == 543
    assert inp.taifex is not None and inp.taifex["fut_foreign_net_oi"] == -93675
    assert [m["grp"] for m in inp.inflow] == ["半導體", "航運"] and [
        m["grp"] for m in inp.outflow
    ] == ["金融"]
    assert not hasattr(inp, "picks")
    # 8084（變更交易）、9999（現金增資）命中全市場重要類別；黑松的自結比率不是——它在名單上也不留、誰都不標「名單」
    assert [(n["stock_id"], n["tags"]) for n in inp.news] == [
        ("8084", ["變更交易／下市"]),
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
    assert inp.market is None and inp.news == [] and inp.inflow == []
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
    assert "資金流入 半導體、航運" in text and "名單" not in text  # T-435：不寫各線檔數
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
        == "anthropic"  # D-219：由 Cloudflare qwen3 改 Claude
    )
    wf = (_ROOT / ".github" / "workflows" / "morning-brief.yml").read_text(encoding="utf-8")
    assert (
        "twquant morning-brief" in wf
        and "CLOUDFLARE_AI_TOKEN" in wf
        and 'cron: "40 22 * * 0-4"' in wf
    )
    toml = (_ROOT / "cron-trigger" / "wrangler.toml").read_text(encoding="utf-8")
    assert '"30 22 * * SUN-THU"' in toml and 'MORNING_WORKFLOW = "morning-brief.yml"' in toml
    # T-409 盤後快訊：另一張表、21:45 那班帶 edition=evening、GHA 備援 21:55
    assert sch.spec_of("evening_brief").pk == ("date",)
    assert sch.spec_of("evening_brief").column_names == sch.spec_of("morning_brief").column_names
    assert (
        '"0 10 * * MON-FRI"' in toml and 'EVENING_CRON = "0 10 * * MON-FRI"' in toml
    )  # 18:00 台北
    assert '--edition "$EDITION" $SKIP' in wf and 'cron: "10 10 * * 1-5"' in wf and "edition:" in wf
    # 版本看是哪條排程，不看幾點跑（備援晚 5 小時跑也不會變成盤前）；盤後備援日期往回 8 小時、已有就跳過
    assert (
        'github.event.schedule }}" = "10 10 * * 1-5"' in wf
        and "-d '-8 hours'" in wf
        and "--skip-existing" in wf
    )
    assert "date +%H" not in wf
    assert CFG.evening_wait_minutes >= 30, "18:00 觸發時 pipeline 有時還沒寫好大盤體檢，要等"
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
    assert any("重大訊息 8084" in x and x.endswith("（變更交易／下市）") for x in f)
    assert not any("名單" in x for x in f)  # T-435：事實裡沒有名單（檔數、「名單」標記都沒有）


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
    # 2026-10-20：「凌晨那則要參考前一天下午那則」→ v8 【研判】對照昨天盤後快訊（延續或修正）
    assert MORNING_BRIEF_PROMPT_VERSION == "2026-10-20.9"
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


# ── T-409：盤後快訊——今天的收盤、今天開盤起的新聞、明天的行事曆、另一張表、盤後提示詞 ──────────
EVENING_NOW = dt.datetime(2026, 10, 17, 21, 45, tzinfo=TZ)


def _news_evening(category: str, limit: int, label: str) -> list[Headline]:
    return [
        _head(3, 5, "美股四大指數收黑", day=17),  # 今天 09:00 以前 → 盤後快訊不列
        _head(5, 10, "台積電法說會上修資本支出", day=17),
        _head(6, 20, "外資今日賣超 300 億", day=17),
        _head(1, 22, "Fed 官員暗示 12 月降息"),  # 昨天
    ]


def test_t409_evening_reads_today_close_news_from_open_and_tomorrow_calendar() -> None:
    conn = _conn(market_date=DAY)
    inp = gather(
        conn,
        "serving",
        DAY,
        CFG,
        CATS,
        fetch_quote=_quote,
        fetch_night=_night,
        fetch_news=_news_evening,
        now=EVENING_NOW,
        edition="evening",
    )
    assert inp.edition == "evening" and inp.market_date == DAY and inp.market is not None
    assert any('"market_daily" WHERE date <= %s' in q for q in conn.cur.seen), "盤後讀今天的收盤"
    cal = [
        prm
        for q, prm in zip(conn.cur.seen, conn.cur.params, strict=True)
        if '"conference_calendar"' in q
    ]
    assert cal == [(DAY + dt.timedelta(days=1),)], "行事曆看明天"
    assert [h.news_id for h in inp.headlines] == [6, 5], "今天 09:00 起的新聞，新到舊"
    f = facts(inp)
    assert "明天法說會 2408 南亞科 15:00" in f and "明天除權息：2882" in f
    assert "2026-10-17 加權指數 -1.23%，收 27,000 點" in f
    assert user_payload(inp).startswith("以下為今天盤後快訊的事實")
    # 盤前照舊：昨天的收盤、今天的行事曆
    conn2 = _conn()
    inp_m = gather(conn2, "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    assert inp_m.edition == "morning" and inp_m.market_date == MDATE
    assert any('"market_daily" WHERE date < %s' in q for q in conn2.cur.seen)
    assert "今天法說會 2408 南亞科 15:00" in facts(inp_m)


def test_t409_evening_rule_text_leads_with_today_and_labels_us_as_last_night() -> None:
    inp = gather(
        _conn(market_date=DAY),
        "serving",
        DAY,
        CFG,
        CATS,
        fetch_quote=_quote,
        fetch_night=_night,
        edition="evening",
    )
    rule = rule_text(inp)
    assert rule.startswith("今日加權 -1.23%，漲跌家數 543：1238")
    assert "前一晚美股收紅" in rule and rule.index("今日加權") < rule.index("前一晚美股")
    assert rule.index("外資賣超") < rule.index("台指期夜盤") < rule.index("前一晚美股")
    # 日夜盤方向相反時，研判素材寫「今日」而不是「前一交易日」
    assert inp.night is not None
    up_night = dataclasses.replace(inp, night=dataclasses.replace(inp.night, chg=0.01))
    assert any("加權指數今日 -1.23%，夜盤卻 +1.00%" in x for x in signals(up_night))
    # 盤前的用詞不變
    inp_m = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    rule_m = rule_text(inp_m)
    assert rule_m.startswith("美股收紅") and "前一交易日加權 -1.23%" in rule_m
    assert inp_m.night is not None
    up_night_m = dataclasses.replace(inp_m, night=dataclasses.replace(inp_m.night, chg=0.01))
    assert any("加權指數前一交易日 -1.23%，夜盤卻 +1.00%" in x for x in signals(up_night_m))


def test_t409_evening_uses_evening_prompt_and_writes_its_own_table(tmp_path: Path) -> None:
    inp = gather(
        _conn(market_date=DAY),
        "serving",
        DAY,
        CFG,
        CATS,
        fetch_quote=_quote,
        fetch_night=_night,
        edition="evening",
    )
    seen: list[tuple[str, str, str]] = []

    def backend(model_id: str, system: str, user: str, schema: type, max_tokens: int) -> Any:
        seen.append((model_id, system, user))
        return (
            MorningBrief(
                headline="外資賣壓未歇，台股收黑",
                bullets=["【研判】外資賣超 250 億，連續賣超 4 天"],
                risk_note="",
                stance="",
                strategy="",
            ),
            3000,
            300,
        )

    cfg = LlmConfig.model_validate(
        {
            "models": {"m": {"id": "qwen-x", "input_usd_per_1m": 1.0, "output_usd_per_1m": 1.0}},
            "tasks": {"morning_brief": "m"},
        }
    )
    brief, model_id, absent, _ = compose(
        InsightClient(cfg, tmp_path, api_key="k", backend=backend),
        inp,
        EVENING_NOW,
        max_tokens=1200,
    )
    assert brief is not None and absent is None and model_id == "qwen-x"
    _, system, user = seen[0]
    assert system == EVENING_BRIEF_SYSTEM and system != MORNING_BRIEF_SYSTEM
    assert "收盤後" in system and "明天" in system and "2026" not in system
    assert user.startswith("以下為今天盤後快訊的事實")
    assert EVENING_BRIEF_PROMPT_VERSION.startswith("2026-") and (
        EVENING_BRIEF_PROMPT_VERSION != MORNING_BRIEF_PROMPT_VERSION
    )

    conn = _conn(market_date=DAY)
    write_brief(
        conn,
        "serving",
        inp,
        rule_text(inp),
        brief,
        model_id=model_id,
        absent_reason=None,
        checks=[],
        now=EVENING_NOW,
    )
    sql = "\n".join(conn.cur.seen)
    assert '"serving"."evening_brief"' in sql and "morning_brief" not in sql
    # 建表一次 commit、寫入一次 commit
    assert len(conn.cur.copied) == 1 and conn.cur.copied[0][0] == DAY and conn.commits == 2

    # 盤前寫原本的表
    conn_m = _conn()
    inp_m = gather(conn_m, "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    write_brief(
        conn_m,
        "serving",
        inp_m,
        rule_text(inp_m),
        None,
        model_id=None,
        absent_reason="x",
        checks=[],
        now=NOW,
    )
    assert '"serving"."morning_brief"' in "\n".join(conn_m.cur.seen)


# ── 2026-10-20：盤前晨報參考前一個交易日的盤後快訊（使用者：「凌晨那則要參考前一天下午那則」）──
def _conn_with_evening(ev_date: dt.date) -> _Conn:
    conn = _conn()
    conn.cur.answers.insert(
        0,
        (
            '"evening_brief"',
            ["date", "generated_at", "headline", "bullets", "risk_note"],
            [
                (
                    ev_date,
                    dt.datetime(2026, 10, 16, 17, 52, tzinfo=TZ),
                    "外資賣壓未歇，台股收黑",
                    json.dumps(
                        ["【方向】偏空｜持股降低、明天不追高", "【研判】外資賣超 250 億"],
                        ensure_ascii=False,
                    ),
                    "今晚美股波動",
                )
            ],
        ),
    )
    return conn


def test_morning_cites_previous_evening_brief() -> None:
    inp = gather(
        _conn_with_evening(MDATE), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night
    )
    assert (
        inp.prev_evening is not None
        and inp.prev_evening["direction"] == "偏空｜持股降低、明天不追高"
    )
    f = facts(inp)
    assert "昨天盤後快訊（10/16 17:52）的研判：外資賣壓未歇，台股收黑" in f
    assert "昨天盤後快訊給的今日方向：偏空｜持股降低、明天不追高" in f
    assert "昨天盤後快訊的風險提醒：今晚美股波動" in f
    assert "昨天盤後快訊" in MORNING_BRIEF_SYSTEM and "修正" in MORNING_BRIEF_SYSTEM
    # 盤後快訊自己不讀前一份
    ev = gather(
        _conn_with_evening(MDATE),
        "serving",
        DAY,
        CFG,
        CATS,
        fetch_quote=_quote,
        fetch_night=_night,
        edition="evening",
    )
    assert ev.prev_evening is None


def test_morning_ignores_evening_brief_from_another_day() -> None:
    conn = _conn_with_evening(MDATE)
    inp = gather(conn, "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    sql = [p for q, p in zip(conn.cur.seen, conn.cur.params, strict=True) if '"evening_brief"' in q]
    assert sql == [(MDATE,)], "只讀前一個交易日（大盤體檢那天）那份"
    assert inp.prev_evening is not None


def test_morning_prev_evening_claims_must_match_stance() -> None:
    inp = gather(
        _conn_with_evening(MDATE), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night
    )
    notes: list[str] = []
    ok_keep = "【研判】延續昨日盤後偏空看法，外資賣超 250 億"
    ok_fix = "【研判】修正昨日盤後偏空看法，費半大漲"
    assert check_prev_evening([ok_keep, "【國際】費半大漲"], "偏空", inp, notes) == [
        ok_keep,
        "【國際】費半大漲",
    ]
    assert check_prev_evening([ok_fix], "偏多", inp, notes) == [ok_fix]
    assert notes == []
    # 2026-10-20 實見：說延續偏空、立場給偏多 → 丟
    assert (
        check_prev_evening(["【研判】美股走強，延續昨日盤後偏空看法，夜盤續弱"], "偏多", inp, notes)
        == []
    )
    assert "今天立場卻是「偏多」" in notes[-1]
    assert check_prev_evening([ok_fix], "偏空", inp, notes) == [] and "說修正" in notes[-1]
    assert check_prev_evening(["【研判】延續昨日盤後偏多看法"], "偏多", inp, notes) == []
    assert "寫成「偏多」" in notes[-1]
    assert check_prev_evening(["【研判】〈延續或修正〉昨日盤後偏空看法"], "偏空", inp, notes) == []
    # 括號一起抄、但裡面是真內容 → 拿掉括號留下（2026-10-20 實測）
    assert check_prev_evening(
        ["【研判】修正昨日盤後〈偏空〉看法，〈資金轉向電路板〉"], "偏多", inp, notes
    ) == ["【研判】修正昨日盤後偏空看法，資金轉向電路板"]
    # 沒有昨天盤後快訊：提到昨日盤後就丟（不准編）
    bare = gather(_conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night)
    assert check_prev_evening(["【研判】延續昨日盤後偏空看法"], "偏空", bare, notes) == []


# ── 晨報接新聞（2026-10-20）：新聞服務的多家同報事件當事實句 ──────────────────────────
def _event(
    ev: str, title: str, outlets: tuple[str, ...], hour: int, stocks: tuple[str, ...] = ()
) -> NewsEvent:
    t = dt.datetime(2026, 10, 16, hour, 0, tzinfo=TZ)
    return NewsEvent(
        event_id=ev,
        title=title,
        url=f"https://x/{ev}",
        outlets=outlets,
        items=len(outlets),
        first_seen_at=t,
        last_seen_at=t,
        stocks=stocks,
    )


def test_events_become_fact_lines_and_direction_checks_use_them() -> None:
    seen: list[tuple[dt.datetime, dt.datetime]] = []

    def events(since: dt.datetime, until: dt.datetime) -> list[NewsEvent]:
        seen.append((since, until))
        return [
            _event(
                "A",
                "台積電跌25元至2395元 台股早盤挫逾400點",
                ("中央社", "經濟日報", "鉅亨網"),
                10,
                ("台積電",),
            ),
            _event(
                "B", "聯準會暗示降息 美債殖利率下滑", ("Bloomberg", "CNBC", "中央社", "鉅亨網"), 22
            ),
        ]

    inp = gather(
        _conn(),
        "serving",
        DAY,
        CFG,
        CATS,
        fetch_quote=_quote,
        fetch_night=_night,
        fetch_events=events,
        now=NOW,
    )
    assert seen == [(dt.datetime(2026, 10, 16, 14, 0, tzinfo=TZ), NOW)], (
        "盤前：前一天 14:00 到現在，跟頭條同一個時間窗"
    )
    f = facts(inp)
    assert (
        "多家同報（3 家：中央社、經濟日報、鉅亨網；10/16 10:00）：台積電跌25元至2395元 台股早盤挫逾400點（提到 台積電）"
        in f
    )
    assert (
        "多家同報（4 家：Bloomberg、CNBC、中央社、鉅亨網；10/16 22:00）：聯準會暗示降息 美債殖利率下滑"
        in f
    )
    # 事實句裡的數字可以被引用
    ok = MorningBrief(
        headline="台積電跌25元，台股早盤挫逾400點",
        bullets=["【大事】3 家媒體報台積電跌25元"],
        risk_note="",
        stance="",
        strategy="",
    )
    out, notes = check_brief(ok, inp)
    assert out is not None and notes == []
    # 方向寫反也會被抓：事件說殖利率下滑，模型寫上升
    inp.news_subjects = ["美債殖利率"]
    assert reversed_directions("美債殖利率上升", inp) == ["美債殖利率"]


def test_events_failure_is_recorded_not_raised_and_absent_means_empty() -> None:
    def broken(since: dt.datetime, until: dt.datetime) -> list[NewsEvent]:
        raise RuntimeError("connection refused")

    inp = gather(
        _conn(),
        "serving",
        DAY,
        CFG,
        CATS,
        fetch_quote=_quote,
        fetch_night=_night,
        fetch_events=broken,
        now=NOW,
    )
    assert inp.events == [] and any(e.startswith("多家同報") and "refused" in e for e in inp.errors)
    bare = gather(
        _conn(), "serving", DAY, CFG, CATS, fetch_quote=_quote, fetch_night=_night, now=NOW
    )
    assert bare.events == [] and not any(e.startswith("多家同報") for e in bare.errors)
    assert not any(x.startswith("多家同報") for x in facts(bare))
