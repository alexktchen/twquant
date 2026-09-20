"""D-122 §〇／§一／§二：買進模型第二輪診斷——校準、門檻、SB／SB2 二選一。

    uv run --all-groups python scripts/s2_buy_diagnostics.py \
        --model SB=<模型目錄> --config SB=configs/ml_sb_up20.yaml \
        --model SB2=<模型目錄> --config SB2=configs/ml_sb2_up10_dn5.yaml

對每個模型：walk-forward isotonic 校準 → 校準診斷（十組、差距 ≤ 10pp、樣本 < 1,000 的組只列不判）
→ 校準後機率 ≥ 70%（H）與 ≥ 80%（L）的列數與**實際成功率**。
然後依 D-122 §〇 寫死的規則二選一：過 70% 門檻的列數較多者，且其實際成功率 ≥ 70%、列數 ≥ 1,000。
每個模型的校準後分數另存為 `<模型目錄>/oos_calibrated.parquet`（供回測使用）。

門檻全部事前登記、不得事後修改。不看任何報酬。需要 TWQUANT_PG_DSN（只用來取交易日曆）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import polars as pl
import psycopg

from twquant.cli import _load_ml
from twquant.ml.calibration import walk_forward_calibrate
from twquant.ml.splits import walk_forward_splits

DEV_END = dt.date(2026, 8, 31)
N_BINS = 10
MAX_GAP = 0.10
MIN_BIN_N = 1_000
CAL_EMBARGO = 21  # D-122 §一
CAL_MIN_WINDOWS = 4  # D-122 §一
THRESHOLDS = {"H": 0.70, "L": 0.80}  # D-122 §七
SELECT_MIN_ROWS = 1_000  # D-122 §〇
SELECT_MIN_SUCCESS = 0.70  # D-122 §〇


def _sessions(dsn: str, start: str, end: str) -> list[dt.date]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT DISTINCT date FROM serving.prices_daily WHERE date BETWEEN %s AND %s "
            "AND stock_id ~ '^[1-9][0-9]{3}$' ORDER BY date",
            (start, end),
        ).fetchall()
    return [r[0] for r in rows]


def _one(name: str, model_dir: Path, config: str, dsn: str) -> dict[str, object]:
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
    windows = [(s.test_start, s.test_end) for s in splits]
    oos = pl.read_parquet(model_dir / "oos_scores.parquet")
    last = oos["date"].max()
    if last is None or last > DEV_END:
        raise SystemExit(f"{name}：樣本外預測到 {last}，越過保留期邊界，拒絕診斷")
    cal = walk_forward_calibrate(
        oos, windows, days, embargo=CAL_EMBARGO, min_windows=CAL_MIN_WINDOWS
    )
    cal.write_parquet(model_dir / "oos_calibrated.parquet")
    judged = cal.filter(pl.col("p_cal").is_not_null() & pl.col("label").is_not_null())
    base = float(judged["label"].mean() or 0.0)
    print(f"\n══ {name}（{meta['model_id']}）")
    print(
        f"標籤：+{meta['label']['barrier_up']:.0%}／−{meta['label']['barrier_down']:.0%}"
        f"　校準後可判 {judged.height:,} 列（{judged['date'].min()} ~ {judged['date'].max()}）"
        f"　整體發生率 {base:.1%}"
    )
    table = (
        judged.with_columns(
            _bin=(pl.col("p_cal") * N_BINS).floor().clip(0, N_BINS - 1).cast(pl.Int32)
        )
        .group_by("_bin")
        .agg(n=pl.len(), predicted=pl.col("p_cal").mean(), actual=pl.col("label").mean())
        .sort("_bin")
        .with_columns(gap=(pl.col("predicted") - pl.col("actual")).abs())
    )
    print(f"  {'校準後機率':<12}{'樣本數':>10}{'預測':>9}{'實際':>9}{'差距':>9}")
    ok_cal = True
    for r in table.to_dicts():
        lo = r["_bin"] / N_BINS
        counted = r["n"] >= MIN_BIN_N
        bad = counted and r["gap"] > MAX_GAP
        ok_cal = ok_cal and not bad
        mark = "  ✗" if bad else ("" if counted else "  （樣本不足，不判）")
        print(
            f"  {lo:.1f}~{lo + 0.1:.1f}    {r['n']:>10,}{r['predicted']:>9.1%}{r['actual']:>9.1%}"
            f"{r['gap']:>9.1%}{mark}"
        )
    print(f"  校準 → {'通過' if ok_cal else '未通過'}")
    out: dict[str, object] = {"name": name, "calibrated": ok_cal}
    n_days = judged["date"].n_unique()
    for tag, thr in THRESHOLDS.items():
        hit = judged.filter(pl.col("p_cal") >= thr)
        succ = float(hit["label"].mean()) if hit.height else float("nan")
        months = hit["date"].dt.strftime("%Y-%m").n_unique() if hit.height else 0
        print(
            f"  {tag}（機率 ≥ {thr:.0%}）：{hit.height:,} 列（每日 {hit.height / n_days:.2f} 檔、"
            f"分布在 {months} 個月）　實際成功率 {succ:.1%}"
        )
        out[f"n_{tag}"] = hit.height
        out[f"succ_{tag}"] = succ
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", action="append", required=True, help="NAME=模型目錄")
    ap.add_argument("--config", action="append", required=True, help="NAME=ML YAML")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        raise SystemExit("缺 TWQUANT_PG_DSN（取交易日曆）")
    models = dict(m.split("=", 1) for m in args.model)
    configs = dict(c.split("=", 1) for c in args.config)
    results = [_one(n, Path(d), configs[n], dsn) for n, d in models.items()]

    print("\n══ 二選一（D-122 §〇：過 70% 門檻列數較多者；須校準通過、成功率 ≥ 70%、列數 ≥ 1,000）")
    eligible = [
        r
        for r in results
        if r["calibrated"]
        and int(r["n_H"]) >= SELECT_MIN_ROWS  # type: ignore[call-overload]
        and float(r["succ_H"]) >= SELECT_MIN_SUCCESS  # type: ignore[arg-type]
    ]
    for r in results:
        why = []
        if not r["calibrated"]:
            why.append("校準未過")
        if int(r["n_H"]) < SELECT_MIN_ROWS:  # type: ignore[call-overload]
            why.append(f"過門檻僅 {r['n_H']} 列")
        if not float(r["succ_H"]) >= SELECT_MIN_SUCCESS:  # type: ignore[arg-type]
            why.append(f"成功率 {float(r['succ_H']):.1%} < 70%")  # type: ignore[arg-type]
        print(f"  {r['name']}：{'合格' if not why else '不合格（' + '、'.join(why) + '）'}")
    if not eligible:
        print("**沒有合格的買進模型。** 依 D-122 §〇，買進這條線停下來，不進回測。")
        return
    winner = max(eligible, key=lambda r: int(r["n_H"]))  # type: ignore[call-overload]
    print(f"**帶進回測：{winner['name']}**")


if __name__ == "__main__":
    main()
