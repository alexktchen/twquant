"""ETF 每日持股歷史回補（SPEC-016 §2.2，T-336）：元大、統一 → PG `etf_holdings_daily`。

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/backfill_etf_holdings.py --write [--etfs 0050,00981A]

取樣（configs/fund_flow.yaml 的 holdings）：
- 主動式：逐交易日。
- 指數型：每 `index_sample_sessions` 個交易日取一份；相鄰兩份的成分不同 → 中間逐日補，生效日才找得到。
  兩份之間的權重由 T-338 用「基準日 ≤ t−1 的最近一份」近似（指數型一週內權重漂移小；SPEC-016 §3 註明）。
- 查詢日＝基準日的下一個交易日（PG 日曆）；最後一個交易日之後逐日試。
- 可續傳：PG 已有的 (ETF, 基準日) 跳過。每檔 ETF 抓完就寫一次（中途停掉不會丟整批）。
- 來源缺某個基準日（PcfMissingBase）→ 只報不停；其他錯誤（權重和不對、格式改版）→ raise 停下（黃金規則 5）。
- `--check` 只抓前 3 份、不寫。
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import itertools
import os
import sys
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from twquant.config import (  # noqa: E402
    EtfMetaConfig,
    FundFlowConfig,
    ServingConfig,
    SystemConfig,
    load_config,
)
from twquant.data import fund_flow_units as ffu  # noqa: E402
from twquant.data.http import HttpClient  # noqa: E402
from twquant.data.serving import fund_flow_ingest as ffi  # noqa: E402
from twquant.data.sources import etf_pcf  # noqa: E402

CHECK_N = 3


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    ap.add_argument("--etfs", default="", help="逗號分隔；預設 holdings.issuers 的全部 ETF")
    ap.add_argument("--end", type=dt.date.fromisoformat, default=None)
    args = ap.parse_args()
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = load_config(ROOT / "configs" / "fund_flow.yaml", FundFlowConfig)
    meta = load_config(ROOT / cfg.etf_meta_path, EtfMetaConfig)
    scfg = load_config(ROOT / "configs" / "serving.yaml", ServingConfig)
    sys_cfg = load_config(ROOT / "configs" / "system.yaml", SystemConfig)
    schema = scfg.pg_schema
    h = cfg.holdings
    scope = ffi.holdings_scope(cfg, meta)
    active = {e.etf_id for e in meta.etfs if e.is_active}
    if args.etfs:
        want = {s.strip() for s in args.etfs.split(",") if s.strip()}
        unknown = sorted(want - {e for e, _, _ in scope})
        if unknown:
            raise SystemExit(f"不在持股範圍（投信 {h.issuers}、units_tracked）：{unknown}")
        scope = [x for x in scope if x[0] in want]

    yuanta = HttpClient(source="yuanta_pcf", rate=sys_cfg.source_rates["twse_rwd"])
    ez = etf_pcf.EzmoneySession(pace_seconds=h.pace_seconds)
    fetchers: dict[str, ffi.QueryFetch] = {
        "元大": lambda etf_id, q: etf_pcf.fetch_yuanta(yuanta, etf_id, q),
        "統一": ez.fetch,
    }

    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        with conn.cursor() as cur:
            cur.execute(f'SELECT max(date) FROM "{schema}".prices_daily')
            last = cur.fetchone()[0]
            end = min(args.end or last, last)
            cal = ffi.sessions_between(cur, schema, cfg.units.history_start, end)
        nxt = {b: (cal[i + 1] if i + 1 < len(cal) else None) for i, b in enumerate(cal)}
        print(
            f"範圍 {cal[0]}～{end}；交易日 {len(cal)}；ETF {[e for e, _, _ in scope]}", flush=True
        )
        gaps: list[str] = []
        total = 0
        for etf_id, iss, listed in scope:
            sessions = [b for b in cal if b > listed]
            with conn.cursor() as cur:
                have = {b for e, b in ffi.present_holdings(cur, schema, sessions) if e == etf_id}
            got: dict[dt.date, etf_pcf.PcfSnapshot] = {}

            def grab(
                b: dt.date, etf_id: str = etf_id, iss: str = iss
            ) -> etf_pcf.PcfSnapshot | None:
                try:
                    return ffi.snapshot_for_base(
                        fetchers[iss], etf_id, b, nxt[b], h.probe_calendar_days
                    )
                except etf_pcf.PcfMissingBase as exc:
                    gaps.append(f"{etf_id} {b}：{exc}")
                    return None

            step = 1 if etf_id in active else h.index_sample_sessions
            sample = sessions[::step]
            if sessions and sessions[-1] not in sample:
                sample.append(sessions[-1])
            if args.check:
                sample = sample[:CHECK_N]
            for b in sample:
                if b not in have and (s := grab(b)) is not None:
                    got[b] = s
            if step > 1 and not args.check:
                # 成分變動的區間逐日補：用 PG 已有的與這次抓到的一起比
                with conn.cursor() as cur:
                    cur.execute(
                        f'SELECT date, array_agg(stock_id ORDER BY stock_id) FROM "{schema}".etf_holdings_daily '
                        "WHERE etf_id = %s GROUP BY date",
                        (etf_id,),
                    )
                    members = {d: tuple(ids) for d, ids in cur.fetchall()}
                members.update({b: tuple(r[0] for r in s.rows) for b, s in got.items()})
                known = sorted(members)
                for a, z in itertools.pairwise(known):
                    if members[a] != members[z]:
                        for b in (d for d in sessions if a < d < z and d not in members):
                            if (s := grab(b)) is not None:
                                got[b] = s
            if got:
                df = ffu.holdings_frame(list(got.values()), h.available_hour, h.weight_sum_max)
                if args.write:
                    ffi.write_holdings(conn, schema, df)
                total += df.height
            print(
                f"  {etf_id}（{iss}，{'主動' if etf_id in active else '指數'}）：已有 {len(have)}、"
                f"新抓 {len(got)} 份；累計 {total} 列；來源缺 {len(gaps)}",
                flush=True,
            )
        print(f"來源缺基準日（只報不停）：{len(gaps)} 筆，例 {gaps[:10]}")
        print(f"[OK] {'寫入' if args.write else '--check 未寫入'} {total} 列")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
