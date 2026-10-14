"""D-177 修訂三／T-358：族群法人象限圖——上櫃三大法人解析、族群金額、x（近 5 日）、y（加速）、軌跡、PG。"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, cast

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data.sources.tpex import parse_institutional
from twquant.data.stock_groups import GroupInstConfig
from twquant.errors import DataError
from twquant.reporting.group_inst import GroupInst, InstDay, group_inst_day, inst_series

_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = _ROOT / "tests" / "fixtures" / "http" / "tpex" / "insti_2026-10-14.json"
_DAY = dt.date(2026, 10, 14)
_UNIVERSE = r"^[1-9][0-9]{3}$"


def _text() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def test_t358_ac1_parse_tpex_institutional_like_twse() -> None:
    """2026-10-14 實抓：外資＝外資及陸資(不含自營商)＋外資自營商；投信；自營商合計（股）。"""
    got = parse_institutional(_text(), _DAY)
    assert got.columns == ["date", "stock_id", "foreign_net", "trust_net", "dealer_net"]
    assert got.get_column("date").unique().to_list() == [_DAY]
    rows = {r["stock_id"]: r for r in got.to_dicts()}
    assert set(rows) == {"00411A", "3105", "5289", "6147", "6488", "8299"}
    assert (rows["5289"]["foreign_net"], rows["5289"]["trust_net"], rows["5289"]["dealer_net"]) == (
        -69_204,
        0,
        -5_951,
    )
    assert (rows["6147"]["foreign_net"], rows["6147"]["trust_net"], rows["6147"]["dealer_net"]) == (
        -898_514,
        5_290_000,
        170_888,
    )


def test_t358_ac2_parse_refuses_wrong_or_shifted_responses() -> None:
    payload = json.loads(_text())
    with pytest.raises(DataError, match="不是 2026-10-13"):
        parse_institutional(_text(), dt.date(2026, 10, 13))  # 要 10/13 卻回 10/14
    shifted = json.loads(_text())
    shifted["tables"][0]["data"][1][4] = "0"  # 外資買賣超被改：買進−賣出對不起來
    with pytest.raises(DataError, match="對不起來"):
        parse_institutional(json.dumps(shifted), _DAY)
    renamed = json.loads(_text())
    renamed["tables"][0]["fields"][2] = "買進金額"
    with pytest.raises(DataError, match="櫃買可能改版"):
        parse_institutional(json.dumps(renamed), _DAY)
    empty = {**payload, "tables": [{**payload["tables"][0], "data": []}]}
    with pytest.raises(DataError, match="是空的"):
        parse_institutional(json.dumps(empty), _DAY)
    with pytest.raises(DataError, match="非 JSON"):
        parse_institutional("<html>因為安全性考量</html>", _DAY)


def test_t358_ac3_tpex_institutional_stays_out_of_the_model_table() -> None:
    """上櫃法人不能走每日擷取進 institutional_daily（模型讀那張；T-037 另案）。"""
    from twquant.data.clean import TPEX_CLEANERS
    from twquant.data.http import HttpClient
    from twquant.data.sources.tpex import TpexSource

    assert "institutional_daily" not in TpexSource(client=cast(HttpClient, None)).tables
    assert "institutional_daily" not in TPEX_CLEANERS


# ── 族群金額 ───────────────────────────────────────────────────────────────

_GROUPS = {"1101": "A", "1102": "A", "1103": "A", "2201": "B", "2202": "B", "3301": "C"}


def _inst(*rows: tuple[str, int, int, int]) -> pl.DataFrame:
    return pl.DataFrame(
        list(rows),
        schema={
            "stock_id": pl.String,
            "foreign_net": pl.Int64,
            "trust_net": pl.Int64,
            "dealer_net": pl.Int64,
        },
        orient="row",
    )


def _closes(*rows: tuple[str, float] | tuple[str, float, dt.date]) -> pl.DataFrame:
    """每檔最近一個收盤；沒給日期＝當天有收盤。"""
    full = [(r[0], r[1], r[2] if len(r) == 3 else _DAY) for r in rows]
    return pl.DataFrame(
        full, schema={"stock_id": pl.String, "close": pl.Float64, "date": pl.Date}, orient="row"
    )


def test_t358_ac4_group_amount_is_shares_times_close() -> None:
    inst = _inst(
        ("1101", 1_000, 200, -100),  # A：收盤 50
        ("1102", -500, 0, 0),  # A：收盤 20
        ("2201", 0, 0, 300),  # B：收盤 10
        ("0050", 9_999, 0, 0),  # 不在全市場（不是四碼普通股）
        ("9999", 7, 0, 0),  # 不在族群表
    )
    closes = _closes(("1101", 50.0), ("1102", 20.0), ("1103", 30.0), ("2201", 10.0), ("3301", 5.0))
    got = {d.group: d for d in group_inst_day(inst, closes, _GROUPS, _DAY, _UNIVERSE)}
    assert sorted(got) == ["A", "B", "C"]
    a = got["A"]
    assert (a.members, a.foreign, a.trust, a.dealer) == (
        3,
        1_000 * 50 - 500 * 20,
        200 * 50,
        -100 * 50,
    )
    assert a.net == pytest.approx(40_000 + 10_000 - 5_000)
    assert (got["B"].members, got["B"].net) == (1, 3_000)  # 2202 沒有收盤：不算成員
    assert (got["C"].members, got["C"].net) == (1, 0.0)  # 法人沒列出＝那天沒買賣，0
    assert group_inst_day(inst, closes, _GROUPS, _DAY, _UNIVERSE) == list(got.values())  # 決定性


def test_t358_ac5_group_amount_refuses_bad_inputs() -> None:
    closes = _closes(("1101", 50.0))
    with pytest.raises(DataError, match="沒有收盤"):
        group_inst_day(_inst(("1101", 1, 0, 0)), _closes(), _GROUPS, _DAY, _UNIVERSE)
    with pytest.raises(DataError, match="有法人買賣卻沒有收盤"):
        group_inst_day(_inst(("1102", 1, 0, 0)), closes, _GROUPS, _DAY, _UNIVERSE)
    with pytest.raises(DataError, match="出現兩次"):
        group_inst_day(
            _inst(("1101", 1, 0, 0), ("1101", 2, 0, 0)), closes, _GROUPS, _DAY, _UNIVERSE
        )
    # 法人沒有買賣（全 0）又沒有收盤的：不影響金額，不擋
    assert group_inst_day(_inst(("1102", 0, 0, 0)), closes, _GROUPS, _DAY, _UNIVERSE)[0].net == 0.0


def test_t358_ac5b_odd_lot_only_trades_use_the_last_close() -> None:
    """2026-10-14 實測：5906、1423、3085 法人只買賣零股／鉅額、當天沒有收盤 → 用最近一個收盤換算，不算當天成員。"""
    before = _DAY - dt.timedelta(days=1)
    inst = _inst(("1101", 100, 0, 0), ("1102", 56, 0, 0))
    closes = _closes(("1101", 50.0), ("1102", 20.0, before))
    a = group_inst_day(inst, closes, _GROUPS, _DAY, _UNIVERSE)[0]
    assert (a.group, a.members, a.foreign) == ("A", 1, 100 * 50 + 56 * 20)
    # 整個族群當天都沒有一般交易、但法人有買賣：照樣有一列，成員 0
    only = group_inst_day(
        _inst(("2201", 10, 0, 0)),
        _closes(("1101", 50.0), ("2201", 8.0, before)),
        _GROUPS,
        _DAY,
        _UNIVERSE,
    )
    assert [(d.group, d.members, d.net) for d in only] == [("A", 1, 0.0), ("B", 0, 80.0)]
    with pytest.raises(DataError, match="前視"):
        group_inst_day(
            inst,
            _closes(("1101", 50.0), ("1102", 20.0, _DAY + dt.timedelta(days=1))),
            _GROUPS,
            _DAY,
            _UNIVERSE,
        )


# ── x、y、軌跡 ─────────────────────────────────────────────────────────────

_CFG = GroupInstConfig(window=2, trail=3, close_lookback_days=30)
_S = [dt.date(2026, 10, 1) + dt.timedelta(days=i) for i in range(6)]


def _day(i: int, group: str, net: float) -> InstDay:
    return InstDay(_S[i], group, 3, net, 0.0, 0.0)


def _by(points: list[GroupInst]) -> dict[tuple[dt.date, str], GroupInst]:
    return {(p.day.date, p.day.group): p for p in points}


def test_t358_ac6_x_is_window_sum_and_y_is_acceleration() -> None:
    nets = [10.0, 20.0, 30.0, -40.0, 50.0, 60.0]
    days = [_day(i, "A", v) for i, v in enumerate(nets)]
    days += [_day(i, "B", 1.0) for i in range(6) if i != 4]  # B 在第 4 天沒有成員有收盤：那天 0
    got = _by(inst_series(days, _S, _CFG))
    a = [got[(s, "A")] for s in _S]
    assert [p.net5 for p in a] == [None, 30.0, 50.0, -10.0, 10.0, 110.0]
    assert [p.accel for p in a] == [
        None,
        None,
        None,
        (-10.0 - 30.0) / 2,
        (10.0 - 50.0) / 2,
        (110.0 + 10.0) / 2,
    ]
    assert a[5].foreign5 == 110.0 and a[5].trust5 == 0.0
    # 軌跡：最近 3 天裡 x、y 都算得出來的
    assert a[5].trail == ((_S[3], -10.0, -20.0), (_S[4], 10.0, -20.0), (_S[5], 110.0, 60.0))
    assert a[3].trail == ((_S[3], -10.0, -20.0),)
    assert (_S[4], "B") not in got and got[(_S[5], "B")].net5 == 1.0  # 第 4 天 B 記 0


def test_t358_ac7_a_day_not_computed_is_unknown_not_zero() -> None:
    days = [_day(i, "A", 10.0) for i in range(6) if i != 2]  # 第 2 天整天沒算到
    got = _by(inst_series(days, _S, _CFG))
    assert got[(_S[3], "A")].net5 is None  # 涵蓋第 2 天
    assert got[(_S[4], "A")].net5 == 20.0 and got[(_S[4], "A")].accel is None  # 前一段涵蓋第 2 天
    assert got[(_S[5], "A")].accel is None
    with pytest.raises(ValueError, match="舊到新"):
        inst_series(days, list(reversed(_S)), _CFG)
    with pytest.raises(ValueError, match="不在 sessions"):
        inst_series([InstDay(dt.date(2026, 9, 1), "A", 1, 0, 0, 0)], _S, _CFG)
    with pytest.raises(ValueError, match="重複"):
        inst_series([_day(0, "A", 1), _day(0, "A", 2)], _S, _CFG)


def test_t358_ac8_registered_window_and_pipeline_wiring() -> None:
    """設定（5 日、軌跡 5 天）、pipeline 排在族群資金之後、雲端不略過、修剪窗口有它且修剪前先建表（靜態）。"""
    from twquant.config import ServingConfig, load_config
    from twquant.data.stock_groups import StockGroupsConfig

    cfg = load_config(_ROOT / "configs" / "stock_groups.yaml", StockGroupsConfig).inst
    assert (cfg.window, cfg.trail, cfg.close_lookback_days) == (5, 5, 30)
    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert source.index('PipelineStep("族群資金"') < source.index('PipelineStep("族群法人"')
    scfg = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig)
    assert "族群法人" not in scfg.cloud.skip_pipeline_steps
    assert scfg.cloud.windows["group_inst_daily"] >= 2 * cfg.window + cfg.trail - 1
    prune_src = source[source.index("def serving_prune(") :]
    assert prune_src.index("ensure_tables(conn, (GROUP_INST_SPEC,)") < prune_src.index(
        "results = prune("
    )


# ── PG（無 DSN 則 skip）────────────────────────────────────────────────────

_SCHEMA = "serving_test_group_inst"


def _connect_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    from twquant.data.serving import schema as sch

    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{_SCHEMA}"')
    conn.commit()
    sch.ensure_tables(
        conn, (sch.spec_of("prices_daily"), sch.spec_of("institutional_daily")), _SCHEMA
    )
    with conn.cursor() as cur:
        for i, d in enumerate(_S[:3]):
            cur.execute(
                f'INSERT INTO "{_SCHEMA}".prices_daily (date, stock_id, open, high, low, close, '
                "volume, amount, available_at) VALUES (%s, '1101', 1, 1, 1, %s, 1, 1, now())",
                (d, 50.0 + i),
            )
        cur.execute(
            f'INSERT INTO "{_SCHEMA}".institutional_daily VALUES (%s, %s, %s, %s, %s, now())',
            (_S[2], "1101", 1000, -5, 0),
        )
    conn.commit()
    return conn


def test_t358_ac9_pg_reads_and_whole_day_overwrite() -> None:
    from twquant.data.serving.group_inst_store import (
        read_closes,
        read_inst_days,
        read_sessions,
        read_twse_inst,
        write_inst,
    )

    conn = _connect_or_skip()
    try:
        assert read_sessions(conn, _SCHEMA, _S[2], 2) == _S[1:3]
        assert read_closes(conn, _SCHEMA, _S[2], 30).to_dicts() == [
            {"stock_id": "1101", "close": 52.0, "date": _S[2]}
        ]
        assert read_closes(conn, _SCHEMA, _S[5], 30).row(0) == (
            "1101",
            52.0,
            _S[2],
        )  # 之後沒交易：最近一個
        assert read_closes(conn, _SCHEMA, _S[5], 2).is_empty()  # 超過往前找的天數
        assert read_twse_inst(conn, _SCHEMA, _S[2]).row(0) == ("1101", 1000, -5, 0)
        assert read_inst_days(conn, _SCHEMA, _S) == []  # 表還沒建：建好、回空
        days = [_day(i, g, 10.0 * (i + 1)) for i in range(4) for g in ("A", "B")]
        at = dt.datetime(2026, 10, 14, 17, 50, tzinfo=TZ)
        write_inst(conn, _SCHEMA, inst_series(days, _S[:4], _CFG), at)
        write_inst(conn, _SCHEMA, inst_series(days[:2], _S[:1], _CFG), at)  # 只覆蓋第 0 天
        back = sorted(read_inst_days(conn, _SCHEMA, _S), key=lambda d: (d.date, d.group))
        assert back == sorted(days, key=lambda d: (d.date, d.group))
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT net5, accel, trail::text FROM "{_SCHEMA}".group_inst_daily '
                "WHERE date = %s AND grp = 'A'",
                (_S[3],),
            )
            net5, accel, trail = cur.fetchone()
        assert (net5, accel) == (70.0, (70.0 - 30.0) / 2)
        assert json.loads(trail) == [{"d": "2026-10-04", "x": 70.0, "y": 20.0}]
        nan = inst_series([InstDay(_S[0], "A", 1, float("nan"), 0, 0)], _S[:1], _CFG)
        with pytest.raises(DataError, match="NaN"):
            write_inst(conn, _SCHEMA, nan, at)
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        conn.commit()
        conn.close()
