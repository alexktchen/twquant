"""洞悉分析的編排與落地（PLAN §3.3、T-219）。

一次 run 對推薦快照的前 N 檔候選：取資料 → 組證據包 → 分析 → 原子保存證據包。

三條規則：

* **每檔候選各自獨立**。任一檔取資料或分析失敗，只記進 failures，其餘照常完成。
* **推薦快照不動一個位元組**。洞悉以 `recommendation_run_id` 反向掛在證據包上，
  讀取端再 join（快照已發布，事後改寫等於竄改）。
* **冪等鍵是輸入雜湊**。證據與 prompt／模型都沒變就重用既有證據包；變了就是新的一份，
  舊的保留下來——它是「當時知道什麼」的證據。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from twquant.errors import DataError, TwquantError
from twquant.insight.candidate_analysis import analyse_candidate
from twquant.insight.client import InsightClient
from twquant.insight.evidence_bundle import build_bundle
from twquant.reporting.insight_evidence import InsightPack, save_pack
from twquant.reporting.recommendation_snapshot import (
    RecommendationCandidate,
    RecommendationSnapshot,
    select_snapshots,
)

#: 取一檔股票的供應層列；由呼叫端注入，故本模組不查 DB（IO 邊界）。
FetchSections = Callable[[str], Mapping[str, Any]]


@dataclass
class InsightRunSummary:
    """What one insight run did, including the candidates it could not finish."""

    recommendation_run_id: str
    requested: int = 0
    analysed: int = 0
    absent: int = 0
    saved: int = 0
    reused: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)


def run_insights(
    data_dir: str | Path,
    *,
    recommendation_run_id: str,
    top_n: int,
    client: InsightClient,
    now: dt.datetime,
    fetch_sections: FetchSections,
) -> InsightRunSummary:
    """Analyse the top N candidates of one saved recommendation run."""
    if top_n < 1:
        raise DataError("insight_run", None, "top_n 必須為正整數")
    catalog = select_snapshots(data_dir, run_id=recommendation_run_id)
    if not catalog.runs:
        raise DataError("insight_run", None, f"找不到推薦快照 run_id={recommendation_run_id}")
    snapshot = catalog.runs[0]

    candidates = sorted(snapshot.candidates, key=lambda c: c.rank)[:top_n]
    summary = InsightRunSummary(recommendation_run_id, requested=len(candidates))
    for candidate in candidates:
        try:
            _analyse_one(
                data_dir,
                snapshot,
                candidate,
                client,
                now,
                fetch_sections,
                summary,
                len(snapshot.candidates),
            )
        except (TwquantError, ValueError, OSError, RuntimeError) as exc:
            # 一檔壞掉不影響其餘；細節只留類型與訊息，不外洩路徑。
            summary.failures.append({"stock_id": candidate.stock_id, "detail": type(exc).__name__})
    return summary


def _analyse_one(
    data_dir: str | Path,
    snapshot: RecommendationSnapshot,
    candidate: RecommendationCandidate,
    client: InsightClient,
    now: dt.datetime,
    fetch_sections: FetchSections,
    summary: InsightRunSummary,
    candidates_total: int,
) -> None:
    """Build, analyse and persist one candidate; an absent analysis still saves its evidence."""
    as_of, stock_id = snapshot.as_of, candidate.stock_id
    recommendation_run_id = snapshot.run_id
    sections = dict(fetch_sections(stock_id))
    # ML 證據取自這一次 run 自己的分數。不從 ml_scores 撈：那張表混有多個模型的回補列，
    # 取「最新一列」會拿到別的模型、甚至半年前的分數（2026-09-06 實跑時抓到）。
    sections["ml"] = {
        "date": as_of.date(),
        "score": candidate.score,
        "rank": candidate.rank,
        "candidates_total": candidates_total,
        "model_id": snapshot.model_id,
        "available_at": as_of,
    }
    bundle = build_bundle(stock_id, as_of, sections)
    outcome = analyse_candidate(
        bundle, client, now=now, recommendation_run_id=recommendation_run_id
    )
    if outcome.analysis is None:
        summary.absent += 1
    else:
        summary.analysed += 1

    pack = InsightPack.model_validate(
        {
            "schema_version": 1,
            "insight_run_id": outcome.insight_run_id,
            "recommendation_run_id": recommendation_run_id,
            "as_of": as_of,
            "stock_id": stock_id,
            "industry": None,
            "evidence": [item.model_dump(mode="json") for item in bundle.evidence],
            "analysis": outcome.analysis.model_dump(mode="json") if outcome.analysis else None,
            "analysis_absent_reason": outcome.absent_reason,
        }
    )
    result = save_pack(data_dir, pack)
    if result.created:
        summary.saved += 1
    else:
        summary.reused += 1
