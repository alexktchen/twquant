"""D-177 修訂／T-353：族群資金——成交比重、資金倍數、漲跌家數、資金流入標記，與 PG 讀寫。"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data.stock_groups import GroupFlowConfig
from twquant.errors import DataError
from twquant.reporting.group_flow import FlowMember, GroupFlow, group_flow

_D = [dt.date(2026, 10, 6), dt.date(2026, 10, 7), dt.date(2026, 10, 8), dt.date(2026, 10, 13)]
_UNIVERSE = r"^[1-9][0-9]{3}$"
_CFG = GroupFlowConfig(
    avg_sessions=3, hot_ratio=1.5, hot_breadth=0.6, hot_min_members=3, max_members=2
)
_GROUPS = {"1101": "A", "1102": "A", "1103": "A", "2201": "B", "2202": "B"}
_NAMES = {"1101": "甲一", "1102": "甲二", "1103": "甲三", "2201": "乙一"}


def _prices(extra: list[tuple[dt.date, str, float, float]] = ()) -> pl.DataFrame:  # type: ignore[assignment]
    """前三天：A 三檔各 10、B 兩檔各 10、沒族群的 9999 50（全市場 100）；收盤都是 10。
    當天：A 各 30（90）、B 各 5（10）、9999 100（全市場 200）；A 兩漲一跌、B 一平一跌。0050 量很大但不算全市場。"""
    rows: list[tuple[dt.date, str, float, float]] = []
    for d in _D[:3]:
        rows += [(d, s, 10.0, 10.0) for s in ("1101", "1102", "1103", "2201", "2202")]
        rows += [(d, "9999", 10.0, 50.0), (d, "0050", 100.0, 1e9)]
    last = _D[3]
    rows += [(last, "1101", 11.0, 30.0), (last, "1102", 10.5, 30.0), (last, "1103", 9.5, 30.0)]
    rows += [(last, "2201", 10.0, 5.0), (last, "2202", 9.0, 5.0), (last, "9999", 10.0, 100.0)]
    rows += [(last, "0050", 100.0, 5e9), *extra]
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "stock_id": pl.String, "close": pl.Float64, "amount": pl.Float64},
        orient="row",
    )


def _flows(prices: pl.DataFrame | None = None, cfg: GroupFlowConfig = _CFG) -> dict[str, GroupFlow]:
    got = group_flow(
        _prices() if prices is None else prices, _GROUPS, _NAMES, _D[3], cfg, _UNIVERSE
    )
    return {f.group: f for f in got}


def test_d177r_ac7_ratio_is_todays_share_over_the_prior_average() -> None:
    """A：比重 90/200＝0.45，前三天平均 0.30 → 資金倍數 1.5；三檔兩漲 → 資金流入。0050 不算進全市場。"""
    f = _flows()
    a, b = f["A"], f["B"]
    assert a.amount == 90.0
    assert a.share == pytest.approx(0.45)
    assert a.share_avg == pytest.approx(0.30)
    assert a.ratio == pytest.approx(1.5)
    assert (a.n, a.up, a.down) == (3, 2, 1)
    assert a.ret == pytest.approx((0.1 + 0.05 - 0.05) / 3)
    assert a.hot is True
    assert (b.up, b.down) == (0, 1)
    assert b.share == pytest.approx(0.05)
    assert b.ratio == pytest.approx(0.25)
    assert b.ret == pytest.approx(-0.05)
    assert b.hot is False
    assert set(f) == {"A", "B"}  # 沒有族群的 9999 只算進全市場


def test_d177r_ac7_hot_needs_ratio_breadth_and_enough_members() -> None:
    """三個條件少一個就不標：倍數門檻、上漲比例、成員數。"""
    assert _flows(cfg=_CFG.model_copy(update={"hot_ratio": 1.6}))["A"].hot is False
    assert _flows(cfg=_CFG.model_copy(update={"hot_breadth": 0.7}))["A"].hot is False
    assert _flows(cfg=_CFG.model_copy(update={"hot_min_members": 4}))["A"].hot is False


def test_d177r_ac7_members_are_the_biggest_by_amount_with_change() -> None:
    a = _flows()["A"]
    # 金額一樣多時照代號；max_members＝2
    assert a.members == (
        FlowMember("1101", "甲一", pytest.approx(0.1), 30.0),  # type: ignore[arg-type]
        FlowMember("1102", "甲二", pytest.approx(0.05), 30.0),  # type: ignore[arg-type]
    )
    assert _flows()["B"].members[1] == FlowMember("2202", None, pytest.approx(-0.1), 5.0)  # type: ignore[arg-type]


def test_d177r_ac7_new_stock_counts_in_amount_but_not_in_breadth() -> None:
    """當天才有行情的新股：成交金額算進族群，但沒有前收、漲跌不算（n 照算）。"""
    new = _prices([(_D[3], "1104", 50.0, 20.0)])
    a = group_flow(new, {**_GROUPS, "1104": "A"}, _NAMES, _D[3], _CFG, _UNIVERSE)[0]
    assert (a.n, a.up, a.down, a.amount) == (4, 2, 1, 110.0)
    assert a.ret == pytest.approx((0.1 + 0.05 - 0.05) / 3)
    assert a.hot is True  # 算得出漲跌的 3 檔裡 2 檔漲


def test_d177r_ac7_a_group_with_no_trades_on_a_prior_day_had_zero_share_then() -> None:
    """C 前兩天沒有成交、前一天 10：前三天平均＝(0+0+10/110)/3，不是只平均有成交的那天。"""
    extra = [(_D[2], "3301", 10.0, 10.0), (_D[3], "3301", 11.0, 20.0)]
    c = group_flow(_prices(extra), {**_GROUPS, "3301": "C"}, _NAMES, _D[3], _CFG, _UNIVERSE)
    got = {f.group: f for f in c}["C"]
    assert got.share_avg == pytest.approx((10 / 110) / 3)
    assert got.share == pytest.approx(20 / 220)


def test_d177r_ac7_not_enough_history_means_no_ratio() -> None:
    """前面不到 avg_sessions 個交易日：沒有資金倍數、不標資金流入（漲跌照算）。"""
    short = _prices().filter(pl.col("date") >= _D[2])
    a = group_flow(short, _GROUPS, _NAMES, _D[3], _CFG, _UNIVERSE)[0]
    assert (a.share_avg, a.ratio, a.hot) == (None, None, False)
    assert (a.up, a.down) == (2, 1)


def test_d177r_ac7_bad_input_raises() -> None:
    with pytest.raises(DataError, match="沒有行情"):
        group_flow(_prices(), _GROUPS, _NAMES, dt.date(2026, 10, 14), _CFG, _UNIVERSE)
    broken = _prices().with_columns(
        pl.when(pl.col("stock_id") == "1101").then(None).otherwise(pl.col("amount")).alias("amount")
    )
    with pytest.raises(DataError, match="空的"):
        group_flow(broken, _GROUPS, _NAMES, _D[3], _CFG, _UNIVERSE)
    with pytest.raises(DataError, match="少了欄位"):
        group_flow(_prices().drop("amount"), _GROUPS, _NAMES, _D[3], _CFG, _UNIVERSE)


def test_d177r_ac7_deterministic_and_sorted_by_group() -> None:
    first = group_flow(_prices(), _GROUPS, _NAMES, _D[3], _CFG, _UNIVERSE)
    shuffled = _prices().sample(fraction=1.0, shuffle=True, seed=7)
    assert group_flow(shuffled, _GROUPS, _NAMES, _D[3], _CFG, _UNIVERSE) == first
    assert [f.group for f in first] == ["A", "B"]


def test_d177r_ac8_step_runs_after_groups_and_in_the_cloud() -> None:
    """族群資金要讀族群表：排在族群標籤後面；雲端不略過；雲端修剪窗口有它、修剪前先建表（靜態）。"""
    from pathlib import Path

    from twquant.config import ServingConfig, load_config

    root = Path(__file__).resolve().parent.parent
    source = (root / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert source.index('PipelineStep("族群標籤"') < source.index('PipelineStep("族群資金"')
    cfg = load_config(root / "configs" / "serving.yaml", ServingConfig)
    assert "族群資金" not in cfg.cloud.skip_pipeline_steps
    assert cfg.cloud.windows["group_flow_daily"] >= 1
    prune_src = source[source.index("def serving_prune(") :]
    assert prune_src.index("ensure_tables(conn, (GROUP_FLOW_SPEC,)") < prune_src.index(
        "results = prune("
    )


# ── PG（無 DSN 則 skip）────────────────────────────────────────────────────

_SCHEMA = "serving_test_group_flow"


def _connect_or_skip() -> Any:
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
        cur.execute(
            f'CREATE TABLE "{_SCHEMA}".prices_daily (date date, stock_id text, close double precision, '
            "amount double precision, PRIMARY KEY (stock_id, date))"
        )
        cur.executemany(
            f'INSERT INTO "{_SCHEMA}".prices_daily VALUES (%s, %s, %s, %s)', _prices().rows()
        )
    conn.commit()
    return conn


def test_d177r_ac8_pg_window_and_whole_day_overwrite() -> None:
    from twquant.data.serving.group_flow_store import read_price_window, write_group_flow

    conn = _connect_or_skip()
    try:
        window = read_price_window(conn, _SCHEMA, _D[3], 2)
        assert sorted(window.get_column("date").unique().to_list()) == _D[2:]
        flows = group_flow(
            read_price_window(conn, _SCHEMA, _D[3], 4), _GROUPS, _NAMES, _D[3], _CFG, _UNIVERSE
        )
        assert flows == group_flow(_prices(), _GROUPS, _NAMES, _D[3], _CFG, _UNIVERSE)
        at = dt.datetime(2026, 10, 13, 17, 40, tzinfo=TZ)
        write_group_flow(conn, _SCHEMA, _D[3], flows, at)
        write_group_flow(conn, _SCHEMA, _D[3], flows[:1], at)  # 重跑整天覆蓋：B 不會留著
        with conn.cursor() as cur:
            cur.execute(f'SELECT grp, ratio, hot, members::text FROM "{_SCHEMA}".group_flow_daily')
            rows = cur.fetchall()
        assert [(g, round(r, 6), h) for g, r, h, _ in rows] == [("A", 1.5, True)]
        assert json.loads(rows[0][3])[0] == {
            "sid": "1101",
            "name": "甲一",
            "chg": pytest.approx(0.1),
            "amount": 30.0,
        }
        with pytest.raises(DataError, match="0 個族群"):
            write_group_flow(conn, _SCHEMA, _D[3], [], at)
        nan = [GroupFlow(**{**flows[0].__dict__, "ratio": float("nan")})]
        with pytest.raises(DataError, match="NaN"):
            write_group_flow(conn, _SCHEMA, _D[3], nan, at)
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        conn.commit()
        conn.close()
