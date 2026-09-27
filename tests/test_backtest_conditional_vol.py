"""T-297／D-149：每日補位的條件式減碼（F1 持股觸發、F2 指數觸發）與反波動部位（F3）。

估計器本身在 tests/test_vol_estimator.py 釘死；這裡守的是引擎接線：
平常逐位元等於既有行為、觸發時走 T-286 同一條減碼路徑、逐日判斷進結果包、
還原價不把分割當報酬、歷史不足就 raise、F3 的格子倍數只降不升。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path

import polars as pl
import pytest

from tests.conftest import write_stock_meta
from twquant.backtest.costs import CostConfig
from twquant.backtest.engine import BacktestResult, run_backtest
from twquant.backtest.exposure import inverse_vol_sizes
from twquant.config import load_config
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError
from twquant.portfolio.config import VolConditional, VolEstimator, VolTargetConfig
from twquant.reporting.result_bundle import bundle_backtest
from twquant.strategy.config import (
    ConditionalVolConfig,
    ExposureTargetConfig,
    InverseVolSizingConfig,
    SlotsConfig,
    StrategyConfig,
)

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
_STOCKS = ("1101", "2330", "2454")
_CAPITAL = 30_000_000.0
_WARMUP = 40  # 回測起點前的歷史；測試用小窗（5＋20−1＝24 個報酬）算得出來
_SWITCH = 70  # 這一天起波動體制改變
_CALM, _WILD = 0.002, 0.05
_VT = VolTargetConfig(
    enabled=True,
    annual_vol=0.15,
    estimator=VolEstimator(window_days=5, method="ewma", halflife=2),
    conditional=VolConditional(percentile_window_days=20, reduce_above_pct=90.0),
    max_leverage=1.0,
)
Swing = Callable[[int, str], float]


def _sessions(n: int, start: dt.date = dt.date(2025, 1, 6)) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _at(d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ)


def _regime(i: int, _sid: str) -> float:
    return _WILD if i >= _SWITCH else _CALM


def _wh(
    tmp_path: Path,
    days: list[dt.date],
    swing: Swing,
    *,
    index_swing: Callable[[int], float] | None = None,
    split: tuple[str, int, float] | None = None,
    extra: dict[str, tuple[int, float]] | None = None,
) -> Warehouse:
    """價格每天在 100×(1±swing) 之間交替（三檔同相位、齊漲齊跌）。

    `split`＝(股票, 第幾天, adj_factor)：那天起價格乘 adj_factor，並寫一筆分割事件。
    `extra`＝{股票: (從第幾天開始有行情, swing)}：模擬晚上市的股票。
    corporate_events 一定有一筆遠在窗口之前的除息——估計器的還原步驟拒絕空的事件表。
    """
    rows = []
    for i, d in enumerate(days):
        for sid in _STOCKS:
            s = swing(i, sid)
            c = 100.0 * (1.0 + s if i % 2 else 1.0 - s)
            if split is not None and sid == split[0] and i >= split[1]:
                c *= split[2]
            rows.append(_row(d, sid, c))
        for sid, (first, s) in (extra or {}).items():
            if i >= first:
                rows.append(_row(d, sid, 100.0 * (1.0 + s if i % 2 else 1.0 - s)))
    px = pl.DataFrame(rows).with_columns(
        pl.col("volume").cast(pl.Int64),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", px, source="twse")
    write_stock_meta(wh, px)
    events = [("1101", days[0], "cash_dividend", 0.99)]
    if split is not None:
        sid, k, adj = split
        events.append((sid, days[k], "split", adj))
    wh.write(
        "clean",
        "corporate_events",
        pl.DataFrame(
            [
                {
                    "date": d,
                    "stock_id": sid,
                    "event_type": et,
                    "adj_factor": adj,
                    "ref_price": None,
                    "available_at": _at(d),
                }
                for sid, d, et, adj in events
            ],
            schema={
                "date": pl.Date,
                "stock_id": pl.String,
                "event_type": pl.String,
                "adj_factor": pl.Float64,
                "ref_price": pl.Float64,
                "available_at": schemas.TW_DT,
            },
        ),
        source="finmind",
    )
    if index_swing is not None:
        idx = pl.DataFrame(
            [
                {
                    "date": d,
                    "index_id": "TAIEX",
                    "open": None,
                    "high": None,
                    "low": None,
                    "close": 20000.0 * (1.0 + index_swing(i) if i % 2 else 1.0 - index_swing(i)),
                    "available_at": _at(d),
                }
                for i, d in enumerate(days)
            ]
        ).with_columns(
            pl.col("available_at").cast(schemas.TW_DT),
            pl.col("open").cast(pl.Float64),
            pl.col("high").cast(pl.Float64),
            pl.col("low").cast(pl.Float64),
        )
        wh.write("clean", "index_daily", idx, source="twse")
    return wh


def _row(d: dt.date, sid: str, c: float) -> dict[str, object]:
    return {
        "date": d,
        "stock_id": sid,
        "open": c,
        "high": c * 1.001,
        "low": c * 0.999,
        "close": c,
        "volume": 50_000_000,
        "amount": c * 50_000_000,
        "limit_up_price": None,
        "limit_down_price": None,
        "available_at": _at(d),
    }


def _cfg(
    *,
    conditional: ConditionalVolConfig | None = None,
    inverse: InverseVolSizingConfig | None = None,
    n: int = 3,
) -> StrategyConfig:
    slots: dict[str, object] = {"n": n, "entry_threshold": 0.0}
    if conditional is not None:
        slots["conditional_vol"] = conditional.model_dump()
    if inverse is not None:
        slots["inverse_vol"] = inverse.model_dump()
    return StrategyConfig.model_validate(
        {
            "name": "cond_vol_test",
            "description": "條件式減碼測試",
            "trial_scope": "test",
            "universe": {"markets": ["TWSE"], "min_adv_twd": None, "min_price": None},
            "signals": [{"factor": "volume.adv_20d", "weight": 1.0}],
            "slots": slots,
            "exit_rules": [{"kind": "time_stop", "sessions": 500}],
        }
    )


def _run(
    wh: Warehouse, days: list[dt.date], cfg: StrategyConfig, start: int = _WARMUP
) -> BacktestResult:
    return run_backtest(
        cfg,
        load_config(_CONFIG_DIR / "costs.yaml", CostConfig),
        days[start],
        days[-1],
        wh,
        initial_capital=_CAPITAL,
    )


def _holdings(trigger: str = "holdings", vt: VolTargetConfig = _VT) -> ConditionalVolConfig:
    return ConditionalVolConfig(
        trigger=trigger,  # type: ignore[arg-type]
        index_id="TAIEX" if trigger == "index" else None,
        vol_target=vt,
    )


def _equity(res: BacktestResult) -> list[float]:
    return [p.equity for p in res.equity]


def _exposure(res: BacktestResult) -> list[float]:
    return [p.positions_value / p.equity for p in res.equity if p.equity > 0]


def test_ac1_never_triggered_is_bit_identical_and_logs_every_night(tmp_path: Path) -> None:
    """目標波動高不可攀 → 每晚都 1 倍 → 與沒有 conditional_vol 的回測逐位元相同；
    但逐日判斷照樣記錄、照樣進結果包（對照組沒有這一欄）。"""
    days = _sessions(100)
    wh = _wh(tmp_path, days, _regime)
    base = _run(wh, days, _cfg())
    same = _run(
        wh, days, _cfg(conditional=_holdings(vt=_VT.model_copy(update={"annual_vol": 10.0})))
    )
    assert _equity(same) == _equity(base)
    assert len(same.ledger) == len(base.ledger)
    assert len(same.exposure) == len(days) - _WARMUP
    assert all(e.mult == 1.0 and not e.triggered for e in same.exposure)
    assert not base.exposure

    def _bundle(res: BacktestResult) -> dict[str, object]:
        return bundle_backtest(
            res,
            strategy=_cfg(),
            cost=load_config(_CONFIG_DIR / "costs.yaml", CostConfig),
            start=days[_WARMUP],
            end=days[-1],
            initial_capital=_CAPITAL,
            max_participation=0.05,
            source="test",
            data_fingerprint="test",
        )

    assert "exposure" not in _bundle(base)
    rows = _bundle(same)["exposure"]
    assert isinstance(rows, list)
    assert len(rows) == len(same.exposure) and rows[-1][0] == days[-1].isoformat()


def test_ac2_holdings_trigger_cuts_exposure_on_a_regime_switch(tmp_path: Path) -> None:
    """F1：前 70 天 ±0.2%、之後 ±5% → 切換後持股波動衝到自身 20 日窗的最高位且遠高於 15%
    → 觸發、倍數＝15% ÷ 持股波動、減碼賣出以 `exposure_target` 進 ledger；切換前與對照組相同。"""
    days = _sessions(100)
    wh = _wh(tmp_path, days, _regime)
    base = _run(wh, days, _cfg())
    cut = _run(wh, days, _cfg(conditional=_holdings("holdings")))
    switch = days[_SWITCH]
    before = [e for e in cut.exposure if e.date < switch]
    after = [e for e in cut.exposure if e.date >= switch]
    assert before and not any(e.triggered for e in before)
    hits = [e for e in after if e.triggered]
    assert hits, "體制切換後沒有任何一晚觸發"
    for e in hits:
        assert e.held_vol is not None and e.trigger_vol == e.held_vol  # F1：觸發序列就是持股
        assert e.mult == pytest.approx(min(0.15 / e.held_vol, 1.0))
        assert e.mult < 0.5
    first_hit = hits[0].date
    eq_base = {p.date: p.equity for p in base.equity}
    for p in cut.equity:  # 第一次觸發那天（含）為止逐位元相同：減碼隔天才執行
        if p.date <= first_hit:
            assert p.equity == eq_base[p.date]
    trims = [t for t in cut.ledger if t.reason == "exposure_target"]
    assert trims and all(t.side == "sell" for t in trims)
    tail = [p.positions_value / p.equity for p in cut.equity if p.date > first_hit][2:]
    assert tail and sum(tail) / len(tail) < 0.5


def test_ac3_index_trigger_looks_at_the_index_but_scales_by_holdings(tmp_path: Path) -> None:
    """F2：大盤平靜時，持股再怎麼波動都不觸發（與對照組逐位元相同）；
    大盤轉入高波動才觸發，而倍數用持股波動——觸發序列與倍數分母是兩條不同的序列。"""
    days = _sessions(100)
    calm_index = _wh(tmp_path / "calm", days, _regime, index_swing=lambda _i: 0.001)
    base = _run(calm_index, days, _cfg())
    same = _run(calm_index, days, _cfg(conditional=_holdings("index")))
    assert _equity(same) == _equity(base)
    assert not any(e.triggered for e in same.exposure)
    wild_index = _wh(
        tmp_path / "wild", days, _regime, index_swing=lambda i: 0.03 if i >= _SWITCH else 0.001
    )
    cut = _run(wild_index, days, _cfg(conditional=_holdings("index")))
    hits = [e for e in cut.exposure if e.triggered]
    assert hits and all(e.date >= days[_SWITCH] for e in hits)
    for e in hits:
        assert e.held_vol is not None and e.trigger_vol is not None
        assert e.trigger_vol != e.held_vol
        assert e.mult == pytest.approx(min(0.15 / e.held_vol, 1.0))
    assert [t for t in cut.ledger if t.reason == "exposure_target"]


def test_ac4_a_split_is_not_a_return(tmp_path: Path) -> None:
    """持股 1:4 分割（價格 ÷4）：還原後報酬≈0，估計器不得把它當成 −75% 而觸發減碼。
    引擎自己也處理分割（T-168），所以結果應與對照組逐位元相同。"""
    days = _sessions(100)
    wh = _wh(tmp_path, days, lambda _i, _s: _CALM, split=("2330", 60, 0.25))
    base = _run(wh, days, _cfg())
    same = _run(wh, days, _cfg(conditional=_holdings("holdings")))
    assert base.splits, "fixture 的分割沒被引擎看到"
    assert not any(e.triggered for e in same.exposure)
    assert _equity(same) == _equity(base)


def test_ac5_insufficient_history_raises_instead_of_defaulting_to_full_exposure(
    tmp_path: Path,
) -> None:
    """回測起點之前的交易日不夠算 24 個報酬 → DataError，而不是「照常 1 倍」。"""
    days = _sessions(100)
    wh = _wh(tmp_path, days, _regime)
    with pytest.raises(DataError):
        _run(wh, days, _cfg(conditional=_holdings("holdings")), start=10)


def test_ac6_inverse_vol_sizes_new_entries_and_never_scales_up(tmp_path: Path) -> None:
    """F3：三檔 σ 約 2%／8%／4%，σ_ref＝中位數 4% → 格子倍數 1.0／0.5（下限）／1.0。
    對照組三檔各一格（pv/3）；F3 的高波動那檔只買一半，低波動那檔不放大。"""
    days = _sessions(100)
    sw = {"1101": 0.01, "2330": 0.04, "2454": 0.02}
    wh = _wh(tmp_path, days, lambda _i, s: sw[s])
    base = _run(wh, days, _cfg())
    sized = _run(wh, days, _cfg(inverse=InverseVolSizingConfig(factor="price.vol_20d", floor=0.5)))
    first = days[_WARMUP + 1]

    def _notional(res: BacktestResult) -> dict[str, float]:
        return {
            t.stock_id: t.shares * t.price
            for t in res.ledger
            if t.side == "buy" and t.date == first
        }

    nb, ns = _notional(base), _notional(sized)
    assert set(nb) == set(ns) == set(_STOCKS)
    assert ns["1101"] == pytest.approx(nb["1101"], rel=0.02)  # σ 最低：只降不升 → 仍是一格
    assert ns["2454"] == pytest.approx(nb["2454"], rel=0.02)  # σ＝σ_ref → 一格
    assert ns["2330"] == pytest.approx(0.5 * nb["2330"], rel=0.05)  # σ 最高 → 下限 0.5
    for sid in _STOCKS:
        assert ns[sid] <= nb[sid] * 1.02
    logged = {e.stock_id: e.mult for e in sized.entry_sizes if e.date == first}
    assert logged == pytest.approx({"1101": 1.0, "2330": 0.5, "2454": 1.0}, abs=0.02)
    assert not base.entry_sizes


def test_ac7_candidate_without_sigma_gets_the_floor(tmp_path: Path) -> None:
    """D-149 §三 補充：新上市恰好第 20 個交易日（有 ADV、沒有 20 日波動）→ 以下限計、σ 記 None；
    σ_ref 只用有值的候選；前 n 名全都缺 σ 才 raise。"""
    days = _sessions(100)
    day = days[_WARMUP]
    wh = _wh(tmp_path, days, _regime, extra={"9999": (_WARMUP - 19, 0.02)})
    cfg = InverseVolSizingConfig(factor="price.vol_20d", floor=0.5)
    got = inverse_vol_sizes(wh.view(day), day, [*_STOCKS, "9999"], ["9999", "1101"], cfg)
    assert got["9999"].sigma is None and got["9999"].mult == 0.5
    assert got["1101"].sigma is not None and 0.5 <= got["1101"].mult <= 1.0
    with pytest.raises(DataError):
        inverse_vol_sizes(wh.view(day), day, ["9999"], ["9999"], cfg)


def test_ac8_config_validation() -> None:
    with pytest.raises(ValueError):
        ConditionalVolConfig(trigger="index", vol_target=_VT)  # 缺 index_id
    with pytest.raises(ValueError):
        ConditionalVolConfig(trigger="holdings", index_id="TAIEX", vol_target=_VT)
    with pytest.raises(ValueError):
        ConditionalVolConfig(
            trigger="holdings", vol_target=_VT.model_copy(update={"enabled": False})
        )
    with pytest.raises(ValueError):
        ConditionalVolConfig(
            trigger="holdings", vol_target=_VT.model_copy(update={"max_leverage": 1.5})
        )
    with pytest.raises(ValueError):
        ConditionalVolConfig(
            trigger="holdings",
            vol_target=_VT.model_copy(update={"estimator": VolEstimator(method="simple")}),
        )
    with pytest.raises(ValueError):
        SlotsConfig(
            n=3,
            entry_threshold=0.0,
            exposure_target=ExposureTargetConfig(target_vol=0.16, window=60, min_mult=0.1),
            conditional_vol=_holdings("holdings"),
        )
    with pytest.raises(ValueError):
        InverseVolSizingConfig(factor="price.vol_20d", floor=0.0)
    with pytest.raises(ValueError):
        InverseVolSizingConfig(factor="price.vol_20d", floor=1.5)
    assert SlotsConfig(n=3, entry_threshold=0.0, conditional_vol=_holdings("index")).conditional_vol
