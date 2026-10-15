"""T-364／D-182：歷史相似情境（完整版）——相似度、鄰居、驗證指標。手算小例子＋暴力對照；不連線。"""

from __future__ import annotations

import datetime as dt
import math
from pathlib import Path

import numpy as np
import pytest

from twquant.config import load_config
from twquant.reporting.score_reference import Bucket, ScoreReferenceConfig
from twquant.reporting.similar import (
    DayScore,
    Library,
    SimilarConfig,
    bucket_baseline,
    context_scale,
    day_scores,
    decode_library,
    encode_library,
    find,
    gain_weights,
    near_days,
    pick_neighbors,
    pick_rows,
    reference,
    spearman,
    validate_line,
    verdict,
    weighted_distance,
)

ROOT = Path(__file__).resolve().parents[1]
CFG = load_config(ROOT / "configs" / "similar_situations.yaml", SimilarConfig)
BUCKETS = load_config(ROOT / "configs" / "score_reference.yaml", ScoreReferenceConfig).buckets


def _sessions(n: int) -> tuple[dt.date, ...]:
    d0 = dt.date(2020, 1, 6)
    return tuple(d0 + dt.timedelta(days=i) for i in range(n))


def _lib(
    days: list[int],
    sids: list[str],
    x: list[list[float]],
    ret: list[float],
    *,
    n_sessions: int,
    ctx: np.ndarray | None = None,
    score: list[float] | None = None,
    surge: list[bool] | None = None,
    hold: int = 2,
) -> Library:
    n = len(days)
    if ctx is None:
        ctx = np.tile(np.arange(n_sessions, dtype=np.float64)[:, None], (1, 3))
    return Library(
        sessions=_sessions(n_sessions),
        day=np.asarray(days, dtype=np.int64),
        exit_day=np.asarray(days, dtype=np.int64) + hold,
        sid=np.asarray(sids),
        score=np.asarray(score if score is not None else [0.3] * n, dtype=np.float64),
        x=np.asarray(x, dtype=np.float64),
        surge=np.asarray(surge if surge is not None else [False] * n, dtype=bool),
        ret=np.asarray(ret, dtype=np.float64),
        ctx=ctx,
    )


def _small_cfg(**over: object) -> SimilarConfig:
    base = CFG.model_dump()
    base["feature_weights"] = {"a": 0.5, "b": 0.5}
    base.update(over)
    return SimilarConfig.model_validate(base)


# ── AC1：登記不變 ──────────────────────────────────────────────────────────
def test_ac1_registration_is_unchanged() -> None:
    assert CFG.registration == "D-182"
    assert CFG.model_id == "lgbm_quietA20_tu_h20_20260430_655df6d7"
    assert (CFG.oos_start, CFG.dev_end) == (dt.date(2017, 10, 2), dt.date(2026, 8, 31))
    assert (CFG.hold_sessions, CFG.notional, CFG.max_participation, CFG.big_loss) == (
        20,
        666666.67,
        0.05,
        -0.15,
    )
    assert CFG.context_features == ["ctx.taiex_ma20_dev", "ctx.taiex_vol20", "ctx.breadth_ma20"]
    assert (CFG.context_keep, CFG.min_weight_share, CFG.k, CFG.dedupe_sessions) == (
        0.25,
        0.5,
        200,
        20,
    )
    assert (CFG.surge_lines, CFG.surge_window, CFG.surge_ratio_min) == (["QUIETV"], 20, 1.5)
    assert CFG.lines == ["QUIET", "QUIETV"]
    assert len(CFG.feature_weights) == 22 and CFG.features[0] == "price.atr_14"
    assert sum(CFG.feature_weights.values()) == pytest.approx(1.0, abs=1e-9)
    v = CFG.validation
    assert (v.library_sell_end, v.query_start, v.min_day_queries) == (
        dt.date(2026, 4, 30),
        dt.date(2026, 5, 1),
        10,
    )
    assert (v.bootstrap, v.seed, v.ci) == (10000, 20261015, 0.95)


def test_ac1_gain_weights_drop_ctx_cover_and_round_like_the_yaml() -> None:
    names = ["ctx.a", "f1", "f2", "f3", "f4"]
    gains = [100.0, 60.0, 30.0, 7.0, 3.0]
    w = gain_weights(names, gains, cover=0.90)
    assert list(w) == ["f1", "f2"]  # 60+30＝90% 剛好到
    assert w == {"f1": 0.6667, "f2": 0.3333}
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-12)
    assert "ctx.a" not in gain_weights(names, gains, cover=1.0)


def test_ac1_config_rejects_bad_weights_and_windows() -> None:
    with pytest.raises(ValueError, match="加總"):
        _small_cfg(feature_weights={"a": 0.5, "b": 0.4})
    with pytest.raises(ValueError, match="大盤特徵"):
        _small_cfg(feature_weights={"ctx.x": 0.5, "b": 0.5})
    bad = CFG.model_dump()
    bad["validation"]["query_start"] = dt.date(2026, 4, 1)
    with pytest.raises(ValueError, match="驗證庫截止"):
        SimilarConfig.model_validate(bad)


# ── AC2：情境庫的結果 ────────────────────────────────────────────────────────
def test_ac2_library_rejects_missing_results_and_backward_exits() -> None:
    with pytest.raises(ValueError, match="缺值"):
        _lib([0], ["A"], [[0.0, 0.0]], [float("nan")], n_sessions=5)
    with pytest.raises(ValueError, match="賣出日"):
        _lib([1], ["A"], [[0.0, 0.0]], [0.1], n_sessions=5, hold=0)
    lib = _lib([0, 1], ["A", "B"], [[0.0, 0.0], [1.0, 1.0]], [0.1, -0.2], n_sessions=5)
    assert len(lib.take(lib.ret > 0)) == 1


# ── AC3：大盤關 ────────────────────────────────────────────────────────────
def test_ac3_near_days_keeps_closest_quarter_with_date_tiebreak() -> None:
    ctx = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 1.0], [-1.0, 2.0, 0.0], [5.0, 1.0, 2.0]])
    lib = _lib(
        [0, 1, 2, 3],
        list("ABCD"),
        [[0, 0]] * 4,
        [0.0] * 4,
        n_sessions=6,
        ctx=np.vstack([ctx, np.zeros((2, 3))]),
    )
    mean, std = context_scale(lib)
    assert mean == pytest.approx(ctx.mean(axis=0)) and std == pytest.approx(ctx.std(axis=0))
    got = near_days(ctx[0], lib, mean, std, keep=0.5)
    assert len(got) == math.ceil(0.5 * 4)
    z = (ctx - mean) / std
    d = np.sqrt(((z - z[0]) ** 2).sum(axis=1))
    assert set(got) == set(np.argsort(d, kind="stable")[:2])
    # 四天離平均一樣遠 → 日期早的先
    tie = np.array([[1.0, 0.0, 1.0], [-1.0, 0.0, 1.0], [0.0, 1.0, -1.0], [0.0, -1.0, -1.0]])
    lib2 = _lib(
        [0, 1, 2, 3],
        list("ABCD"),
        [[0, 0]] * 4,
        [0.0] * 4,
        n_sessions=6,
        ctx=np.vstack([tie, np.zeros((2, 3))]),
    )
    m2, s2 = context_scale(lib2)
    assert list(near_days(m2, lib2, m2, s2, keep=0.25)) == [0]
    assert list(near_days(m2, lib2, m2, s2, keep=0.5)) == [0, 1]


def test_ac3_missing_context_raises() -> None:
    ctx = np.ones((4, 3))
    ctx[:, 0] = [1.0, 2.0, 3.0, 4.0]
    ctx[:, 1] = [2.0, 1.0, 2.0, 1.0]
    ctx[:, 2] = [0.0, 1.0, 1.0, 0.0]
    ctx[1, 0] = np.nan
    lib = _lib([0, 1], ["A", "B"], [[0, 0], [0, 0]], [0.0, 0.0], n_sessions=4, ctx=ctx)
    with pytest.raises(ValueError, match="缺大盤狀態"):
        context_scale(lib)


# ── AC4：加權距離 ──────────────────────────────────────────────────────────
def _brute(q: np.ndarray, c: np.ndarray, w: np.ndarray, min_share: float) -> np.ndarray:
    out = np.empty((len(q), len(c)))
    for i, a in enumerate(q):
        for j, b in enumerate(c):
            m = ~np.isnan(a) & ~np.isnan(b)
            share = w[m].sum()
            out[i, j] = (
                math.inf
                if share < min_share
                else math.sqrt((w[m] * (a[m] - b[m]) ** 2).sum() / share)
            )
    return out


def test_ac4_weighted_distance_matches_brute_force_with_missing_values() -> None:
    rng = np.random.default_rng(0)
    q = rng.normal(size=(7, 5))
    c = rng.normal(size=(30, 5))
    q[rng.random(q.shape) < 0.2] = np.nan
    c[rng.random(c.shape) < 0.3] = np.nan
    w = np.array([0.4, 0.25, 0.15, 0.12, 0.08])
    got = weighted_distance(q, c, w, 0.5)
    want = _brute(q, c, w, 0.5)
    assert np.array_equal(np.isinf(got), np.isinf(want))
    fin = np.isfinite(want)
    assert got[fin] == pytest.approx(want[fin], abs=1e-9)


def test_ac4_missing_is_not_zero() -> None:
    w = np.array([0.5, 0.5])
    q = np.array([[1.0, np.nan]])
    c = np.array([[1.0, 0.0], [1.0, 5.0]])
    # 第二維題目缺值：兩個候選一樣近（若把缺值當 0，第一個會比較近）
    d = weighted_distance(q, c, w, 0.5)
    assert d[0, 0] == d[0, 1] == 0.0
    # 只剩一半權重以下可比 → 不比
    assert np.isinf(weighted_distance(q, c, w, 0.6)).all()


# ── AC5：鄰居、同檔去重、排序可重現 ──────────────────────────────────────────
def test_ac5_pick_neighbors_dedupes_same_stock_within_window() -> None:
    dist = np.array([0.1, 0.2, 0.3, 0.4, 0.5])
    day = np.array([10, 15, 40, 12, 100])
    sid = np.array(["A", "A", "A", "B", "B"])
    got = pick_neighbors(dist, day, sid, k=10, dedupe_sessions=20)
    # A@15 與 A@10 相差 5 < 20 → 略過；A@40 相差 30 → 留；B 兩次相差 88 → 都留
    assert list(got) == [0, 2, 3, 4]
    assert list(pick_neighbors(dist, day, sid, k=2, dedupe_sessions=20)) == [0, 2]


def test_ac5_ties_order_by_date_then_code_and_inf_is_skipped() -> None:
    dist = np.array([0.2, 0.2, 0.2, np.inf])
    day = np.array([5, 3, 3, 1])
    sid = np.array(["C", "B", "A", "D"])
    assert list(pick_neighbors(dist, day, sid, k=5, dedupe_sessions=1)) == [2, 1, 0]


def test_ac5_widens_the_batch_when_dedupe_removes_many() -> None:
    # 前 10×k 名全是同一檔、同一段：要放寬批次才湊得到 k 個
    n = 200
    dist = np.arange(n, dtype=np.float64)
    day = np.zeros(n, dtype=np.int64)
    sid = np.array(["A"] * (n - 3) + ["B", "C", "D"])
    got = pick_neighbors(dist, day, sid, k=4, dedupe_sessions=20)
    assert list(got) == [0, n - 3, n - 2, n - 1]


# ── AC6：爆量線只找爆量情境 ──────────────────────────────────────────────────
def test_ac6_surge_line_only_matches_surge_situations() -> None:
    cfg = _small_cfg(k=5, context_keep=1.0)
    ctx = np.zeros((10, 3))
    ctx[:, 0] = np.arange(10.0)
    ctx[:, 1] = np.arange(10.0) % 2
    ctx[:, 2] = np.arange(10.0) % 3
    lib = _lib(
        [0, 1, 2, 3],
        list("ABCD"),
        [[0.0, 0.0], [0.1, 0.1], [5.0, 5.0], [6.0, 6.0]],
        [0.01, 0.02, 0.03, 0.04],
        n_sessions=10,
        ctx=ctx,
        surge=[False, False, True, True],
    )
    scale = context_scale(lib)
    w = np.array([0.5, 0.5])
    q = np.array([[0.0, 0.0]])
    allp = find(lib, ctx[5], q, w, cfg, scale, surge_only=False)[0]
    surg = find(lib, ctx[5], q, w, cfg, scale, surge_only=True)[0]
    assert list(allp.rows) == [0, 1, 2, 3]
    assert list(surg.rows) == [2, 3]
    assert surg.stats["n"] == 2 and surg.stats["median"] == pytest.approx(0.035)


# ── AC7：驗證 ──────────────────────────────────────────────────────────────
def test_ac7_spearman_and_untestable_days() -> None:
    a = np.array([1.0, 2.0, 3.0, 4.0])
    assert spearman(a, a * 10) == pytest.approx(1.0)
    assert spearman(a, -a) == pytest.approx(-1.0)
    assert spearman(np.ones(4), a) is None
    s = day_scores(
        dt.date(2026, 5, 4),
        np.array([0.1, -0.2]),
        np.array([0.0, 0.1]),
        np.array([0.0, 0.0]),
        np.array([0.0, 1.0]),
        np.array([0.5, 0.5]),
        -0.15,
        10,
    )
    assert s.ic_full is None and s.ic_simple is None  # 題數不夠
    assert s.se_full == pytest.approx(0.0 + 0.0) and s.se_simple == pytest.approx(0.25 + 0.25)


def test_ac7_bucket_baseline_uses_same_buckets_and_surge_subset() -> None:
    lib = _lib(
        [0, 0, 1, 1],
        list("ABCD"),
        [[0, 0]] * 4,
        [0.10, -0.20, 0.30, -0.40],
        n_sessions=5,
        score=[0.1, 0.1, 0.6, 0.6],
        surge=[True, False, True, False],
    )
    base = bucket_baseline(lib, BUCKETS, -0.15, surge_only=False)
    assert base["<0.25"] == pytest.approx((-0.05, 0.5))
    assert base[">=0.50"] == pytest.approx((-0.05, 0.5))
    surg = bucket_baseline(lib, BUCKETS, -0.15, surge_only=True)
    assert surg["<0.25"] == pytest.approx((0.10, 0.0)) and surg[">=0.50"] == pytest.approx(
        (0.30, 0.0)
    )


def _days(ic_f: float, ic_s: float, se_f: float, se_s: float, n: int = 30) -> list[DayScore]:
    out = []
    for i in range(n):
        jitter = 0.01 * ((i % 5) - 2)
        out.append(
            DayScore(
                dt.date(2026, 5, 1) + dt.timedelta(days=i), 20, ic_f + jitter, ic_s, se_f, se_s
            )
        )
    return out


def test_ac7_verdict_rule_and_reproducible_bootstrap() -> None:
    better = verdict(_days(0.10, 0.02, 1.0, 1.0), 2000, 7, 0.95)
    assert better.passed and better.d_ic_ci[0] > 0
    again = verdict(_days(0.10, 0.02, 1.0, 1.0), 2000, 7, 0.95)
    assert again == better
    worse_brier = verdict(_days(0.10, 0.02, 3.0, 1.0), 2000, 7, 0.95)
    assert not worse_brier.passed  # 排序變好、但大賠機率顯著變差 → 不通過
    same = verdict(_days(0.02, 0.02, 1.0, 1.0), 2000, 7, 0.95)
    assert not same.passed  # 都沒有比較好


def test_ac7_questions_must_come_after_the_library() -> None:
    cfg = _small_cfg(k=3, context_keep=1.0)
    ctx = np.zeros((12, 3))
    ctx[:, 0] = np.arange(12.0)
    ctx[:, 1] = np.arange(12.0) % 2
    ctx[:, 2] = np.arange(12.0) % 3
    lib = _lib(
        [0, 1, 2, 3],
        list("ABCD"),
        [[0.0, 0.0], [1.0, 1.0], [2.0, 2.0], [3.0, 3.0]],
        [0.1, -0.2, 0.05, 0.3],
        n_sessions=12,
        ctx=ctx,
        score=[0.1, 0.3, 0.45, 0.6],
    )
    early = _lib([4], ["E"], [[0.0, 0.0]], [0.0], n_sessions=12, ctx=ctx, score=[0.3])
    with pytest.raises(ValueError, match="不得偷看"):
        validate_line(lib, early, np.array([0.5, 0.5]), cfg, BUCKETS, surge_only=False)
    late = _lib(
        [9, 9],
        ["E", "F"],
        [[0.0, 0.0], [3.0, 3.0]],
        [0.2, -0.3],
        n_sessions=12,
        ctx=ctx,
        score=[0.1, 0.6],
    )
    days = validate_line(lib, late, np.array([0.5, 0.5]), cfg, BUCKETS, surge_only=False)
    assert len(days) == 1 and days[0].n == 2


def test_ac7_bucket_type_is_shared_with_d179() -> None:
    assert isinstance(BUCKETS[0], Bucket) and BUCKETS[0].lo == 0.0


def test_ac7_reference_compares_brier_with_always_guessing_the_average() -> None:
    lib = _lib(
        [0, 1, 2, 3],
        list("ABCD"),
        [[0, 0]] * 4,
        [0.1, -0.2, 0.0, -0.3],
        n_sessions=12,
        surge=[True, True, False, False],
    )
    q = _lib([8, 9], ["E", "F"], [[0, 0]] * 2, [-0.5, 0.2], n_sessions=12, surge=[True, False])
    days = _days(0.10, 0.02, 1.0, 1.0, n=5)
    r = reference(lib, q, days, -0.15, surge_only=False)
    assert (r.base_rate, r.actual_rate) == (0.5, 0.5)
    assert r.brier_base == pytest.approx(0.25)
    assert (
        r.ic_full_t is not None and r.ic_full_t > 0 and r.ic_simple_t is None
    )  # 簡化版每天一樣 → 沒有變異
    s = reference(lib, q, days, -0.15, surge_only=True)
    assert (s.base_rate, s.actual_rate) == (0.5, 1.0) and s.brier_base == pytest.approx(0.25)


# ── AC8：情境庫上雲的壓縮格式 ────────────────────────────────────────────────
def _ctx(n: int) -> np.ndarray:
    ctx = np.zeros((n, 3))
    ctx[:, 0] = np.arange(float(n))
    ctx[:, 1] = np.arange(float(n)) % 2
    ctx[:, 2] = np.arange(float(n)) % 3
    return ctx


def test_ac8_encode_decode_round_trip_keeps_missing_values() -> None:
    ctx = _ctx(12)
    ctx[11, 1] = np.nan  # 沒有情境的日子可以缺大盤狀態
    lib = _lib(
        [0, 1, 2, 3],
        ["2330", "0050", "2330", "1101"],
        [[0.5, np.nan], [1.25, -2.0], [np.nan, np.nan], [3.0, 4.0]],
        [0.1, -0.2, 0.05, 0.3],
        n_sessions=12,
        ctx=ctx,
        score=[0.1, 0.3, 0.45, 0.6],
        surge=[True, False, True, False],
    )
    parts = encode_library(lib, ["a", "b"], "D-182")
    back, feats, reg = decode_library(parts)
    assert (feats, reg) == (["a", "b"], "D-182")
    assert back.sessions == lib.sessions and list(back.sid) == list(lib.sid)
    assert np.array_equal(back.day, lib.day) and np.array_equal(back.exit_day, lib.exit_day)
    assert np.array_equal(back.surge, lib.surge)
    assert np.allclose(back.x, lib.x, equal_nan=True) and np.isnan(back.x[2]).all()
    assert np.allclose(back.ret, lib.ret, atol=1e-7) and np.allclose(
        back.score, lib.score, atol=1e-7
    )
    assert np.isnan(back.ctx[11, 1]) and back.ctx[5, 0] == 5.0


def test_ac8_decode_rejects_missing_parts() -> None:
    lib = _lib([0], ["A"], [[0.0, 0.0]], [0.1], n_sessions=5)
    parts = encode_library(lib, ["a", "b"], "D-182")
    del parts["x.000"]
    with pytest.raises(ValueError, match="少了"):
        decode_library(parts)
    with pytest.raises(ValueError, match="特徵"):
        encode_library(lib, ["a"], "D-182")


# ── AC9：當天名單的比對 ────────────────────────────────────────────────────
def _today(sids: list[str], x: list[list[float]], ctx_row: np.ndarray) -> object:
    import polars as pl

    return pl.DataFrame(
        {
            "date": [dt.date(2026, 10, 15)] * len(sids),
            "stock_id": sids,
            "a": [r[0] for r in x],
            "b": [r[1] for r in x],
            "ctx.taiex_ma20_dev": [ctx_row[0]] * len(sids),
            "ctx.taiex_vol20": [ctx_row[1]] * len(sids),
            "ctx.breadth_ma20": [ctx_row[2]] * len(sids),
        }
    )


def test_ac9_pick_rows_only_quiet_lines_with_examples_and_span() -> None:
    cfg = _small_cfg(k=3, context_keep=1.0, examples=2)
    ctx = _ctx(12)
    lib = _lib(
        [0, 1, 2, 3],
        list("ABCD"),
        [[0.0, 0.0], [0.1, 0.1], [5.0, 5.0], [6.0, 6.0]],
        [0.01, 0.02, 0.03, 0.04],
        n_sessions=12,
        ctx=ctx,
        surge=[False, False, True, True],
    )
    frame = _today(["X", "Y"], [[0.0, 0.0], [6.0, 6.0]], ctx[5])
    rows = pick_rows([("CORE", "X"), ("QUIET", "X"), ("QUIETV", "Y")], frame, lib, cfg)  # type: ignore[arg-type]
    assert [(r.line, r.stock_id) for r in rows] == [("QUIET", "X"), ("QUIETV", "Y")]
    quiet, surge = rows
    assert quiet.stats["n"] == 3 and [e["sid"] for e in quiet.examples] == ["A", "B"]
    assert quiet.examples[0] == {"d": lib.sessions[0].isoformat(), "sid": "A", "r": 0.01}
    assert quiet.span == (lib.sessions[0], lib.sessions[2])
    assert [e["sid"] for e in surge.examples] == ["D", "C"]  # 爆量線只找爆量情境


def test_ac9_pick_not_in_the_day_frame_raises() -> None:
    from twquant.errors import DataError

    cfg = _small_cfg(k=3, context_keep=1.0)
    ctx = _ctx(12)
    lib = _lib([0, 1], ["A", "B"], [[0.0, 0.0], [1.0, 1.0]], [0.1, 0.2], n_sessions=12, ctx=ctx)
    frame = _today(["X"], [[0.0, 0.0]], ctx[5])
    with pytest.raises(DataError, match="不在當天的推論列"):
        pick_rows([("QUIET", "Z")], frame, lib, cfg)  # type: ignore[arg-type]
    assert pick_rows([("CORE", "Z")], frame, lib, cfg) == []  # type: ignore[arg-type]


# ── AC10：排程接線（靜態）──────────────────────────────────────────────────
def test_ac10_step_runs_after_reasons_and_in_the_cloud() -> None:
    from twquant.config import ServingConfig

    source = (ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert source.index('PipelineStep("名單理由"') < source.index('PipelineStep("相似情境"')
    scfg = load_config(ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "相似情境" not in scfg.cloud.skip_pipeline_steps
    assert "pick_similar" not in scfg.cloud.windows  # 全史保留：雲端因子過兩天就算不回來
    assert "similar_library" not in scfg.cloud.windows


# ── AC11：PG 讀寫（無 DSN 則 skip）──────────────────────────────────────────
_SCHEMA = "serving_test_similar"


def _connect_or_skip() -> object:
    import os

    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{_SCHEMA}"')
    conn.commit()
    return conn


def test_ac11_pg_library_and_whole_day_overwrite() -> None:
    from twquant.conventions import now_tw
    from twquant.data.serving.similar_store import read_library, write_library, write_similar
    from twquant.errors import DataError
    from twquant.reporting.similar import SimilarRow

    conn = _connect_or_skip()
    try:
        assert read_library(conn, _SCHEMA) is None  # 還沒發布
        lib = _lib([0, 1], ["A", "B"], [[0.0, 1.0], [np.nan, 2.0]], [0.1, -0.2], n_sessions=5)
        parts = encode_library(lib, ["a", "b"], "D-182")
        write_library(conn, _SCHEMA, parts, "D-182", now_tw())
        write_library(conn, _SCHEMA, parts, "D-182", now_tw())  # 整表覆蓋，重跑冪等
        got = read_library(conn, _SCHEMA)
        assert got == parts
        day = dt.date(2026, 10, 15)
        stats = {
            "n": 2,
            "mean": 0.0,
            "median": 0.0,
            "win_rate": 0.5,
            "big_loss_rate": 0.5,
            "p10": -0.2,
            "p90": 0.1,
            "best": 0.1,
            "worst": -0.2,
        }
        row = SimilarRow(
            "QUIET",
            "2330",
            stats,
            [{"d": "2020-01-06", "sid": "A", "r": 0.1}],
            (dt.date(2020, 1, 6), dt.date(2020, 1, 7)),
        )
        write_similar(
            conn,
            _SCHEMA,
            day,
            [row, row.__class__("QUIETV", "2330", stats, [], None)],
            "D-182",
            now_tw(),
        )
        write_similar(conn, _SCHEMA, day, [row], "D-182", now_tw())  # 整天覆蓋
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(f'SELECT line, n, examples::text, span_from FROM "{_SCHEMA}".pick_similar')
            assert cur.fetchall() == [
                ("QUIET", 2, '[{"d": "2020-01-06", "r": 0.1, "sid": "A"}]', dt.date(2020, 1, 6))
            ]
        with pytest.raises(DataError, match="找不到可比的情境"):
            write_similar(
                conn,
                _SCHEMA,
                day,
                [SimilarRow("QUIET", "1101", {**stats, "n": 0}, [], None)],
                "D-182",
                now_tw(),
            )
    finally:
        with conn.cursor() as cur:  # type: ignore[attr-defined]
            cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        conn.commit()  # type: ignore[attr-defined]
        conn.close()  # type: ignore[attr-defined]
