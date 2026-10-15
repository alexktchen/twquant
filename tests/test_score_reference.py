"""D-179／T-359：歷史同區間參考——登記不得改、分區間、爆量、結果包的新買進、同一套逐筆規則、期間、開發期上界。"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.backtest import costs as ct
from twquant.config import load_config
from twquant.errors import DataError
from twquant.reporting.holdout import config_fingerprint
from twquant.reporting.score_reference import (
    ALL,
    Bucket,
    LineRef,
    ScoreReferenceConfig,
    bucket_lists,
    bucket_of,
    bundle_entries,
    bundle_line_rows,
    distribution,
    render,
    score_line_rows,
    volume_surge,
)

_ROOT = Path(__file__).resolve().parents[1]
_CC = ct.CostConfig()


def _cfg(**over: Any) -> ScoreReferenceConfig:
    base = load_config(_ROOT / "configs" / "score_reference.yaml", ScoreReferenceConfig)
    return base.model_copy(update=over) if over else base


def test_d179_ac1_registered_definition_is_unchanged() -> None:
    """登記之後不得修改：關鍵數值寫死比對，整份設定用指紋比對（要換定義請另開登記）。"""
    cfg = _cfg()
    assert cfg.registration == "D-179"
    assert cfg.dev_end == dt.date(2026, 8, 31)
    assert (cfg.hold_sessions, cfg.lot_size, cfg.max_participation, cfg.big_loss) == (
        20,
        1,
        0.05,
        -0.15,
    )
    assert cfg.notional == pytest.approx(10_000_000 / 15, rel=1e-6)
    assert [b.key for b in cfg.buckets] == [
        "<0.25",
        "0.25-0.30",
        "0.30-0.35",
        "0.35-0.40",
        "0.40-0.50",
        ">=0.50",
    ]
    assert cfg.lines["CORE"].kind == "bundle" and set(cfg.lines["CORE"].bundles or {}) == {
        "dev",
        "unseen",
    }
    for code in ("QUIET", "QUIETV"):
        ref = cfg.lines[code]
        assert ref.model_id == "lgbm_quietA20_tu_h20_20260430_655df6d7"
        assert (ref.oos_start, ref.train_end) == (dt.date(2017, 10, 2), dt.date(2026, 4, 30))
    assert (cfg.lines["QUIETV"].volume_window, cfg.lines["QUIETV"].volume_ratio_min) == (20, 1.5)
    assert cfg.lines["QUIET"].volume_window is None
    assert (
        config_fingerprint(cfg)
        == "d7279a1acbb38be8892ee427ffc61de568ed43531262058e2bfb99b3aa7de63f"
    ), "D-179 的定義在登記之後被改了；要換定義請另開登記"
    with pytest.raises(ValueError, match="連續不重疊"):
        ScoreReferenceConfig.model_validate(
            {**cfg.model_dump(mode="json"), "buckets": [{"key": "a", "lo": 0.1, "hi": 0.5}]}
        )
    with pytest.raises(ValueError, match="一起給"):
        LineRef(
            kind="score",
            model_id="m",
            oos_start=dt.date(2020, 1, 1),
            train_end=dt.date(2021, 1, 1),
            volume_window=20,
        )


_B = [Bucket(key="lo", lo=0.0, hi=0.3), Bucket(key="hi", lo=0.3, hi=1.0001)]
_D = [dt.date(2026, 1, 5) + dt.timedelta(days=i) for i in range(40)]


def _scores(*rows: tuple[int, str, float]) -> pl.DataFrame:
    return pl.DataFrame(
        [(_D[i], sid, s) for i, sid, s in rows],
        schema={"date": pl.Date, "stock_id": pl.String, "score": pl.Float64},
        orient="row",
    )


def test_d179_ac2_buckets_are_left_closed_and_daily_lists_follow_the_score() -> None:
    assert bucket_of(0.0, _B) == "lo" and bucket_of(0.3, _B) == "hi" and bucket_of(1.0, _B) == "hi"
    assert (
        bucket_of(-0.1, _B) is None
        and bucket_of(1.5, _B) is None
        and bucket_of(float("nan"), _B) is None
    )
    sc = _scores((0, "1101", 0.2), (0, "1102", 0.5), (0, "1103", 0.9), (1, "1101", 0.35))
    lists = bucket_lists(sc, _B)
    assert lists["hi"] == {_D[0]: ["1103", "1102"], _D[1]: ["1101"]}  # 分數高的在前
    assert lists["lo"] == {_D[0]: ["1101"], _D[1]: []}  # 那天有打分、區間沒股票＝空名單（有公布）
    keep = pl.DataFrame({"date": [_D[0]], "stock_id": ["1102"]})
    assert bucket_lists(sc, _B, keep)["hi"] == {_D[0]: ["1102"], _D[1]: []}
    with pytest.raises(DataError, match="不在任何區間"):
        bucket_lists(_scores((0, "1101", 1.5)), _B)
    with pytest.raises(DataError, match="空值或非有限數"):
        bucket_lists(_scores((0, "1101", float("nan"))), _B)


def test_d179_ac3_volume_surge_uses_the_prior_window_mean() -> None:
    rows = [(_D[i], "1101", 100.0) for i in range(5)] + [
        (_D[5], "1101", 160.0),
        (_D[6], "1101", 140.0),
    ]
    rows += [(_D[i], "1102", 100.0) for i in range(7)]
    px = pl.DataFrame(
        rows, schema={"date": pl.Date, "stock_id": pl.String, "volume": pl.Float64}, orient="row"
    )
    got = volume_surge(px, 5, 1.5)
    assert got.to_dicts() == [
        {"date": _D[5], "stock_id": "1101"}
    ]  # 160 ≥ 100×1.5；隔天 140 對 112 不到 1.5


def _bundle(ledger: list[dict[str, Any]], end: dt.date = _D[-1]) -> dict[str, Any]:
    return {
        "start": _D[0].isoformat(),
        "end": end.isoformat(),
        "equity": [[d.isoformat(), 1.0, 1.0, 0.0] for d in _D],
        "ledger": ledger,
        "unfilled": [],
    }


def _t(i: int, sid: str, side: str, shares: int = 1000, price: float = 100.0) -> dict[str, Any]:
    return {
        "date": _D[i].isoformat(),
        "stock_id": sid,
        "side": side,
        "shares": shares,
        "price": price,
    }


def test_d179_ac4_bundle_entries_are_new_positions_with_the_previous_session_as_signal() -> None:
    b = _bundle(
        [_t(1, "1101", "buy"), _t(3, "1101", "buy"), _t(5, "1101", "sell"), _t(5, "1102", "buy")]
    )
    assert bundle_entries(b, _D) == [(_D[0], "1101"), (_D[4], "1102")]  # 加碼不算、賣出不算
    with pytest.raises(DataError, match="第一天"):
        bundle_entries(_bundle([_t(0, "1101", "buy")]), _D)


def _row(px: float) -> dict[str, Any]:
    return {
        "open": px,
        "close": px,
        "_prev_close": px,
        "_adv": 1e10,
        "limit_up_price": None,
        "limit_down_price": None,
    }


def _panel(paths: Mapping[str, Sequence[float]]) -> dict[dt.date, dict[str, dict[str, Any]]]:
    """每檔一條價格路徑（不足的天數沿用最後一個價）。"""
    return {
        d: {sid: _row(p[min(i, len(p) - 1)]) for sid, p in paths.items()} for i, d in enumerate(_D)
    }


def _small(**over: Any) -> ScoreReferenceConfig:
    """用 3 天持有期、不扣成本的小設定，報酬好算；其餘照登記。"""
    return _cfg(**{"hold_sessions": 3, "notional": 100_000.0, "dev_end": _D[-1], **over})


_NO_COST = ct.CostConfig(
    commission_rate=0.0,
    commission_min_twd=0.0,
    tax_rate_sell=0.0,
    cost_scale=0.0,
    slippage=ct.SlippageConfig(model="none"),
)


def test_d179_ac5_score_line_rows_follow_the_scorecard_rule_and_split_by_train_end() -> None:
    cfg = _small()
    ref = LineRef(kind="score", model_id="m", oos_start=_D[0], train_end=_D[10])
    # 1101：d1 公布（分數 0.5）→ d2 開盤 100 買、抱 3 天、d6 開盤 110 賣＝+10%；d12 再公布一次 → d13 買 110、d17 賣 99＝−10%
    # 1102：d1 公布（分數 0.2）→ d2 買 50、d6 賣 55＝+10%
    # 1101 在 d3 分數掉到 0.1（離開 ≥0.50、進 <0.25）：d4 買 100、d8 賣 110＝+10%；d12 才又算新進 ≥0.50
    panel = _panel({"1101": [100] * 6 + [110] * 7 + [110] * 4 + [99], "1102": [50] * 5 + [55]})
    sc = _scores(
        (1, "1101", 0.5), (1, "1102", 0.2), (2, "1101", 0.5), (3, "1101", 0.1), (12, "1101", 0.5)
    )
    rows = score_line_rows("QUIET", ref, sc, _D, panel, {}, {}, _NO_COST, cfg)
    by = {(r.period, r.bucket): r for r in rows}
    assert set(by) == {(p, b.key) for p in ("dev", "unseen") for b in cfg.buckets}
    hi = by[("dev", ">=0.50")]
    assert (
        hi.n == 1 and hi.median == pytest.approx(0.10) and hi.signals_from == _D[1]
    )  # d2 還抱著：不重複
    assert by[("unseen", ">=0.50")].n == 1 and by[("unseen", ">=0.50")].median == pytest.approx(
        -0.10
    )
    assert by[("dev", "<0.25")].n == 2 and by[("dev", "<0.25")].win_rate == 1.0
    assert by[("dev", "0.30-0.35")].n == 0 and by[("dev", "0.30-0.35")].median is None
    # 爆量條件：只留量夠的 (日, 檔)
    vref = LineRef(
        kind="score",
        model_id="m",
        oos_start=_D[0],
        train_end=_D[10],
        volume_window=1,  # d1 只有一個前一天（d0）可比
        volume_ratio_min=1.5,
    )
    vol = pl.DataFrame(
        [
            (d, sid, 100.0 if not (sid == "1102" and d == _D[1]) else 300.0)
            for d in _D[:5]
            for sid in ("1101", "1102")
        ],
        schema={"date": pl.Date, "stock_id": pl.String, "volume": pl.Float64},
        orient="row",
    )
    vrows = {
        (r.period, r.bucket): r
        for r in score_line_rows("QUIETV", vref, sc, _D, panel, {}, {}, _NO_COST, cfg, vol)
    }
    assert vrows[("dev", "<0.25")].n == 1 and vrows[("dev", ">=0.50")].n == 0
    with pytest.raises(ValueError, match="得給 prices"):
        score_line_rows("QUIETV", vref, sc, _D, panel, {}, {}, _NO_COST, cfg)


def test_d179_ac6_nothing_after_dev_end_is_used() -> None:
    cfg = _small(dev_end=_D[20])
    ref = LineRef(kind="score", model_id="m", oos_start=_D[0], train_end=_D[10])
    panel = _panel({"1101": [100]})
    with pytest.raises(DataError, match="交易日序列"):
        score_line_rows("QUIET", ref, _scores((1, "1101", 0.5)), _D, panel, {}, {}, _NO_COST, cfg)
    with pytest.raises(DataError, match="超出"):
        score_line_rows(
            "QUIET", ref, _scores((25, "1101", 0.5)), _D[:21], panel, {}, {}, _NO_COST, cfg
        )
    # 到 dev_end 還沒賣出的筆不算（不是 0）
    sc = _scores((1, "1101", 0.5), (2, "1101", 0.1), (19, "1101", 0.5))
    rows = score_line_rows("QUIET", ref, sc, _D[:21], panel, {}, {}, _NO_COST, cfg)
    assert {(r.period, r.bucket): r.n for r in rows if r.n}[("dev", ">=0.50")] == 1
    with pytest.raises(DataError, match="越過開發期上界"):
        bundle_line_rows(
            "CORE",
            {"dev": _bundle([]), "unseen": _bundle([], end=_D[21])},
            _D[:21],
            panel,
            {},
            {},
            _NO_COST,
            cfg,
        )


def test_d179_ac7_bundle_line_is_one_bucket_per_period() -> None:
    cfg = _small()
    panel = _panel({"1101": [100] * 2 + [100] * 4 + [120], "1102": [50] * 4 + [50] * 4 + [45]})
    dev = _bundle(
        [_t(2, "1101", "buy"), _t(4, "1102", "buy"), _t(6, "1101", "buy", 500)]
    )  # 第三筆是加碼
    unseen = _bundle([_t(2, "1102", "buy")])
    rows = bundle_line_rows(
        "CORE", {"dev": dev, "unseen": unseen}, _D, panel, {}, {}, _NO_COST, cfg
    )
    assert [(r.period, r.bucket, r.n, r.lo) for r in rows] == [
        ("dev", ALL, 2, None),
        ("unseen", ALL, 1, None),
    ]
    assert rows[0].mean == pytest.approx((0.20 - 0.10) / 2) and rows[0].win_rate == 0.5
    assert rows[0].p10 is not None and rows[0].worst == pytest.approx(-0.10)


def test_d179_ac8_distribution_and_report() -> None:
    d = distribution([0.1, -0.2, 0.05, 0.3, -0.16], -0.15)
    assert (d["n"], d["win_rate"], d["big_loss_rate"]) == (5, 0.6, 0.4)
    assert d["p10"] < d["median"] < d["p90"] and d["median"] == pytest.approx(0.05)
    assert distribution([0.1], -0.15)["p10"] is None and distribution([], -0.15)["mean"] is None
    cfg = _cfg()
    rows = bundle_line_rows(
        "CORE",
        {"dev": _bundle([]), "unseen": _bundle([])},
        _D,
        _panel({"1101": [100]}),
        {},
        {},
        _NO_COST,
        _small(),
    )
    text = render(rows, cfg, {"CORE": "大盤精選"})
    assert "# D-179 歷史同區間參考" in text and "## 大盤精選（CORE）" in text
    assert "| 開發期（規則在這段資料上試出來的，只當參考） | ALL | 0 | — |" in text


# ── PG（無 DSN 則 skip）────────────────────────────────────────────────────

_SCHEMA = "serving_test_score_reference"


def _connect_or_skip() -> Any:
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


def test_d179_ac9_pg_whole_table_overwrite_and_read_back() -> None:
    from twquant.conventions import TZ
    from twquant.data.serving.score_reference_store import read_reference, write_reference
    from twquant.reporting.score_reference import RefRow

    conn = _connect_or_skip()
    try:
        assert read_reference(conn, _SCHEMA) == []  # 表還沒建：回空，不 raise
        at = dt.datetime(2026, 10, 14, 23, 0, tzinfo=TZ)
        labels = {"dev": "開發期", "unseen": "沒看過"}
        a = RefRow(
            "QUIET",
            "dev",
            "<0.25",
            0.0,
            0.25,
            3,
            0.01,
            0.0,
            0.5,
            0.0,
            -0.1,
            0.1,
            0.2,
            -0.1,
            _D[0],
            _D[5],
        )
        core = RefRow(
            "CORE",
            "unseen",
            ALL,
            None,
            None,
            0,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )
        write_reference(conn, _SCHEMA, [a, core], labels, "D-179", at)
        write_reference(conn, _SCHEMA, [a], labels, "D-179", at)  # 整表覆蓋：CORE 不會留著
        assert read_reference(conn, _SCHEMA) == [a]
        with pytest.raises(DataError, match="沒有任何一列"):
            write_reference(conn, _SCHEMA, [], labels, "D-179", at)
        with pytest.raises(DataError, match="NaN"):
            write_reference(
                conn,
                _SCHEMA,
                [a.__class__(**{**a.__dict__, "mean": float("nan")})],
                labels,
                "D-179",
                at,
            )
        with pytest.raises(DataError, match="沒有說明"):
            write_reference(conn, _SCHEMA, [a], {"unseen": "x"}, "D-179", at)
        with conn.cursor() as cur:
            cur.execute(f'SELECT label, registration FROM "{_SCHEMA}".score_reference')
            assert cur.fetchall() == [("開發期", "D-179")]
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        conn.commit()
        conn.close()
