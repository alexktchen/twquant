"""D-127 §四：第三輪買進模型 SB3 的三條診斷——**全過才准進回測**。

    uv run --all-groups python scripts/s3_diagnostics.py \
        --model <SB3 模型目錄> --config configs/ml_sb3_vol.yaml \
        --baseline <SB 模型目錄>

- **A 標籤基礎率不再隨行情擺動**：全市場「先觸上緣」比例的**月間標準差**，須 < 基準模型（SB）的一半。
- **B 模型挑得出方向**：每日前 15 名的「先觸上緣 − 先觸下緣」須 > 0，以月為單位 t ≥ 2。
  （D-126 量到 SB 在平靜日是 33.4% vs 50.6%，差 −17pp——這是要修的致命傷。）
- **C 平常日子也要有訊號**：校準後機率 ≥ 0.60 的日子須分布在 ≥ 50% 的月份（SB 為 7.8%）。

門檻逐字取自 D-127，事前登記、不得事後修改。不看任何報酬。拒收保留期資料。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import polars as pl
import psycopg

from twquant.cli import _load_ml
from twquant.ml.calibration import walk_forward_calibrate
from twquant.ml.splits import walk_forward_splits

DEV_END = dt.date(2026, 8, 31)
ENTRY_THRESHOLD = 0.60  # D-127 §四
TOP_N = 15
MIN_T = 2.0
MIN_MONTH_SHARE = 0.50
MAX_BASE_VOL_RATIO = 0.50
CAL_EMBARGO, CAL_MIN_WINDOWS = 21, 4


def _sessions(dsn: str, start: str, end: str) -> list[dt.date]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM serving.prices_daily WHERE date BETWEEN %s AND %s "
            "AND stock_id ~ '^[1-9][0-9]{3}$' ORDER BY date",
            (start, end),
        ).fetchall()
    return [r[0] for r in rows]


def _calibrated(model_dir: Path, config: str, dsn: str) -> pl.DataFrame:
    """樣本外分數 → walk-forward 校準；順手存檔供回測使用。"""
    cached = model_dir / "oos_calibrated.parquet"
    if cached.exists():
        return pl.read_parquet(cached)
    meta = json.loads((model_dir / "meta.json").read_text(encoding="utf-8"))
    cfg = _load_ml(config)
    days = _sessions(dsn, meta["data_from"], meta["data_to"])
    splits = walk_forward_splits(
        days,
        train_min_days=cfg.split.train_min_days,
        test_days=cfg.split.test_days,
        embargo_days=cfg.split.embargo_days,
        vault_days=cfg.split.vault_days,
    )
    out = walk_forward_calibrate(
        pl.read_parquet(model_dir / "oos_scores.parquet"),
        [(s.test_start, s.test_end) for s in splits],
        days,
        embargo=CAL_EMBARGO,
        min_windows=CAL_MIN_WINDOWS,
    )
    out.write_parquet(cached)
    return out


def attach_outcomes(oos: pl.DataFrame, dsn: str, cfg_label: object) -> pl.DataFrame:
    """把標籤的 0 拆成「先觸下緣」與「兩邊都沒碰」。

    標籤本身是二元的，分不出這兩者；而診斷 B 登記的統計量是「上緣比例 − **下緣**比例」。
    這裡用與 `triple_barrier_labels` 逐字相同的邊界重算一次三態，只多輸出 `outcome`。
    """
    horizon = int(getattr(cfg_label, "horizon_days", 0) or 20)
    k_up = float(getattr(cfg_label.label, "barrier_up", 1.0) or 1.0)
    k_dn = float(getattr(cfg_label.label, "barrier_down", 1.0) or 1.0)
    window = int(getattr(cfg_label.label, "vol_window", 20) or 20)
    scaled = bool(getattr(cfg_label.label, "vol_scaled", False))
    lo = oos["date"].min()
    hi = oos["date"].max()
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT date, stock_id, open, close FROM serving.prices_daily "
            "WHERE stock_id ~ '^[1-9][0-9]{3}$' AND date >= %s - 400 AND date <= %s + 90 "
            "AND close IS NOT NULL AND open IS NOT NULL",
            (lo, hi),
        ).fetchall()
    px = pl.DataFrame(
        rows,
        schema={"date": pl.Date, "stock_id": pl.String, "open": pl.Float64, "close": pl.Float64},
        orient="row",
    ).sort("stock_id", "date")
    if scaled:
        daily = (pl.col("close") / pl.col("close").shift(1) - 1.0).over("stock_id", order_by="date")
        sigma = daily.rolling_std(window, min_samples=window).over("stock_id", order_by="date") * (
            horizon**0.5
        )
        px = px.with_columns(_s=pl.when(sigma > 0).then(sigma).otherwise(None))
    else:
        px = px.with_columns(_s=pl.lit(1.0))
    entry = pl.col("open").shift(-1).over("stock_id", order_by="date")
    up = pl.lit(None, dtype=pl.Int32)
    dn = pl.lit(None, dtype=pl.Int32)
    for k in range(horizon, 0, -1):
        r = pl.col("close").shift(-k).over("stock_id", order_by="date") / entry - 1.0
        up = pl.when(r >= pl.col("_s") * k_up).then(pl.lit(k, dtype=pl.Int32)).otherwise(up)
        dn = pl.when(r <= -pl.col("_s") * k_dn).then(pl.lit(k, dtype=pl.Int32)).otherwise(dn)
    px = px.with_columns(_up=up, _dn=dn).with_columns(
        outcome=pl.when(
            pl.col("_up").is_not_null()
            & (pl.col("_dn").is_null() | (pl.col("_up") < pl.col("_dn")))
        )
        .then(pl.lit("up"))
        .when(pl.col("_dn").is_not_null())
        .then(pl.lit("down"))
        .otherwise(pl.lit("none"))
    )
    return oos.join(px.select("date", "stock_id", "outcome"), on=["date", "stock_id"], how="left")


def _monthly_base(oos: pl.DataFrame) -> pl.Series:
    """每月「全市場先觸上緣」的比例。"""
    return (
        oos.filter(pl.col("label").is_not_null())
        .with_columns(m=pl.col("date").dt.strftime("%Y-%m"))
        .group_by("m")
        .agg(rate=pl.col("label").mean())
        .sort("m")["rate"]
    )


def _welch(a: list[float], mu: float = 0.0) -> float | None:
    """單樣本 t：月平均值是否顯著大於 mu。"""
    if len(a) < 2:
        return None
    m = sum(a) / len(a)
    v = sum((x - m) ** 2 for x in a) / (len(a) - 1)
    se = math.sqrt(v / len(a))
    return (m - mu) / se if se > 0 else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--baseline", required=True, help="第二輪 SB 的模型目錄（診斷 A 的對照）")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        raise SystemExit("缺 TWQUANT_PG_DSN")

    cal = _calibrated(Path(args.model), args.config, dsn)
    last = cal["date"].max()
    if last is None or last > DEV_END:
        raise SystemExit(f"樣本外到 {last}，越過保留期邊界，拒絕診斷")
    base = pl.read_parquet(Path(args.baseline) / "oos_calibrated.parquet")

    judged = cal.filter(pl.col("p_cal").is_not_null() & pl.col("label").is_not_null())
    _cfg = _load_ml(args.config)
    judged = attach_outcomes(judged, dsn, _cfg)
    n_days, n_months = judged["date"].n_unique(), judged["date"].dt.strftime("%Y-%m").n_unique()
    print(
        f"樣本外 {judged.height:,} 列、{n_days} 個交易日、{n_months} 個月"
        f"　整體先觸上緣比例 {judged['label'].mean():.1%}\n"
    )

    new_m, old_m = _monthly_base(cal), _monthly_base(base)
    sd_new = float(new_m.std() or 0.0)
    sd_old = float(old_m.std() or 0.0)
    ok_a = sd_new < sd_old * MAX_BASE_VOL_RATIO
    print("【A】標籤基礎率的月間波動（須 < 基準的一半）")
    print(f"  SB3 月間標準差 {sd_new:.3f}（平均 {float(new_m.mean() or 0):.1%}）")
    print(f"  SB  月間標準差 {sd_old:.3f}（平均 {float(old_m.mean() or 0):.1%}）")
    print(
        f"  比值 {sd_new / sd_old if sd_old else float('nan'):.2f} → {'通過' if ok_a else '未通過'}\n"
    )

    ranked = judged.with_columns(rk=pl.col("p_cal").rank("ordinal", descending=True).over("date"))
    top = ranked.filter(pl.col("rk") <= TOP_N)
    if "outcome" not in top.columns:
        raise SystemExit(
            "診斷 B 需要三態結果欄 `outcome`（up／down／none）。"
            "**不得用 1 − label 當「先觸下緣」**：label=0 同時包含「先觸下緣」與「兩邊都沒碰」，"
            "2026-09-21 就因此把 +10.0pp 的優勢算成 −21.5pp、誤判未通過。"
        )
    monthly = (
        top.with_columns(m=pl.col("date").dt.strftime("%Y-%m"))
        .group_by("m")
        .agg(
            edge=(pl.col("outcome") == "up").mean() - (pl.col("outcome") == "down").mean(),
        )
        .sort("m")
    )
    edges = monthly["edge"].to_list()
    t = _welch(edges)
    ok_b = t is not None and t >= MIN_T and sum(edges) / len(edges) > 0
    up_share = float((top["outcome"] == "up").mean())
    dn_share = float((top["outcome"] == "down").mean())
    print(f"【B】模型挑得出方向（每日前 {TOP_N} 名，須 t ≥ {MIN_T}）")
    print(
        f"  先觸上緣 {up_share:.1%}　先觸下緣 {dn_share:.1%}　都沒碰 {1 - up_share - dn_share:.1%}"
    )
    print(
        f"  月平均優勢 {sum(edges) / len(edges):+.1%}（{len(edges)} 個月）　t = {t if t is None else f'{t:+.2f}'}"
    )
    print(f"  → {'通過' if ok_b else '未通過'}\n")

    hit = judged.filter(pl.col("p_cal") >= ENTRY_THRESHOLD)
    months = hit["date"].dt.strftime("%Y-%m").n_unique() if hit.height else 0
    share = months / n_months
    ok_c = share >= MIN_MONTH_SHARE
    print(
        f"【C】平常日子也有訊號（機率 ≥ {ENTRY_THRESHOLD:.0%} 須分布 ≥ {MIN_MONTH_SHARE:.0%} 的月份）"
    )
    print(
        f"  {hit.height:,} 列、{hit['date'].n_unique() if hit.height else 0} 個交易日、"
        f"{months}/{n_months} 個月（{share:.0%}）　實際命中率 "
        f"{hit['label'].mean():.1%}"
        if hit.height
        else "  沒有任何列過門檻"
    )
    print(f"  → {'通過' if ok_c else '未通過'}\n")

    if ok_a and ok_b and ok_c:
        print("**三關皆過**：SB3 可以進 H／L 回測（D-127 §五）。")
    else:
        failed = [n for n, ok in (("A", ok_a), ("B", ok_b), ("C", ok_c)) if not ok]
        print(
            f"**未通過：診斷 {'、'.join(failed)}。** 依 D-127 §七，不進回測；"
            "不得調 k 或換特徵後重跑，須另立裁決。"
        )


if __name__ == "__main__":
    main()
