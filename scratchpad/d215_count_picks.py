"""D-215：登記寫完之後，用本機 PG 數「帶量突破」最近每天會列幾檔（只數檔數，不看之後的報酬）。

用法：TWQUANT_PG_DSN=postgresql://twquant:twquant@localhost:5433/twquant uv run --all-groups python scratchpad/d215_count_picks.py --from 2026-09-01
"""

from __future__ import annotations

import argparse
import datetime as dt
import os

import psycopg

import twquant.factors.builtins  # noqa: F401
from twquant.cli import _CONFIG_DIR, _line_picks
from twquant.data.serving.pg_view import PgTableSource
from twquant.strategy.config import load_strategy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=dt.date.fromisoformat, required=True)
    args = ap.parse_args()
    cfg = load_strategy(str(_CONFIG_DIR / "strategies" / "breakout_v20.yaml"))
    assert cfg.slots is not None
    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        days = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT date FROM serving.prices_daily WHERE date >= %s ORDER BY date",
                (args.start,),
            ).fetchall()
        ]
        src = PgTableSource(conn, "serving", cache=True, since=args.start - dt.timedelta(days=200))
        total = 0
        nonempty = 0
        for day in days:
            picks = _line_picks(cfg, src, day, cfg.slots.n)
            assert picks is not None
            total += len(picks)
            nonempty += bool(picks)
            print(day, len(picks), " ".join(f"{s}({v:.1f})" for s, v in picks), flush=True)
    print(f"{len(days)} 個交易日；有股票 {nonempty} 天；合計 {total} 檔；平均 {total / max(1, len(days)):.1f} 檔/天")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
