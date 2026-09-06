"""T-097：財報品質因子（GP/A、ROE、應計項目、資產成長、SUE、負債比）+ size 控制。

每條公式都有文獻出處（見 factors/quality.py），這裡把三個容易錯的地方鎖死：
1. **PIT**：財報以法定申報期限為可見日（Q3 → 11/14 22:00、年報 → 次年 3/31 22:00），
   在期限當天 20:00 截點仍不可見，隔天才可見。
2. **現金流量表是累計 YTD**：單季 = 本期 YTD − 上期 YTD（Q1 照抄）；不拆的話 accruals 差四倍。
3. **TTM 要四個連續季**：缺一季就 null，不得用三季充數。
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from pathlib import Path

import polars as pl
import pytest

import twquant.factors.builtins  # noqa: F401  # 註冊全部因子
from tests.conftest import write_stock_meta
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.factors import compute as fc
from twquant.factors.registry import get, list_factors
from twquant.factors.testing import assert_no_lookahead
from twquant.strategy.config import UniverseConfig

_STOCKS = ["1101", "2330"]
_NAMES = [
    "fundamental.gross_profitability",
    "fundamental.roe",
    "fundamental.accruals",
    "fundamental.asset_growth",
    "fundamental.sue",
    "fundamental.leverage",
    "fundamental.log_market_cap",
]
# 12 季：2021Q1 … 2023Q4（period_end）
_Q = [
    dt.date(y, m, d) for y in (2021, 2022, 2023) for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))
]
# 16 季（2021Q1 … 2024Q4）：SUE 要 13 季才有值，反前視檢查用這份才不會空轉
_Q16 = _Q + [dt.date(2024, m, d) for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
# 評估用交易日：2024-01 上旬（2023Q3 已於 2023-11-14 可見；2023Q4 要到 2024-03-31 22:00）
_DAYS = [dt.date(2024, 1, d) for d in (2, 3, 4, 5, 8, 9, 10)]
_CLOSE = {"1101": 40.0, "2330": 600.0}


def _at(d: dt.date, hour: int = 18) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, hour, tzinfo=TZ)


def _deadline(pe: dt.date) -> dt.datetime:
    m = {3: (0, 5, 15), 6: (0, 8, 14), 9: (0, 11, 14), 12: (1, 3, 31)}[pe.month]
    return dt.datetime(pe.year + m[0], m[1], m[2], 22, tzinfo=TZ)


# ── 合成財報：用簡單等差數列，讓手算可行 ────────────────────────────────
# k = 季序（0..11）。1101：GP=100+10k、NI=40+2k、資產=1000+50k、權益=500+20k、股本=1000（→100 股）
# 單季營業現金流 CFO=30+k（表內存 **YTD 累計**）。EPS 刻意非線性，讓 SUE 的分母不為 0。
_EPS = {
    "1101": [1.0, 1.1, 1.3, 1.2, 1.5, 1.4, 1.9, 1.6, 2.0, 1.7, 2.4, 2.1],
    "2330": [5.0, 5.2, 5.1, 5.4, 5.6, 5.3, 5.9, 5.8, 6.1, 6.0, 6.7, 6.2],
}


_EPS16 = {s: _EPS[s] + [_EPS[s][-1] + 0.3 * (i + 1) for i in range(4)] for s in _STOCKS}


def _gp(s: str, k: int) -> float:
    return (100.0 if s == "1101" else 1000.0) + 10.0 * k


def _ni(s: str, k: int) -> float:
    return (40.0 if s == "1101" else 400.0) + 2.0 * k


def _ta(s: str, k: int) -> float:
    return (1000.0 if s == "1101" else 20000.0) + 50.0 * k


def _eq(s: str, k: int) -> float:
    return (500.0 if s == "1101" else 12000.0) + 20.0 * k


def _cfo_q(s: str, k: int) -> float:
    return (30.0 if s == "1101" else 300.0) + 1.0 * k


def _shares_capital(s: str) -> float:
    return 1000.0 if s == "1101" else 25_000.0  # 股本（元）；面額 10 → 股數 = /10


def _statements(
    quarters: list[dt.date] = _Q, eps: dict[str, list[float]] | None = None
) -> pl.DataFrame:
    eps = eps or _EPS
    rows: list[dict[str, object]] = []
    for s in _STOCKS:
        ytd = 0.0
        for k, pe in enumerate(quarters):
            if pe.month == 3:
                ytd = 0.0
            ytd += _cfo_q(s, k)
            at = _deadline(pe)
            items = [
                ("income", "GrossProfit", _gp(s, k)),
                ("income", "EquityAttributableToOwnersOfParent", _ni(s, k)),
                ("income", "EPS", eps[s][k]),
                ("balance", "TotalAssets", _ta(s, k)),
                ("balance", "Liabilities", _ta(s, k) - _eq(s, k)),
                ("balance", "EquityAttributableToOwnersOfParent", _eq(s, k)),
                ("balance", "OrdinaryShare", _shares_capital(s)),
                ("cashflow", "CashFlowsFromOperatingActivities", ytd),  # 累計 YTD
            ]
            for st, typ, v in items:
                rows.append(
                    {
                        "date": pe,
                        "stock_id": s,
                        "statement": st,
                        "type": typ,
                        "value": v,
                        "available_at": at,
                        "available_at_estimated": True,
                    }
                )
    return pl.DataFrame(rows).with_columns(pl.col("available_at").cast(schemas.TW_DT))


def _prices(days: list[dt.date]) -> pl.DataFrame:
    rows = [
        {
            "date": d,
            "stock_id": s,
            "open": _CLOSE[s],
            "high": _CLOSE[s],
            "low": _CLOSE[s],
            "close": _CLOSE[s],
            "volume": 1000,
            "amount": _CLOSE[s] * 1000,
            "limit_up_price": None,
            "limit_down_price": None,
            "available_at": _at(d),
        }
        for d in days
        for s in _STOCKS
    ]
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )


def _wh(
    tmp_path: Path,
    days: list[dt.date] = _DAYS,
    st: pl.DataFrame | None = None,
    *,
    meta: bool = True,
) -> Warehouse:
    """`meta=False` 刻意不寫 `stock_meta`，供 T-143 的「缺表要 raise」測試用。"""
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(days), source="twse")
    if meta:
        write_stock_meta(wh, _prices(days))
    wh.write(
        "clean", "financial_statements", st if st is not None else _statements(), source="finmind"
    )
    return wh


def _vals(wh: Warehouse, name: str, day: dt.date) -> dict[str, float]:
    out = fc.compute([name], wh.view(day), dates=[day])
    return {r["stock_id"]: r["value"] for r in out.to_dicts()}


# 2024-01 可見的最新季 = 2023Q3 → k=10；四季前 k=6；TTM = k 7..10
_K, _K4 = 10, 6


def test_all_seven_registered_with_literature_directions() -> None:
    names = list_factors()
    for n in _NAMES:
        assert n in names, f"{n} 未註冊"
    assert get("fundamental.gross_profitability").direction == 1  # Novy-Marx 2013
    assert get("fundamental.roe").direction == 1  # FF 2015 RMW
    assert get("fundamental.accruals").direction == -1  # Sloan 1996
    assert get("fundamental.asset_growth").direction == -1  # Cooper–Gulen–Schill 2008
    assert get("fundamental.sue").direction == 1  # Bernard–Thomas 1989
    assert get("fundamental.leverage").direction == -1  # QMJ safety
    assert get("fundamental.log_market_cap").direction == -1  # size


def test_gross_profitability_is_ttm_gp_over_latest_assets(tmp_path: Path) -> None:
    got = _vals(_wh(tmp_path), "fundamental.gross_profitability", _DAYS[-1])
    ttm = sum(_gp("1101", k) for k in range(_K - 3, _K + 1))
    assert got["1101"] == pytest.approx(ttm / _ta("1101", _K))


def test_roe_uses_average_of_latest_and_four_quarters_ago_equity(tmp_path: Path) -> None:
    got = _vals(_wh(tmp_path), "fundamental.roe", _DAYS[-1])
    ttm = sum(_ni("2330", k) for k in range(_K - 3, _K + 1))
    avg_eq = (_eq("2330", _K) + _eq("2330", _K4)) / 2
    assert got["2330"] == pytest.approx(ttm / avg_eq)


def test_accruals_decumulates_ytd_cashflow(tmp_path: Path) -> None:
    """若把 YTD 直接當單季，TTM CFO 會被高估數倍、accruals 變成大負值——此測試鎖死拆季。"""
    got = _vals(_wh(tmp_path), "fundamental.accruals", _DAYS[-1])
    ttm_ni = sum(_ni("1101", k) for k in range(_K - 3, _K + 1))
    ttm_cfo = sum(_cfo_q("1101", k) for k in range(_K - 3, _K + 1))
    avg_ta = (_ta("1101", _K) + _ta("1101", _K4)) / 2
    assert got["1101"] == pytest.approx((ttm_ni - ttm_cfo) / avg_ta)


def test_asset_growth_is_yoy_of_total_assets(tmp_path: Path) -> None:
    got = _vals(_wh(tmp_path), "fundamental.asset_growth", _DAYS[-1])
    assert got["2330"] == pytest.approx(_ta("2330", _K) / _ta("2330", _K4) - 1.0)


def test_sue_requires_eight_prior_yoy_diffs_else_null(tmp_path: Path) -> None:
    """12 季資料下，最新季 k=10 的前 8 個同比差需要 j=2..9 → j<4 無法計算 → null。
    延長到 16 季後才有值，且等於手算。"""
    q16 = _Q + [dt.date(2024, m, d) for m, d in ((3, 31), (6, 30), (9, 30), (12, 31))]
    eps16 = {s: _EPS[s] + [_EPS[s][-1] + 0.3 * (i + 1) for i in range(4)] for s in _STOCKS}

    days = [dt.date(2025, 4, d) for d in (1, 2, 3)]  # 2024Q4 於 2025-03-31 22:00 可見 → k=15
    got = _vals(_wh(tmp_path, days, _statements(q16, eps16)), "fundamental.sue", days[-1])
    e = eps16["1101"]

    def diff(j: int) -> float:
        return e[j] - e[j - 4]

    cur = diff(15)
    prior = [diff(j) for j in range(7, 15)]  # 前 8 個：j=7..14
    assert got["1101"] == pytest.approx(cur / statistics.stdev(prior))
    # 12 季版本：k=10 時前 8 個需要 j=2..9，j<4 不存在 → null
    assert "1101" not in _vals(_wh(tmp_path / "short"), "fundamental.sue", _DAYS[-1])


def test_leverage_is_liabilities_over_assets(tmp_path: Path) -> None:
    got = _vals(_wh(tmp_path), "fundamental.leverage", _DAYS[-1])
    assert got["1101"] == pytest.approx((_ta("1101", _K) - _eq("1101", _K)) / _ta("1101", _K))


def test_log_market_cap_uses_asof_close_and_par_value_shares(tmp_path: Path) -> None:
    """市值 = 當日收盤 × (股本 ÷ 面額 10)。股本來自最新可見的資產負債表，收盤來自當日。"""
    got = _vals(_wh(tmp_path), "fundamental.log_market_cap", _DAYS[-1])
    assert got["2330"] == pytest.approx(math.log(600.0 * (25_000.0 / 10.0)))


def test_pit_annual_report_visible_only_after_deadline(tmp_path: Path) -> None:
    """2023Q4（年報）期限 2024-03-31 22:00：3/31 的 20:00 截點看不到，4/1 看得到。"""
    days = [dt.date(2024, 3, 29), dt.date(2024, 4, 1), dt.date(2024, 4, 2)]
    wh = _wh(tmp_path, days)
    before = _vals(wh, "fundamental.leverage", dt.date(2024, 3, 29))  # 最新可見 = 2023Q3（k=10）
    after = _vals(wh, "fundamental.leverage", dt.date(2024, 4, 1))  # 最新可見 = 2023Q4（k=11）
    k10 = (_ta("1101", 10) - _eq("1101", 10)) / _ta("1101", 10)
    k11 = (_ta("1101", 11) - _eq("1101", 11)) / _ta("1101", 11)
    assert before["1101"] == pytest.approx(k10) and after["1101"] == pytest.approx(k11)


def test_ttm_requires_four_consecutive_quarters(tmp_path: Path) -> None:
    """缺一季（把 2023Q1 拿掉）→ 2024-01 的 TTM 因子必須是 null，不得用三季充數。"""
    st = _statements().filter(pl.col("date") != dt.date(2023, 3, 31))
    wh = _wh(tmp_path, _DAYS, st)
    assert _vals(wh, "fundamental.gross_profitability", _DAYS[-1]) == {}
    assert _vals(wh, "fundamental.accruals", _DAYS[-1]) == {}
    # 時點值因子不受影響
    assert set(_vals(wh, "fundamental.leverage", _DAYS[-1])) == set(_STOCKS)


@pytest.mark.parametrize("name", _NAMES)
def test_no_lookahead(tmp_path: Path, name: str) -> None:
    """反前視必須是**非空轉**的：12 季夾具下 SUE 全 null，兩邊都空也會通過。
    故用 16 季夾具，並先斷言該因子在切點真的有值。"""
    days = [dt.date(2025, 3, 28), dt.date(2025, 3, 31), dt.date(2025, 4, 1), dt.date(2025, 4, 2)]
    wh = _wh(tmp_path, days, _statements(_Q16, _EPS16))
    assert _vals(wh, name, days[-1]), f"{name} 在切點無值——反前視檢查會空轉"
    assert_no_lookahead(name, wh, days[1:])


# ── 合併數回退（FinMind 9.1% 股票季不給「歸屬母公司」列）─────────────────────


def _swap_parent_to_consolidated(
    st: pl.DataFrame, *, nci: float = 0.0, stocks: tuple[str, ...] = ("1101",)
) -> pl.DataFrame:
    """把指定股票的「歸屬母公司」列換成合併數（＋少數股權），模擬金融業／無少數股權公司的格式。"""
    parent = pl.col("type") == "EquityAttributableToOwnersOfParent"
    mine = pl.col("stock_id").is_in(stocks)
    keep = st.filter(~(parent & mine))
    swapped = st.filter(parent & mine).with_columns(
        type=pl.when(pl.col("statement") == "income")
        .then(pl.lit("IncomeAfterTaxes"))
        .otherwise(pl.lit("Equity")),
        value=pl.col("value") + nci,
    )
    extra = (
        swapped.with_columns(type=pl.lit("NoncontrollingInterests"), value=pl.lit(nci))
        if nci
        else swapped.clear()
    )
    return pl.concat([keep, swapped, extra])


def test_consolidated_fallback_when_parent_line_missing(tmp_path: Path) -> None:
    """只有合併數、沒有少數股權列時，合併數就是母公司數 → ROE 與有母公司列時完全相同。"""
    base = _vals(_wh(tmp_path / "a"), "fundamental.roe", _DAYS[-1])
    st = _swap_parent_to_consolidated(_statements())
    got = _vals(_wh(tmp_path / "b", _DAYS, st), "fundamental.roe", _DAYS[-1])
    assert got["1101"] == pytest.approx(base["1101"])
    assert got["2330"] == pytest.approx(base["2330"])  # 2330 未換，不受影響


def test_fallback_subtracts_noncontrolling_interests(tmp_path: Path) -> None:
    """有少數股權列時，母公司數 = 合併數 − 少數股權；直接用合併數會高估。"""
    base = _vals(_wh(tmp_path / "a"), "fundamental.roe", _DAYS[-1])
    st = _swap_parent_to_consolidated(_statements(), nci=7.0)
    got = _vals(_wh(tmp_path / "b", _DAYS, st), "fundamental.roe", _DAYS[-1])
    assert got["1101"] == pytest.approx(base["1101"])


def test_parent_line_wins_over_consolidated_fallback(tmp_path: Path) -> None:
    """母公司列存在時，回退不得覆蓋它——加一條數值不同的合併數列，結果必須不變。"""
    base = _vals(_wh(tmp_path / "a"), "fundamental.roe", _DAYS[-1])
    st = _statements()
    bogus = st.filter(
        (pl.col("type") == "EquityAttributableToOwnersOfParent") & (pl.col("statement") == "income")
    ).with_columns(type=pl.lit("IncomeAfterTaxes"), value=pl.col("value") * 3.0)
    got = _vals(_wh(tmp_path / "b", _DAYS, pl.concat([st, bogus])), "fundamental.roe", _DAYS[-1])
    assert got["1101"] == pytest.approx(base["1101"])


# ── T-119／D-059：宇宙層的品質硬濾網 ────────────────────────────────
#
# 測試放在本檔而非 test_strategy.py，是因為財報 fixture（`_statements`／`_wh`）在這裡；
# 受測對象是 `strategy.universe.universe_ids`。


def test_ac1_min_roe_filters_universe_by_quality(tmp_path: Path) -> None:
    """`min_roe` 在**排序之前**把低 ROE 的股票移出池子（D-059 的機轉）。"""
    from twquant.strategy.config import UniverseConfig
    from twquant.strategy.universe import universe_ids

    wh = _wh(tmp_path)
    day = _DAYS[-1]
    roe = _vals(wh, "fundamental.roe", day)
    assert len(roe) == 2, f"fixture 應有兩檔 ROE，得到 {roe}"
    lo_id, hi_id = sorted(roe, key=lambda s: roe[s])
    mid = (roe[lo_id] + roe[hi_id]) / 2

    base = UniverseConfig(
        min_adv_twd=None, min_price=None, max_jump_ratio=None, max_gap_sessions=None
    )
    assert set(universe_ids(wh.view(day), day, base)) == {lo_id, hi_id}
    kept = universe_ids(wh.view(day), day, base.model_copy(update={"min_roe": mid}))
    assert kept == [hi_id], f"門檻 {mid:.4f} 應只留 {hi_id}（ROE {roe}）"


def test_ac1_min_roe_none_is_bit_identical(tmp_path: Path) -> None:
    """未啟用時輸出必須與加此功能之前逐位元相同——既有 12 個策略配置都沒有這個欄位。"""
    from twquant.strategy.config import UniverseConfig
    from twquant.strategy.universe import universe_ids

    wh = _wh(tmp_path)
    day = _DAYS[-1]
    cfg = UniverseConfig(min_adv_twd=None, min_price=None)
    assert cfg.min_roe is None
    assert universe_ids(wh.view(day), day, cfg) == universe_ids(
        wh.view(day), day, cfg.model_copy(update={"min_roe": None})
    )


def test_ac1_null_roe_is_excluded_not_kept(tmp_path: Path) -> None:
    """D-059 事前寫定：ROE 為 null 一律排除。

    「不知道它是不是品質股」不等於「它是品質股」。把缺值當成通過，等於讓沒有財報的
    新股與非標準公司整批繞過濾網——那是靜默放行（黃金規則 5 的精神）。
    """
    from twquant.strategy.config import UniverseConfig
    from twquant.strategy.universe import universe_ids

    # 只給 1101 財報；2330 無財報 → ROE null
    st = _statements().filter(pl.col("stock_id") == "1101")
    wh = _wh(tmp_path, st=st)
    day = _DAYS[-1]
    base = UniverseConfig(
        min_adv_twd=None, min_price=None, max_jump_ratio=None, max_gap_sessions=None
    )
    assert set(universe_ids(wh.view(day), day, base)) == {"1101", "2330"}
    kept = universe_ids(wh.view(day), day, base.model_copy(update={"min_roe": -1e9}))
    assert kept == ["1101"], "ROE 為 null 的 2330 不得因門檻極低而被放行"


def test_ac1_min_roe_respects_pit_cutoff(tmp_path: Path) -> None:
    """濾網只能看訊號日**當下可得**的財報——與因子走同一條 PitView，不得繞過。

    以年報法定申報期限為界：期限當日 20:00 的 as_of 看不到，隔日看得到（同 test_ac_*_pit）。
    """
    from twquant.strategy.config import UniverseConfig
    from twquant.strategy.universe import universe_ids

    wh = _wh(tmp_path)
    base = UniverseConfig(
        min_adv_twd=None, min_price=None, max_jump_ratio=None, max_gap_sessions=None
    )
    day = _DAYS[-1]
    roe = _vals(wh, "fundamental.roe", day)
    hi = max(roe, key=lambda s: roe[s])
    # 門檻設在兩檔之間 → 只留高的那檔；濾網若讀到未來財報，留下的集合會改變
    mid = sum(roe.values()) / 2
    assert universe_ids(wh.view(day), day, base.model_copy(update={"min_roe": mid})) == [hi]


# ── T-121：宇宙層市值排名濾網 ──────────────────────────────────────
#
# 為什麼用「排名」而非絕對市值：T-120 已指出絕對門檻在長歷史上會漂移
# （3,000 萬 TWD 在 2005 與 2026 是完全不同的門檻）。排名依定義不漂移。
#
# 排名的母體是**當日有市值的四碼普通股全體**，不是流動性濾網之後的殘存集合。
# 這是刻意的：「市值前 100 大」是市場事實，不該隨我們把 min_adv_twd 調高調低而變動，
# 否則 T-120 的門檻漂移問題會經由排名滲進來。


def _uni_base() -> UniverseConfig:
    return UniverseConfig(
        min_adv_twd=None, min_price=None, max_jump_ratio=None, max_gap_sessions=None
    )


def test_ac1_mktcap_rank_max_keeps_only_the_largest(tmp_path: Path) -> None:
    """`mktcap_rank_max=1` 只留市值最大的一檔（2330 市值 1.5e6 vs 1101 的 4e3）。"""
    from twquant.strategy.universe import universe_ids

    wh = _wh(tmp_path)
    day = _DAYS[-1]
    base = _uni_base()
    assert set(universe_ids(wh.view(day), day, base)) == {"1101", "2330"}
    assert universe_ids(wh.view(day), day, base.model_copy(update={"mktcap_rank_max": 1})) == [
        "2330"
    ]


def test_ac1_mktcap_rank_min_excludes_the_largest(tmp_path: Path) -> None:
    """`mktcap_rank_min=2` 排除市值前 1 大——A 軌小型股宇宙就是靠這個成立。"""
    from twquant.strategy.universe import universe_ids

    wh = _wh(tmp_path)
    day = _DAYS[-1]
    assert universe_ids(
        wh.view(day), day, _uni_base().model_copy(update={"mktcap_rank_min": 2})
    ) == ["1101"]


def test_ac1_mktcap_rank_band_can_be_combined(tmp_path: Path) -> None:
    """兩端同時給定時取交集；空集合是合法結果（由 build_targets 那層 raise，不在此處）。"""
    from twquant.strategy.universe import universe_ids

    wh = _wh(tmp_path)
    day = _DAYS[-1]
    both = _uni_base().model_copy(update={"mktcap_rank_min": 1, "mktcap_rank_max": 2})
    assert universe_ids(wh.view(day), day, both) == ["1101", "2330"]
    empty = _uni_base().model_copy(update={"mktcap_rank_min": 2, "mktcap_rank_max": 1})
    assert universe_ids(wh.view(day), day, empty) == []


def test_ac1_mktcap_none_is_bit_identical(tmp_path: Path) -> None:
    """未啟用時輸出必須與加此功能之前逐位元相同——既有 15 個策略配置都沒有這兩個欄位。"""
    from twquant.strategy.config import UniverseConfig
    from twquant.strategy.universe import universe_ids

    wh = _wh(tmp_path)
    day = _DAYS[-1]
    cfg = UniverseConfig(min_adv_twd=None, min_price=None)
    assert cfg.mktcap_rank_max is None and cfg.mktcap_rank_min is None
    assert universe_ids(wh.view(day), day, cfg) == universe_ids(
        wh.view(day), day, cfg.model_copy(update={"mktcap_rank_max": None, "mktcap_rank_min": None})
    )


def test_ac1_null_mktcap_is_excluded_not_kept(tmp_path: Path) -> None:
    """市值為 null 一律排除，比照 `min_roe`（D-059）。

    「不知道它多大」不等於「它符合規模條件」。把缺值當成通過，等於讓沒有財報的
    新股整批繞過濾網（黃金規則 5 的精神）。
    """
    from twquant.strategy.universe import universe_ids

    # 只給 1101 財報 → 2330 無股本 → 市值 null
    wh = _wh(tmp_path, st=_statements().filter(pl.col("stock_id") == "1101"))
    day = _DAYS[-1]
    base = _uni_base()
    assert set(universe_ids(wh.view(day), day, base)) == {"1101", "2330"}
    # 門檻寬到不可能擋掉任何有值的股票，2330 仍須因 null 被排除
    kept = universe_ids(wh.view(day), day, base.model_copy(update={"mktcap_rank_max": 999}))
    assert kept == ["1101"], "市值為 null 的 2330 不得因門檻寬鬆而被放行"


def test_ac2_mktcap_gate_has_no_lookahead(tmp_path: Path) -> None:
    """f(data[:t]) 必須等於 f(data)[:t]：只給到 t 的資料湖，與完整資料湖，在 t 的宇宙相同。

    `assert_no_lookahead` 的簽章吃因子函數，套不到宇宙濾網上，故在此直接檢驗同一性質。
    """
    from twquant.strategy.universe import universe_ids

    day = _DAYS[3]
    full = _wh(tmp_path / "full")
    upto = _wh(tmp_path / "upto", days=[d for d in _DAYS if d <= day])
    cfg = _uni_base().model_copy(update={"mktcap_rank_max": 1})
    assert universe_ids(full.view(day), day, cfg) == universe_ids(upto.view(day), day, cfg)


def test_ac2_mktcap_gate_respects_pit_cutoff(tmp_path: Path) -> None:
    """濾網只能看訊號日當下可得的財報——與因子走同一條 PitView，不得繞過。

    股本改成隨季別遞增，若濾網讀到尚未公布的年報（2023Q4，2024-03-31 22:00 才可見），
    1101 的市值會跳到超過 2330，排名互換 → 留下的集合會改變。
    """
    from twquant.strategy.universe import universe_ids

    st = _statements().with_columns(
        value=pl.when(
            (pl.col("type") == "OrdinaryShare") & (pl.col("date") == dt.date(2023, 12, 31))
        )
        .then(pl.lit(1e9))  # 未來才可見的巨額股本
        .otherwise(pl.col("value"))
    )
    wh = _wh(tmp_path, st=st)
    day = _DAYS[-1]  # 2024-01-10，2023Q4 年報尚不可見
    cfg = _uni_base().model_copy(update={"mktcap_rank_max": 1})
    assert universe_ids(wh.view(day), day, cfg) == ["2330"], "讀到未來股本會讓 1101 變成最大"


# ── T-143：`markets` 濾網做成真的（先前是 no-op）────────────────────
#
# 現在擋住興櫃的只是「`prices_daily` 剛好沒有它們」——一個靠巧合成立的防線。
# D-065 發現 T-137 差點就把 387 檔興櫃、316,389 列補進去，那會讓它們無聲進入宇宙。
#
# null 的處置（D-069）：實測回測窗（2022-07 起）內只有 1 檔無市場別（3718，
# FinMind 查得到 tpex，已補），6 檔真下市股最後行情都在 2020 年、不在任何回測窗內。
# 故「null 一律排除」不會造成存活者偏誤——但這是**量出來的**，不是假設的，
# 所以 `test_ac1_markets_null_is_excluded` 之外另有一條實測守門的驗收（見 TASKS）。


def _meta(rows: list[tuple[str, str]]) -> pl.DataFrame:
    """(stock_id, market) → stock_meta 最小骨架。"""
    return pl.DataFrame(
        {
            "stock_id": [r[0] for r in rows],
            "name": [None] * len(rows),
            "market": [r[1] for r in rows],
            "industry": [None] * len(rows),
            "list_date": [None] * len(rows),
            "delist_date": [None] * len(rows),
        },
        schema={
            "stock_id": pl.String,
            "name": pl.String,
            "market": pl.String,
            "industry": pl.String,
            "list_date": pl.Date,
            "delist_date": pl.Date,
        },
    )


def _wh_with_meta(tmp_path: Path, meta: pl.DataFrame) -> Warehouse:
    wh = _wh(tmp_path)
    wh.write("clean", "stock_meta", meta, source="twse")
    return wh


def test_ac1_markets_filters_by_stock_meta(tmp_path: Path) -> None:
    """`markets: [TWSE]` 只留上市股——先前這個旗標是 no-op，兩檔都會留下。"""
    from twquant.strategy.universe import universe_ids

    wh = _wh_with_meta(tmp_path, _meta([("1101", "TWSE"), ("2330", "TPEX")]))
    day = _DAYS[-1]
    base = _uni_base()
    assert set(universe_ids(wh.view(day), day, base)) == {"1101", "2330"}
    assert universe_ids(wh.view(day), day, base.model_copy(update={"markets": ["TWSE"]})) == [
        "1101"
    ]


def test_ac1_markets_null_is_excluded(tmp_path: Path) -> None:
    """市場別為 null 一律排除，比照 `min_roe` 與市值篩。

    「不知道它在哪個市場」不等於「它在允許的市場」。放行等於讓沒有 meta 的股票
    整批繞過濾網——而興櫃正是「有價格、沒 meta」的那一類（D-065）。
    """
    from twquant.strategy.universe import universe_ids

    wh = _wh_with_meta(tmp_path, _meta([("1101", "TWSE")]))  # 2330 無 meta
    day = _DAYS[-1]
    kept = universe_ids(
        wh.view(day), day, _uni_base().model_copy(update={"markets": ["TWSE", "TPEX"]})
    )
    assert kept == ["1101"], "無 stock_meta 的 2330 不得因市場別缺值而被放行"


def test_ac1_markets_empty_list_disables_the_filter(tmp_path: Path) -> None:
    """空清單＝不過濾。既有 15 個配置都寫 `[TWSE, TPEX]`，這條是給診斷用的逃生門。"""
    from twquant.strategy.universe import universe_ids

    wh = _wh_with_meta(tmp_path, _meta([("1101", "TWSE")]))
    day = _DAYS[-1]
    kept = universe_ids(wh.view(day), day, _uni_base().model_copy(update={"markets": []}))
    assert set(kept) == {"1101", "2330"}, "空清單時不得過濾，含無 meta 者"


def test_ac1_markets_without_stock_meta_table_raises(tmp_path: Path) -> None:
    """沒有 stock_meta 卻要求市場別過濾 → raise，不得靜默放行全部。

    靜默放行會讓濾網在缺表時「成功地什麼都沒過濾」——正是黃金規則 5 要防的。
    """
    from twquant.errors import DataError
    from twquant.strategy.universe import universe_ids

    wh = _wh(tmp_path, meta=False)  # 刻意不寫 stock_meta
    day = _DAYS[-1]
    with pytest.raises(DataError, match="stock_meta"):
        universe_ids(wh.view(day), day, _uni_base().model_copy(update={"markets": ["TWSE"]}))
