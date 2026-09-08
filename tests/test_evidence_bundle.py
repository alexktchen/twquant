"""T-217 / PLAN §3.4: evidence bundle construction from serving-layer rows."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from twquant.conventions import TZ
from twquant.errors import DataError
from twquant.insight.evidence_bundle import EvidenceBundle, build_bundle, bundle_hash, evidence_uri

AS_OF = dt.datetime(2026, 9, 4, 20, 0, tzinfo=TZ)
BEFORE = dt.datetime(2026, 9, 3, 9, 0, tzinfo=TZ)
AFTER = dt.datetime(2026, 9, 5, 9, 0, tzinfo=TZ)


def _sections(**patch: Any) -> dict[str, Any]:
    rows: dict[str, Any] = {
        "ml": {
            "date": dt.date(2026, 9, 4),
            "score": 0.13,
            "rank_pct": 0.99,
            "model_id": "lgbm_net_h20",
            "available_at": BEFORE,
        },
        "monthly_revenue": [
            {
                "date": dt.date(2026, 8, 10),
                "year_month": "2026-07",
                "revenue": 1234567.0,
                "yoy": 0.18,
                "available_at": BEFORE,
                "available_at_estimated": False,
            }
        ],
        "stock_valuation": {
            "date": dt.date(2026, 9, 4),
            "per": 12.5,
            "pbr": 1.8,
            "dividend_yield": 0.031,
            "available_at": BEFORE,
        },
        "institutional": [
            {
                "date": dt.date(2026, 9, 3),
                "foreign_net": 2000,
                "trust_net": 100,
                "dealer_net": 0,
                "available_at": BEFORE,
            },
            {
                "date": dt.date(2026, 9, 4),
                "foreign_net": 1500,
                "trust_net": -50,
                "dealer_net": 0,
                "available_at": BEFORE,
            },
        ],
        "margin": [
            {
                "date": dt.date(2026, 9, 4),
                "margin_balance": 1000,
                "short_balance": 50,
                "available_at": BEFORE,
            }
        ],
        "industry": {
            "industry": "半導體",
            "net_yi": 12.5,
            "ret_pct": 2.31,
            "n": 2,
            "window": 20,
            "as_of": dt.date(2026, 9, 4),
        },
        "corporate_events": [
            {"date": dt.date(2026, 8, 20), "event_type": "cash_dividend", "available_at": BEFORE}
        ],
        "disposition": [],
    }
    rows.update(patch)
    return rows


def _by_id(bundle: EvidenceBundle) -> dict[str, Any]:
    return {item.evidence_id: item for item in bundle.evidence}


def test_ac217_1_every_section_produces_evidence_with_internal_uris() -> None:
    bundle = build_bundle("2330", AS_OF, _sections())

    sections = {item.evidence_id.split("_", 1)[0] for item in bundle.evidence}
    assert sections == {"ml", "fundamentals", "chips", "industry", "events"}
    # D-081(e): serving-derived evidence carries an internal URI, never a fake web address.
    assert all(item.url.startswith("twquant://serving/") for item in bundle.evidence)
    assert (
        evidence_uri("monthly_revenue", "2330", dt.date(2026, 8, 10))
        == "twquant://serving/monthly_revenue/2330/2026-08-10"
    )
    assert all(item.stock_id == "2330" for item in bundle.evidence)


def test_ac217_1_direction_comes_from_rules_not_from_a_model() -> None:
    positive = _by_id(build_bundle("2330", AS_OF, _sections()))
    assert positive["fundamentals_revenue_yoy"].direction == "support"
    assert positive["chips_foreign_net"].direction == "support"
    assert positive["industry_flow"].direction == "support"
    # The ML score is the candidate itself, so it is context, never its own supporting evidence.
    assert positive["ml_score"].direction == "neutral"

    base = _sections()
    negative = _by_id(
        build_bundle(
            "2330",
            AS_OF,
            _sections(
                monthly_revenue=[{**base["monthly_revenue"][0], "yoy": -0.12}],
                institutional=[
                    {
                        "date": dt.date(2026, 9, 4),
                        "foreign_net": -3000,
                        "trust_net": -200,
                        "dealer_net": 0,
                        "available_at": BEFORE,
                    }
                ],
                industry={**base["industry"], "net_yi": -4.0},
            ),
        )
    )
    assert negative["fundamentals_revenue_yoy"].direction == "contradict"
    assert negative["chips_foreign_net"].direction == "contradict"
    assert negative["industry_flow"].direction == "contradict"


def test_ac217_2_an_estimated_availability_can_never_support_a_claim() -> None:
    """D-081(f): an estimated availability timestamp is not PIT-solid enough to support."""
    base = _sections()
    estimated = _by_id(
        build_bundle(
            "2330",
            AS_OF,
            _sections(
                monthly_revenue=[{**base["monthly_revenue"][0], "available_at_estimated": True}]
            ),
        )
    )
    item = estimated["fundamentals_revenue_yoy"]
    assert item.availability == "estimated"
    assert item.direction == "neutral"  # Downgraded from support, and said so.
    assert "估計" in item.title


def test_ac217_2_rows_available_after_the_cutoff_are_excluded_and_recorded() -> None:
    base = _sections()
    bundle = build_bundle(
        "2330",
        AS_OF,
        _sections(monthly_revenue=[{**base["monthly_revenue"][0], "available_at": AFTER}]),
    )

    assert "fundamentals_revenue_yoy" not in _by_id(bundle)
    assert "monthly_revenue" in bundle.missing
    # Its absence is visible as a gap, not silently dropped.
    assert any(i.direction == "gap" and "月營收" in i.title for i in bundle.evidence)


def test_ac217_2_absent_sections_become_gaps_and_news_is_always_a_gap() -> None:
    bundle = build_bundle(
        "2330", AS_OF, _sections(monthly_revenue=[], institutional=[], industry=None)
    )

    gaps = {i.evidence_id for i in bundle.evidence if i.direction == "gap"}
    assert "fundamentals_revenue_missing" in gaps
    assert "chips_institutional_missing" in gaps
    assert "industry_missing" in gaps
    # There is no news/filing source in the repo, so the section is honestly empty every time.
    assert "events_news_unavailable" in gaps
    assert "news" in bundle.missing


def test_ac217_2_a_disposition_contradicts_and_survives_into_the_bundle() -> None:
    bundle = _by_id(
        build_bundle(
            "2330",
            AS_OF,
            _sections(
                disposition=[
                    {
                        "date": dt.date(2026, 9, 1),
                        "effective_start": dt.date(2026, 9, 2),
                        "effective_end": dt.date(2026, 9, 12),
                        "available_at": BEFORE,
                    }
                ]
            ),
        )
    )
    assert bundle["events_disposition"].direction == "contradict"


def test_ac217_3_the_bundle_hash_is_deterministic_and_content_addressed() -> None:
    base = _sections()
    first = build_bundle("2330", AS_OF, _sections())
    again = build_bundle("2330", AS_OF, _sections())
    changed = build_bundle(
        "2330", AS_OF, _sections(monthly_revenue=[{**base["monthly_revenue"][0], "yoy": 0.42}])
    )

    assert bundle_hash(first) == bundle_hash(again)
    assert bundle_hash(first) != bundle_hash(changed)
    assert first.bundle_hash == bundle_hash(first)
    assert len(first.bundle_hash) == 16


def test_ac217_3_evidence_ids_are_unique_and_the_bundle_is_pack_ready() -> None:
    from twquant.reporting.insight_evidence import InsightPack

    bundle = build_bundle("2330", AS_OF, _sections())
    ids = [item.evidence_id for item in bundle.evidence]
    assert len(ids) == len(set(ids))

    # The bundle drops straight into an evidence pack without further massaging.
    pack = InsightPack.model_validate(
        {
            "schema_version": 1,
            "insight_run_id": "ins_x",
            "as_of": AS_OF,
            "stock_id": "2330",
            "evidence": [item.model_dump(mode="json") for item in bundle.evidence],
            "analysis": None,
            "analysis_absent_reason": "尚未分析",
        }
    )
    assert len(pack.evidence) == len(bundle.evidence)


def test_ac217_3_a_naive_cutoff_or_int_stock_id_is_refused() -> None:
    with pytest.raises(DataError, match="as_of"):
        build_bundle("2330", dt.datetime(2026, 9, 4, 20, 0), _sections())
    with pytest.raises(DataError, match="stock_id"):
        build_bundle(2330, AS_OF, _sections())  # type: ignore[arg-type]
