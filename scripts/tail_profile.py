"""D-143 §二 的事後探索：台股「飆股」的基礎率、因子區辨力與出場規則比較。

**這是事後探索，不是裁決、不是事前登記的證據。** filter 是看過結果才挑的，
DSR 試驗數已達 175（WHERE-IS-EVERYTHING §6 ①），任何一格要晉升須另立事前登記。
本檔的存在只為了讓 D-143 §二 引用的每個數字可以重跑。

定義：飆股事件 = 訊號日收盤起算，未來 H 個交易日內的最大累積報酬 >= 門檻。
價格為未還原價，故以 |日報酬| > 11%（超過 2015-06 後的漲跌幅上限）視為公司行動或
異常，該日報酬不可信，涵蓋它的視窗一律剔除，避免減資／面額變更造出假飆股。

用法：
    TWQUANT_PG_DSN=postgresql://... uv run --all-groups python scripts/tail_profile.py
"""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import polars as pl
import psycopg
from numpy.lib.stride_tricks import sliding_window_view

DSN = os.environ.get("TWQUANT_PG_DSN", "postgresql://twquant:twquant@localhost:5433/twquant")
START, END = "2015-06-01", "2026-08-31"
HORIZON = 60
EVENT = 0.50
COST = 0.005  # 來回成本估計：手續費兩趟打六折 + 證交稅 0.3% + 滑價
SAMPLE_EVERY = 10  # 因子面板每 N 個交易日取樣一次
BAD_RET = 0.11  # 超過漲跌幅上限 => 公司行動，視窗作廢

FACTORS = [
    "price.mom_20d",
    "price.mom_60d",
    "price.high_52w",
    "price.vol_20d",
    "price.reversal_5d",
    "price.rs_market_20d",
    "volume.turnover_z_20d",
    "fundamental.log_market_cap",
    "fundamental.revenue_yoy",
    "fundamental.earnings_yield",
    "chip.foreign_net_10d",
    "chip.short_margin_ratio",
]


def _fetch(sql: str, params: tuple[Any, ...] | None = None) -> list[tuple[Any, ...]]:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def load_prices() -> pl.DataFrame:
    """四碼普通股的日收盤與成交金額；附日報酬與公司行動旗標。"""
    rows = _fetch(
        """
        select date, stock_id, open, close, amount from serving.prices_daily
        where date between %s and %s and stock_id ~ '^[1-9][0-9]{3}$' and close > 0
        """,
        (START, END),
    )
    df = pl.DataFrame(
        rows, schema=["date", "stock_id", "open", "close", "amount"], orient="row"
    ).with_columns(
        pl.col("date").cast(pl.Date),
        pl.col("open").cast(pl.Float64),
        pl.col("close").cast(pl.Float64),
        pl.col("amount").cast(pl.Float64),
    )
    df = df.sort(["stock_id", "date"])
    prev = pl.col("close").shift(1).over("stock_id")
    return df.with_columns(
        (pl.col("close") / prev - 1).alias("ret"),
        (pl.col("open") / prev - 1).alias("open_ret"),
        pl.col("amount").rolling_mean(20).over("stock_id").alias("adv20"),
    ).with_columns(((pl.col("ret").abs() > BAD_RET).fill_null(False)).cast(pl.Int8).alias("bad"))


def forward_paths(df: pl.DataFrame) -> pl.DataFrame:
    """逐檔算未來 HORIZON 日的路徑統計與各種出場規則的報酬。"""
    out: list[pl.DataFrame] = []
    for (sid,), g in df.group_by(["stock_id"], maintain_order=True):
        close = g["close"].to_numpy()
        bad = g["bad"].to_numpy()
        if len(close) < HORIZON + 2:
            continue
        fwd = sliding_window_view(close[1:], HORIZON)
        badw = sliding_window_view(bad[1:], HORIZON).sum(axis=1)
        n = fwd.shape[0]
        r = fwd / close[:n][:, None] - 1.0
        runmax = np.maximum.accumulate(np.concatenate([np.zeros((n, 1)), r], axis=1), axis=1)[
            :, :-1
        ]
        cols: dict[str, np.ndarray] = {
            "fwd_max": r.max(axis=1),
            "hold_h": r[:, -1],
            "hold20": r[:, 19],
        }
        for up, dn, name in [(0.30, -0.15, "tp30_sl15"), (0.50, -0.15, "tp50_sl15")]:
            k_up = np.where((r >= up).any(1), (r >= up).argmax(1), HORIZON + 1)
            k_dn = np.where((r <= dn).any(1), (r <= dn).argmax(1), HORIZON + 1)
            cols[name] = np.where(k_up < k_dn, up, np.where(k_dn < k_up, dn, r[:, -1]))
            cols[f"{name}_k"] = np.minimum(np.minimum(k_up, k_dn), HORIZON - 1) + 1
        for trail, name in [(0.10, "trail10"), (0.15, "trail15")]:
            hit = r <= (runmax - trail)
            k = np.where(hit.any(1), hit.argmax(1), HORIZON - 1)
            cols[name] = r[np.arange(n), k]
            cols[f"{name}_k"] = k + 1
        # ratchet：初始停損 -15%，浮盈超過 +20% 後改為自高點回落 10%
        hit = np.where(runmax >= 0.20, r <= (runmax - 0.10), r <= -0.15)
        k = np.where(hit.any(1), hit.argmax(1), HORIZON - 1)
        cols["ratchet"] = r[np.arange(n), k]
        cols["ratchet_k"] = k + 1
        out.append(
            pl.DataFrame(
                {
                    "stock_id": [sid] * n,
                    "date": g["date"].to_numpy()[:n],
                    "adv20": g["adv20"].to_numpy()[:n],
                    "badw": badw.astype(np.int16),
                    **{key: val.astype(np.float32) for key, val in cols.items()},
                }
            )
        )
    return pl.concat(out).filter(pl.col("badw") == 0).drop("badw")


def load_factor_panel(dates: list[Any]) -> pl.DataFrame:
    """取樣日的因子橫斷面百分位（`factor_values.pct`，已是 PIT 物化結果）。"""
    panel: pl.DataFrame | None = None
    for factor in FACTORS:
        rows = _fetch(
            "select stock_id, date, pct from serving.factor_values "
            "where factor = %s and date = ANY(%s)",
            (factor, dates),
        )
        if not rows:
            print(f"  ! {factor} 在取樣日沒有任何值，略過")
            continue
        one = (
            pl.DataFrame(rows, schema=["stock_id", "date", "pct"], orient="row")
            .with_columns(pl.col("date").cast(pl.Date), pl.col("pct").cast(pl.Float32))
            .rename({"pct": factor})
        )
        panel = (
            one
            if panel is None
            else panel.join(one, on=["stock_id", "date"], how="full", coalesce=True)
        )
    if panel is None:
        raise RuntimeError("factor_values 在取樣日一列都沒有——先確認物化是否跑過")
    return panel


def report_base_rate(paths: pl.DataFrame) -> None:
    ev = pl.col("fwd_max") >= EVENT
    by_year = (
        paths.with_columns(pl.col("date").dt.year().alias("year"))
        .group_by("year")
        .agg(
            pl.len().alias("obs"),
            (ev.mean() * 100).round(2).alias("event_pct"),
            pl.col("stock_id").filter(ev).n_unique().alias("n_stocks_hit"),
        )
        .sort("year")
    )
    print(f"\n[A] 飆股基礎率（未來 {HORIZON} 交易日最大漲幅 >= {EVENT:.0%}）")
    print(by_year)
    liq = (
        pl.when(pl.col("adv20") < 1e7)
        .then(pl.lit("1 <1千萬"))
        .when(pl.col("adv20") < 5e7)
        .then(pl.lit("2 1-5千萬"))
        .when(pl.col("adv20") < 2e8)
        .then(pl.lit("3 0.5-2億"))
        .when(pl.col("adv20") < 1e9)
        .then(pl.lit("4 2-10億"))
        .otherwise(pl.lit("5 >10億"))
    )
    print("\n[B] 依流動性分層")
    print(
        paths.with_columns(liq.alias("liq"))
        .group_by("liq")
        .agg(pl.len().alias("obs"), (ev.mean() * 100).round(2).alias("event_pct"))
        .sort("liq")
    )


def report_factor_lift(panel: pl.DataFrame) -> None:
    base = (panel["fwd_max"] >= EVENT).mean()
    print(f"\n[C] 單因子區辨力（取樣日基礎率 {base:.2%}）")
    print(f"{'factor':32}{'D1':>8}{'D5':>8}{'D10':>8}{'lift_D10':>10}")
    for factor in FACTORS:
        if factor not in panel.columns:
            continue
        d = panel.filter(pl.col(factor).is_not_null())
        if d.height < 50_000:
            continue
        g = (
            d.with_columns((pl.col(factor) * 10).floor().clip(0, 9).cast(pl.Int8).alias("dec"))
            .group_by("dec")
            .agg(((pl.col("fwd_max") >= EVENT).mean() * 100).alias("rate"))
            .sort("dec")
        )
        rate = dict(g.iter_rows())
        lo, mid, hi = (
            rate.get(0, float("nan")),
            rate.get(4, float("nan")),
            rate.get(9, float("nan")),
        )
        print(f"{factor:32}{lo:>8.2f}{mid:>8.2f}{hi:>8.2f}{hi / (base * 100):>10.2f}")


def report_exits(panel: pl.DataFrame) -> None:
    """同一批進場、不同出場規則的每筆淨期望值（成本後）。"""
    rules = [
        ("hold20", None, 20.0),
        ("hold_h", None, float(HORIZON)),
        ("tp30_sl15", "tp30_sl15_k", None),
        ("tp50_sl15", "tp50_sl15_k", None),
        ("trail10", "trail10_k", None),
        ("trail15", "trail15_k", None),
        ("ratchet", "ratchet_k", None),
    ]
    picks = {
        "全市場（基準）": pl.lit(True),
        "波動前20% ∩ 60日動能前20%": (pl.col("price.vol_20d") >= 0.8)
        & (pl.col("price.mom_60d") >= 0.8),
        "… ∩ 52週高前10%": (pl.col("price.vol_20d") >= 0.8)
        & (pl.col("price.mom_60d") >= 0.8)
        & (pl.col("price.high_52w") >= 0.9),
    }
    for name, expr in picks.items():
        d = panel.filter(expr)
        if d.height < 1000:
            continue
        print(f"\n[D] {name}  n={d.height:,}")
        print(
            f"{'出場規則':14}{'淨期望%':>10}{'平均天數':>10}{'年化%':>9}{'最大單筆%':>11}{'正年/年':>9}"
        )
        for rule, kcol, fixed_days in rules:
            net = d[rule].mean() - COST
            days = float(d[kcol].mean()) if kcol else (fixed_days or 1.0)
            ann = (1 + net) ** (252 / max(days, 1.0)) - 1
            yr = d.group_by(pl.col("date").dt.year()).agg((pl.col(rule).mean() - COST).alias("m"))
            pos = int((yr["m"] > 0).sum())
            print(
                f"{rule:14}{net * 100:>10.2f}{days:>10.0f}{ann * 100:>9.1f}"
                f"{d[rule].min() * 100:>11.1f}{f'{pos}/{yr.height}':>9}"
            )


def report_fill_risk(panel: pl.DataFrame) -> None:
    """隔日開盤漲停 => 買不到。以開盤漲幅 >= 9.5% 近似（limit_up_price 在 PG 覆蓋 0%）。"""
    sel = panel.filter(
        (pl.col("price.vol_20d") >= 0.8)
        & (pl.col("price.mom_60d") >= 0.8)
        & (pl.col("price.high_52w") >= 0.9)
    )
    for name, d in [("全市場", panel), ("波動∩動能∩52週高", sel)]:
        d = d.filter(pl.col("next_open_ret").is_not_null())
        if d.height == 0:
            continue
        print(
            f"[E] {name:22} n={d.height:>8,}  隔日開盤漲停 {(d['next_open_ret'] >= 0.095).mean():.2%}"
            f"  成交額中位 {d['adv20'].median() / 1e6:.0f} 百萬"
        )


def main() -> None:
    prices = load_prices()
    print(f"prices rows={prices.height:,} stocks={prices['stock_id'].n_unique():,}")
    paths = forward_paths(prices)
    print(f"labeled obs={paths.height:,}")
    report_base_rate(paths)

    dates = sorted(paths["date"].unique().to_list())[::SAMPLE_EVERY]
    print(f"\n取樣日 {len(dates)} 天（每 {SAMPLE_EVERY} 個交易日）")
    next_open = (
        prices.with_columns(pl.col("date").shift(1).over("stock_id").alias("sig_date"))
        .select(
            pl.col("sig_date").alias("date"), "stock_id", pl.col("open_ret").alias("next_open_ret")
        )
        .drop_nulls("date")
    )
    panel = (
        paths.filter(pl.col("date").is_in(dates))
        .join(load_factor_panel(dates), on=["stock_id", "date"], how="inner")
        .join(next_open, on=["stock_id", "date"], how="left")
    )
    print(f"panel rows={panel.height:,}")
    report_factor_lift(panel)
    report_exits(panel)
    print()
    report_fill_risk(panel)


if __name__ == "__main__":
    main()
