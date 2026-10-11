"""ETF 已發行單位數歷史回補（SPEC-016 §2.2，T-335）：FinMind → PG `etf_units_daily`、`etf_meta`。

    FINMIND_TOKEN=... TWQUANT_PG_DSN=... uv run --all-groups python scripts/backfill_etf_units.py --check
    FINMIND_TOKEN=... TWQUANT_PG_DSN=... uv run --all-groups python scripts/backfill_etf_units.py --write

每日的 TWSE 擷取（`fund-flow units`／pipeline「ETF 單位數」）只補最近幾天；這支一次補整段歷史。
- 日期範圍：`units.history_start`（PG 行情起點）到 `--end`（預設 PG 行情最後一天）。範圍外的列不收，印出筆數。
- 交易日曆＝PG prices_daily 有列的日子；FinMind 有、日曆沒有的日子 → raise（日曆缺日要先修）。
- **不做完整性閘門**：ETF 停牌或來源缺日在歷史上會發生；缺的日子差分是 null（不跨日），
  缺列彙總印出來（只報不修）。單日比值異常也只報不修。
- PG 已有的同鍵列（每日擷取寫過的）單位數必須一致，不一致 → raise、一列都不寫。
- `--check` 只抓、只算、只報；`--write` 才寫。
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from twquant.config import EtfMetaConfig, FundFlowConfig, ServingConfig, load_config  # noqa: E402
from twquant.data import fund_flow_units as ffu  # noqa: E402
from twquant.data.serving import fund_flow_ingest as ffi  # noqa: E402
from twquant.data.sources import finmind as fm  # noqa: E402
from twquant.data.sources.etf_units import finmind_units_rows  # noqa: E402
from twquant.errors import DataError  # noqa: E402

PACE_S = 1.0  # FinMind 有 token 時 600 次／小時；88 檔一次跑完不會碰到


def fetch(
    http: httpx.Client, token: str, dataset: str, etf_id: str, start: dt.date, end: dt.date
) -> list[Any]:
    """FinMind 一檔 ETF 整段歷史（失敗 raise，含代號與狀態）。"""
    params = {
        "dataset": dataset,
        "data_id": etf_id,
        "start_date": str(start),
        "end_date": str(end),
        "token": token,
    }
    resp = http.get(fm.API, params=params)
    time.sleep(PACE_S)
    payload: Any = resp.json() if resp.status_code == 200 else None
    if not isinstance(payload, dict) or payload.get("status") != 200:
        raise DataError(
            "etf_units_daily",
            None,
            f"{etf_id}：FinMind HTTP {resp.status_code} {str(payload)[:150]}",
        )
    data = payload.get("data")
    if not isinstance(data, list):
        raise DataError("etf_units_daily", None, f"{etf_id}：FinMind 回應缺 data")
    return data


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    ap.add_argument("--end", type=dt.date.fromisoformat, default=None)
    ap.add_argument("--etfs", default="", help="逗號分隔；預設 etf_meta.yaml 全部")
    args = ap.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    token = os.environ.get("FINMIND_TOKEN")
    if not token:
        raise SystemExit("FINMIND_TOKEN 未設（放 .env，黃金規則 9）")
    cfg = load_config(ROOT / "configs" / "fund_flow.yaml", FundFlowConfig)
    meta = load_config(ROOT / cfg.etf_meta_path, EtfMetaConfig)
    scfg = load_config(ROOT / "configs" / "serving.yaml", ServingConfig)
    schema = scfg.pg_schema
    ids = [e.etf_id for e in meta.etfs if e.units_tracked]
    skipped = [(e.etf_id, e.note) for e in meta.etfs if not e.units_tracked]
    print(f"不追蹤單位數（etf_meta.yaml 明列）：{skipped}", flush=True)
    if args.etfs:
        want = [s.strip() for s in args.etfs.split(",") if s.strip()]
        unknown = sorted(set(want) - set(ids))
        if unknown:
            raise SystemExit(f"不在 etf_meta.yaml：{unknown}")
        ids = want

    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT max(date) FROM "{schema}".prices_daily')
            last = cur.fetchone()[0]
        start, end = cfg.units.history_start, args.end or last
        with conn.cursor() as cur:
            cal = ffi.sessions_between(cur, schema, start, end)
        print(f"範圍 {start}～{end}；交易日 {len(cal)} 天；ETF {len(ids)} 檔", flush=True)

        rows: list[dict[str, Any]] = []
        with httpx.Client(timeout=60) as http:
            for k, etf_id in enumerate(ids, 1):
                got = finmind_units_rows(
                    fetch(http, token, cfg.units.finmind_dataset, etf_id, start, end),
                    cfg.units.finmind_field,
                )
                rows += got
                print(f"  [{k}/{len(ids)}] {etf_id} {len(got)} 列", flush=True)
        raw = pl.DataFrame(
            rows, schema={"date": pl.Date, "etf_id": pl.String, "units": pl.Int64}, orient="row"
        )
        outside = raw.filter((pl.col("date") < start) | (pl.col("date") > end)).height
        raw = raw.filter(pl.col("date").is_between(start, end))
        print(f"範圍外不收 {outside} 列；收 {raw.height} 列", flush=True)

        with conn.cursor() as cur:
            events = ffi.split_events(cur, schema, cfg.units.split_event_types)
        null_days = ffu.split_null_days(events, cal, cfg.units.split_null_sessions_before)
        df = ffu.units_frame(
            raw, cal, sorted(set(raw["date"].to_list())), null_days, cfg.units.available_hour
        )

        with conn.cursor() as cur:
            old = ffi.units_on(cur, schema, sorted(set(df["date"].to_list())))
        clash = df.join(old, on=["date", "etf_id"], suffix="_pg").filter(
            pl.col("units") != pl.col("units_pg")
        )
        if clash.height:
            raise DataError(
                "etf_units_daily",
                clash["date"][0],
                f"{clash.height} 列與 PG 既有單位數不同，例 {clash.head(5).rows()}",
            )

        lack = ffu.missing_etfs(
            df,
            meta.model_copy(update={"etfs": tuple(e for e in meta.etfs if e.etf_id in ids)}),
            cal,
        )
        by_year = Counter(d.year for d, v in lack.items() for _ in v)
        print(
            f"缺列（ETF×交易日，只報不修）：{sum(by_year.values())}；逐年 {dict(sorted(by_year.items()))}"
        )
        worst = Counter(s for v in lack.values() for s in v).most_common(10)
        print(f"缺最多的 ETF：{worst}")
        jumps = ffu.units_jumps(df, cfg.units.jump_report_ratio)
        print(
            f"單日比值超出 ×{cfg.units.jump_report_ratio}（只報不修）：{jumps.height} 列，例 "
            f"{jumps.select('date', 'etf_id', 'units', 'units_delta').head(10).rows()}"
        )
        print(
            f"差分 null：{df['units_delta'].null_count()} / {df.height}；分割窗 {sum(len(v) for v in null_days.values())} 天"
        )

        if args.write:
            n = ffi.write_meta(conn, schema, meta)
            ffi.write_units(conn, schema, df)
            print(f"[OK] etf_meta {n} 檔、etf_units_daily {df.height} 列寫入 {schema}")
        else:
            print("--check：未寫入")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
