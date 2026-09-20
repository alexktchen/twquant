"""金庫期 RankIC（D-106 的 P1／P3／P4）。

標籤的建法**逐字複製 `ml train` 的分支**（cli.py 的 label 區塊）——同一組函數、
同一份配置、同一組參數。若改用「簡化版報酬」當標籤，量到的就不是同一件事。

只讀分數與價格、只算相關係數，不寫任何東西。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from typing import cast

import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from twquant.cli import _load_ml, _load_serving, _ml_features_resolved  # noqa: E402
from twquant.backtest.costs import CostConfig  # noqa: E402
from twquant.config import load_config  # noqa: E402
from twquant.data.serving import store  # noqa: E402
from twquant.data.serving.ml_store import (  # noqa: E402
    read_corporate_events,
    read_monthly_revenue,
    read_prices_close,
    read_prices_training,
)
from twquant.ml.dataset import (  # noqa: E402
    attach_adjustment,
    cost_adjusted_return_labels,
    forward_drawdown_labels,
    month_end_signal_dates,
    revenue_surprise_labels,
)
from twquant.ml.evaluate import daily_rank_ic  # noqa: E402

CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
VAULT_LO, VAULT_HI = dt.date(2026, 3, 2), dt.date(2026, 9, 8)
# 標籤要往前推 h 天，故價格要多讀一段；起點也往前一點以取得完整交易日曆
PX_LO, PX_HI = dt.date(2026, 1, 2), dt.date(2026, 9, 8)

CASES = [
    ("lgbm_lw_net_h20_20260225_83890036", "ml_h20_cost_aware_lw", "基礎 H20 成本感知"),
    ("lgbm_lw_rev_h15_20260225_9d4a149c", "ml_f_revenue_surprise_lw", "F 營收驚奇"),
    ("lgbm_lw_dd_h60_20260225_1bd7c6a0", "ml_r_drawdown60_lw", "R 60 日回撤"),
]


def _labels(cfg_name: str) -> tuple[pl.DataFrame, int]:
    """建該模型的標籤（date, stock_id, label），回傳（標籤, horizon）。"""
    cfg = _load_ml(str(CONFIG_DIR / f"{cfg_name}.yaml"))
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        reader = {
            "cost_adjusted_return": read_prices_training,
            "forward_drawdown": read_prices_close,
            "revenue_surprise": read_prices_close,
        }[cfg.label.transform]
        px = reader(conn, scfg.pg_schema, PX_LO, PX_HI)
        events = (
            read_corporate_events(conn, scfg.pg_schema, PX_HI) if cfg.label.total_return else None
        )
        revenue = (
            read_monthly_revenue(conn, scfg.pg_schema, PX_LO, PX_HI)
            if cfg.label.transform == "revenue_surprise"
            else None
        )
    finally:
        conn.close()
    px = px.filter(pl.col("stock_id").str.contains(scfg.cloud.stock_pattern))
    if events is not None:
        px = attach_adjustment(px, events)
    days: list[dt.date] = sorted(px["date"].unique().to_list())
    sig = month_end_signal_dates(days) if cfg.label.signal_dates == "month_end" else days

    if cfg.label.transform == "revenue_surprise":
        assert revenue is not None
        lab = revenue_surprise_labels(
            revenue, sig, days, max_lag_sessions=cast("int", cfg.label.max_lag_sessions)
        )
    elif cfg.label.transform == "forward_drawdown":
        lab = forward_drawdown_labels(
            px,
            sig,
            horizon=cfg.horizon_days,
            adjusted=cast("bool", cfg.label.total_return),
            max_span_sessions=cfg.label.max_span_sessions,
        )
    else:
        lab = cost_adjusted_return_labels(
            px,
            horizon=cfg.horizon_days,
            adv_window_sessions=cfg.label.adv_window_sessions,
            position_notional_twd=cfg.label.position_notional_twd,
            downside_penalty=cfg.label.downside_penalty,
            cost_cfg=load_config(CONFIG_DIR / "costs.yaml", CostConfig),
            adjusted=cast("bool", cfg.label.total_return),
            max_span_sessions=cfg.label.max_span_sessions,
        )
    return lab, cfg.horizon_days


def _scores(model_id: str) -> pl.DataFrame:
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT date, stock_id, rank_pct FROM "{scfg.pg_schema}"."ml_scores" '
                "WHERE model_id = %s AND date BETWEEN %s AND %s",
                (model_id, VAULT_LO, VAULT_HI),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    return pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [str(r[1]) for r in rows],
            "score": [float(r[2]) for r in rows],
        }
    )


def _ic(scores: pl.DataFrame, labels: pl.DataFrame) -> tuple[pl.DataFrame, float, float]:
    joined = scores.join(labels, on=["date", "stock_id"], how="inner")
    if joined.is_empty():
        raise ValueError("分數與標籤沒有交集——拒絕回報 0")
    daily = daily_rank_ic(joined, "score", "label")
    mean = float(daily["ic"].mean())
    pos = float((daily["ic"] > 0).mean())
    return daily, mean, pos


def main() -> None:
    print(f"金庫期 {VAULT_LO} ~ {VAULT_HI}\n")
    out: dict[str, tuple[float, float, int]] = {}
    for model_id, cfg_name, label in CASES:
        lab, h = _labels(cfg_name)
        sc = _scores(model_id)
        daily, mean, pos = _ic(sc, lab)
        out[label] = (mean, pos, daily.height)
        print(f"{label:16s} h={h:2d}  訊號日 {daily.height:4d}  RankIC {mean:+.4f}  為正 {pos:.0%}")
        if daily.height >= 6:
            print(
                "    逐月：",
                ", ".join(
                    f"{k[:7]} {v:+.3f}"
                    for k, v in daily.with_columns(pl.col("date").cast(pl.Utf8))
                    .group_by(pl.col("date").str.slice(0, 7))
                    .agg(pl.col("ic").mean())
                    .sort("date")
                    .iter_rows()
                ),
            )

    # P3：R 模型相對單一因子 price.vol_20d 的增量
    lab, _ = _labels("ml_r_drawdown60_lw")
    scfg = _load_serving()
    conn = store.connect(scfg)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT date, stock_id, z FROM "{scfg.pg_schema}"."factor_values" '
                "WHERE factor = 'price.vol_20d' AND date BETWEEN %s AND %s",
                (VAULT_LO, VAULT_HI),
            )
            rows = cur.fetchall()
    finally:
        conn.close()
    # 低波動＝不易大跌＝標籤百分位高，故取負號讓方向與模型分數一致
    vol = pl.DataFrame(
        {
            "date": [r[0] for r in rows],
            "stock_id": [str(r[1]) for r in rows],
            "score": [-float(r[2]) for r in rows],
        }
    )
    _, vol_ic, vol_pos = _ic(vol, lab)
    model_ic = out["R 60 日回撤"][0]
    print(f"\nP3：R 模型 {model_ic:+.4f} vs 單一因子 -vol_20d {vol_ic:+.4f}（為正 {vol_pos:.0%}）")
    print(f"     增量 {model_ic - vol_ic:+.4f}（門檻 +0.05）")
    print(f"\nP4：F {out['F 營收驚奇'][0]:+.4f} vs 基礎 H20 {out['基礎 H20 成本感知'][0]:+.4f}")

    # 穩健性：最終模型 train_end=2026-02-25，但 h=20 的標籤往前推 20 個交易日
    # 可及 ~2026-03-25——那已經在金庫裡。故把那一段單獨拿掉再看一次。
    base_lab, _ = _labels("ml_h20_cost_aware_lw")
    base_daily = daily_rank_ic(
        _scores("lgbm_lw_net_h20_20260225_83890036").join(
            base_lab, on=["date", "stock_id"], how="inner"
        ),
        "score",
        "label",
    ).sort("date")
    cuts = {}
    for key, cut in [
        ("all", dt.date(2026, 1, 1)),
        ("ex_contaminated", dt.date(2026, 3, 26)),
        ("ex_march", dt.date(2026, 4, 1)),
        ("may_on", dt.date(2026, 5, 1)),
    ]:
        w = base_daily.filter(pl.col("date") >= cut)
        cuts[key] = {"ic": float(w["ic"].mean()), "pos": float((w["ic"] > 0).mean()), "n": w.height}
    monthly = {
        str(k): float(v)
        for k, v in base_daily.with_columns(pl.col("date").cast(pl.Utf8))
        .group_by(pl.col("date").str.slice(0, 7))
        .agg(pl.col("ic").mean())
        .sort("date")
        .iter_rows()
    }
    payload = {
        "models": {
            label: {"ic": mean, "pos": pos, "n": n} for label, (mean, pos, n) in out.items()
        },
        "base_cuts": cuts,
        "base_monthly": monthly,
        "p3": {
            "model": model_ic,
            "vol20d": vol_ic,
            "delta": model_ic - vol_ic,
            "threshold": 0.05,
        },
    }
    Path("D:/project/twquant/data/vault_ic.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\n→ D:/project/twquant/data/vault_ic.json")


if __name__ == "__main__":
    main()
