"""D-111：ML 選股名單的晉級門檻。

在此之前每個候選一律 `ml_candidate`，於是畫面上「每天推薦 200 檔」
——那其實是 API 的 page_size，不是模型選的。門檻宣告在 configs/ml.yaml，
本模組只套用；這些測試釘住「套用」的語意。
"""

from __future__ import annotations

import datetime as dt

import pytest

from twquant.config import RecommendationBucketConfig
from twquant.reporting.recommendation_export import BucketPolicy, snapshot_from_scores
from twquant.reporting.recommendation_snapshot import RecommendationSnapshot

DAY = dt.date(2026, 9, 7)
AVAILABLE = dt.datetime(2026, 9, 7, 20, 0, tzinfo=dt.UTC).astimezone(
    dt.timezone(dt.timedelta(hours=8))
)


def _rows(n: int) -> list[dict[str, object]]:
    """n 檔，分數由高到低，stock_id 為 1000, 1001, ...。"""
    return [
        {
            "date": DAY,
            "stock_id": f"{1000 + i}",
            "score": float(n - i),
            "available_at": AVAILABLE,
        }
        for i in range(n)
    ]


def _snap(rows: list[dict[str, object]], policy: BucketPolicy | None) -> RecommendationSnapshot:
    return snapshot_from_scores(
        rows, model_id="m", day=DAY, artifact_fingerprint="fp", policy=policy
    )


def _buckets(snap: RecommendationSnapshot) -> list[str]:
    return [c.bucket for c in sorted(snap.candidates, key=lambda c: c.rank)]


def test_ac1_no_policy_keeps_legacy_behaviour() -> None:
    """未給政策時全部維持 ml_candidate——既有快照逐字不變。"""
    assert set(_buckets(_snap(_rows(30), None))) == {"ml_candidate"}


def test_ac2_bands_without_risk_gate() -> None:
    """沒有風控分數時退化成純排名切點。"""
    b = _buckets(_snap(_rows(60), BucketPolicy(top_n=20, watch_n=50)))
    assert b[:20] == ["ml_top20"] * 20
    assert b[20:50] == ["ml_watch"] * 30
    assert b[50:] == ["ml_candidate"] * 10


def test_ac3_risk_gate_excludes_and_promotes_survivors() -> None:
    """被閘門剔除者標 ml_risk_excluded，且**不佔用前 20 名的名額**。"""
    rows = _rows(30)
    # 前 5 名裡有 2 檔預測回撤最深（rank_pct < 0.1）
    risk = {f"{1000 + i}": 0.5 for i in range(30)}
    risk["1000"] = 0.02
    risk["1002"] = 0.05
    b = _buckets(
        _snap(rows, BucketPolicy(top_n=20, watch_n=25, risk_gate_keep_pct=0.9, risk_scores=risk))
    )
    assert b[0] == "ml_risk_excluded"
    assert b[2] == "ml_risk_excluded"
    # 存活者遞補：剔除 2 檔後，前 20 名的存活者一路排到第 22 列
    assert b.count("ml_top20") == 20
    assert b[21] == "ml_top20"


def test_ac4_missing_risk_score_never_promotes() -> None:
    """查不到風控分數的個股不得晉級——缺資料時放行等於靜默降級（黃金規則 5）。"""
    rows = _rows(10)
    risk = {f"{1000 + i}": 0.5 for i in range(10)}
    del risk["1000"]  # 第 1 名沒有風控分數
    b = _buckets(
        _snap(rows, BucketPolicy(top_n=3, watch_n=5, risk_gate_keep_pct=0.9, risk_scores=risk))
    )
    assert b[0] == "ml_candidate"
    assert b[1:4] == ["ml_top20"] * 3


def test_ac5_excluded_rows_are_kept_not_dropped() -> None:
    """剔除不是刪除：全部候選照樣留在快照裡，名次仍是全宇宙名次。"""
    rows = _rows(30)
    risk = {f"{1000 + i}": (0.01 if i < 5 else 0.5) for i in range(30)}
    snap = _snap(rows, BucketPolicy(top_n=20, watch_n=25, risk_gate_keep_pct=0.9, risk_scores=risk))
    assert len(snap.candidates) == 30
    assert [c.rank for c in sorted(snap.candidates, key=lambda c: c.rank)] == list(range(1, 31))


def test_ac6_config_rejects_inverted_bands() -> None:
    """watch_n 小於 top_n 是設定錯誤，載入時就要擋。"""
    with pytest.raises(ValueError, match="watch_n"):
        RecommendationBucketConfig(top_n=50, watch_n=20)


def test_ac6_config_rejects_bad_keep_pct() -> None:
    with pytest.raises(ValueError, match="risk_gate_keep_pct"):
        RecommendationBucketConfig(risk_gate_keep_pct=1.5)


def test_ac7_liquidity_gate_excludes_without_consuming_slots() -> None:
    """D-113：不在流動性合格名單內者標 ml_liquidity_excluded，且**不佔前 20 名的名額**。"""
    rows = _rows(30)
    liquid = frozenset(str(r["stock_id"]) for r in rows) - {"1000", "1002"}
    b = _buckets(_snap(rows, BucketPolicy(top_n=20, watch_n=25, liquid_ids=liquid)))
    assert b[0] == "ml_liquidity_excluded"
    assert b[2] == "ml_liquidity_excluded"
    assert b.count("ml_top20") == 20
    assert b[21] == "ml_top20"  # 第 22 名遞補進前 20


def test_ac8_liquidity_gate_runs_before_risk_gate() -> None:
    """同時不合格時標流動性——買不到的股票，回撤預測多深都無關。"""
    rows = _rows(10)
    risk = {str(r["stock_id"]): 0.5 for r in rows} | {"1000": 0.01}
    liquid = frozenset(risk) - {"1000"}
    policy = BucketPolicy(
        top_n=3, watch_n=5, risk_gate_keep_pct=0.9, risk_scores=risk, liquid_ids=liquid
    )
    assert _buckets(_snap(rows, policy))[0] == "ml_liquidity_excluded"


def test_ac9_no_liquidity_list_means_gate_off() -> None:
    """liquid_ids=None ＝ 閘門未宣告，行為與 D-111 逐字相同。"""
    b = _buckets(_snap(_rows(30), BucketPolicy(top_n=20, watch_n=25)))
    assert "ml_liquidity_excluded" not in b


def test_ac10_liquidity_config_is_validated() -> None:
    with pytest.raises(ValueError, match="min_adv_pctl"):
        RecommendationBucketConfig(liquidity_min_adv_pctl=1.5)
