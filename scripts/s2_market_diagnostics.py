"""D-124 §三／§四：市場狀態閘門（b1）與大跌預警（b2）的診斷。**通過才准掛進回測。**

    uv run --all-groups python scripts/s2_market_diagnostics.py --model <帶進回測的買進模型目錄>

b1：緩跌日過門檻者的實際成功率須**低於**其餘日子（以月為單位 t ≥ 2），且恐慌日不得低於一般日。
b2：樣本外事件至少 2/3 有「事件期間過半日子預警亮著」，且非事件日誤報率 ≤ 20%；
    另須贏過同樣亮燈比例的笨規則「TAIEX 在 20 日線下」。
**12 年只有 9 個大跌事件——b2 通過也只代表「沒有被推翻」。**
門檻與數字全部取自 D-124，事前登記、不得事後修改。不看任何報酬。
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import sys
from pathlib import Path

import polars as pl
import psycopg

from twquant.ml.market_warning import (
    WarningConfig,
    event_spans,
    walk_forward_warning,
    warning_features,
)
from twquant.ml.regime import CALM, GRIND, PANIC, market_regime

DEV_END = dt.date(2026, 8, 31)
ENTRY_THRESHOLD = 0.70  # D-124 §七 H
MIN_T = 2.0
REGIME = {"ma_window": 60, "vol_window": 20, "vol_lookback": 250, "vol_pctl": 0.90}  # D-124 §三
WARN = WarningConfig(  # D-124 §四
    horizon=20,
    drop=0.10,
    long_window=60,
    short_window=20,
    change_window=10,
    min_events=3,
    refit_sessions=244,
    alarm_pctl=0.85,
    l2=1e-3,
    max_iter=100,
)
MIN_EVENT_RECALL = 2 / 3
MAX_FALSE_ALARM = 0.20
BASELINE_MA = 20


def _read(dsn: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    with psycopg.connect(dsn) as conn:
        idx = conn.execute(
            "SELECT date, close FROM serving.index_daily WHERE index_id='TAIEX' "
            "AND close IS NOT NULL AND date <= %s ORDER BY date",
            (DEV_END,),
        ).fetchall()
        px = conn.execute(
            "SELECT date, stock_id, close FROM serving.prices_daily "
            "WHERE stock_id ~ '^[1-9][0-9]{3}$' AND close IS NOT NULL AND date <= %s",
            (DEV_END,),
        ).fetchall()
    return (
        pl.DataFrame(idx, schema={"date": pl.Date, "close": pl.Float64}, orient="row"),
        pl.DataFrame(
            px, schema={"date": pl.Date, "stock_id": pl.String, "close": pl.Float64}, orient="row"
        ),
    )


def _welch(a: list[float], b: list[float]) -> float | None:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (mb - ma) / se if se > 0 else None  # 正值＝b（其餘日）高於 a（緩跌日）


def gate_diagnostic(index: pl.DataFrame, model_dir: Path) -> bool:
    regime = market_regime(index, **REGIME)
    share = regime.drop_nulls().group_by("regime").len().sort("regime")
    total = share["len"].sum()
    print("【b1】市場狀態閘門")
    print(
        "  各狀態占比："
        + "、".join(f"{r['regime']} {r['len'] / total:.0%}" for r in share.to_dicts())
    )
    cal = pl.read_parquet(model_dir / "oos_calibrated.parquet").filter(
        pl.col("p_cal").is_not_null() & pl.col("label").is_not_null()
    )
    cal = cal.join(regime, on="date", how="left")
    for name, frame in (
        ("全部樣本（基礎發生率）", cal),
        (
            f"過門檻者（機率 ≥ {ENTRY_THRESHOLD:.0%}）",
            cal.filter(pl.col("p_cal") >= ENTRY_THRESHOLD),
        ),
    ):
        print(f"  {name}：")
        for r in (
            frame.drop_nulls("regime")
            .group_by("regime")
            .agg(
                n=pl.len(),
                rate=pl.col("label").mean(),
                months=pl.col("date").dt.strftime("%Y-%m").n_unique(),
            )
            .sort("regime")
            .to_dicts()
        ):
            print(
                f"    {r['regime']:<6} {r['n']:>9,} 列、{r['months']:>3} 個月　實際成功率 {r['rate']:.1%}"
            )
    picks = cal.filter(pl.col("p_cal") >= ENTRY_THRESHOLD).drop_nulls("regime")
    monthly = (
        picks.with_columns(_m=pl.col("date").dt.strftime("%Y-%m"), _g=pl.col("regime") == GRIND)
        .group_by("_m", "_g")
        .agg(rate=pl.col("label").mean())
    )
    grind = monthly.filter(pl.col("_g"))["rate"].to_list()
    rest = monthly.filter(~pl.col("_g"))["rate"].to_list()
    t = _welch(grind, rest)
    panic = picks.filter(pl.col("regime") == PANIC)["label"].mean()
    calm = picks.filter(pl.col("regime") == CALM)["label"].mean()
    if t is None:
        print(
            f"  月區塊：緩跌 {len(grind)} 個月、其餘 {len(rest)} 個月——**樣本不足，無法判定** → 依登記視為未通過"
        )
        return False
    ok = t >= MIN_T and (panic is None or calm is None or float(panic) >= float(calm))
    print(
        f"  月區塊 t = {t:+.2f}（須 ≥ {MIN_T}）；恐慌日 {panic if panic is None else f'{panic:.1%}'} vs 一般日 {calm if calm is None else f'{calm:.1%}'}"
    )
    print(f"  → {'通過' if ok else '未通過'}")
    return ok


def warning_diagnostic(index: pl.DataFrame, prices: pl.DataFrame) -> bool:
    print("\n【b2】大跌預警　（硬限制：事件太少，通過也只代表「未被推翻」）")
    feats = warning_features(index, prices, WARN)
    out = walk_forward_warning(feats, WARN).join(feats.select("date", "close", "label"), on="date")
    judged = out.filter(pl.col("alarm").is_not_null() & pl.col("label").is_not_null())
    if judged.is_empty():
        print("  沒有可判的樣本外日子 → 未通過")
        return False
    dates, labels, alarms = (
        judged["date"].to_list(),
        judged["label"].to_list(),
        judged["alarm"].to_list(),
    )
    ma = index.sort("date").with_columns(
        _b=pl.col("close") < pl.col("close").rolling_mean(BASELINE_MA, min_samples=BASELINE_MA)
    )
    base = dict(zip(ma["date"].to_list(), ma["_b"].to_list(), strict=True))
    spans = event_spans(dates, labels, WARN.horizon)
    print(f"  樣本外 {dates[0]} ~ {dates[-1]}（{len(dates)} 日）、獨立事件 {len(spans)} 個")
    caught = caught_base = 0
    for a, b in spans:
        cover = sum(1 for i in range(a, b + 1) if alarms[i]) / (b - a + 1)
        cover_b = sum(1 for i in range(a, b + 1) if base.get(dates[i])) / (b - a + 1)
        # 事件開始前 10 個交易日內預警有沒有亮過（資訊用，不進判定）
        early = any(alarms[i] for i in range(max(0, a - 10), a))
        caught += cover >= 0.5
        caught_base += cover_b >= 0.5
        print(
            f"    {dates[a]} ~ {dates[b]}：預警涵蓋 {cover:.0%}（笨規則 {cover_b:.0%}）　事件前 10 日{'有' if early else '沒有'}亮過"
        )
    normal = [i for i, y in enumerate(labels) if y == 0.0]
    fa = sum(1 for i in normal if alarms[i]) / len(normal)
    fa_base = sum(1 for i in normal if base.get(dates[i])) / len(normal)
    recall, recall_b = caught / len(spans), caught_base / len(spans)
    print(
        f"  事件召回 {caught}/{len(spans)}＝{recall:.0%}（須 ≥ {MIN_EVENT_RECALL:.0%}）；誤報率 {fa:.1%}（須 ≤ {MAX_FALSE_ALARM:.0%}）"
    )
    print(f"  對照「TAIEX 在 {BASELINE_MA} 日線下」：召回 {recall_b:.0%}、誤報率 {fa_base:.1%}")
    beats = (recall, -fa) > (recall_b, -fa_base)
    ok = recall >= MIN_EVENT_RECALL and fa <= MAX_FALSE_ALARM and beats
    print(
        f"  → {'未被推翻（可掛進回測）' if ok else '未通過'}"
        + ("" if beats else "　［沒有贏過笨規則］")
    )
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--model", required=True, help="帶進回測的買進模型目錄（含 oos_calibrated.parquet）"
    )
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        raise SystemExit("缺 TWQUANT_PG_DSN")
    index, prices = _read(dsn)
    ok1 = gate_diagnostic(index, Path(args.model))
    ok2 = warning_diagnostic(index, prices)
    print(
        f"\n**結論**：市場狀態閘門{'可用' if ok1 else '不用'}；大跌預警{'可用（未被推翻）' if ok2 else '不用'}。"
    )


if __name__ == "__main__":
    main()
