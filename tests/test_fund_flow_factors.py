"""SPEC-016 T-338：資金流因子——反前視（AC-12）、手算（AC-13）、缺持股／差分未知不得為 0（AC-14）、決定性（AC-15）。

手造資料湖：交易日 D1..D8；股票 2330／2317／1101；ETF 0050（收盤 10）、00981A（收盤 20）。
持股（基準日 D1）：0050＝2330 60%、2317 40%；00981A＝2330 50%、1101 40%。
單位數 21:00 才公布 → 因子把日期前移到下一個交易日（D-008）。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.fund_flow  # noqa: F401  # 匯入即註冊（刻意不在 builtins）
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors.registry import get
from twquant.factors.testing import assert_no_lookahead

D = [dt.date(2026, 1, d) for d in (5, 6, 7, 8, 9, 12, 13, 14)]
STOCKS = {"2330": 100.0, "2317": 50.0, "1101": 40.0}
ETFS = {"0050": 10.0, "00981A": 20.0}
VOL = 1_000_000
# 單位數差分（units 從 1,000,000 起累加）；00981A 在 D[4] 是分割窗 → null
DELTA = {
    "0050": [None, 100, -50, 0, 30, 0, 0, 0],
    "00981A": [None, 10, 0, 0, None, 5, 0, 0],
}
TRUST = {"2330": 20, "2317": -10, "1101": 3}  # 每天相同（股）


def _at(d: dt.date, h: int) -> dt.datetime:
    return dt.datetime.combine(d, dt.time(h), TZ)


def _build(tmp_path: Path, *, with_holdings: bool = True) -> Warehouse:
    wh = Warehouse(tmp_path)
    px_rows = []
    for d in D:
        for sid, c in {**STOCKS, **ETFS}.items():
            px_rows.append((d, sid, c, c, c, c, VOL, c * VOL, None, None, _at(d, 18)))
    for d in D:
        wh.write(
            "clean",
            "prices_daily",
            pl.DataFrame(
                [r for r in px_rows if r[0] == d],
                schema={
                    "date": pl.Date,
                    "stock_id": pl.String,
                    "open": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "close": pl.Float64,
                    "volume": pl.Int64,
                    "amount": pl.Float64,
                    "limit_up_price": pl.Float64,
                    "limit_down_price": pl.Float64,
                    "available_at": schemas.TW_DT,
                },
                orient="row",
            ),
        )
        units = []
        for etf, deltas in DELTA.items():
            k = D.index(d)
            units.append(
                (
                    d,
                    etf,
                    1_000_000 + sum(x or 0 for x in deltas[: k + 1]),
                    deltas[k],
                    None,
                    _at(d, 21),
                )
            )
        wh.write(
            "clean",
            "etf_units_daily",
            pl.DataFrame(
                units,
                schema={
                    "date": pl.Date,
                    "etf_id": pl.String,
                    "units": pl.Int64,
                    "units_delta": pl.Int64,
                    "nav": pl.Float64,
                    "available_at": schemas.TW_DT,
                },
                orient="row",
            ),
        )
        wh.write(
            "clean",
            "institutional_daily",
            pl.DataFrame(
                [(d, s, 0, t, 0, _at(d, 18)) for s, t in TRUST.items()],
                schema={
                    "date": pl.Date,
                    "stock_id": pl.String,
                    "foreign_net": pl.Int64,
                    "trust_net": pl.Int64,
                    "dealer_net": pl.Int64,
                    "available_at": schemas.TW_DT,
                },
                orient="row",
            ),
        )
    # with_holdings=False：表存在，但只有一檔不在單位數裡的 ETF（0056）→ 追蹤中的 ETF 都沒有已知持股
    hold = (
        [
            ("0050", "2330", 0.6),
            ("0050", "2317", 0.4),
            ("00981A", "2330", 0.5),
            ("00981A", "1101", 0.4),
        ]
        if with_holdings
        else [("0056", "2330", 0.5)]
    )
    if hold:
        wh.write(
            "clean",
            "etf_holdings_daily",
            pl.DataFrame(
                [(D[0], e, s, 1000, w, _at(D[0], 18)) for e, s, w in hold],
                schema={
                    "date": pl.Date,
                    "etf_id": pl.String,
                    "stock_id": pl.String,
                    "shares": pl.Int64,
                    "weight": pl.Float64,
                    "available_at": schemas.TW_DT,
                },
                orient="row",
            ),
        )
    return wh


def _run(wh: Warehouse, name: str, as_of: dt.date, **params: object) -> pl.DataFrame:
    fdef = get(name)
    assert fdef.fn is not None
    out = fdef.fn(wh.view(as_of), {**fdef.params, **params})
    assert isinstance(out, pl.DataFrame)
    return out


def _val(df: pl.DataFrame, d: dt.date, sid: str) -> float | None:
    hit = df.filter((pl.col("date") == d) & (pl.col("stock_id") == sid))
    return hit["value"][0] if hit.height else None


_NAMES = [
    "chip.etf_flow_pressure_5d",
    "chip.trust_disc_net_10d",
    "chip.trust_disc_streak",
    "market.etf_flow_total_5d",
    "market.etf_holdings_coverage",
]


@pytest.mark.parametrize("name", _NAMES)
def test_ac12_no_lookahead(tmp_path: Path, name: str) -> None:
    wh = _build(tmp_path)
    assert_no_lookahead(name, wh, [D[3], D[5], D[7]])


def test_ac13_hand_computed_flow_and_disc(tmp_path: Path) -> None:
    wh = _build(tmp_path)
    # D[1] 的流量：0050 +100×10＝1000 → 2330 600、2317 400；00981A +10×20＝200 → 2330 100、1101 80
    # 21:00 公布 → 標在 D[2]
    p = _run(wh, "chip.etf_flow_pressure_5d", D[7], window=1, adv_window=1)
    assert _val(p, D[2], "2330") == pytest.approx(700 / (100 * VOL))
    assert _val(p, D[2], "2317") == pytest.approx(400 / (50 * VOL))
    assert _val(p, D[2], "1101") == pytest.approx(80 / (40 * VOL))
    # D[2] 的流量：0050 −50×10＝−500 → 2330 −300、2317 −200；00981A 0 → 標在 D[3]
    assert _val(p, D[3], "2330") == pytest.approx(-300 / (100 * VOL))
    # 投信裁量＝投信買賣超 − 流量÷收盤（股）：2330 在 D[1]＝20 − 700/100＝13 → 標在 D[2]
    q = _run(wh, "chip.trust_disc_net_10d", D[7], window=1)
    assert _val(q, D[2], "2330") == pytest.approx(13 / VOL)
    assert _val(q, D[2], "2317") == pytest.approx((-10 - 400 / 50) / VOL)
    # 2 日窗：D[1]、D[2] → 2330：13 + (20 + 3)＝36
    q2 = _run(wh, "chip.trust_disc_net_10d", D[7], window=2)
    assert _val(q2, D[3], "2330") == pytest.approx(36 / (2 * VOL))
    # 大盤：D[1] 全體申贖 1000 + 200 ＝ 1200；普通股成交金額 (100+50+40)×VOL → 標在 D[2]
    m = _run(wh, "market.etf_flow_total_5d", D[7], window=1)
    assert _val(m, D[2], "2330") == pytest.approx(1200 / (190 * VOL))


def test_ac14_unknown_delta_is_null_not_zero(tmp_path: Path) -> None:
    wh = _build(tmp_path)
    p = _run(wh, "chip.etf_flow_pressure_5d", D[7], window=1, adv_window=1)
    # 00981A 在 D[4] 差分未知（分割窗）→ 它持有的 2330、1101 在 D[5] 無值；只被 0050 持有的 2317 有值
    assert _val(p, D[5], "2330") is None and _val(p, D[5], "1101") is None
    assert _val(p, D[5], "2317") == pytest.approx(30 * 10 * 0.4 / (50 * VOL))
    # 5 日窗內含未知那天 → 整個窗無值
    p5 = _run(wh, "chip.etf_flow_pressure_5d", D[7], window=5, adv_window=1)
    assert _val(p5, D[7], "2330") is None
    m = _run(wh, "market.etf_flow_total_5d", D[7], window=1)
    assert _val(m, D[5], "2330") is None


def test_ac14_no_holdings_means_not_decomposed(tmp_path: Path) -> None:
    wh = _build(tmp_path, with_holdings=False)
    p = _run(wh, "chip.etf_flow_pressure_5d", D[7], window=1, adv_window=1)
    assert p["value"].to_list() and set(p["value"].to_list()) == {0.0}  # 沒有已知持股 → 結構性零
    cov = _run(wh, "market.etf_holdings_coverage", D[7])
    assert set(cov["value"].to_list()) == {0.0}  # 覆蓋率 0＝完全沒拆（is_decomposed=false）
    q = _run(wh, "chip.trust_disc_net_10d", D[7], window=1)
    assert _val(q, D[2], "2330") == pytest.approx(20 / VOL)  # 沒拆 → 等於投信買賣超
    cov2 = _run(_build(tmp_path / "h"), "market.etf_holdings_coverage", D[7])
    assert _val(cov2, D[2], "2330") == pytest.approx(1.0)


def test_ac14_stale_holdings_drop_out(tmp_path: Path) -> None:
    wh = _build(tmp_path)
    p = _run(wh, "chip.etf_flow_pressure_5d", D[7], window=1, adv_window=1, max_stale=2)
    # 持股基準日 D[0]；D[3] 的流量離基準日 3 個交易日 > 2 → 不算已知持股 → 0
    assert _val(p, D[4], "2330") == 0.0
    cov = _run(wh, "market.etf_holdings_coverage", D[7], max_stale=2)
    assert _val(cov, D[4], "2330") == 0.0


def test_ac14_streak_breaks_on_sell_and_unknown(tmp_path: Path) -> None:
    wh = _build(tmp_path)
    s = _run(wh, "chip.trust_disc_streak", D[7])
    # 2317：每天投信 −10 → 裁量 < 0 → 連買 0
    assert _val(s, D[2], "2317") == 0.0
    # 2330：D[0] 還沒有「基準日 < 當日」的持股 → 流量結構性零、裁量＝20 > 0 → D[1] 標 1；
    # D[1] 裁量 13 → D[2] 標 2；D[2] 裁量 20 → D[3] 標 3；D[4] 未知 → D[5] 無值
    assert _val(s, D[1], "2330") == 1.0
    assert _val(s, D[2], "2330") == 2.0 and _val(s, D[3], "2330") == 3.0
    assert _val(s, D[5], "2330") is None
    assert _val(s, D[6], "2330") == 1.0  # 未知之後重新起算


@pytest.mark.parametrize("name", _NAMES)
def test_ac15_deterministic(tmp_path: Path, name: str) -> None:
    wh = _build(tmp_path)
    a = _run(wh, name, D[7])
    b = _run(wh, name, D[7])
    assert a.equals(b)


def test_ac15_not_in_builtins() -> None:
    """雲端沒有 ETF 三張表：這些因子不得進每日物化的 builtins（D-170）。"""
    import ast

    src = Path(__file__).resolve().parent.parent / "src" / "twquant" / "factors" / "builtins.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    mods = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not any("fund_flow" in m for m in mods)
