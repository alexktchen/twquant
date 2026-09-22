"""D-131 §四：個股下跌機率模型 SD 的三條診斷——**全過才准用**。

    uv run --all-groups python scripts/sd_diagnostics.py \
        --model <SD 模型目錄> --config configs/ml_sd_down10.yaml

1. **校準**：walk-forward isotonic 校準後，十組的預測與實際差 ≤ 10 個百分點（樣本 < 1,000 只列不判）。
2. **贏過 `price.vol_20d` 單因子**（核心關卡）：以測試窗為單位配對比較 AUC，
   差 ≥ 0.02 且 t ≥ 2。沒贏 → 模型只是把波動度換個說法，直接用波動度排序即可，本線收線。
3. **當濾網真的有用**：對排序模型每日前 7%（R5b 的候選池）剔除「下跌機率最高的一成」後，
   該池子實際跌 10% 的比例須下降 ≥ 5 個百分點，以月為單位 t ≥ 2。

門檻逐字取自 D-131，事前登記、不得事後修改。拒收保留期資料。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

import numpy as np
import polars as pl
import psycopg

from twquant.cli import _load_ml
from twquant.ml.calibration import walk_forward_calibrate
from twquant.ml.splits import walk_forward_splits

DEV_END = dt.date(2026, 8, 31)
N_BINS, MAX_GAP, MIN_BIN_N = 10, 0.10, 1_000
CAL_EMBARGO, CAL_MIN_WINDOWS = 21, 4
MIN_AUC_EDGE, MIN_T = 0.02, 2.0  # D-131 §四.2
RANK_MODEL = "lgbm_lw_net_h20_20260225_83890036"
TOP_PCT, DROP_DECILE, MIN_FILTER_GAIN = 0.93, 0.90, 0.05  # D-131 §四.3


def _auc(score: np.ndarray, label: np.ndarray) -> float:
    """Mann-Whitney U 的等價形式；兩類任一為空回 nan。"""
    pos, neg = label == 1.0, label == 0.0
    n_p, n_n = int(pos.sum()), int(neg.sum())
    if n_p == 0 or n_n == 0:
        return float("nan")
    order = np.argsort(score, kind="stable")
    ranks = np.empty(len(score), dtype=float)
    ranks[order] = np.arange(1, len(score) + 1)
    # 同分取平均名次，否則 AUC 會被排序的任意性影響
    _, inv, counts = np.unique(score, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, weights=ranks)
    ranks = (sums / counts)[inv]
    return float((ranks[pos].sum() - n_p * (n_p + 1) / 2) / (n_p * n_n))


def _t_stat(xs: list[float], mu: float = 0.0) -> float | None:
    xs = [x for x in xs if not math.isnan(x)]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    v = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    se = math.sqrt(v / len(xs))
    return (m - mu) / se if se > 0 else None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        raise SystemExit("缺 TWQUANT_PG_DSN")
    mdir = Path(args.model)
    cfg = _load_ml(args.config)
    meta = json.loads((mdir / "meta.json").read_text(encoding="utf-8"))

    with psycopg.connect(dsn) as conn:
        days = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT date FROM serving.prices_daily WHERE date BETWEEN %s AND %s "
                "AND stock_id ~ '^[1-9][0-9]{3}$' ORDER BY date",
                (meta["data_from"], meta["data_to"]),
            ).fetchall()
        ]
    splits = walk_forward_splits(
        days,
        train_min_days=cfg.split.train_min_days,
        test_days=cfg.split.test_days,
        embargo_days=cfg.split.embargo_days,
        vault_days=cfg.split.vault_days,
    )
    windows = [(s.test_start, s.test_end) for s in splits]
    oos = pl.read_parquet(mdir / "oos_scores.parquet")
    if (last := oos["date"].max()) is None or last > DEV_END:
        raise SystemExit(f"樣本外到 {last}，越過保留期邊界，拒絕診斷")
    cal = walk_forward_calibrate(
        oos, windows, days, embargo=CAL_EMBARGO, min_windows=CAL_MIN_WINDOWS
    )
    cal.write_parquet(mdir / "oos_calibrated.parquet")
    judged = cal.filter(pl.col("p_cal").is_not_null() & pl.col("label").is_not_null())
    n_months = judged["date"].dt.strftime("%Y-%m").n_unique()
    print(
        f"樣本外 {judged.height:,} 列、{judged['date'].n_unique()} 個交易日、{n_months} 個月"
        f"　整體「20 日內跌 10%」比例 {judged['label'].mean():.1%}\n"
    )

    # 【一】校準
    table = (
        judged.with_columns(
            _b=(pl.col("p_cal") * N_BINS).floor().clip(0, N_BINS - 1).cast(pl.Int32)
        )
        .group_by("_b")
        .agg(n=pl.len(), predicted=pl.col("p_cal").mean(), actual=pl.col("label").mean())
        .sort("_b")
        .with_columns(gap=(pl.col("predicted") - pl.col("actual")).abs())
    )
    ok_a = True
    print(f"【一】校準　{'機率區間':<10}{'樣本數':>10}{'預測':>8}{'實際':>8}{'差距':>8}")
    for r in table.to_dicts():
        counted = r["n"] >= MIN_BIN_N
        bad = counted and r["gap"] > MAX_GAP
        ok_a = ok_a and not bad
        lo = r["_b"] / N_BINS
        print(
            f"        {lo:.1f}~{lo + 0.1:.1f}  {r['n']:>10,}{r['predicted']:>8.1%}"
            f"{r['actual']:>8.1%}{r['gap']:>8.1%}"
            + ("  ✗" if bad else "" if counted else "  （不判）")
        )
    print(f"  → {'通過' if ok_a else '未通過'}\n")

    # 【二】贏過 vol_20d
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT date, stock_id, z FROM serving.factor_values "
            "WHERE factor = 'price.vol_20d' AND date BETWEEN %s AND %s",
            (judged["date"].min(), judged["date"].max()),
        ).fetchall()
    vol = pl.DataFrame(
        rows, schema={"date": pl.Date, "stock_id": pl.String, "vol": pl.Float64}, orient="row"
    )
    paired = judged.join(vol, on=["date", "stock_id"], how="inner").drop_nulls("vol")
    edges: list[float] = []
    for start, end in windows:
        w = paired.filter(pl.col("date").is_between(start, end))
        if w.height < 500:
            continue
        y = w["label"].to_numpy()
        a_m = _auc(w["p_cal"].to_numpy(), y)
        a_v = _auc(w["vol"].to_numpy(), y)
        if not (math.isnan(a_m) or math.isnan(a_v)):
            edges.append(a_m - a_v)
    t_b = _t_stat(edges)
    mean_edge = sum(edges) / len(edges) if edges else float("nan")
    ok_b = bool(edges) and mean_edge >= MIN_AUC_EDGE and t_b is not None and t_b >= MIN_T
    print(f"【二】贏過 price.vol_20d 單因子（須差 ≥ {MIN_AUC_EDGE}、t ≥ {MIN_T}）")
    print(
        f"  {len(edges)} 個測試窗　AUC 差平均 {mean_edge:+.4f}　t = {t_b if t_b is None else f'{t_b:+.2f}'}"
    )
    print(
        f"  正窗數 {sum(1 for e in edges if e > 0)}/{len(edges)}　→ {'通過' if ok_b else '未通過'}\n"
    )

    # 【三】當濾網有用
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT date, stock_id, rank_pct FROM serving.ml_scores "
            "WHERE model_id = %s AND date BETWEEN %s AND %s",
            (RANK_MODEL, judged["date"].min(), judged["date"].max()),
        ).fetchall()
    rank = pl.DataFrame(
        rows, schema={"date": pl.Date, "stock_id": pl.String, "rank_pct": pl.Float64}, orient="row"
    )
    pool = judged.join(rank, on=["date", "stock_id"], how="inner").filter(
        pl.col("rank_pct") >= TOP_PCT
    )
    pool = pool.with_columns(
        _dp=pl.col("p_cal").rank("ordinal", descending=False).over("date") / pl.len().over("date")
    )
    kept = pool.filter(pl.col("_dp") <= DROP_DECILE)
    monthly = (
        pool.with_columns(m=pl.col("date").dt.strftime("%Y-%m"))
        .group_by("m")
        .agg(before=pl.col("label").mean())
        .join(
            kept.with_columns(m=pl.col("date").dt.strftime("%Y-%m"))
            .group_by("m")
            .agg(after=pl.col("label").mean()),
            on="m",
        )
        .with_columns(gain=pl.col("before") - pl.col("after"))
    )
    gains = monthly["gain"].to_list()
    t_c = _t_stat(gains)
    mean_gain = sum(gains) / len(gains) if gains else float("nan")
    ok_c = bool(gains) and mean_gain >= MIN_FILTER_GAIN and t_c is not None and t_c >= MIN_T
    print(f"【三】當濾網有用（剔除下跌機率最高的一成，跌 10% 比例須降 ≥ {MIN_FILTER_GAIN:.0%}）")
    print(
        f"  候選池 {pool.height:,} 列　剔除前 {pool['label'].mean():.1%} → 剔除後 {kept['label'].mean():.1%}"
    )
    print(
        f"  月平均改善 {mean_gain:+.1%}（{len(gains)} 個月）　t = {t_c if t_c is None else f'{t_c:+.2f}'}"
    )
    print(f"  → {'通過' if ok_c else '未通過'}\n")

    if ok_a and ok_b and ok_c:
        print("**三關皆過**：SD 可以掛進 R5b 當濾網（D-131 §五 → R6）。")
    else:
        bad_names = [n for n, ok in (("一", ok_a), ("二", ok_b), ("三", ok_c)) if not ok]
        print(
            f"**未通過：診斷 {'、'.join(bad_names)}。** 依 D-131 §七，不得調視野、改門檻、換特徵後重跑。"
            + (
                "\n  診斷二未過＝模型只是把波動度換個說法，直接用 vol_20d 排序即可，本線收線。"
                if not ok_b
                else ""
            )
        )


if __name__ == "__main__":
    main()
