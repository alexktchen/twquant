"""T-372／D-187：證據包接上重大訊息、處置、注意股；qwen3 關推理；prompt 改版。不連網、不連 DB。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from twquant.conventions import TZ
from twquant.insight.client import InsightClient
from twquant.insight.config import LlmConfig, load_llm_config
from twquant.insight.evidence_bundle import build_bundle
from twquant.insight.prompts import CANDIDATE_ANALYSIS_SYSTEM, PROMPT_VERSION
from twquant.reporting.insight_sources import fetch_material_news, fetch_sections

_ROOT = Path(__file__).resolve().parent.parent
AS_OF = dt.datetime(2026, 10, 16, 20, 0, tzinfo=TZ)
BEFORE = dt.datetime(2026, 10, 16, 18, 0, tzinfo=TZ)


def _news(subject: str, at: dt.datetime) -> dict[str, Any]:
    return {"date": at.date(), "subject": subject, "clause": "20", "available_at": at}


def _ids(bundle: Any) -> dict[str, Any]:
    return {item.evidence_id: item for item in bundle.evidence}


# ── AC-1：有公告 → 每則一條中性證據，新到舊，標題含發言時間與主旨；不再是缺口 ──────
def test_ac1_filings_become_neutral_evidence_newest_first() -> None:
    rows = [
        _news("本公司董事會決議發放現金股利", dt.datetime(2026, 10, 10, 17, 40, tzinfo=TZ)),
        _news("公告本公司取得機器設備", dt.datetime(2026, 10, 15, 14, 5, 9, tzinfo=TZ)),
    ]
    bundle = build_bundle("2330", AS_OF, {"material_news": {"window_days": 30, "rows": rows}})
    items = _ids(bundle)
    assert items["events_news_1"].title == "2026-10-15 14:05 重大訊息：公告本公司取得機器設備"
    assert items["events_news_2"].title.startswith("2026-10-10 17:40 重大訊息：")
    assert {items[k].direction for k in ("events_news_1", "events_news_2")} == {"neutral"}
    assert items["events_news_1"].url == "twquant://serving/mops_announcements/2330/2026-10-15"
    assert "events_news_unavailable" not in items and "news" not in bundle.missing


# ── AC-2：來源涵蓋但沒有公告＝事實；來源沒涵蓋（None 或沒給）＝缺口 ────────────────
def test_ac2_covered_but_quiet_is_a_fact_and_uncovered_stays_a_gap() -> None:
    quiet = _ids(build_bundle("2330", AS_OF, {"material_news": {"window_days": 12, "rows": []}}))
    assert quiet["events_news_none"].title == "近 12 日無重大訊息"
    assert quiet["events_news_none"].direction == "neutral"
    for sections in ({"material_news": None}, {}):
        bundle = build_bundle("2330", AS_OF, sections)
        assert _ids(bundle)["events_news_unavailable"].direction == "gap"
        assert "news" in bundle.missing


# ── AC-3：截點之後的公告不進證據包（PIT） ────────────────────────────────────
def test_ac3_filings_after_the_cutoff_are_excluded() -> None:
    late = _news("盤後公告", dt.datetime(2026, 10, 16, 21, 30, tzinfo=TZ))
    items = _ids(
        build_bundle("2330", AS_OF, {"material_news": {"window_days": 30, "rows": [late]}})
    )
    assert "events_news_1" not in items and "events_news_none" in items


# ── AC-4：注意股次數；處置照舊是反對 ────────────────────────────────────────
def test_ac4_attention_flags_are_counted_and_dispositions_contradict() -> None:
    attention = {
        "window_days": 10,
        "rows": [
            {"date": dt.date(2026, 10, 14), "available_at": BEFORE - dt.timedelta(days=2)},
            {"date": dt.date(2026, 10, 16), "available_at": BEFORE},
        ],
    }
    disposition = [
        {
            "date": dt.date(2026, 10, 15),
            "effective_start": dt.date(2026, 10, 16),
            "effective_end": dt.date(2026, 10, 29),
            "available_at": BEFORE - dt.timedelta(days=1),
        }
    ]
    items = _ids(build_bundle("2330", AS_OF, {"attention": attention, "disposition": disposition}))
    assert items["events_attention"].title == "近 10 日被列注意股 2 次（最近 2026-10-16）"
    assert items["events_attention"].direction == "neutral"
    assert items["events_disposition"].direction == "contradict"
    assert "events_attention" not in _ids(build_bundle("2330", AS_OF, {"attention": {"rows": []}}))


# ── AC-5：取資料端只在來源真的涵蓋時才回列表，窗口天數是實際涵蓋的天數 ─────────────
class _Cursor:
    def __init__(self, answers: list[tuple[str, list[str], list[tuple[Any, ...]]]]) -> None:
        self.answers = answers
        self.description: list[tuple[str]] = []
        self._rows: list[tuple[Any, ...]] = []
        self.seen: list[str] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[Any, ...]) -> None:
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


def _span(
    lo: dt.date | None, hi: dt.date | None, table: str | None = "serving.mops_announcements"
) -> _Conn:
    return _Conn(
        [
            ("to_regclass", ["t"], [(table,)]),
            ("min(date)", ["lo", "hi"], [(lo, hi)]),
            ("SELECT date, subject", ["date", "subject", "clause", "available_at"], []),
        ]
    )


def test_ac5_coverage_rules_for_the_news_window() -> None:
    day = AS_OF.date()
    assert fetch_material_news(_span(None, None, table=None), "serving", "2330", AS_OF) is None
    assert fetch_material_news(_span(None, None), "serving", "2330", AS_OF) is None
    stale = _span(day - dt.timedelta(days=20), day - dt.timedelta(days=3))
    assert (
        fetch_material_news(stale, "serving", "2330", AS_OF) is None
    )  # 來源停了，不說「沒有公告」
    young = _span(day - dt.timedelta(days=4), day)
    assert fetch_material_news(young, "serving", "2330", AS_OF) == {"window_days": 4, "rows": []}
    full = _span(day - dt.timedelta(days=30), day)
    assert fetch_material_news(full, "serving", "2330", AS_OF) == {"window_days": 30, "rows": []}


def test_ac5_fetch_sections_reads_disposition_attention_and_news_from_pg() -> None:
    conn = _span(AS_OF.date() - dt.timedelta(days=30), AS_OF.date())
    sections = fetch_sections(conn, "serving", "2330", AS_OF)
    assert sections["disposition"] == []
    assert sections["attention"] == {"window_days": 10, "rows": []}
    assert sections["material_news"] == {"window_days": 30, "rows": []}
    joined = "\n".join(conn.cur.seen)
    assert "disposition_stocks" in joined and "effective_end >= %s" in joined
    assert "attention_stocks" in joined and "mops_announcements" in joined


# ── AC-6：qwen3 帶 /no_think；prompt 改版並說明公告的讀法 ─────────────────────────
class _Tiny(BaseModel):
    ok: bool


def test_ac6_model_suffix_is_appended_only_for_that_model(tmp_path: Path) -> None:
    llm = load_llm_config(_ROOT / "configs" / "llm.yaml")
    assert llm.model_for("candidate_analysis").system_suffix == " /no_think"
    assert llm.model_for("advisor").system_suffix == ""

    seen: list[str] = []

    def backend(model_id: str, system: str, user: str, schema: type, max_tokens: int) -> Any:
        seen.append(system)
        return _Tiny(ok=True), 10, 5

    cfg = LlmConfig.model_validate(
        {
            "models": {
                "q": {
                    "id": "@cf/x",
                    "provider": "cloudflare",
                    "input_usd_per_1m": 0.1,
                    "output_usd_per_1m": 0.1,
                    "input_neurons_per_1m": 1.0,
                    "output_neurons_per_1m": 1.0,
                    "system_suffix": " /no_think",
                },
                "o": {"id": "claude-x", "input_usd_per_1m": 1.0, "output_usd_per_1m": 1.0},
            },
            "tasks": {"daily_brief": "q", "advisor": "o"},
        }
    )
    client = InsightClient(
        cfg, tmp_path, backend=backend, api_key="k", cf_account_id="a", cf_token="t"
    )
    for task in ("daily_brief", "advisor"):
        res, _ = client.complete_detailed(
            task=task, system="SYS", user="U", schema=_Tiny, prompt_version="v", now=AS_OF
        )
        assert res is not None
    assert seen == ["SYS /no_think", "SYS"]


def test_ac6_prompt_version_bumped_and_filings_rule_present() -> None:
    assert PROMPT_VERSION == "2026-10-17.1"
    assert (
        "重大訊息" in CANDIDATE_ANALYSIS_SYSTEM
        and "不要因為有公告就判 support" in CANDIDATE_ANALYSIS_SYSTEM
    )
