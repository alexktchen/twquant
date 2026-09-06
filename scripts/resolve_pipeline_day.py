"""輸出 GitHub Actions 的 DAY/FROM7 環境變數（SPEC-012）。"""

from __future__ import annotations

import argparse
import datetime as dt
from collections.abc import Sequence

from twquant.conventions import now_tw
from twquant.scheduling import latest_scheduled_day, parse_slot


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--slot", action="append", required=True, help="台北班次 HH:MM，可重複")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--backfill-days", type=int, default=7)
    parser.add_argument("--now", help="測試用 ISO tz-aware datetime；正式執行省略")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """解析最近班次並以 KEY=VALUE 形式輸出，供追加至 GITHUB_ENV。"""
    args = _parser().parse_args(argv)
    if args.backfill_days < 0:
        raise ValueError(f"backfill_days 須 >= 0，得到 {args.backfill_days}")
    current = dt.datetime.fromisoformat(args.now) if args.now else now_tw()
    day = latest_scheduled_day(
        current,
        tuple(parse_slot(value) for value in args.slot),
        lookback_days=args.lookback_days,
    )
    print(f"DAY={day.isoformat()}")
    print(f"FROM7={(day - dt.timedelta(days=args.backfill_days)).isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
