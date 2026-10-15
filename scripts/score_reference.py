"""歷史同區間參考（D-179／T-359）：本機算一次，寫 serving.score_reference（可同時寫雲端）＋ markdown 報告。

讀本機 PG 的 ml_scores（A20 walk-forward 樣本外分數）、行情、除權息，與 --data-dir 的回測結果包（大盤精選）；
定義寫死在 configs/score_reference.yaml（登記後不改）。行情只讀到 dev_end（2026-08-31），第二保留期一律不碰。

    TWQUANT_PG_DSN=postgresql://twquant:twquant@localhost:5433/twquant \\
    TWQUANT_SUPABASE_DSN=... \\
    uv run --all-groups python scripts/score_reference.py \\
        --data-dir D:/project/twquant/data --out docs/reports/d179-score-reference.md --cloud
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

import polars as pl

from twquant.backtest.costs import CostConfig
from twquant.backtest.engine import dividends_by_date, price_panel, splits_by_date
from twquant.config import ServingConfig, load_config
from twquant.conventions import now_tw
from twquant.data import calendar as cal
from twquant.data.serving import store
from twquant.data.serving.pg_view import PgTableSource
from twquant.data.serving.score_reference_store import write_reference
from twquant.reporting.pick_board import PickBoardConfig
from twquant.reporting.score_reference import (
    RefRow,
    ScoreReferenceConfig,
    bundle_line_rows,
    render,
    score_line_rows,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIGS = ROOT / "configs"
_UNIVERSE = r"^[1-9][0-9]{3}$"


def _say(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _scores(conn: object, schema: str, model_id: str, start: dt.date, end: dt.date) -> pl.DataFrame:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            f'SELECT date, stock_id, score FROM "{schema}"."ml_scores" '
            "WHERE model_id = %s AND date BETWEEN %s AND %s",
            (model_id, start, end),
        )
        rows = cur.fetchall()
    if not rows:
        raise SystemExit(f"ml_scores 沒有 {model_id} 在 {start}～{end} 的分數")
    return pl.DataFrame(
        rows, schema={"date": pl.Date, "stock_id": pl.String, "score": pl.Float64}, orient="row"
    )


def _volumes(conn: object, schema: str, start: dt.date, end: dt.date) -> pl.DataFrame:
    with conn.cursor() as cur:  # type: ignore[attr-defined]
        cur.execute(
            f'SELECT date, stock_id, volume FROM "{schema}"."prices_daily" '
            "WHERE date BETWEEN %s AND %s AND stock_id ~ %s",
            (start, end, _UNIVERSE),
        )
        rows = cur.fetchall()
    return pl.DataFrame(
        rows, schema={"date": pl.Date, "stock_id": pl.String, "volume": pl.Float64}, orient="row"
    )


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":  # Windows 主控台
        getattr(sys.stdout, "reconfigure")(encoding="utf-8")  # noqa: B009
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--data-dir", required=True, help="結果包所在的 data 目錄（絕對路徑）")
    ap.add_argument("--out", required=True, help="markdown 報告輸出路徑")
    ap.add_argument("--cloud", action="store_true", help="也寫進雲端（TWQUANT_SUPABASE_DSN）")
    args = ap.parse_args()

    cfg = load_config(CONFIGS / "score_reference.yaml", ScoreReferenceConfig)
    cost = load_config(CONFIGS / "costs.yaml", CostConfig)
    board = load_config(CONFIGS / "pick_board.yaml", PickBoardConfig)
    names = {s.code: s.name for s in board.strategies}
    scfg = load_config(CONFIGS / "serving.yaml", ServingConfig)
    data_dir = Path(args.data_dir)

    starts = [ref.oos_start for ref in cfg.lines.values() if ref.oos_start]
    bundles: dict[str, dict[str, dict[str, object]]] = {}
    for code, ref in cfg.lines.items():
        if ref.kind == "bundle" and ref.bundles:
            bundles[code] = {
                p: json.loads((data_dir / path).read_text(encoding="utf-8"))
                for p, path in ref.bundles.items()
            }
            starts += [dt.date.fromisoformat(str(b["start"])) for b in bundles[code].values()]
    start = min(starts)
    _say(f"交易日 {start}～{cfg.dev_end}（行情只讀到開發期上界）")

    conn = store.connect(scfg)
    rows: list[RefRow] = []
    try:
        source = PgTableSource(
            conn, scfg.pg_schema, cache=True, since=start - dt.timedelta(days=cfg.warmup_days)
        )
        sessions = cal.load(source).between(start, cfg.dev_end)
        if not sessions or sessions[-1] > cfg.dev_end:
            raise SystemExit("交易日序列不對")
        _say(f"{len(sessions)} 個交易日；讀行情面板…")
        panel = price_panel(source, start, cfg.dev_end)
        divs = dividends_by_date(source, start, cfg.dev_end)
        splits = splits_by_date(source, start, cfg.dev_end)
        for code, ref in cfg.lines.items():
            t0 = time.time()
            if ref.kind == "bundle":
                part = bundle_line_rows(
                    code, bundles[code], sessions, panel, divs, splits, cost, cfg
                )
            else:
                assert ref.model_id and ref.oos_start
                scores = _scores(conn, scfg.pg_schema, ref.model_id, ref.oos_start, cfg.dev_end)
                mine = [d for d in sessions if d >= ref.oos_start]
                prices = None
                if ref.volume_window is not None:
                    lo = ref.oos_start - dt.timedelta(days=ref.volume_window * 3)
                    prices = _volumes(conn, scfg.pg_schema, lo, cfg.dev_end)
                _say(f"{code}：{scores.height:,} 筆分數、{scores['date'].n_unique()} 天")
                part = score_line_rows(
                    code, ref, scores, mine, panel, divs, splits, cost, cfg, prices
                )
            rows += part
            _say(
                f"{code} 完成（{time.time() - t0:.0f} 秒）："
                + "；".join(f"{r.period}/{r.bucket} {r.n} 筆" for r in part)
            )
        at = now_tw()
        write_reference(conn, scfg.pg_schema, rows, cfg.periods, cfg.registration, at)
        _say("已寫本機 serving.score_reference")
    finally:
        conn.close()
    if args.cloud:
        cloud = store.connect_cloud(scfg)
        try:
            write_reference(cloud, scfg.pg_schema, rows, cfg.periods, cfg.registration, at)
        finally:
            cloud.close()
        _say("已寫雲端 serving.score_reference")
    out = Path(args.out)
    out.write_text(
        render(rows, cfg, names) + f"\n產生於 {at:%Y-%m-%d %H:%M}（{cfg.registration}）\n",
        encoding="utf-8",
    )
    _say(f"報告：{out}")


if __name__ == "__main__":
    main()
