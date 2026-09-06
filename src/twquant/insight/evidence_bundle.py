"""證據包建構（PLAN §3.4、PRD-002 §4）。

純運算層：資料由呼叫端從供應層取好後傳入，本模組不讀檔、不查 DB、不連網。

三條貫穿全檔的規則：

* **方向由規則決定，不由 LLM 決定**（PRD-002 §5）。支持／反對是可重現的計算結果，
  所以沒有 LLM 時證據包照樣有方向可看。
* **截點之後才可得的列一律排除**，並留下對應的 gap 與 missing 條目——不靜默丟掉。
* **`available_at` 是估計值時只能中性呈現**（D-081f）：PIT 強度不足以支持任何主張。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from twquant.conventions import assert_tw_timestamp, normalize_stock_id
from twquant.errors import DataError, TwquantError
from twquant.reporting.insight_evidence import InsightEvidence

_URI_PREFIX = "twquant://serving"
_HASH_LENGTH = 16
# 目前 repo 內沒有任何消息／公告／法說來源（見 D-081 的事實記錄）；這個區段
# 因此恆為缺口，直到 T-223 接上真正的來源為止。不得由 LLM 以常識補寫。
_NEWS_GAP_REASON = "repo 內尚無消息／公告／法說來源，本區段恆為缺口（T-223）"


@dataclass(frozen=True)
class EvidenceBundle:
    """One candidate's evidence plus the sections that could not be filled."""

    stock_id: str
    as_of: dt.datetime
    evidence: tuple[InsightEvidence, ...]
    missing: tuple[str, ...]
    bundle_hash: str


# evidence_id 以 "<section>_<name>" 命名；合約的 id 樣式不含冒號，故用底線分節。
def evidence_uri(table: str, stock_id: str, day: dt.date | None) -> str:
    """Return the internal URI for a serving-derived fact (D-081e)."""
    return f"{_URI_PREFIX}/{table}/{stock_id}/{day.isoformat() if day else 'latest'}"


def _timestamps(row: Mapping[str, Any], as_of: dt.datetime) -> tuple[dt.datetime, dt.datetime]:
    """Return (published_at, available_at); the row is the caller's own serving row."""
    available = row.get("available_at")
    if not isinstance(available, dt.datetime):
        raise DataError("evidence_bundle", None, "供應層列缺 available_at")
    return available, as_of


def _item(
    evidence_id: str,
    *,
    title: str,
    table: str,
    stock_id: str,
    day: dt.date | None,
    available_at: dt.datetime,
    captured_at: dt.datetime,
    direction: str,
    availability: str = "actual",
    industry: str | None = None,
) -> InsightEvidence:
    """Build one evidence item; estimated availability can never carry a support direction."""
    if availability == "estimated" and direction == "support":
        direction = "neutral"
    return InsightEvidence.model_validate(
        {
            "evidence_id": evidence_id,
            "title": title,
            "url": evidence_uri(table, stock_id, day),
            "published_at": available_at,
            "available_at": available_at,
            "captured_at": captured_at,
            "stock_id": stock_id,
            "industry": industry,
            "direction": direction,
            "availability": availability,
            "reason_ref": None,
        }
    )


def _gap(
    evidence_id: str, title: str, stock_id: str, as_of: dt.datetime, table: str
) -> InsightEvidence:
    """A known gap: recorded as evidence so an absence is visible rather than inferred."""
    return _item(
        evidence_id,
        title=title,
        table=table,
        stock_id=stock_id,
        day=None,
        available_at=as_of,
        captured_at=as_of,
        direction="gap",
    )


def _usable(rows: Sequence[Mapping[str, Any]], as_of: dt.datetime) -> list[Mapping[str, Any]]:
    """Keep only rows whose availability is at or before the cutoff."""
    return [
        row
        for row in rows
        if isinstance(row.get("available_at"), dt.datetime) and row["available_at"] <= as_of
    ]


def _ml_section(
    stock_id: str, as_of: dt.datetime, row: Mapping[str, Any] | None
) -> tuple[list[InsightEvidence], list[str]]:
    """The candidate's own model score: context for the analysis, never its own support."""
    if not row or not _usable([row], as_of):
        return [_gap("ml_missing", "無當日 ML 推論分數", stock_id, as_of, "ml_scores")], [
            "ml_score"
        ]
    available, captured = _timestamps(row, as_of)
    title = (
        f"模型 {row.get('model_id')} 於 {row.get('date')} 的分數 {float(row['score']):.5f}"
        f"（宇宙分位 {float(row.get('rank_pct', 0.0)):.4f}）"
    )
    return [
        _item(
            "ml_score",
            title=title,
            table="ml_scores",
            stock_id=stock_id,
            day=row.get("date"),
            available_at=available,
            captured_at=captured,
            direction="neutral",
        )
    ], []


def _fundamentals_section(
    stock_id: str, as_of: dt.datetime, sections: Mapping[str, Any]
) -> tuple[list[InsightEvidence], list[str]]:
    """Monthly revenue drives the direction; valuation is context only."""
    out: list[InsightEvidence] = []
    missing: list[str] = []
    revenue = _usable(sections.get("monthly_revenue") or [], as_of)
    if not revenue:
        out.append(
            _gap(
                "fundamentals_revenue_missing",
                "無可用月營收（截點前）",
                stock_id,
                as_of,
                "monthly_revenue",
            )
        )
        missing.append("monthly_revenue")
    else:
        row = max(revenue, key=lambda r: r["date"])
        available, captured = _timestamps(row, as_of)
        estimated = bool(row.get("available_at_estimated"))
        yoy = float(row.get("yoy") or 0.0)
        title = (
            f"{row.get('year_month')} 月營收 {float(row.get('revenue') or 0.0):,.0f}，年增 {yoy * 100:.1f}%"
            + ("（可取得時間為估計值，僅供參考）" if estimated else "")
        )
        out.append(
            _item(
                "fundamentals_revenue_yoy",
                title=title,
                table="monthly_revenue",
                stock_id=stock_id,
                day=row.get("date"),
                available_at=available,
                captured_at=captured,
                direction="support" if yoy > 0 else "contradict" if yoy < 0 else "neutral",
                availability="estimated" if estimated else "actual",
            )
        )
    valuation = sections.get("stock_valuation")
    if valuation and _usable([valuation], as_of):
        available, captured = _timestamps(valuation, as_of)
        out.append(
            _item(
                "fundamentals_valuation",
                title=(
                    f"PER {valuation.get('per')}、PBR {valuation.get('pbr')}、"
                    f"殖利率 {float(valuation.get('dividend_yield') or 0.0) * 100:.2f}%"
                ),
                table="stock_valuation",
                stock_id=stock_id,
                day=valuation.get("date"),
                available_at=available,
                captured_at=captured,
                direction="neutral",
            )
        )
    else:
        missing.append("stock_valuation")
    return out, missing


def _chips_section(
    stock_id: str, as_of: dt.datetime, sections: Mapping[str, Any]
) -> tuple[list[InsightEvidence], list[str]]:
    """Institutional net buying gives the direction; margin balances are context."""
    out: list[InsightEvidence] = []
    missing: list[str] = []
    inst = _usable(sections.get("institutional") or [], as_of)
    if not inst:
        out.append(
            _gap(
                "chips_institutional_missing",
                "無可用三大法人買賣超（截點前）",
                stock_id,
                as_of,
                "institutional_daily",
            )
        )
        missing.append("institutional")
    else:
        foreign = sum(float(row.get("foreign_net") or 0.0) for row in inst)
        trust = sum(float(row.get("trust_net") or 0.0) for row in inst)
        latest = max(inst, key=lambda r: r["date"])
        available, captured = _timestamps(latest, as_of)
        out.append(
            _item(
                "chips_foreign_net",
                title=f"近 {len(inst)} 個交易日外資合計淨買 {foreign:,.0f} 股、投信 {trust:,.0f} 股",
                table="institutional_daily",
                stock_id=stock_id,
                day=latest.get("date"),
                available_at=available,
                captured_at=captured,
                direction="support" if foreign > 0 else "contradict" if foreign < 0 else "neutral",
            )
        )
    margin = _usable(sections.get("margin") or [], as_of)
    if margin:
        latest = max(margin, key=lambda r: r["date"])
        available, captured = _timestamps(latest, as_of)
        out.append(
            _item(
                "chips_margin",
                title=f"融資餘額 {float(latest.get('margin_balance') or 0.0):,.0f}、融券 {float(latest.get('short_balance') or 0.0):,.0f}",
                table="margin_daily",
                stock_id=stock_id,
                day=latest.get("date"),
                available_at=available,
                captured_at=captured,
                direction="neutral",
            )
        )
    else:
        missing.append("margin")
    return out, missing


def _industry_section(
    stock_id: str, as_of: dt.datetime, row: Mapping[str, Any] | None
) -> tuple[list[InsightEvidence], list[str]]:
    """Sector flow direction, always labelled as the current classification (SPEC-014 §6)."""
    if not row:
        return [_gap("industry_missing", "無產業資金流資料", stock_id, as_of, "sector_flows")], [
            "industry"
        ]
    net = float(row.get("net_yi") or 0.0)
    title = (
        f"產業「{row.get('industry')}」近 {row.get('window')} 交易日法人淨流入 {net:.2f} 億、"
        f"報酬 {float(row.get('ret_pct') or 0.0):.2f}%（涵蓋 {row.get('n')} 檔；當前分類，非歷史 PIT 分類）"
    )
    return [
        _item(
            "industry_flow",
            title=title,
            table="sector_flows",
            stock_id=stock_id,
            day=row.get("as_of"),
            available_at=as_of,
            captured_at=as_of,
            direction="support" if net > 0 else "contradict" if net < 0 else "neutral",
            industry=str(row.get("industry")) if row.get("industry") else None,
        )
    ], []


def _events_section(
    stock_id: str, as_of: dt.datetime, sections: Mapping[str, Any]
) -> tuple[list[InsightEvidence], list[str]]:
    """Dividends and splits are context; a disposition contradicts; news is always a gap."""
    out: list[InsightEvidence] = []
    for row in _usable(sections.get("disposition") or [], as_of):
        available, captured = _timestamps(row, as_of)
        out.append(
            _item(
                "events_disposition",
                title=f"處置股票 {row.get('effective_start')}~{row.get('effective_end')}",
                table="disposition_stocks",
                stock_id=stock_id,
                day=row.get("date"),
                available_at=available,
                captured_at=captured,
                direction="contradict",
            )
        )
        break
    corporate = _usable(sections.get("corporate_events") or [], as_of)
    if corporate:
        latest = max(corporate, key=lambda r: r["date"])
        available, captured = _timestamps(latest, as_of)
        out.append(
            _item(
                "events_corporate",
                title=f"{latest.get('date')} {latest.get('event_type')}",
                table="corporate_events",
                stock_id=stock_id,
                day=latest.get("date"),
                available_at=available,
                captured_at=captured,
                direction="neutral",
            )
        )
    out.append(_gap("events_news_unavailable", _NEWS_GAP_REASON, stock_id, as_of, "news"))
    return out, ["news"]


def build_bundle(stock_id: str, as_of: dt.datetime, sections: Mapping[str, Any]) -> EvidenceBundle:
    """Assemble one candidate's evidence bundle from already-fetched serving rows.

    ``sections`` holds the caller's raw rows keyed by source (``ml``, ``monthly_revenue``,
    ``stock_valuation``, ``institutional``, ``margin``, ``industry``, ``corporate_events``,
    ``disposition``). Nothing here reaches the database, so the point-in-time cutoff the
    caller queried with is the only cutoff that matters, and it is re-checked per row.
    """
    if not isinstance(stock_id, str):
        raise DataError("evidence_bundle", None, "stock_id 必須是字串以保留前導零")
    stock = normalize_stock_id(stock_id)
    try:
        cutoff = assert_tw_timestamp(as_of)
    except TwquantError as exc:
        raise DataError(
            "evidence_bundle", None, f"as_of 必須是 tz-aware Asia/Taipei：{exc}"
        ) from exc

    evidence: list[InsightEvidence] = []
    missing: list[str] = []
    for items, gaps in (
        _ml_section(stock, cutoff, sections.get("ml")),
        _fundamentals_section(stock, cutoff, sections),
        _chips_section(stock, cutoff, sections),
        _industry_section(stock, cutoff, sections.get("industry")),
        _events_section(stock, cutoff, sections),
    ):
        evidence.extend(items)
        missing.extend(gaps)

    ids = [item.evidence_id for item in evidence]
    if len(ids) != len(set(ids)):
        raise DataError("evidence_bundle", None, f"evidence_id 重複：{sorted(ids)}")
    frozen = tuple(evidence)
    return EvidenceBundle(
        stock_id=stock,
        as_of=cutoff,
        evidence=frozen,
        missing=tuple(missing),
        bundle_hash=_hash(stock, cutoff, frozen),
    )


def _hash(stock_id: str, as_of: dt.datetime, evidence: tuple[InsightEvidence, ...]) -> str:
    """Content-address the bundle so an unchanged input reuses its saved analysis (PLAN §3.3)."""
    payload = {
        "stock_id": stock_id,
        "as_of": as_of.isoformat(),
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:_HASH_LENGTH]


def bundle_hash(bundle: EvidenceBundle) -> str:
    """Recompute a bundle's content hash; equal content always yields the same value."""
    return _hash(bundle.stock_id, bundle.as_of, bundle.evidence)
