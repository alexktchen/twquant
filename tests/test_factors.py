"""T-012 / SPEC-002：因子框架（註冊表、compute、標準化、反前視護欄）。"""

from __future__ import annotations

import datetime as dt
import random
from pathlib import Path
from typing import cast

import polars as pl
import pytest

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import PitView, Warehouse
from twquant.errors import TwquantError
from twquant.factors import compute as fc
from twquant.factors import price as _price  # noqa: F401  # 觸發 mom_20d 註冊
from twquant.factors import registry
from twquant.factors.registry import factor, get, list_factors
from twquant.factors.standardize import standardize
from twquant.factors.testing import assert_no_lookahead


def _synthetic_prices(days: int, stocks: dict[str, float]) -> pl.DataFrame:
    """days 個交易日、每檔股價從基準每日 +1 遞增。日期用 2026-06 起連續平日近似。"""
    base = dt.date(2026, 6, 1)
    rows = []
    for i in range(days):
        d = base + dt.timedelta(days=i)
        at = dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ)
        for sid, p0 in stocks.items():
            c = p0 + i
            rows.append(
                {
                    "date": d,
                    "stock_id": sid,
                    "open": c,
                    "high": c,
                    "low": c,
                    "close": c,
                    "volume": 1000,
                    "amount": c * 1000.0,
                    "limit_up_price": None,
                    "limit_down_price": None,
                    "available_at": at,
                }
            )
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )


def _warehouse(tmp_path: Path, days: int = 25) -> Warehouse:
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _synthetic_prices(days, {"0001": 100.0, "0002": 50.0}))
    return wh


# ── 註冊表 ──────────────────────────────────────────────────────


def test_ac1_mom20d_registered() -> None:
    assert "price.mom_20d" in list_factors()
    assert "price.mom_20d" in list_factors(category="price")
    fdef = get("price.mom_20d")
    assert fdef.direction == 1 and fdef.anchor_invariant is True


def test_ac1_duplicate_register_raises() -> None:
    with pytest.raises(TwquantError, match="已註冊"):

        @factor(name="price.mom_20d", category="price", tables=["prices_daily"], lookback_days=25)
        def _dup(view: PitView, params: dict[str, object]) -> pl.DataFrame:
            return pl.DataFrame()


def test_bad_name_format_raises() -> None:
    with pytest.raises(TwquantError, match="格式不符"):
        registry.register(registry.FactorDef("price.Bad-Name", "price", 1, ("prices_daily",), 5))


def test_category_mismatch_raises() -> None:
    with pytest.raises(TwquantError, match="前綴"):
        registry.register(registry.FactorDef("chip.x", "price", 1, ("prices_daily",), 5))


# ── compute ─────────────────────────────────────────────────────


def test_compute_mom20d_values(tmp_path: Path) -> None:
    wh = _warehouse(tmp_path, days=25)
    out = fc.compute(["price.mom_20d"], wh.view(dt.date(2026, 6, 25)))
    assert out.columns == ["date", "stock_id", "factor", "value"]
    # 第 21 天(index 20, 2026-06-21) 0001：close=120, 20 日前 close=100 → 0.2
    row = out.filter(
        (pl.col("stock_id") == "0001") & (pl.col("date") == dt.date(2026, 6, 21))
    ).to_dicts()[0]
    assert row["value"] == pytest.approx(120 / 100 - 1)
    assert row["factor"] == "price.mom_20d"


def test_compute_dates_filter(tmp_path: Path) -> None:
    wh = _warehouse(tmp_path, days=25)
    d = dt.date(2026, 6, 25)
    out = fc.compute(["price.mom_20d"], wh.view(d), dates=[d])
    assert set(out["date"].to_list()) == {d}


def test_ac8_compute_params_override_registry_defaults(tmp_path: Path) -> None:
    wh = _warehouse(tmp_path, days=25)
    seen: list[dict[str, object]] = []

    @factor(
        name="price.params_probe",
        category="price",
        tables=["prices_daily"],
        lookback_days=0,
        params={"window": 20, "keep": "default"},
    )
    def _probe(view: PitView, params: dict[str, object]) -> pl.DataFrame:
        seen.append(params)
        window = params["window"]
        assert isinstance(window, (int, float))
        return pl.DataFrame(
            {
                "date": [dt.date(2026, 6, 25)],
                "stock_id": ["0001"],
                "value": [float(window)],
            }
        )

    out = fc.compute(
        ["price.params_probe"],
        wh.view(dt.date(2026, 6, 25)),
        params={"price.params_probe": {"window": 5}},
    )
    assert out["value"].to_list() == [5.0]
    assert seen == [{"window": 5, "keep": "default"}]


# ── 標準化（AC-4）─────────────────────────────────────────────────


def test_ac4_standardize_zero_mean_unit_std() -> None:
    df = pl.DataFrame(
        {
            "date": [dt.date(2026, 6, 30)] * 5,
            "stock_id": ["a", "b", "c", "d", "e"],
            "factor": ["price.x"] * 5,
            "value": [1.0, 2.0, 3.0, 4.0, 100.0],  # 含極端值
        }
    )
    std = standardize(df)
    m = std.select(pl.col("value").mean()).item()
    s = std.select(pl.col("value").std()).item()
    mx = std.select(pl.col("value").max()).item()
    assert abs(m) < 1e-9
    assert abs(s - 1.0) < 1e-6
    # 極端值被 winsorize 拉回，不再是離群的巨值
    assert mx < 3.0


def test_ac4_standardize_nan_one_stock_does_not_nuke_group() -> None:
    """D-020 回歸防線：某 (date,factor) 群內單一 NaN 不得汙染同群其他股票的 z，
    也不得影響另一群。退化股自身維持非有限，供下游以 is_finite 過濾。"""
    d = dt.date(2026, 6, 30)
    df = pl.DataFrame(
        {
            "date": [d] * 8,
            "stock_id": ["a", "b", "c", "bad", "a", "b", "c", "d"],
            "factor": ["price.x"] * 4 + ["price.y"] * 4,
            "value": [1.0, 2.0, 3.0, float("nan"), 10.0, 20.0, 30.0, 40.0],
        }
    )
    std = standardize(df)

    # 含 NaN 的 price.x 群：其他三檔仍得有限 z，且橫斷面 mean≈0 / std≈1（NaN 不參與估計）
    x_good = std.filter((pl.col("factor") == "price.x") & (pl.col("stock_id") != "bad"))
    assert x_good.select(pl.col("value").is_finite().all()).item()
    assert abs(x_good.select(pl.col("value").mean()).item()) < 1e-9
    assert abs(x_good.select(pl.col("value").std()).item() - 1.0) < 1e-6

    # 退化股自身維持 NaN（≠ 自己），下游 filter(is_finite) 才能剔除它而非整因子消失
    bad_z = std.filter(pl.col("stock_id") == "bad").select("value").item()
    assert bad_z != bad_z

    # 另一群 price.y 完全不受鄰群 NaN 影響
    y = std.filter(pl.col("factor") == "price.y")
    assert y.select(pl.col("value").is_finite().all()).item()
    assert abs(y.select(pl.col("value").mean()).item()) < 1e-9
    assert abs(y.select(pl.col("value").std()).item() - 1.0) < 1e-6


# ── 反前視護欄（AC-3）────────────────────────────────────────────


def test_ac3_mom20d_no_lookahead(tmp_path: Path) -> None:
    wh = _warehouse(tmp_path, days=25)
    assert_no_lookahead(
        "price.mom_20d",
        wh,
        [dt.date(2026, 6, 22), dt.date(2026, 6, 24), dt.date(2026, 6, 25)],
    )


@factor(
    name="price.bad_lookahead",
    category="price",
    tables=["prices_daily"],
    lookback_days=2,
    params={},
)
def _bad_peek(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """故意偷看未來（shift(-1) = 次日收盤）的壞因子，供護欄反向測試。"""
    prices = (
        view.table("prices_daily").select("date", "stock_id", "close").sort(["stock_id", "date"])
    )
    nxt = pl.col("close").shift(-1).over("stock_id", order_by="date")
    return (
        prices.with_columns((nxt / pl.col("close") - 1.0).alias("value"))
        .select("date", "stock_id", "value")
        .drop_nulls("value")
        .collect()
    )


def test_ac3_guard_catches_lookahead(tmp_path: Path) -> None:
    wh = _warehouse(tmp_path, days=10)
    with pytest.raises(AssertionError, match="前視"):
        assert_no_lookahead("price.bad_lookahead", wh, [dt.date(2026, 6, 5), dt.date(2026, 6, 8)])


# ── T-107／T-108（D-051／D-052 建議甲）：MAD = 0 的橫斷面被壓成常數 ──────────


def _sparse_frame(day: dt.date, zeros: int, tail: list[float]) -> pl.DataFrame:
    """稀疏因子的橫斷面：`zeros` 檔同為 0（→ MAD = 0），其餘幾檔有值。"""
    vals = [0.0] * zeros + tail
    return pl.DataFrame(
        {
            "date": [day] * len(vals),
            "stock_id": [f"{i:04d}" for i in range(len(vals))],
            "factor": ["chip.trust_streak"] * len(vals),
            "value": vals,
        }
    )


def test_ac1_sparse_factor_is_not_collapsed_to_a_constant() -> None:
    """AC-1（T-107）：過半同值時，標準化不得把整個橫斷面壓成常數。

    重現的缺陷：`winsorize_mad` 在 MAD = 0 時上下界同為 median，`clip` 使橫斷面變常數，
    z 全為 0。實測 2025-06-30 的 `chip.trust_streak` 有 16 個相異原始值、**1 個相異 z**
    ——因子被標準化關掉了，而沒有任何一關報錯（黃金規則 5 的同型失效）。
    """
    from twquant.factors.standardize import winsorize_mad, zscore

    df = _sparse_frame(dt.date(2024, 1, 2), zeros=14, tail=[1.0, 2.0, 3.0, 7.0])
    broken = zscore(winsorize_mad(df))
    assert broken["value"].n_unique() == 1, "舊路徑確實把橫斷面壓成常數（缺陷重現）"

    fixed = standardize(df)
    assert fixed["value"].n_unique() == 5, "同分取平均名次 → 4 個非零值＋1 組零值"
    # 單調：原始值的順序必須被保留（Spearman IC 不變是這個修法的立論基礎）
    pairs = sorted(zip(df["value"].to_list(), fixed["value"].to_list(), strict=True))
    assert [b for _, b in pairs] == sorted(b for _, b in pairs)


def test_ac2_dense_factor_is_bit_identical_after_the_fix() -> None:
    """AC-2（T-108）：MAD > 0 的橫斷面走與先前**逐位元相同**的路徑。

    這是修法可以直接上線的前提——既有模型與回測的數字不得被順手改掉。
    """
    from twquant.factors.standardize import winsorize_mad, zscore

    rng = random.Random(20260906)
    day = dt.date(2024, 1, 2)
    dense = pl.DataFrame(
        {
            "date": [day] * 200,
            "stock_id": [f"{i:04d}" for i in range(200)],
            "factor": ["price.mom_20d"] * 200,
            "value": [rng.gauss(0.0, 1.0) for _ in range(200)],
        }
    )
    assert standardize(dense)["value"].to_list() == zscore(winsorize_mad(dense))["value"].to_list()

    # 同一批裡同時有稀疏與密集因子時，路由必須逐 (date, factor) 各走各的
    mixed = pl.concat([dense, _sparse_frame(day, zeros=14, tail=[1.0, 2.0, 3.0, 7.0])])
    out = standardize(mixed)
    assert (
        out.filter(pl.col("factor") == "price.mom_20d")["value"].to_list()
        == zscore(winsorize_mad(dense))["value"].to_list()
    )
    assert out.filter(pl.col("factor") == "chip.trust_streak")["value"].n_unique() == 5


def test_ac3_rank_path_is_invariant_to_the_magnitude_of_outliers() -> None:
    """AC-3：排名路徑只看**順序**，不看數值大小——這正是它勝過 D-051 option B 的地方。

    option B（MAD = 0 時跳過截尾）把原始尺度直接餵給 z-score，於是一檔離群值
    有多離群就會改變當日**每一個** z（實測 |z| 中位約 10、p99 17~25）。
    排名轉換下，把 1e6 換成 8.0 之後每一個 z 逐位元不變。

    另驗標準化的契約仍成立（均值 0、標準差 1），以及無資訊的同分大群靠近 0。
    """
    day = dt.date(2024, 1, 2)
    huge = standardize(_sparse_frame(day, zeros=180, tail=[1.0, 5.0, 900.0, 1e6]))["value"]
    mild = standardize(_sparse_frame(day, zeros=180, tail=[1.0, 5.0, 6.0, 8.0]))["value"]
    assert huge.to_list() == mild.to_list(), "同一組順序必須得到同一組 z"

    assert huge.mean() == pytest.approx(0.0, abs=1e-12)
    assert huge.std() == pytest.approx(1.0, rel=1e-9)
    # 180 檔同分（無資訊）擠在 0 附近；只有真正有值的少數幾檔被推到尾端
    assert float(cast("float", huge.abs().min())) < 0.2
    assert int((huge.abs() > 3.0).sum()) <= 4, "尾端只能是真的有相異值的那幾檔"

    # 對照：option B 在同一組資料上，離群值的大小會改變當日每一個 z
    from twquant.factors.standardize import zscore

    b_huge = zscore(_sparse_frame(day, zeros=180, tail=[1.0, 5.0, 900.0, 1e6]))["value"]
    b_mild = zscore(_sparse_frame(day, zeros=180, tail=[1.0, 5.0, 6.0, 8.0]))["value"]
    assert b_huge.to_list() != b_mild.to_list()


def test_ac3_all_identical_cross_section_stays_zero() -> None:
    """AC-3：整個橫斷面真的只有一個值時，輸出仍是 0——那時候本來就沒有排序可言。"""
    df = _sparse_frame(dt.date(2024, 1, 2), zeros=20, tail=[])
    assert standardize(df)["value"].to_list() == [0.0] * 20


def test_ac3_non_finite_values_survive_the_rank_path() -> None:
    """AC-3：非有限值原樣保留（同 `zscore` 的既有契約），由下游 filter 剔除。"""
    df = _sparse_frame(dt.date(2024, 1, 2), zeros=14, tail=[1.0, 2.0, 3.0, 7.0]).with_columns(
        pl.when(pl.col("stock_id") == "0000")
        .then(float("inf"))
        .otherwise(pl.col("value"))
        .alias("value")
    )
    out = standardize(df)
    assert out.filter(pl.col("stock_id") == "0000")["value"][0] == float("inf")
    rest = out.filter(pl.col("stock_id") != "0000")["value"]
    assert rest.is_finite().all(), "單一非有限值不得污染整個橫斷面"
