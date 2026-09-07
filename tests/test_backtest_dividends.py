"""T-113／D-085：引擎收股利現金。

**缺陷**：`state.cash` 原本只有買賣結算三處，沒有任何收益路徑。除息當天引擎看到
價格跳空下跌、記為虧損，而股利現金從未入帳。而 `reporting/benchmarks.py` 的基準
走**含息**序列——於是 113 筆回測的每一個 CAGR 都在跟不同口徑的東西比。

實測影響（`scripts/dividend_drag.py`）：期間股利占初始權益 8.86%~24.38%，
CAGR 的含息下界比價格報酬高 +0.89~+3.36pp，而且**幅度不一致**，連排序都會變。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from tests.conftest import write_stock_meta
from twquant.backtest.costs import CostConfig
from twquant.backtest.engine import BacktestResult, run_backtest
from twquant.config import load_config
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.strategy.config import StrategyConfig

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
_STOCKS = ("1101", "2330", "2454")
_PRICE = 100.0


def _sessions(n: int, start: dt.date = dt.date(2025, 1, 6)) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _wh(
    tmp_path: Path,
    days: list[dt.date],
    events: list[dict[str, object]] | None = None,
    *,
    ex_date: dt.date | None = None,
    drop: float = 0.0,
) -> Warehouse:
    """三檔等價股票；`ex_date` 當天起價格下跌 `drop`（模擬除息跳空）。"""
    rows = []
    for d in days:
        for sid in _STOCKS:
            c = _PRICE * (1.0 - drop) if ex_date is not None and d >= ex_date else _PRICE
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 10_000_000,
                    "amount": c * 10_000_000,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ),
                }
            )
    px = pl.DataFrame(rows).with_columns(
        pl.col("volume").cast(pl.Int64),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", px, source="twse")
    write_stock_meta(wh, px)
    if events:
        ev = pl.DataFrame(
            events,
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "event_type": pl.String,
                "adj_factor": pl.Float64,
                "ref_price": pl.Float64,
                "available_at": schemas.TW_DT,
            },
        )
        wh.write("clean", "corporate_events", ev, source="finmind")
    return wh


def _event(day: dt.date, sid: str, adj: float, etype: str = "cash_dividend") -> dict[str, object]:
    return {
        "date": day,
        "stock_id": sid,
        "event_type": etype,
        "adj_factor": adj,
        "ref_price": None,
        "available_at": dt.datetime(day.year, day.month, day.day, 18, 0, tzinfo=TZ),
    }


def _cfg() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "div_test",
            "description": "股利入帳測試",
            "trial_scope": "test",
            "universe": {"markets": ["TWSE"], "min_adv_twd": None, "min_price": None},
            "signals": [{"factor": "price.mom_20d", "weight": 1.0}],
            "selection": {"method": "top_n", "top_n": 3},
            "weighting": "equal",
            "max_weight_per_stock": 0.4,
            "rebalance": {"schedule": "monthly", "day": "MON", "execution": "next_open"},
        }
    )


def _run(wh: Warehouse, days: list[dt.date]) -> BacktestResult:
    return run_backtest(
        _cfg(),
        load_config(_CONFIG_DIR / "costs.yaml", CostConfig),
        days[0],
        days[-1],
        wh,
        initial_capital=10_000_000.0,
    )


def test_ac1_cash_dividend_is_credited_to_cash(tmp_path: Path) -> None:
    """AC-1：除息日把現金入帳，且金額 = 持股數 × 前收 × (1 − adj_factor)。"""
    days = _sessions(60)
    ex = days[40]
    # 每股配 5 元（adj_factor = 0.95），價格同步跌 5%
    ev = [_event(ex, sid, 0.95) for sid in _STOCKS]
    with_div = _run(_wh(tmp_path / "a", days, ev, ex_date=ex, drop=0.05), days)
    without = _run(_wh(tmp_path / "b", days, None, ex_date=ex, drop=0.05), days)

    assert with_div.dividends, "應有股利明細"
    total = sum(d.cash for d in with_div.dividends)
    for d in with_div.dividends:
        assert d.per_share == pytest.approx(_PRICE * 0.05), "每股金額用**除息前**的收盤算"
        assert d.cash == pytest.approx(d.shares * d.per_share)
    assert total > 0
    # **在除息當日**，兩條權益曲線的差就是那筆現金——此時尚未再平衡，沒有別的變數。
    # 不比期末：多出來的現金會在下次再平衡被投入，於是產生手續費與不同的部位大小，
    # 兩條曲線之後就不只差股利了（實測期末差 494,700 vs 股利 495,000，少的 299 是成本）。
    eq_a = {p.date: p.equity for p in with_div.equity}
    eq_b = {p.date: p.equity for p in without.equity}
    assert eq_a[ex] - eq_b[ex] == pytest.approx(total, rel=1e-9)
    # 除息**前一日**兩者必須完全相同（在那之前沒有任何差異來源）
    prev = days[39]
    assert eq_a[prev] == pytest.approx(eq_b[prev], rel=1e-12)


def test_ac1_per_share_uses_the_pre_ex_close(tmp_path: Path) -> None:
    """AC-1：入帳必須發生在當日收盤價更新**之前**。

    用除息後的價格算，每股金額會少 5%——這種錯不會讓任何測試變紅，只會讓
    含息報酬一直偏低，所以要有一條測試直接盯住它。
    """
    days = _sessions(60)
    ex = days[40]
    r = _run(_wh(tmp_path, days, [_event(ex, "1101", 0.90)], ex_date=ex, drop=0.10), days)
    d = next(x for x in r.dividends if x.stock_id == "1101")
    assert d.per_share == pytest.approx(_PRICE * 0.10), "＝前收 100 × 0.10，不是除息後的 90 × 0.10"
    assert d.per_share != pytest.approx(_PRICE * 0.9 * 0.10)


def test_ac2_splits_are_not_treated_as_dividends(tmp_path: Path) -> None:
    """AC-2：**分割不得入帳**。

    1:4 分割的 `adj_factor` 是 0.25，套同一條公式會把七成五的部位價值當成現金
    發下去——憑空生錢。分割要改的是**股數**（T-168），本引擎明確不碰。
    """
    days = _sessions(60)
    ex = days[40]
    # 不模擬 75% 的價格跳空：那會觸發 D-029 的衛生濾網（max_jump_ratio=0.35）把宇宙清空，
    # 測到的就變成濾網而不是入帳規則。這裡只驗「split 事件不得產生現金」。
    r = _run(_wh(tmp_path, days, [_event(ex, s, 0.25, "split") for s in _STOCKS]), days)
    assert r.dividends == [], "split 不得產生任何入帳"


def test_ac2_reverse_split_and_bad_factors_are_skipped(tmp_path: Path) -> None:
    """AC-2：`adj_factor >= 1` 一律略過——那不是配發，照公式會算出**負的**股利。"""
    days = _sessions(60)
    ex = days[40]
    ev = [
        _event(ex, "1101", 4.0, "reverse_split"),
        _event(ex, "2330", 1.0, "cash_dividend"),
        _event(ex, "2454", 1.02, "stock_dividend"),
    ]
    r = _run(_wh(tmp_path, days, ev), days)
    assert r.dividends == []


def test_ac3_stock_dividend_is_converted_to_cash(tmp_path: Path) -> None:
    """AC-3：股票股利與權息**折算成現金**（D-085 選項 A）。

    真的配股數會產生非整股部位，撐破 `PaperBroker` 的 `tol_cash=100.0` 而擋住實盤；
    折現不是真實現金流，但總報酬正確。
    """
    days = _sessions(60)
    ex = days[40]
    for etype in ("stock_dividend", "cash_and_stock_dividend"):
        r = _run(
            _wh(tmp_path / etype, days, [_event(ex, "1101", 0.9, etype)], ex_date=ex, drop=0.1),
            days,
        )
        got = [d for d in r.dividends if d.stock_id == "1101"]
        assert got, f"{etype} 應折算成現金入帳"
        assert got[0].event_type == etype


def test_ac4_equity_identity_holds(tmp_path: Path) -> None:
    """AC-4：`equity == cash + positions_value` 這條恆等式不得被入帳破壞。"""
    days = _sessions(60)
    ex = days[40]
    r = _run(
        _wh(tmp_path, days, [_event(ex, s, 0.95) for s in _STOCKS], ex_date=ex, drop=0.05), days
    )
    for pt in r.equity:
        assert pt.equity == pytest.approx(pt.cash + pt.positions_value, rel=1e-12)


def test_ac5_holding_return_includes_dividends(tmp_path: Path) -> None:
    """AC-5：`Holding.ret` 要含股利。

    不含的話，除息會在持倉報表上顯示成虧損——那是價格跳空，不是賠錢。
    """
    days = _sessions(60)
    ex = days[40]
    ev = [_event(ex, s, 0.95) for s in _STOCKS]
    r = _run(_wh(tmp_path, days, ev, ex_date=ex, drop=0.05), days)
    assert r.final_holdings, "期末應有持倉"
    for h in r.final_holdings:
        assert h.dividends > 0, "本輪收到的股利要掛在持倉上"
        # 價格跌 5%、股利補回 5% → 含息報酬約 0
        assert h.ret == pytest.approx(0.0, abs=1e-6)
        assert h.last_close == pytest.approx(_PRICE * 0.95)


def test_ac6_no_corporate_events_table_means_no_dividends(tmp_path: Path) -> None:
    """AC-6：沒有 `corporate_events` 時退回「無股利」，不得炸掉。

    測試 fixture 與早期資料湖都可能沒有這張表；讓回測整個跑不動不是正確反應。
    """
    days = _sessions(60)
    r = _run(_wh(tmp_path, days), days)
    assert r.dividends == []
    assert r.final_equity > 0


def test_ac6_events_outside_the_window_are_ignored(tmp_path: Path) -> None:
    """AC-6：視窗外的除息日不得入帳（否則會憑空多一筆）。"""
    days = _sessions(60)
    outside = days[0] - dt.timedelta(days=30)
    r = _run(_wh(tmp_path, days, [_event(outside, "1101", 0.9)]), days)
    assert r.dividends == []


def test_ac7_dividends_only_for_stocks_actually_held(tmp_path: Path) -> None:
    """AC-7：沒持有就沒有股利——入帳依當下的 `state.holdings`，不是宇宙。"""
    days = _sessions(60)
    ex = days[3]  # 第一次再平衡成交之前，尚無持倉
    r = _run(_wh(tmp_path, days, [_event(ex, s, 0.9) for s in _STOCKS]), days)
    assert r.dividends == [], "還沒建倉就不該收到股利"


# ── D-086：--no-dividends 開關 ────────────────────────────────────────────


def _run_flag(wh: Warehouse, days: list[dt.date], *, credit: bool) -> BacktestResult:
    return run_backtest(
        _cfg(),
        load_config(_CONFIG_DIR / "costs.yaml", CostConfig),
        days[0],
        days[-1],
        wh,
        initial_capital=10_000_000.0,
        credit_dividends=credit,
    )


def test_no_dividends_flag_reverts_to_price_return(tmp_path: Path) -> None:
    """`credit_dividends=False` 回到價格報酬口徑，且結果記得住是哪一種。

    存在的理由是 D-086：含息與不含息兩次相減，才分離得出「股利本身」與
    「部位變大之後撞參與率上限」——後者實測占了 A 軌 47.3M 期末差裡的 13.6M。
    """
    days = _sessions(60)
    ex = days[40]
    wh = _wh(tmp_path, days, [_event(ex, s, 0.95) for s in _STOCKS], ex_date=ex, drop=0.05)
    on = _run_flag(wh, days, credit=True)
    off = _run_flag(wh, days, credit=False)

    assert on.dividends and not off.dividends
    assert on.credit_dividends is True and off.credit_dividends is False
    assert on.final_equity > off.final_equity, "含息必須高於價格報酬"

    # 除息**之前**兩者逐位元相同——開關不得影響其他任何路徑
    ea = {p.date: p.equity for p in on.equity}
    eb = {p.date: p.equity for p in off.equity}
    before = [d for d in ea if d < ex]
    assert before
    assert all(ea[d] == eb[d] for d in before), "除息日之前不得有任何差異"


def test_bundle_records_the_dividend_mode(tmp_path: Path) -> None:
    """結果包必須記下口徑。

    不記的話，含息與不含息的結果包長得一模一樣，誰也分不出手上這份是哪一種
    ——而那正是最容易造成靜默不一致的地方。
    """
    from twquant.reporting.result_bundle import bundle_backtest

    days = _sessions(60)
    ex = days[40]
    wh = _wh(tmp_path, days, [_event(ex, s, 0.95) for s in _STOCKS], ex_date=ex, drop=0.05)
    cost = load_config(_CONFIG_DIR / "costs.yaml", CostConfig)
    for credit in (True, False):
        res = _run_flag(wh, days, credit=credit)
        b = bundle_backtest(
            res,
            strategy=_cfg(),
            cost=cost,
            start=days[0],
            end=days[-1],
            initial_capital=10_000_000.0,
            max_participation=0.05,
            source="test",
            data_fingerprint="x",
        )
        assert b["credit_dividends"] is credit
        assert bool(b["dividends"]) is credit
