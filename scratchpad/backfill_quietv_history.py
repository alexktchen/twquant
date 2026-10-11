"""D-174（使用者 2026-10-11：「補」）：把第三條線「高分爆量」的每日名單補到與潛伏飆股同一段歷史，本機 PG 與雲端都寫。

選股用每日流程同一個函式（cli._line_picks → slot_candidates），規則寫死在 quiet_p35v.yaml；
分數是 2026-04-30 訓練完的模型之後每天打的分（樣本外），所以這是照規則重播，不是事後挑。
用法：uv run --all-groups python scratchpad/backfill_quietv_history.py [--write]
"""

from __future__ import annotations

import argparse
import datetime as dt
import os

import psycopg

import twquant.factors.builtins  # noqa: F401
from twquant.cli import _CONFIG_DIR, _line_picks, _strategy_model_id
from twquant.conventions import now_tw
from twquant.data.serving.pg_view import PgTableSource
from twquant.data.serving.picks_store import write_line
from twquant.strategy.config import load_strategy

LOCAL = "postgresql://twquant:twquant@localhost:5433/twquant"
LINE = "QUIETV"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="真的寫入（預設只印）")
    args = ap.parse_args()
    cfg = load_strategy(str(_CONFIG_DIR / "strategies" / "quiet_p35v.yaml"))
    assert cfg.slots is not None
    model_id = _strategy_model_id(cfg)
    with psycopg.connect(LOCAL) as lc:
        days = [
            r[0]
            for r in lc.execute(
                "SELECT DISTINCT date FROM serving.daily_picks WHERE line = 'QUIET' ORDER BY date"
            ).fetchall()
        ]
        have = {
            r[0]
            for r in lc.execute(
                "SELECT DISTINCT date FROM serving.daily_picks WHERE line = %s", (LINE,)
            ).fetchall()
        }
        src = PgTableSource(lc, "serving", cache=True, since=dt.date(2025, 11, 1))
        out: dict[dt.date, list[tuple[str, float | None]]] = {}
        for day in days:
            if day in have:
                continue
            picks = _line_picks(cfg, src, day, cfg.slots.n)
            assert picks is not None, day
            out[day] = picks
            print(day, len(picks), [s for s, _ in picks], flush=True)
    print(f"model {model_id}；共 {len(out)} 天要補；有股票 {sum(1 for p in out.values() if p)} 天")
    if not args.write:
        return 0
    at = now_tw()
    for dsn in (LOCAL, os.environ["TWQUANT_SUPABASE_DSN"]):
        with psycopg.connect(dsn) as c:
            for day, picks in out.items():
                write_line(c, "serving", day, LINE, picks, model_id, at, True)
            c.commit()
    print("已寫入本機與雲端")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
