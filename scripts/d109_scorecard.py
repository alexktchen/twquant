"""D-109 五條門檻的**診斷**計分：把既有結果包逐條對一遍，看卡在哪一條。

    uv run --all-groups python scripts/d109_scorecard.py \
        --bundles D:/project/twquant/data/tracks_vault_nodiv

**這不是裁決。** D-109 的裁決只能在第二保留期（2026-09-09 起、最早 2027-03-01 開）做一次；
本腳本只接受結束日 ≤ 2026-08-31 的結果包（D-109 §四.3），超過即拒絕。
門檻數字逐字取自 D-109 §三，事前登記、不得修改。基準 0050 取價格報酬（與 nodiv 結果包同口徑）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

import numpy as np
import psycopg

DEV_END = dt.date(2026, 8, 31)  # D-109 §四.3：分析視窗上界
MAX_MDD = 0.25  # ①
MIN_CALMAR = 0.5  # ②
MAX_CORR = 0.7  # ③
MIN_EXPOSURE = 0.5  # ④
STRESS_DD = 0.10  # ⑤ 指數回撤達此幅度才算壓力段
MAX_STRESS_MULT = 1.0  # ⑤
BENCH = "0050"


def _drawdown_window(x: np.ndarray) -> tuple[int, int, float]:
    dd = x / np.maximum.accumulate(x) - 1.0
    trough = int(dd.argmin())
    return int(np.argmax(x[: trough + 1])), trough, float(dd[trough])


def _bench(dsn: str, start: str, end: str) -> dict[str, float]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT date, close FROM serving.prices_daily "
            "WHERE stock_id = %s AND date BETWEEN %s AND %s ORDER BY date",
            (BENCH, start, end),
        ).fetchall()
    return {d.isoformat(): float(px) for d, px in rows}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundles", required=True, nargs="+", help="結果包 JSON 或其所在目錄")
    args = ap.parse_args()
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        raise SystemExit("缺 TWQUANT_PG_DSN（基準 0050 取自 PG）")
    paths: list[Path] = []
    for raw in args.bundles:
        p = Path(raw)
        paths.extend(sorted(p.glob("*.json")) if p.is_dir() else [p])

    print(
        f"{'code':<13}{'報酬':>7}{'①MDD':>8}{'②Calmar':>9}{'③相關':>7}{'④曝險':>7}{'⑤放大':>7}  ①②③④⑤"
    )
    for path in paths:
        b = json.loads(path.read_text(encoding="utf-8"))
        if dt.date.fromisoformat(b["end"]) > DEV_END:
            raise SystemExit(f"{path.name} 結束於 {b['end']}，越過 D-109 的保留期邊界，拒絕計分")
        dates = [e[0] for e in b["equity"]]
        bench = _bench(dsn, dates[0], dates[-1])
        missing = [d for d in dates if d not in bench]
        if missing:
            raise SystemExit(f"{BENCH} 缺 {len(missing)} 個交易日的行情（首筆 {missing[0]}）")
        eq = np.array([e[1] for e in b["equity"]], dtype=float)
        pos = np.array([e[3] for e in b["equity"]], dtype=float)
        bx = np.array([bench[d] for d in dates])
        total = eq[-1] / float(b["initial_capital"]) - 1.0
        ann = (1.0 + total) ** (252.0 / len(eq)) - 1.0
        mdd = _drawdown_window(eq)[2]
        calmar = ann / abs(mdd) if mdd < 0 else float("inf")
        corr = float(np.corrcoef(np.diff(eq) / eq[:-1], np.diff(bx) / bx[:-1])[0, 1])
        expo = float((pos / eq).mean())
        peak, trough, bdd = _drawdown_window(bx)
        if bdd <= -STRESS_DD:
            seg = eq[peak : trough + 1]
            mult: float | None = float(seg.min() / seg[0] - 1.0) / bdd
        else:
            mult = None  # 未受檢：整體最多「未被推翻」
        marks = [
            mdd >= -MAX_MDD,
            calmar >= MIN_CALMAR and ann > 0,
            corr <= MAX_CORR,
            expo >= MIN_EXPOSURE,
            None if mult is None else mult <= MAX_STRESS_MULT,
        ]
        shown = "".join("－" if m is None else ("✓" if m else "✗") for m in marks)
        mult_s = "未受檢" if mult is None else f"{mult:.2f}×"
        print(
            f"{path.name.split('_')[0]:<13}{total:>8.1%}{mdd:>8.1%}{calmar:>9.2f}"
            f"{corr:>8.2f}{expo:>8.0%}{mult_s:>8}  {shown}"
        )


if __name__ == "__main__":
    main()
