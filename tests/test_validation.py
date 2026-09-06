"""T-019：驗證工具（DSR、bootstrap、walk-forward、param plateau）。"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401
from tests.conftest import write_stock_meta
from twquant.backtest import validation as v
from twquant.backtest.costs import CostConfig
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.strategy.config import SignalConfig, StrategyConfig

_CC = CostConfig()


# ── DSR ─────────────────────────────────────────────────────────────
def test_expected_max_sharpe_scales_with_trials() -> None:
    trials = [0.0, 0.01, -0.01, 0.02, -0.02, 0.015]
    emax = v.expected_max_sharpe(trials)
    assert emax > 0  # 多重測試下期望最大 SR 為正
    assert v.expected_max_sharpe([0.5]) == pytest.approx(0.5)  # 單試驗退化


def test_deflated_sharpe_monotone() -> None:
    trials = [0.0, 0.01, -0.01, 0.02, -0.02, 0.015]
    high = v.deflated_sharpe(0.30, trials, n_obs=250)
    low = v.deflated_sharpe(0.03, trials, n_obs=250)
    assert 0.0 <= low <= high <= 1.0
    assert high > 0.9  # 觀察 SR 遠高於期望最大 → 高信心
    assert v.deannualize_sharpe(1.0 * (252**0.5)) == pytest.approx(1.0)


# ── Bootstrap ───────────────────────────────────────────────────────
def test_bootstrap_ci_reproducible_and_ordered() -> None:
    rets = [0.001 * ((i % 7) - 3) for i in range(120)]  # 有正有負
    lo1, hi1 = v.bootstrap_sharpe_ci(rets, n=500, seed=42)
    lo2, hi2 = v.bootstrap_sharpe_ci(rets, n=500, seed=42)
    assert (lo1, hi1) == (lo2, hi2)  # 同 seed 決定性
    assert lo1 <= hi1


# ── walk-forward / plateau（合成資料）───────────────────────────────
def _wh(tmp_path: Path) -> Warehouse:
    base = dt.date(2026, 5, 1)
    slopes = {"1101": 1.0, "2330": 2.0, "2454": 0.5}
    rows = []
    for i in range(40):
        d = base + dt.timedelta(days=i)
        for sid, slope in slopes.items():
            c = 100.0 + slope * i
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 100_000,
                    "amount": 1e9,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
                }
            )
    df = pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df, source="twse")
    write_stock_meta(wh, df)
    return wh


def _cfg() -> StrategyConfig:
    return StrategyConfig.model_validate(
        {
            "name": "mom_v",
            "signals": [SignalConfig(factor="price.mom_20d", weight=1.0, params={"window": 10})],
            "selection": {"method": "top_n", "top_n": 2},
            "weighting": "equal",
            "max_weight_per_stock": 0.6,
            "universe": {"min_adv_twd": None, "min_price": None},
            "rebalance": {"schedule": "daily"},
            "overlays": [],
        }
    )


def test_walk_forward_windows_disjoint_and_contiguous(tmp_path: Path) -> None:
    wh = _wh(tmp_path)
    wf = v.walk_forward(_cfg(), _CC, wh, dt.date(2026, 5, 20), dt.date(2026, 6, 9), test_days=5)
    assert len(wf.windows) >= 2
    for a, b in zip(wf.windows, wf.windows[1:], strict=False):
        assert a.test_end < b.test_start  # AC-9：互斥、無重疊
    assert all(w.result.metrics.n_days >= 2 for w in wf.windows)


def test_param_plateau_grid(tmp_path: Path) -> None:
    wh = _wh(tmp_path)
    cells = v.param_plateau(
        _cfg(),
        _CC,
        wh,
        dt.date(2026, 5, 20),
        dt.date(2026, 6, 9),
        signal_index=0,
        param_grid=[5, 10],
    )
    assert [c.params["window"] for c in cells] == [5, 10]
    assert all(isinstance(c.sharpe, float) for c in cells)


# ── T-127／D-061 R1：配對比較的區塊標準誤 ──────────────────────────
#
# 為什麼不能用日頻 t 值：持股期 20 個交易日，日報酬高度自相關，
# 把每日配對差當獨立樣本會嚴重高估顯著性——那正是「看起來有效」的主要來源。


def test_ac12_block_se_is_larger_than_the_naive_daily_se() -> None:
    """自相關的序列上，區塊標準誤必須大於天真的日頻標準誤。

    構造一個「月內同號、月間變號」的序列：日頻看來變異極小（幾乎恆為 ±d），
    但真正的不確定性在月與月之間。日頻 SE 會低估，區塊 SE 不會。
    """
    import datetime as dt

    from twquant.backtest.validation import paired_block_stats

    days: list[dt.date] = []
    diffs: list[float] = []
    for m in range(1, 13):
        sign = 1.0 if m % 2 else -1.0
        for d in range(1, 21):
            days.append(dt.date(2025, m, d))
            diffs.append(sign * 0.01)
    st = paired_block_stats(days, diffs)
    naive = (sum((x - st.mean) ** 2 for x in diffs) / (len(diffs) - 1)) ** 0.5 / len(diffs) ** 0.5
    assert st.block_se is not None
    # 這個構造下比值是可手算的：每區塊 20 日、12 個區塊，
    # block_se/naive = sqrt(20) × sqrt(12/11) × sqrt(239/240) ≈ 4.66。
    # 釘死數值而非只斷言「比較大」——後者無法分辨「區塊化生效」與「算錯但剛好較大」。
    expected = (20.0 * 12.0 / 11.0 * 239.0 / 240.0) ** 0.5
    assert st.block_se / naive == pytest.approx(expected, rel=1e-9), (
        f"區塊 SE {st.block_se:.6f} / 日頻 SE {naive:.6f} = {st.block_se / naive:.4f}，"
        f"預期 {expected:.4f}"
    )
    assert st.block_se > naive * 4, "區塊化必須放大不確定性"
    assert st.n_blocks == 12


def test_ac12_block_se_zero_when_every_block_identical() -> None:
    """所有月份的均值相同 → 月間無變異 → 區塊 SE 為 0（而非 NaN 或例外）。"""
    import datetime as dt

    from twquant.backtest.validation import paired_block_stats

    days = [dt.date(2025, m, d) for m in range(1, 5) for d in range(1, 11)]
    st = paired_block_stats(days, [0.002] * len(days))
    assert st.mean == pytest.approx(0.002)
    assert st.block_se == pytest.approx(0.0)


def test_ac12_single_block_cannot_estimate_uncertainty() -> None:
    """只有一個月時無從估計月間變異——回傳 None 而不是假裝 SE 是 0。

    回傳 0 會讓下游把它讀成「極度顯著」，那是最糟的失敗模式（黃金規則 5 的精神）。
    """
    import datetime as dt

    from twquant.backtest.validation import paired_block_stats

    days = [dt.date(2025, 1, d) for d in range(1, 21)]
    st = paired_block_stats(days, [0.001] * 20)
    assert st.n_blocks == 1
    assert st.block_se is None
    assert st.t_stat is None


def test_ac12_length_mismatch_raises() -> None:
    import datetime as dt

    from twquant.backtest.validation import paired_block_stats

    with pytest.raises(ValueError, match="長度"):
        paired_block_stats([dt.date(2025, 1, 2)], [0.1, 0.2])


def test_ac12_indistinguishable_when_t_below_two() -> None:
    """「未能區分」是合法結果（SPEC-013 §3.7），必須由函數本身表態而非留給讀者猜。"""
    import datetime as dt
    import random

    from twquant.backtest.validation import paired_block_stats

    rnd = random.Random(20260905)
    days = [dt.date(2025, m, d) for m in range(1, 13) for d in range(1, 21)]
    st = paired_block_stats(days, [rnd.gauss(0.0, 0.01) for _ in days])
    assert st.t_stat is not None
    assert abs(st.t_stat) < 2.0
    assert st.indistinguishable is True
