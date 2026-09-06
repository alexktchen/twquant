"""T-158 / T-155：D-074（營收驚奇）與 D-075（未來回撤）兩條事前登記研究線的標籤。

兩者共用切分與 LightGBM 參數，只換預測標的，故測的重點是**標籤的支撐集**
——它到底看了哪些資料。前向標籤不受 `assert_no_lookahead` 約束（那是給因子的），
所以這裡用等價但方向相反的性質：改動視窗外的資料不得改變標籤。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from twquant.config import MlConfig, load_config
from twquant.conventions import TZ
from twquant.factors import registry
from twquant.ml.dataset import (
    forward_drawdown_labels,
    month_end_signal_dates,
    revenue_surprise_labels,
)

_CONFIGS = Path(__file__).resolve().parents[1] / "configs"
_STOCKS = [f"{1000 + i * 7:04d}" for i in range(24)]


def _weekdays(start: dt.date, n_days: int) -> list[dt.date]:
    out: list[dt.date] = []
    d = start
    while len(out) < n_days:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _revenue(yoy: dict[tuple[str, int], float], months: int = 8) -> pl.DataFrame:
    """每月 10 日公布的月營收 YoY；`yoy` 以 (stock_id, 月) 覆寫預設值。"""
    return pl.DataFrame(
        [
            {
                "stock_id": sid,
                "period_end": dt.date(2024, m, 28),
                "available_at": dt.datetime(2024, m, 10, 18, 0, tzinfo=TZ),
                "yoy": yoy.get((sid, m), 0.01 * m + 0.05 * i),
            }
            for i, sid in enumerate(_STOCKS)
            for m in range(1, months + 1)
        ]
    )


def _prices(days: list[dt.date], path: dict[str, list[float]]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"date": d, "stock_id": sid, "close": series[i]}
            for sid, series in path.items()
            for i, d in enumerate(days)
        ]
    )


# ── 訊號日 ────────────────────────────────────────────────────────────────


def test_month_end_signal_dates_takes_last_trading_day() -> None:
    days = _weekdays(dt.date(2024, 1, 1), 90)
    sig = month_end_signal_dates(days)
    # 3/30、3/31 是週末，故 3 月底是 3/29——是最後一個**交易日**，不是曆月最後一天
    assert sig[:3] == [dt.date(2024, 1, 31), dt.date(2024, 2, 29), dt.date(2024, 3, 29)]
    assert all(s in days for s in sig)


def test_month_end_signal_dates_rejects_empty() -> None:
    with pytest.raises(ValueError, match="trading_days 為空"):
        month_end_signal_dates([])


# ── F 系列（D-074 / T-158） ───────────────────────────────────────────────


def test_ac1_revenue_surprise_label_is_the_orthogonal_residual() -> None:
    """AC-1：標籤＝下次 YoY 對（本月 YoY、加速度）的橫斷面 OLS 殘差。

    直接用下月 YoY 當標籤時最強的預測子是加速度自己（先導實測 RankIC −0.29），
    那是均值回歸不是訊號；殘差化就是要把它壓掉。

    構造法：先挑一組擾動 eps，把它對設計矩陣 [1, yoy, accel] 投影後扣掉，
    使 eps ⟂ 設計矩陣；則 OLS 殘差**恰等於** eps，label 的排序必等於 eps 的排序。
    """
    days = _weekdays(dt.date(2024, 1, 1), 130)
    sig = month_end_signal_dates(days)
    n = len(_STOCKS)
    rng = np.random.default_rng(0)
    prev = np.linspace(-0.3, 0.4, n)  # yoy(3 月)
    now = rng.permutation(np.linspace(-0.2, 0.6, n))  # yoy(4 月) → yoy_now
    design = np.column_stack([np.ones(n), now, now - prev])
    raw = rng.normal(size=n)
    eps = raw - design @ np.linalg.lstsq(design, raw, rcond=None)[0]
    nxt = 0.5 + 2.0 * now - 3.0 * (now - prev) + eps  # yoy(5 月) → 標籤的目標

    overrides: dict[tuple[str, int], float] = {}
    for i, sid in enumerate(_STOCKS):
        overrides[sid, 3] = float(prev[i])
        overrides[sid, 4] = float(now[i])
        overrides[sid, 5] = float(nxt[i])
    lab = revenue_surprise_labels(_revenue(overrides), sig, days, max_lag_sessions=15)

    at_t = lab.filter(pl.col("date") == dt.date(2024, 4, 30)).sort("stock_id")
    assert at_t.height == n
    order = np.argsort(np.argsort(eps[[_STOCKS.index(s) for s in at_t["stock_id"].to_list()]]))
    got = np.argsort(np.argsort(at_t["label"].to_numpy()))
    assert np.array_equal(order, got), "label 的排序須等於正交擾動的排序"


def test_ac2_revenue_surprise_is_pit_next_announcement_only() -> None:
    """AC-2：標籤只看**緊接訊號日之後**的那一次公布；更後面的公布不得影響它。"""
    days = _weekdays(dt.date(2024, 1, 1), 130)
    sig = month_end_signal_dates(days)
    base = revenue_surprise_labels(_revenue({}), sig, days, max_lag_sessions=15)
    bumped = revenue_surprise_labels(
        _revenue({(sid, m): 9.9 * (i + 1) for i, sid in enumerate(_STOCKS) for m in (6, 7, 8)}),
        sig,
        days,
        max_lag_sessions=15,
    )
    # 2、3 月底的訊號日，其「下一次公布」分別在 3、4 月 → 不受 6 月起的改動影響
    early = [d for d in base["date"].unique().to_list() if d < dt.date(2024, 4, 1)]
    assert early, "測資需涵蓋 4 月前的訊號日"
    lhs = base.filter(pl.col("date").is_in(early)).sort("date", "stock_id")
    rhs = bumped.filter(pl.col("date").is_in(early)).sort("date", "stock_id")
    assert lhs.equals(rhs)
    # 而 5 月底的訊號日（下一次公布在 6 月）必須反映得到改動——否則上面那條無意義
    late = base.filter(pl.col("date") == dt.date(2024, 5, 31)).sort("stock_id")
    late_bumped = bumped.filter(pl.col("date") == dt.date(2024, 5, 31)).sort("stock_id")
    assert not late.equals(late_bumped)


def test_ac3_revenue_surprise_drops_late_filings() -> None:
    """AC-3：下一次公布距訊號日超過 max_lag_sessions 個開市日者無標籤（遲報不是驚奇）。"""
    days = _weekdays(dt.date(2024, 1, 1), 130)
    sig = month_end_signal_dates(days)
    lenient = revenue_surprise_labels(_revenue({}), sig, days, max_lag_sessions=15)
    assert not lenient.is_empty()
    # 公布日固定在每月 10 日 → 距月底約 6~8 個開市日；門檻壓到 3 即全部落榜
    with pytest.raises(ValueError, match="營收驚奇標籤為空"):
        revenue_surprise_labels(_revenue({}), sig, days, max_lag_sessions=3)


def test_ac4_revenue_surprise_raises_on_non_finite_yoy() -> None:
    """AC-4：非有限 yoy 直接 raise。

    inf 不是缺值，`drop_nulls` 攔不住它，而 OLS 會把它擴散到當日**整個橫斷面**
    的殘差——一列壞資料毀掉一整天的樣本（黃金規則 5）。
    """
    days = _weekdays(dt.date(2024, 1, 1), 130)
    sig = month_end_signal_dates(days)
    with pytest.raises(ValueError, match="非有限值"):
        revenue_surprise_labels(
            _revenue({(_STOCKS[1], 3): float("inf")}), sig, days, max_lag_sessions=15
        )


def test_ac5_revenue_surprise_config_bans_fundamental_features() -> None:
    """AC-5：D-074 的「禁營收類特徵」寫在配置層，不是靠審查。

    標籤是營收的殘差，放營收特徵進去等於把答案的一部分餵給模型；而特徵清單
    是最容易「順手加一個」的地方。
    """
    cfg = load_config(_CONFIGS / "ml_f_revenue_surprise.yaml", MlConfig)
    assert cfg.label.transform == "revenue_surprise"
    assert not [f for f in cfg.features if f.startswith("fundamental.")]
    with pytest.raises(ValueError, match="禁止營收／財報類特徵"):
        MlConfig.model_validate(
            {
                "horizon_days": 15,
                "features": ["price.mom_20d", "fundamental.revenue_yoy"],
                "label": {
                    "transform": "revenue_surprise",
                    "signal_dates": "month_end",
                    "max_lag_sessions": 15,
                },
                "split": {"embargo_days": 20},
            }
        )
    # 空 features（＝全部註冊因子）同樣要擋——那會把 fundamental.* 一起帶進來
    with pytest.raises(ValueError, match="不得用「空 features"):
        MlConfig.model_validate(
            {
                "horizon_days": 15,
                "label": {
                    "transform": "revenue_surprise",
                    "signal_dates": "month_end",
                    "max_lag_sessions": 15,
                },
                "split": {"embargo_days": 20},
            }
        )


# ── R 系列（D-075 / T-155） ───────────────────────────────────────────────


def test_ac1_drawdown_label_is_anchored_at_t_close_not_run_max() -> None:
    """AC-1：標籤以 **t 日收盤**為基準，不是 t 之後的 run-max。

    兩種定義在「先漲後跌」的路徑上給出不同的答案：run-max 版把漲上去再跌回原點
    算成三成的回撤，t 日收盤版算出 0。D-075 實測 run-max 版有 13~41% 的 IC
    來自與 `price.high_52w` 共用的「當下價位在區間中的位置」，故一律採用後者。
    """
    days = _weekdays(dt.date(2024, 1, 1), 40)
    h = 10
    # 1101：漲到 150 再跌回 100（run-max −33%、t 日收盤 0%）；另兩檔實際下跌
    up_then_back = [100.0 + 10.0 * i for i in range(6)] + [140.0, 130.0, 120.0, 110.0]
    up_then_back += [100.0] * (len(days) - len(up_then_back))
    down = [100.0 - i for i in range(len(days))]
    px = _prices(days, {"1101": up_then_back, "2330": down, "2454": down})
    t = days[0]

    assert _raw_drawdown(px, t, h)["1101"] == pytest.approx(0.0), "未來 10 日最低點＝t 日收盤"
    window = up_then_back[1 : 1 + h]
    assert min(window) / max(window) - 1.0 < -0.3, "run-max 版會把同一段算成三成回撤"

    out = forward_drawdown_labels(px, [t], horizon=h)
    assert out.filter(pl.col("stock_id") == "1101")["label"][0] == pytest.approx(1.0)


def _raw_drawdown(px: pl.DataFrame, t: dt.date, h: int) -> dict[str, float]:
    """測試輔助：直接由價格算出「相對 t 日收盤的最低點跌幅」，供與標籤對照。"""
    out: dict[str, float] = {}
    for sid, part in px.sort("date").group_by("stock_id"):
        anchor = part.filter(pl.col("date") == t)["close"][0]
        out[str(sid[0])] = (
            min(part.filter(pl.col("date") > t)["close"].to_list()[:h]) / anchor - 1.0
        )
    return out


def test_ac2_drawdown_uses_total_return_prices() -> None:
    """AC-2：除息跳空不是回撤。未還原時會被當成虧損（D-054 的同一個失效模式）。"""
    days = _weekdays(dt.date(2024, 1, 1), 40)
    # 2330 在第 4 個交易日除息 10%：價格 100 → 90，但總報酬持平
    raw = [100.0] * 3 + [90.0] * (len(days) - 3)
    px = _prices(days, {"2330": raw, "1101": [100.0] * len(days), "2454": [100.0] * len(days)})
    adj = px.with_columns(
        pl.when((pl.col("stock_id") == "2330") & (pl.col("date") >= days[3]))
        .then(0.9)
        .otherwise(1.0)
        .alias("adj")
    )
    t = days[0]
    unadjusted = forward_drawdown_labels(px, [t], horizon=10)
    adjusted = forward_drawdown_labels(adj, [t], horizon=10, adjusted=True)
    # 未還原：2330 是當日唯一「下跌」的一檔 → 百分位最低
    assert unadjusted.filter(pl.col("stock_id") == "2330")["label"][0] < 0.5
    # 還原後三檔完全一樣 → 並列，不再有假的落後者
    assert adjusted["label"].n_unique() == 1


def test_ac2_drawdown_requires_adj_when_adjusted() -> None:
    days = _weekdays(dt.date(2024, 1, 1), 20)
    px = _prices(days, {"2330": [100.0] * 20})
    with pytest.raises(ValueError, match="adjusted=True 需要 adj 欄"):
        forward_drawdown_labels(px, [days[0]], horizon=5, adjusted=True)


def test_ac3_drawdown_nulls_windows_spanning_data_gaps() -> None:
    """AC-3：橫跨資料缺口的視窗設 null（T-139）。

    某檔中間缺 N 個開市日時，`shift(-h)` 撈到的是 h **列**之後而非 h **個交易日**
    之後的價格——「60 日最低點」實際橫跨數年，而模型會把它當成真實的極端回撤。
    """
    days = _weekdays(dt.date(2024, 1, 1), 60)
    flat = {sid: [100.0] * 60 for sid in ("1101", "2330", "2454")}
    full = _prices(days, flat)
    # 2330 自第 3 個交易日起整段缺席（停牌），其餘兩檔完整
    holed = full.filter(~((pl.col("stock_id") == "2330") & pl.col("date").is_in(days[2:40])))
    t = days[1]
    lenient = forward_drawdown_labels(holed, [t], horizon=10)
    guarded = forward_drawdown_labels(holed, [t], horizon=10, max_span_sessions=21)
    assert lenient.filter(pl.col("stock_id") == "2330")["label"][0] is not None
    assert guarded.filter(pl.col("stock_id") == "2330")["label"][0] is None
    assert guarded.filter(pl.col("stock_id") == "1101")["label"][0] is not None, "沒缺口的不受影響"


def test_ac4_drawdown_label_support_is_exactly_the_forward_window() -> None:
    """AC-4：標籤只看 (t, t+h] 這一段。

    前向標籤不適用 `assert_no_lookahead`（它本來就看未來），故改驗等價的鏡像性質：
    改動視窗**之前**與**之後**的價格都不得改變標籤。這同時抓得到「視窗長度算錯」
    ——那是比前視更常見的錯。
    """
    days = _weekdays(dt.date(2024, 1, 1), 40)
    h = 10
    px = _prices(days, {sid: [100.0] * len(days) for sid in ("1101", "2330", "2454")})
    t = days[5]
    base = forward_drawdown_labels(px, [t], horizon=h)

    def _bump(mask: pl.Expr) -> pl.DataFrame:
        # 砸到 1 元：只要落在支撐集內，2330 的標籤必然掉到最低
        return forward_drawdown_labels(
            px.with_columns(
                pl.when(mask & (pl.col("stock_id") == "2330"))
                .then(1.0)
                .otherwise(pl.col("close"))
                .alias("close")
            ),
            [t],
            horizon=h,
        )

    assert _bump(pl.col("date") < t).equals(base), "視窗之前的價格不得影響標籤"
    assert _bump(pl.col("date") > days[5 + h]).equals(base), "視窗之後的價格不得影響標籤"
    inside = _bump(pl.col("date") == days[5 + h])
    assert inside.filter(pl.col("stock_id") == "2330")["label"][0] < 0.5, "第 h 天在視窗內"


def test_ac5_drawdown_config_pins_monthly_signal_and_embargo() -> None:
    """AC-5：訊號日為月底、embargo ≥ horizon 由配置層把關（D-075／D-077）。"""
    cfg = load_config(_CONFIGS / "ml_r_drawdown60.yaml", MlConfig)
    assert cfg.label.transform == "forward_drawdown"
    assert cfg.label.signal_dates == "month_end"
    assert cfg.label.total_return is True
    # D-077：embargo 20 < horizon 60 是洩漏——訓練列的標籤視窗會蓋掉測試段前 40 天
    assert cfg.split.embargo_days >= cfg.horizon_days
    with pytest.raises(ValueError, match="embargo_days"):
        MlConfig.model_validate(
            {
                "horizon_days": 60,
                "features": ["price.mom_20d"],
                "label": {
                    "transform": "forward_drawdown",
                    "signal_dates": "month_end",
                    "total_return": True,
                    "max_span_sessions": 126,
                },
                "split": {"embargo_days": 20},
            }
        )
    with pytest.raises(ValueError, match="signal_dates=month_end"):
        MlConfig.model_validate(
            {
                "horizon_days": 60,
                "features": ["price.mom_20d"],
                "label": {
                    "transform": "forward_drawdown",
                    "total_return": True,
                    "max_span_sessions": 126,
                },
                "split": {"embargo_days": 60},
            }
        )


def test_daily_signal_dates_rejected_for_alt_labels_and_vice_versa() -> None:
    """月頻標籤不得用 daily 訊號日，日頻標籤也不得用 month_end。

    允許任一方向，就等於允許一個未登記的變體悄悄長出來。
    """
    with pytest.raises(ValueError, match="只適用於 D-074／D-075 的"):
        MlConfig.model_validate(
            {
                "horizon_days": 20,
                "features": ["price.mom_20d"],
                "label": {
                    "transform": "cost_adjusted_return",
                    "signal_dates": "month_end",
                    "total_return": True,
                },
                "split": {"embargo_days": 20},
            }
        )


# ── 兩份配置的互相約束 ────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["ml_f_revenue_surprise.yaml", "ml_r_drawdown60.yaml"])
def test_alt_label_config_features_are_registered_factors(name: str) -> None:
    """配置列的每個特徵都必須是已註冊因子——打錯字要在載入時就死，
    而不是跑了三小時 walk-forward 之後才發現某一欄全 null。"""
    import twquant.factors.builtins  # noqa: F401  # 副作用：註冊全因子

    cfg = load_config(_CONFIGS / name, MlConfig)
    known = set(registry.list_factors())
    assert set(cfg.features) <= known, f"未註冊的特徵：{sorted(set(cfg.features) - known)}"
    assert not [f for f in cfg.features if f.startswith("ml.")], "模型分數餵回自己是自我參照洩漏"


def test_alt_label_configs_share_one_feature_list() -> None:
    """F 與 R 的特徵清單逐字相同——兩條線唯一的差別是標籤。

    若清單不同，「R 的 IC 比 F 高」就同時可以歸因到特徵選擇，說不出標籤的貢獻。
    """
    f = load_config(_CONFIGS / "ml_f_revenue_surprise.yaml", MlConfig)
    r = load_config(_CONFIGS / "ml_r_drawdown60.yaml", MlConfig)
    assert f.features == r.features
    assert f.lgbm == r.lgbm, "不調參（D-074／D-075 明文）"


def test_alt_label_configs_match_base_model_lgbm_and_split() -> None:
    """LightGBM 參數與切分（除 embargo）與 ml_h20_cost_aware.yaml 逐字相同。

    這是「可比」的唯一來源；窗數少不是放寬它的理由。
    """
    base = load_config(_CONFIGS / "ml_h20_cost_aware.yaml", MlConfig)
    for name in ("ml_f_revenue_surprise.yaml", "ml_r_drawdown60.yaml"):
        cfg = load_config(_CONFIGS / name, MlConfig)
        assert cfg.lgbm == base.lgbm, name
        assert cfg.split.train_min_years == base.split.train_min_years, name
        assert cfg.split.test_months == base.split.test_months, name
        assert cfg.split.vault_months == base.split.vault_months, name
        assert cfg.split.days_per_year == base.split.days_per_year, name
        assert cfg.split.days_per_month == base.split.days_per_month, name
