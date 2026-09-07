"""T-163／D-062：相對流動性門檻 `min_adv_pctl`。

為什麼要它（實測，非假設）：絕對門檻 `min_adv_twd=30M` 的留存率有 **九成八**
由當年市場成交熱度解釋（2015-06 起 r = +0.982），亦即宇宙大小是**行情的函數**。
壓力年宇宙縮四成、成分也不同——而 D-038 §5 的晉升條件裡有最大回撤，
回撤正好發生在壓力年。等於在最需要穩定基準的時段換了一個池子。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl
import pytest

from tests.conftest import write_stock_meta
from twquant.conventions import TZ
from twquant.data.warehouse import Warehouse
from twquant.errors import ConfigError
from twquant.strategy.config import UniverseConfig
from twquant.strategy.universe import universe_ids

_SESSIONS = 40  # 需 > 20 才有 ADV（rolling_median(20)）


def _wh(
    tmp_path: Path, amounts: dict[str, float], *, closes: dict[str, float] | None = None
) -> Warehouse:
    """每檔給一個固定的日成交金額，跑滿 40 個工作日，好讓 20 日中位數就等於該值。"""
    rows = []
    d = dt.date(2025, 1, 6)  # 週一
    for _ in range(_SESSIONS):
        if d.weekday() < 5:
            for sid, amt in amounts.items():
                c = (closes or {}).get(sid, 100.0)
                rows.append(
                    {
                        "date": d,
                        "stock_id": sid,
                        "open": c,
                        "high": c,
                        "low": c,
                        "close": c,
                        "volume": 1000,
                        "amount": amt,
                        "limit_up_price": None,
                        "limit_down_price": None,
                        "available_at": dt.datetime(d.year, d.month, d.day, 18, 0, tzinfo=TZ),
                    }
                )
        d += dt.timedelta(days=1)
    df = pl.DataFrame(rows).with_columns(
        pl.col("volume").cast(pl.Int64),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", df, source="twse")
    write_stock_meta(wh, df)
    return wh


def _last_session(wh: Warehouse) -> dt.date:
    days: list[dt.date] = (
        wh.view(dt.date(2025, 12, 31)).table("prices_daily").collect()["date"].to_list()
    )
    return max(days)


# 10 檔，成交金額由 1M 遞增到 10M
_TEN = {f"{1000 + i:04d}": float(i) * 1_000_000 for i in range(1, 11)}


def test_ac1_keeps_top_fraction(tmp_path: Path) -> None:
    """AC-1：`min_adv_pctl=0.70` 保留 ADV 前 30%——10 檔留 3 檔（最高的三檔）。"""
    wh = _wh(tmp_path, _TEN)
    d = _last_session(wh)
    ids = universe_ids(
        wh.view(d), d, UniverseConfig(min_adv_twd=None, min_adv_pctl=0.70, min_price=None)
    )
    assert ids == ["1008", "1009", "1010"], "1M~10M 的前 30% ＝ 8M/9M/10M 三檔"
    # 切點用 linear 而非 polars 預設的 nearest：後者會把切點那一檔也留下（4 檔），
    # 讓「前 30%」變成「大約 30% 再多一檔」。


def test_ac2_threshold_moves_with_the_cross_section_not_with_the_market_level(
    tmp_path: Path,
) -> None:
    """AC-2：**這是整條裁決的重點**——全市場成交量放大十倍，宇宙大小不變。

    絕對門檻在同一組資料上會從「留 0 檔」變成「留 10 檔」；相對門檻兩次都留 3 檔。
    """
    quiet = _wh(tmp_path / "quiet", _TEN)
    boom = _wh(tmp_path / "boom", {k: v * 10 for k, v in _TEN.items()})
    rel = UniverseConfig(min_adv_twd=None, min_adv_pctl=0.70, min_price=None)
    absolute = UniverseConfig(min_adv_twd=30_000_000, min_price=None)

    n_rel = [
        len(universe_ids(w.view(_last_session(w)), _last_session(w), rel)) for w in (quiet, boom)
    ]
    n_abs = [
        len(universe_ids(w.view(_last_session(w)), _last_session(w), absolute))
        for w in (quiet, boom)
    ]
    assert n_rel == [3, 3], "相對門檻：行情放大十倍，宇宙大小不動"
    assert n_abs == [0, 8], "絕對門檻：同一批公司，宇宙從 0 檔變 8 檔"


def test_ac3_population_excludes_stocks_without_enough_history(tmp_path: Path) -> None:
    """AC-3：ADV 為 null（歷史不足 20 日）者不進母體、也不入選。

    「不知道是不是流動股」不等於「是流動股」——與 `min_roe` 的處理一致。
    """
    wh = _wh(tmp_path, _TEN)
    d = _last_session(wh)
    # 加一檔只有最後一天有資料的新股：ADV 為 null
    df = wh.view(d).table("prices_daily").collect()
    newbie = (
        df.filter(pl.col("date") == d)
        .head(1)
        .with_columns(pl.lit("9999").alias("stock_id"), pl.lit(1e12).alias("amount"))
    )
    wh2 = Warehouse(tmp_path / "x")
    both = pl.concat([df, newbie])
    wh2.write("clean", "prices_daily", both, source="twse")
    write_stock_meta(wh2, both)
    ids = universe_ids(
        wh2.view(d), d, UniverseConfig(min_adv_twd=None, min_adv_pctl=0.70, min_price=None)
    )
    assert "9999" not in ids, "ADV 為 null 不得因為當天金額最大而入選"
    assert ids == ["1008", "1009", "1010"], "母體不含它，故切點與前一條相同"


def test_ac4_both_liquidity_gates_at_once_is_rejected() -> None:
    """AC-4：兩道流動性濾網互斥。

    疊起來的交集既不是相對門檻也不是絕對門檻，而且絕對那道會把行情耦合帶回來
    ——那正是這條裁決要消滅的東西。且**不自動關掉** `min_adv_twd`：
    靜默改變既有配置的語意是黃金規則 5 禁止的。
    """
    with pytest.raises(ValueError, match="互斥"):
        UniverseConfig(min_adv_twd=30_000_000, min_adv_pctl=0.70)
    # 顯式關掉絕對門檻才合法
    assert UniverseConfig(min_adv_twd=None, min_adv_pctl=0.70).min_adv_pctl == 0.70


def test_ac4_pctl_must_be_strictly_between_zero_and_one() -> None:
    """AC-4：0 與 1 都不合法——0 等於不過濾（改用 None），1 等於只留最大的那一檔。"""
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            UniverseConfig(min_adv_twd=None, min_adv_pctl=bad)


def test_ac5_existing_configs_are_unchanged(tmp_path: Path) -> None:
    """AC-5：`min_adv_pctl` 預設 None ⇒ 既有配置**逐位元不變**。

    D-062 的裁決不重跑既有 113 筆回測，前提就是它們的宇宙沒被動到。
    """
    wh = _wh(tmp_path, _TEN)
    d = _last_session(wh)
    before = universe_ids(wh.view(d), d, UniverseConfig(min_adv_twd=5_000_000, min_price=None))
    after = universe_ids(
        wh.view(d),
        d,
        UniverseConfig(min_adv_twd=5_000_000, min_adv_pctl=None, min_price=None),
    )
    assert before == after == ["1005", "1006", "1007", "1008", "1009", "1010"]


def test_ac6_cut_is_computed_before_the_hygiene_filter(tmp_path: Path) -> None:
    """AC-6：切點的母體**不受衛生濾網影響**（同 T-121 的市值排名母體）。

    「ADV 前 30%」是市場事實。若母體是衛生濾網之後的殘存集合，改一個
    `max_jump_ratio` 就會讓流動性門檻跟著移，而那個移動不會出現在任何結果說明裡。
    """
    wh = _wh(tmp_path, _TEN, closes={"1010": 100.0})
    d = _last_session(wh)
    df = wh.view(d).table("prices_daily").collect()
    # 讓成交金額最大的 1010 在最後一天暴跌 60%（觸發 max_jump_ratio 衛生濾網）
    holed = df.with_columns(
        pl.when((pl.col("stock_id") == "1010") & (pl.col("date") == d))
        .then(40.0)
        .otherwise(pl.col("close"))
        .alias("close")
    )
    wh2 = Warehouse(tmp_path / "h")
    wh2.write("clean", "prices_daily", holed, source="twse")
    write_stock_meta(wh2, holed)
    ids = universe_ids(
        wh2.view(d), d, UniverseConfig(min_adv_twd=None, min_adv_pctl=0.70, min_price=None)
    )
    # 1010 被衛生濾網踢掉，但它**仍留在切點的母體裡**，故切點不變（8M）、剩 1008/1009。
    # 若母體改成衛生濾網之後的 9 檔，切點會降到 7.3M 而讓 1007 也入選——那正是要避免的。
    assert ids == ["1008", "1009"], "母體含 1010 → 切點不因它被踢掉而下移"


def test_ac7_config_file_round_trip(tmp_path: Path) -> None:
    """AC-7：YAML 走得通（載入器與長窗口配置要用它）。"""
    from twquant.config import load_config

    p = tmp_path / "u.yaml"
    p.write_text(
        "name: t\ndescription: t\ntrial_scope: t\n"
        "universe: {markets: [TWSE], min_adv_twd: null, min_adv_pctl: 0.70, min_price: 10.0}\n"
        "signals: [{factor: price.mom_20d, weight: 1.0}]\n"
        "selection: {method: top_n, top_n: 5}\n",
        encoding="utf-8",
    )
    from twquant.strategy.config import StrategyConfig

    cfg = load_config(p, StrategyConfig)
    assert cfg.universe.min_adv_pctl == 0.70
    assert cfg.universe.min_adv_twd is None

    p.write_text(
        "name: t\ndescription: t\ntrial_scope: t\n"
        "universe: {markets: [TWSE], min_adv_pctl: 0.70}\n"
        "signals: [{factor: price.mom_20d, weight: 1.0}]\n"
        "selection: {method: top_n, top_n: 5}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="互斥"):
        load_config(p, StrategyConfig)
