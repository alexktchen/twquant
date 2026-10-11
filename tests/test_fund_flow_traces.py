"""SPEC-016 T-339（A 級，先凍結）：事件去重與前置空窗（AC-16）、配對不足丟棄並計數（AC-17）、
最晚日期守門（AC-18）、報告欄位與 JSON 重算 markdown（AC-19）、研究登記簿（AC-20）。"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import polars as pl
import pytest

from twquant.config import FundFlowEventsConfig, FundFlowTracesConfig
from twquant.conventions import TZ
from twquant.reporting import fund_flow_traces as ft

_ALL = [dt.date(2026, 3, 2) + dt.timedelta(days=k) for k in range(40)]
S = [d for d in _ALL if d.weekday() < 5]  # 交易日曆（週一～五）
_EV = {"event": pl.String, "date": pl.Date, "stock_id": pl.String}


def _ev(rows: list[tuple[str, dt.date, str]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=_EV, orient="row")


# ── AC-16 事件 ────────────────────────────────────────────────────────────────


def test_ac16_dedup_keeps_first_within_window() -> None:
    e = _ev(
        [
            ("E3", S[0], "2330"),
            ("E3", S[2], "2330"),
            ("E3", S[4], "2330"),
            ("E4", S[1], "2330"),
            ("E3", S[1], "2317"),
        ]
    )
    got = set(ft.dedup(e, S, 3).iter_rows())
    # S[2] 距 S[0] 2 日 ≤ 3 → 丟；S[4] 距被保留的 S[0] 4 日 > 3 → 留；不同事件、不同股各自計
    assert got == {
        ("E3", S[0], "2330"),
        ("E3", S[4], "2330"),
        ("E4", S[1], "2330"),
        ("E3", S[1], "2317"),
    }


def _disc(vals: list[float | None], sid: str = "2330") -> pl.DataFrame:
    return pl.DataFrame(
        {"date": S[: len(vals)], "stock_id": [sid] * len(vals), "disc": vals},
        schema={"date": pl.Date, "stock_id": pl.String, "disc": pl.Float64},
    )


def test_ac16_streak_start_needs_quiet_before_and_known_days() -> None:
    # 5 日 ≤ 0 之後連 3 日 > 0 → 第 3 日（S[7]）是 E5；第 4 日不重複算
    d = _disc([-1, 0, -2, -1, 0, 5, 6, 7, 8])
    assert ft.streak_start_events(d, S, 3, 5, buy=True, name="E5")["date"].to_list() == [S[7]]
    # 前置空窗裡有一天買超 → 不成立
    d2 = _disc([-1, 3, -2, -1, 0, 5, 6, 7])
    assert ft.streak_start_events(d2, S, 3, 5, buy=True, name="E5").height == 0
    # 空窗裡有一天未知 → 不成立（不得當 0）
    d3 = _disc([-1, None, -2, -1, 0, 5, 6, 7])
    assert ft.streak_start_events(d3, S, 3, 5, buy=True, name="E5").height == 0
    # 賣方向
    d4 = _disc([1, 0, 2, 1, 0, -5, -6, -7])
    assert ft.streak_start_events(d4, S, 3, 5, buy=False, name="E6")["date"].to_list() == [S[7]]


def test_ac16_streak_does_not_bridge_missing_session() -> None:
    d = _disc([-1, 0, -2, -1, 0, 5, 6, 7]).filter(pl.col("date") != S[6])  # 該股缺 S[6]
    assert ft.streak_start_events(d, S, 3, 5, buy=True, name="E5").height == 0


def test_ac16_tail_events_need_sign_and_percentile() -> None:
    p = pl.DataFrame(
        {
            "date": [S[0]] * 20,
            "stock_id": [f"{1000 + i}" for i in range(20)],
            "x": [float(i - 10) for i in range(20)],
        }
    )
    hi = ft.tail_events(p, "x", hi=0.95, lo=None, name="E3")
    lo = ft.tail_events(p, "x", hi=None, lo=0.05, name="E4")
    assert hi["stock_id"].to_list() == ["1019"] and lo["stock_id"].to_list() == ["1000"]
    neg = p.with_columns(pl.col("x") - 100)  # 全部 < 0：頂端百分位也不是申購潮
    assert ft.tail_events(neg, "x", hi=0.95, lo=None, name="E3").height == 0


def test_ac16_quarter_end_and_rebalance_signal_date() -> None:
    q = ft.quarter_end_days(S, 2)
    assert dt.date(2026, 3, 30) in q and dt.date(2026, 3, 31) in q and dt.date(2026, 3, 27) not in q
    rebal = pl.DataFrame(
        {
            "stock_id": ["6446", "3661"],
            "action": ["add", "remove"],
            "available_at": [
                dt.datetime(2026, 3, 18, 16, 0, tzinfo=TZ),
                dt.datetime(2026, 3, 18, 21, 0, tzinfo=TZ),
            ],
        }
    )
    got = {(r[0], r[2]): r[1] for r in ft.rebalance_events(rebal, S, 20).iter_rows()}
    assert got[("E1", "6446")] == dt.date(2026, 3, 18)  # 16:00 公布 ≤ 當日 20:00 截點
    assert got[("E2", "3661")] == dt.date(2026, 3, 19)  # 21:00 公布 → 下一個交易日


def test_ac16_detect_events_e8_requires_all_three() -> None:
    cfg = FundFlowEventsConfig(flow_hi_pctl=0.9, absorb_pctl=0.8, dedup_sessions=5)
    ids = [f"{1000 + i}" for i in range(20)]
    day = S[10]
    uni = pl.DataFrame({"date": [day] * 20, "stock_id": ids})

    def panel(vals: list[float]) -> pl.DataFrame:
        return pl.DataFrame({"date": [day] * 20, "stock_id": ids, "value": vals})

    empty = pl.DataFrame(schema={"date": pl.Date, "stock_id": pl.String, "value": pl.Float64})
    ev = ft.detect_events(
        sessions=S,
        rebal=pl.DataFrame(
            schema={
                "stock_id": pl.String,
                "action": pl.String,
                "available_at": pl.Datetime("us", "Asia/Taipei"),
            }
        ),
        pressure=panel([float(i) for i in range(20)]),  # 1018、1019 是申購潮
        disc=pl.DataFrame(schema={"date": pl.Date, "stock_id": pl.String, "disc": pl.Float64}),
        disc_net=empty,
        sbl=panel([1.0] * 18 + [9.0, 0.0]),  # 只有 1018 借券高
        margin=panel([1.0] * 18 + [9.0, 9.0]),
        universe=uni,
        cfg=cfg,
        cutoff_hour=20,
    )
    got = {(r[0], r[2]) for r in ev.iter_rows()}
    assert ("E3", "1019") in got and ("E3", "1018") in got
    assert ("E8", "1018") in got and ("E8", "1019") not in got


# ── AC-17 配對 ────────────────────────────────────────────────────────────────


def test_ac17_few_matches_dropped_and_counted() -> None:
    day = S[0]
    ids = [f"{1000 + i}" for i in range(12)]
    pool_b = pl.DataFrame(
        {
            "date": [day] * 12,
            "stock_id": ids,
            "_vol_b": [0] * 6 + [1] * 6,
            "_mom_b": [0] * 12,
            "_adv_b": [0] * 12,
        },
        schema={
            "date": pl.Date,
            "stock_id": pl.String,
            "_vol_b": pl.Int32,
            "_mom_b": pl.Int32,
            "_adv_b": pl.Int32,
        },
    )
    rets = pl.DataFrame(
        {
            "date": [day] * 12,
            "stock_id": ids,
            "r1": [0.01 * i for i in range(12)],
            "pre": [0.0] * 12,
        }
    )
    # 1000 那格 6 檔、扣掉自己剩 5 → 夠；1006、1007 那格扣掉兩個事件股剩 4 → 丟；9999 不在池內
    events = _ev(
        [("E3", day, "1000"), ("E3", day, "1006"), ("E3", day, "1007"), ("E3", day, "9999")]
    )
    x, drops = ft.matched_excess(events, pool_b, rets, [1], matched_min=5)
    assert x["stock_id"].to_list() == ["1000"]
    assert drops == {"not_in_pool": 1, "few_matches": 2}
    assert x["x_r1"][0] == pytest.approx(-0.03)  # 0.00 − 1001..1005 的中位數 0.03


def test_ac17_paths_do_not_bridge_gaps() -> None:
    rows = [(S[k], "2330", 100.0 + k, 100.0 + k) for k in range(8) if k != 4]
    px = ft.with_pos(
        pl.DataFrame(
            rows,
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "open_adj": pl.Float64,
                "close_adj": pl.Float64,
            },
            orient="row",
        ),
        S,
    )
    p = ft.paths(px, [1, 2], 2)
    r = {d: (a, b, c) for d, _, a, b, c in p.iter_rows()}
    assert r[S[0]][0] == pytest.approx(102 / 101 - 1)
    assert r[S[2]][0] is None  # 進場 S[3]、出場 S[4] 缺 → null
    assert r[S[2]][2] == pytest.approx(102 / 100 - 1)
    assert r[S[5]][2] is None  # 事前窗跨過缺的 S[4]


# ── AC-18 最晚日期 ─────────────────────────────────────────────────────────────


def test_ac18_last_date_guard() -> None:
    ft.guard_last_date(
        pl.DataFrame({"date": [dt.date(2026, 8, 31)]}), dt.date(2026, 8, 31), "prices"
    )
    with pytest.raises(ft.LastDateError, match="第二保留期"):
        ft.guard_last_date(
            pl.DataFrame({"date": [dt.date(2026, 9, 9)]}), dt.date(2026, 8, 31), "prices"
        )


# ── AC-19 報告 ────────────────────────────────────────────────────────────────


def _report() -> dict[str, Any]:
    tcfg = FundFlowTracesConfig(horizons=(1, 5), post_sessions=5, bootstrap_n=200)
    rng = [(S[k], f"{1000 + k}") for k in range(10)]
    x = pl.DataFrame(
        {
            "event": ["E3"] * 10,
            "date": [d for d, _ in rng],
            "stock_id": [s for _, s in rng],
            "n_matched": [6] * 10,
            "x_r1": [0.01 * (k - 3) for k in range(10)],
            "x_r5": [0.02 * (k - 3) for k in range(10)],
            "x_pre": [0.0] * 10,
            "raw_r1": [0.0] * 10,
            "raw_r5": [0.0] * 10,
            "raw_pre": [0.0] * 10,
        }
    )
    cov = pl.DataFrame({"date": [d for d, _ in rng], "value": [1.0] * 5 + [0.0] * 5})
    res = ft.summarize(
        x,
        strata={"全市場": x.select("date", "stock_id")},
        coverage=cov,
        tcfg=tcfg,
        cost=0.005,
        split_date=S[3],
        decomposed_min=0.5,
    )
    return {
        "meta": {
            "run_date": "2026-10-11",
            "first_date": str(S[0]),
            "last_date": "2026-08-31",
            "horizons": [1, 5],
            "ref": 5,
            "cost": 0.005,
            "matched_min": 5,
            "drops": {"not_in_pool": 0, "few_matches": 0},
            "strata": ["全市場"],
        },
        "results": res,
    }


def test_ac19_report_fields_and_json_roundtrip() -> None:
    rep = _report()
    node = rep["results"]["全市場"]["E3"]
    h5 = node["horizons"]["5"]
    for key in ("n", "mean", "median", "hit", "per_day", "net_mean", "net_per_day", "boot"):
        assert key in h5
    assert h5["per_day"] == pytest.approx(h5["mean"] / 5)
    assert h5["net_mean"] == pytest.approx(h5["mean"] - 0.005)
    assert set(node["segments"]) == {"decomposed", "not_decomposed", "before_split", "after_split"}
    assert node["segments"]["decomposed"]["n"] == 5
    assert node["by_year"] and node["stocks"] == 10
    md = ft.render_markdown(rep)
    assert ft.render_markdown(json.loads(json.dumps(rep, ensure_ascii=False))) == md
    for word in ("通過", "不通過", "失敗", "成功", "有效", "無效"):
        assert word not in md, f"報告不得含判讀字句：{word}"


def test_ac19_bootstrap_deterministic() -> None:
    v = pl.DataFrame({"date": S[:28], "x": [0.01 * (k % 5 - 2) for k in range(28)]})  # 跨 3、4 月
    a = ft.block_bootstrap(v, "x", 300, 42)
    assert a == ft.block_bootstrap(v, "x", 300, 42)
    assert a["ci_lo"] is not None and a["ci_hi"] is not None and a["mean"] is not None
    assert a["ci_lo"] <= a["mean"] <= a["ci_hi"]


# ── AC-20 研究登記簿 ──────────────────────────────────────────────────────────


def test_ac20_research_entries() -> None:
    rows = ft.research_entries(_report(), "abc123", "2026-10-11")
    assert rows
    assert all(r["trial_scope"] == "flow:traces" and r["params_hash"] == "abc123" for r in rows)
    assert {r["factor"] for r in rows} == {"E3"} and rows[0]["metric_name"] == "excess_r5"
