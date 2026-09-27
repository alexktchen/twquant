"""第 1 批免費籌碼回補（T-309／D-154）：當沖、借券賣出、處置股、注意股 → PG。

用法（env：TWQUANT_PG_DSN；finmind 子指令另需 FINMIND_TOKEN——只從環境讀、永不印出）：

    # 處置／注意公告：證交所＋櫃買，一年一個請求（2014~2026 每種 13 個，四種共 52 個）
    uv run --all-groups python scripts/backfill_chip_batch1.py alerts --market twse --kind disposition
    uv run --all-groups python scripts/backfill_chip_batch1.py alerts --market tpex --kind attention \\
        --from-year 2014 --to-year 2026

    # FinMind 逐檔全期（免費註冊層 600 次/小時 → 每請求間隔 6.2 秒；2,093 檔約 3.6 小時／資料集）
    uv run --all-groups python scripts/backfill_chip_batch1.py finmind --dataset daytrade --start 2014-01-01
    uv run --all-groups python scripts/backfill_chip_batch1.py finmind --dataset sbl --stocks 2330,6488

    # 只重印品質報告（不抓、不寫）
    uv run --all-groups python scripts/backfill_chip_batch1.py report --dataset sbl

為什麼是腳本而非 CLI 指令：同 `scripts/backfill_finmind.py`——一次性大量歷史回補（D-014／D-044）。
每日增量是 T-310（官方端點、接進每日 pipeline），不在這裡。

規則（D-154 §二）：
- **只寫 PG**（使用者裁決：資料只在 PG），走 `serving.sync.upsert`。四張表是直寫表（DIRECT_PG_SPECS），
  不在鏡射清單——`serving rebuild` 不會清掉它們。
- 建表只建這四張（`ensure_tables`），**不跑** `ensure_schema`：後者對既有大表下
  `CREATE INDEX IF NOT EXISTS`，要先拿 ShareLock，另一個 session 正在寫的表會被卡住。
- 公告：每年一個交易；一年的回應解析、驗證、查重都過了才寫。同一 (stock_id, date) 與 PG 既有列
  內容不同 → 停（`--allow-changes` 才覆寫）。回應是 HTML／428（WAF）→ 立刻停，不重試。
- FinMind：每檔一個交易；一檔失敗（HTTP、映射、驗證）→ 該檔一列都不寫、記下、繼續；結束時列出全部
  失敗並以非零碼結束。402＝額度用盡 → 等 10 分鐘重試（上限 2 小時，超過即停整批）；連續 N 檔失敗
  也停（多半是 token 或額度層級的問題，繼續只會燒額度）。已有列的股票預設跳過（可續傳），`--force` 重抓。
- 日誌同時寫 `D:/project/twquant/data/logs/chip_batch1_<時間>_<部分>.log`——**絕對路徑**，
  不得落在 worktree 自己的 data/（CLAUDE.md §0.2）。
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import os
import pathlib
import re
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, TextIO

import httpx
import polars as pl
import psycopg

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from twquant.config import ServingConfig, SourceRate
from twquant.conventions import now_tw
from twquant.data.http import HttpClient
from twquant.data.serving import direct_ingest
from twquant.data.serving import schema as sch
from twquant.data.serving import store
from twquant.data.serving.sync import upsert
from twquant.data.sources import alerts
from twquant.data.sources import finmind as fm
from twquant.data.sources.tpex import browser_client
from twquant.errors import DataError, QualityGateError

_LOG_DIR = "D:/project/twquant/data/logs"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
_STOCK_ID = re.compile(r"[0-9A-Z]{4,6}")
# 因子宇宙：四碼普通股（含已下市），與 WHERE-IS-EVERYTHING ⑪ 同一條規則。
_UNIVERSE_PATTERN = "^[1-9][0-9]{3}$"

_FINMIND: dict[
    str, tuple[str, str, Callable[[Sequence[Mapping[str, Any]]], list[dict[str, Any]]]]
] = {
    "daytrade": ("daytrade_daily", fm.DAYTRADE_DATASET, fm.daytrade_rows),
    "sbl": ("sbl_short_daily", fm.SBL_DATASET, fm.sbl_short_rows),
}
_REPORT_TABLES = {
    "daytrade": "daytrade_daily",
    "sbl": "sbl_short_daily",
    "disposition": "disposition_stocks",
    "attention": "attention_stocks",
}


class _Log:
    """印到螢幕、同時追加寫進日誌檔（逐行 flush，程序被砍也留得下已發生的事）。"""

    def __init__(self, path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._fh: TextIO = path.open("a", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = f"[{now_tw():%H:%M:%S}] {msg}"
        print(line, flush=True)
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()


def _log_for(args: argparse.Namespace, part: str) -> _Log:
    log_dir = pathlib.Path(args.log_dir)
    if not log_dir.is_absolute():
        raise SystemExit(
            f"--log-dir 必須是絕對路徑（相對路徑在 worktree 裡會寫進錯的 data/）：{args.log_dir}"
        )
    return _Log(log_dir / f"chip_batch1_{now_tw():%Y%m%d-%H%M}_{part}.log")


def _pg_target(conn: Any) -> str:
    """連線目標（host:port/db，不含帳密），寫進日誌以免寫錯庫。"""
    info = conn.info
    return f"{info.host}:{info.port}/{info.dbname}"


def _parse_stocks(text: str) -> list[str]:
    ids = sorted({s.strip() for s in text.split(",") if s.strip()})
    bad = [s for s in ids if not _STOCK_ID.fullmatch(s)]
    if not ids or bad:
        raise SystemExit(f"--stocks 格式不對（逗號分隔的代號）：{text!r}，不合法：{bad}")
    return ids


# ── 公告（處置／注意）────────────────────────────────────────────────────────


def _existing_keys(conn: Any, schema: str, table: str, df: pl.DataFrame) -> int:
    if df.is_empty():
        return 0
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT count(*) FROM "{schema}"."{table}" t '
            "JOIN unnest(%s::text[], %s::date[]) AS k(stock_id, date) "
            "ON t.stock_id = k.stock_id AND t.date = k.date",
            (df["stock_id"].to_list(), df["date"].to_list()),
        )
        return int(cur.fetchone()[0])


def _year_line(year: int, kind: str, parsed: alerts.Parsed, df: pl.DataFrame, exact: int) -> str:
    head = (
        f"{year}：回應 {len(parsed.rows):,} 列（佔位略過 {parsed.placeholders}、完全重複合併 {exact}）"
        f"→ {df.height:,} 列／{df['stock_id'].n_unique() if df.height else 0:,} 檔"
    )
    if df.is_empty():
        return head
    span = "~".join(str(d) for d in (df["date"].min(), df["date"].max()))
    if kind == "disposition":
        nulls = f"level null {df['level'].null_count()}、minutes null {df['match_minutes'].null_count()}"
    else:
        nulls = f"clauses null {df['clauses'].null_count()}、close null {df['close'].null_count()}"
    return f"{head}｜公告日 {span}｜{nulls}"


def cmd_alerts(args: argparse.Namespace) -> int:
    """一年一個請求：抓 → 解析 → 驗證 → 查重 → 與 PG 比對 → upsert（每年一個交易）。"""
    log = _log_for(args, f"alerts-{args.market}-{args.kind}")
    table = alerts.TABLE_OF_KIND[args.kind]
    spec = sch.spec_of(table)
    today = now_tw().date()
    if args.from_year > args.to_year:
        raise SystemExit(f"--from-year {args.from_year} 晚於 --to-year {args.to_year}")
    rate = SourceRate(max_requests=1, per_seconds=args.sleep)
    client = (
        browser_client(rate)
        if args.market == "tpex"
        else HttpClient(source=f"twse_{args.kind}", rate=rate)
    )
    cfg = ServingConfig(pg_schema=args.pg_schema)
    status = 0
    with store.connect(cfg) as conn:
        log(
            f"alerts {args.market} {args.kind} → {cfg.pg_schema}.{table}（PG {_pg_target(conn)}）；"
            f"{args.from_year}~{args.to_year}，請求間隔 ≥ {args.sleep:g} 秒；日誌 {log.path}"
        )
        sch.ensure_tables(conn, sch.DIRECT_PG_SPECS, cfg.pg_schema)
        written = 0
        for year in range(args.from_year, args.to_year + 1):
            start = dt.date(year, 1, 1)
            if start > today:
                log(f"{year}：尚未開始，略過")
                continue
            end = min(dt.date(year, 12, 31), today)
            try:
                payload = alerts.fetch_alerts(client, args.market, args.kind, start, end)
                parsed = alerts.parse_alerts(args.market, args.kind, payload)
                df, exact = alerts.build_frame(table, parsed.rows)
            except (DataError, QualityGateError) as exc:
                log(f"{year}：停止——{exc}")
                if "anti_bot_suspected" in str(exc):
                    log("被 WAF 擋了：不要重試，隔幾小時再從這一年續跑（--from-year）。")
                else:
                    log("回應與解析器的認知不符：先看上面那一列、補規則與測試，再從這一年續跑。")
                status = 2
                break
            existing = _existing_keys(conn, cfg.pg_schema, table, df)
            with conn.cursor() as cur:
                changed = direct_ingest.changed_rows(cur, cfg.pg_schema, spec, df)
            if changed.height:
                log(f"{year}：{changed.height} 列與 PG 既有同鍵列內容不同，例：")
                for row in changed.head(5).iter_rows(named=True):
                    log(f"    {row}")
                if not args.allow_changes:
                    log("停止（要覆寫請確認原因後加 --allow-changes）。")
                    status = 2
                    break
            with conn.cursor() as cur:
                upsert(cur, cfg.pg_schema, spec, df)
            conn.commit()
            written += df.height
            log(
                _year_line(year, args.kind, parsed, df, exact)
                + f"｜PG 已有同鍵 {existing:,} 列（內容不同 {changed.height}）"
            )
        log(f"本次 upsert {written:,} 列。PG 內 {args.market} 依公告年：")
        _alerts_summary(conn, cfg.pg_schema, table, args.market, log)
    log.close()
    return status


def _alerts_summary(conn: Any, schema: str, table: str, market: str | None, log: _Log) -> None:
    where = "WHERE market = %s" if market else ""
    params: tuple[Any, ...] = (market,) if market else ()
    extra = (
        "count(*) FILTER (WHERE level IS NULL), count(*) FILTER (WHERE match_minutes IS NULL)"
        if table == "disposition_stocks"
        else "count(*) FILTER (WHERE clauses IS NULL), count(*) FILTER (WHERE close IS NULL)"
    )
    labels = (
        ("level null", "minutes null")
        if table == "disposition_stocks"
        else (
            "clauses null",
            "close null",
        )
    )
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT market, extract(year FROM date)::int, count(*), count(DISTINCT stock_id), {extra} "
            f'FROM "{schema}"."{table}" {where} GROUP BY 1, 2 ORDER BY 1, 2',
            params,
        )
        for mk, year, n, n_ids, null_a, null_b in cur.fetchall():
            log(
                f"    {mk} {year}：{n:,} 列／{n_ids:,} 檔｜{labels[0]} {null_a}、{labels[1]} {null_b}"
            )


# ── FinMind（當沖／借券）──────────────────────────────────────────────────────


class _QuotaExhausted(Exception):
    """402 等超過上限：額度沒有回來，整批停。"""


class _Pacer:
    """兩個請求之間至少隔 interval 秒（從上一個請求送出起算）。"""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self.interval - (time.monotonic() - self._last)
            if remaining > 0:
                time.sleep(remaining)
        self._last = time.monotonic()


@dataclasses.dataclass
class _Tally:
    ok: int = 0
    empty: int = 0
    rows: int = 0
    exact_dups: int = 0
    identity_violations: int = 0
    failures: list[tuple[str, str]] = dataclasses.field(default_factory=list)


def _fetch_finmind(
    http: httpx.Client,
    pacer: _Pacer,
    args: argparse.Namespace,
    dataset: str,
    table: str,
    sid: str,
    log: _Log,
) -> list[Any]:
    params = {"dataset": dataset, "data_id": sid, "start_date": args.start, "end_date": args.end}
    waited = 0.0
    while True:
        pacer.wait()
        resp = http.get(fm.API, params=params)
        payload: Any = resp.json() if resp.status_code == 200 else None
        quota = resp.status_code == 402 or (
            isinstance(payload, dict) and payload.get("status") == 402
        )
        if not quota:
            break
        # 402＝額度用盡：可預期的節流，不是資料問題——等額度回來再抓同一檔。
        if waited >= args.max_wait:
            raise _QuotaExhausted(
                f"FinMind 額度等了 {waited / 60:.0f} 分鐘仍未恢復（上限 {args.max_wait / 60:.0f} 分鐘）"
            )
        log(f"  [{sid}] FinMind 額度用盡（402），等 {args.cooldown / 60:.0f} 分鐘後重試")
        time.sleep(args.cooldown)
        waited += args.cooldown
    if resp.status_code != 200:
        raise DataError(table, None, f"{sid}：HTTP {resp.status_code}：{resp.text[:150]!r}")
    if not isinstance(payload, dict) or payload.get("status") != 200:
        status = payload.get("status") if isinstance(payload, dict) else None
        msg = str(payload.get("msg"))[:120] if isinstance(payload, dict) else ""
        raise DataError(table, None, f"{sid}：FinMind status={status!r} msg={msg!r}")
    data = payload.get("data")
    if not isinstance(data, list):
        raise DataError(table, None, f"{sid}：FinMind 回應缺 data 陣列")
    return data


def _universe(conn: Any, schema: str, start: dt.date) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT DISTINCT stock_id FROM "{schema}".prices_daily '
            "WHERE date >= %s AND stock_id ~ %s ORDER BY stock_id",
            (start, _UNIVERSE_PATTERN),
        )
        return [r[0] for r in cur.fetchall()]


def _loaded(conn: Any, schema: str, table: str, ids: list[str]) -> set[str]:
    """已有任何一列的股票（走主鍵逐檔探一次，不掃整表）。"""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT s FROM unnest(%s::text[]) AS s WHERE EXISTS "
            f'(SELECT 1 FROM "{schema}"."{table}" t WHERE t.stock_id = s)',
            (ids,),
        )
        return {r[0] for r in cur.fetchall()}


def cmd_finmind(args: argparse.Namespace) -> int:
    """逐檔全期：抓 → 映射 → 驗證 → upsert（每檔一個交易）；失敗的檔一列都不寫，最後列出。"""
    token = os.environ.get("FINMIND_TOKEN")
    if not token:
        raise SystemExit("FINMIND_TOKEN 未設（放 .env，黃金規則 9）")
    log = _log_for(args, f"finmind-{args.dataset}")
    table, dataset, mapper = _FINMIND[args.dataset]
    spec = sch.spec_of(table)
    args.end = args.end or now_tw().date().isoformat()
    start = dt.date.fromisoformat(args.start)
    dt.date.fromisoformat(args.end)  # 格式不對就在發第一個請求前失敗
    cfg = ServingConfig(pg_schema=args.pg_schema)
    tally = _Tally()
    stopped = ""
    with store.connect(cfg) as conn:
        sch.ensure_tables(conn, sch.DIRECT_PG_SPECS, cfg.pg_schema)
        universe = (
            _parse_stocks(args.stocks) if args.stocks else _universe(conn, cfg.pg_schema, start)
        )
        done = set() if args.force else _loaded(conn, cfg.pg_schema, table, universe)
        conn.commit()  # 結束唯讀交易：下面幾小時的 HTTP 迴圈不得掛著「idle in transaction」
        todo = [s for s in universe if s not in done]
        log(
            f"finmind {dataset} → {cfg.pg_schema}.{table}（PG {_pg_target(conn)}）；"
            f"{args.start}~{args.end}；宇宙 {len(universe):,} 檔、已有 {len(done):,}、待抓 {len(todo):,}；"
            f"間隔 {args.sleep:g} 秒 → 約 {len(todo) * args.sleep / 3600:.1f} 小時；日誌 {log.path}"
        )
        pacer = _Pacer(args.sleep)
        headers = {"User-Agent": _UA, "Authorization": f"Bearer {token}"}
        t0 = time.monotonic()
        consecutive = 0
        with httpx.Client(headers=headers, timeout=120) as http:
            for i, sid in enumerate(todo, 1):
                try:
                    rows = mapper(_fetch_finmind(http, pacer, args, dataset, table, sid, log))
                    stray = sorted({r["stock_id"] for r in rows} - {sid})
                    if stray:
                        raise DataError(table, None, f"{sid}：回應混進其他代號 {stray}")
                    df, exact = alerts.build_frame(table, rows)
                    if df.is_empty():
                        tally.empty += 1
                        log(f"  [{sid}] 無資料（{args.start}~{args.end}）")
                    else:
                        with conn.cursor() as cur:
                            upsert(cur, cfg.pg_schema, spec, df)
                        conn.commit()
                        tally.ok += 1
                        tally.rows += df.height
                        tally.exact_dups += exact
                        note = ""
                        if args.dataset == "sbl":
                            bad = fm.sbl_identity_violations(df).height
                            tally.identity_violations += bad
                            note = f"、恆等式違反 {bad}"
                        span = "~".join(str(d) for d in (df["date"].min(), df["date"].max()))
                        log(f"  [{sid}] {df.height:,} 列（{span}、完全重複合併 {exact}{note}）")
                    consecutive = 0
                except _QuotaExhausted as exc:
                    tally.failures.append((sid, str(exc)))
                    stopped = str(exc)
                    break
                except (
                    DataError,
                    QualityGateError,
                    httpx.HTTPError,
                    psycopg.Error,
                    ValueError,
                    TypeError,
                ) as exc:
                    conn.rollback()
                    msg = f"{type(exc).__name__}: {exc}"[:300]
                    tally.failures.append((sid, msg))
                    log(f"  [{sid}] 失敗，這一檔一列都沒寫：{msg}")
                    consecutive += 1
                    if consecutive >= args.max_consecutive_failures:
                        stopped = (
                            f"連續 {consecutive} 檔失敗——多半是 token、額度層級或來源的問題，"
                            "停下以免燒光額度"
                        )
                        break
                if i % args.every == 0 or i == len(todo):
                    elapsed = time.monotonic() - t0
                    eta = elapsed / i * (len(todo) - i) / 60
                    log(
                        f"  進度 {i:,}/{len(todo):,}｜成功 {tally.ok:,}（{tally.rows:,} 列）"
                        f"、無資料 {tally.empty:,}、失敗 {len(tally.failures):,}"
                        f"｜已 {elapsed / 60:.1f} 分、剩約 {eta:.0f} 分"
                    )
        if stopped:
            log(f"整批停止：{stopped}。已寫入的檔保留；重跑會從沒寫過的檔續抓。")
        log(
            f"本次：成功 {tally.ok:,} 檔（{tally.rows:,} 列）、無資料 {tally.empty:,}、"
            f"失敗 {len(tally.failures):,}；完全重複合併 {tally.exact_dups:,} 列"
            + (
                f"；借券恆等式違反（本次寫入）{tally.identity_violations:,} 列"
                if args.dataset == "sbl"
                else ""
            )
        )
        _finmind_report(conn, cfg.pg_schema, args, universe if args.stocks else None, log)
    for sid, msg in tally.failures:
        log(f"  失敗 {sid}：{msg}")
    log.close()
    if stopped:
        return 2
    return 1 if tally.failures else 0


# ── 品質報告（只讀）──────────────────────────────────────────────────────────


def _finmind_report(
    conn: Any, schema: str, args: argparse.Namespace, ids: list[str] | None, log: _Log
) -> None:
    where = "WHERE stock_id = ANY(%s)" if ids else ""
    params: tuple[Any, ...] = (ids,) if ids else ()
    scope = f"{len(ids)} 檔" if ids else "全表"
    with conn.cursor() as cur:
        if args.dataset == "sbl":
            # 與 finmind.sbl_identity_violations 同一條恆等式（TWSE TWT93U 附註）。
            broken = "sbl_balance <> sbl_prev_balance + sbl_sell - sbl_return + sbl_adjust"
            cur.execute(
                f"SELECT count(*), count(DISTINCT stock_id), min(date), max(date), "
                f"count(*) FILTER (WHERE {broken}), "
                f"count(*) FILTER (WHERE {broken} AND coalesce(sbl_covering, 0) <> 0), "
                "count(*) FILTER (WHERE sbl_adjust <> 0), "
                "count(*) FILTER (WHERE sbl_next_quota IS NULL), "
                "count(*) FILTER (WHERE sbl_covering IS NULL) "
                f'FROM "{schema}".sbl_short_daily {where}',
                params,
            )
            n, n_ids, lo, hi, bad, bad_cov, adj, q_null, c_null = cur.fetchone()
            log(
                f"品質（sbl_short_daily，{scope}）：{n:,} 列／{n_ids:,} 檔（{lo}~{hi}）｜"
                f"恆等式違反 {bad:,} 列（其中 covering≠0 的 {bad_cov:,}）｜調整≠0 {adj:,}｜"
                f"quota null {q_null:,}、covering null {c_null:,}"
            )
            if bad:
                cur.execute(
                    "SELECT stock_id, date, sbl_prev_balance, sbl_sell, sbl_return, sbl_adjust, "
                    f'sbl_balance, sbl_covering FROM "{schema}".sbl_short_daily '
                    f"{where + ' AND ' if where else 'WHERE '}{broken} ORDER BY date, stock_id LIMIT 5",
                    params,
                )
                for row in cur.fetchall():
                    log(f"    違反例：{row}")
        elif args.dataset == "daytrade":
            cur.execute(
                "SELECT count(*), count(DISTINCT stock_id), min(date), max(date), "
                "count(*) FILTER (WHERE sell_first_suspended), "
                "count(*) FILTER (WHERE daytrade_shares = 0) "
                f'FROM "{schema}".daytrade_daily {where}',
                params,
            )
            n, n_ids, lo, hi, suspended, zero = cur.fetchone()
            log(
                f"品質（daytrade_daily，{scope}）：{n:,} 列／{n_ids:,} 檔（{lo}~{hi}）｜"
                f"暫停先賣後買 {suspended:,} 列｜當沖 0 股 {zero:,} 列"
            )
            dwhere = "AND d.stock_id = ANY(%s)" if ids else ""
            joined = (
                "SELECT d.stock_id, d.date, d.daytrade_shares, p.close, "
                "d.daytrade_buy_amount / d.daytrade_shares / p.close AS rb, "
                "d.daytrade_sell_amount / d.daytrade_shares / p.close AS rs "
                f'FROM "{schema}".daytrade_daily d JOIN "{schema}".prices_daily p '
                "ON p.stock_id = d.stock_id AND p.date = d.date "
                f"WHERE d.daytrade_shares > 0 AND p.close > 0 {dwhere}"
            )
            lo_r, hi_r = args.ratio_low, args.ratio_high
            cur.execute(
                f"WITH j AS ({joined}) SELECT count(*), "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY rb), "
                "percentile_cont(0.5) WITHIN GROUP (ORDER BY rs), "
                "avg(CASE WHEN rb < %s OR rb > %s THEN 1.0 ELSE 0.0 END), "
                "avg(CASE WHEN rs < %s OR rs > %s THEN 1.0 ELSE 0.0 END) FROM j",
                (*params, lo_r, hi_r, lo_r, hi_r),
            )
            m, med_b, med_s, out_b, out_s = cur.fetchone()
            log(
                f"品質：當沖均價 ÷ 當日收盤（{m:,} 列有對到價）：買進中位數 {med_b or 0:.4f}、"
                f"賣出中位數 {med_s or 0:.4f}；落在 [{lo_r}, {hi_r}] 外的比例 買 {float(out_b or 0):.4%}"
                f"、賣 {float(out_s or 0):.4%}"
            )
            cur.execute(
                f"WITH j AS ({joined}) SELECT stock_id, date, daytrade_shares, close, rb FROM j "
                "WHERE rb > 0 ORDER BY abs(ln(rb)) DESC LIMIT 5",
                params,
            )
            for row in cur.fetchall():
                log(f"    偏離最大：{row}")
            cur.execute(
                f'SELECT count(*) FROM "{schema}".daytrade_daily d WHERE NOT EXISTS '
                f'(SELECT 1 FROM "{schema}".prices_daily p '
                f"WHERE p.stock_id = d.stock_id AND p.date = d.date) {dwhere}",
                params,
            )
            log(f"品質：當沖列在 prices_daily 找不到同日價格 {cur.fetchone()[0]:,} 列")


def cmd_report(args: argparse.Namespace) -> int:
    """只讀：重印某張表的品質報告。"""
    log = _log_for(args, f"report-{args.dataset}")
    cfg = ServingConfig(pg_schema=args.pg_schema)
    ids = _parse_stocks(args.stocks) if args.stocks else None
    with store.connect(cfg) as conn:
        log(f"report {args.dataset}（PG {_pg_target(conn)}）")
        if args.dataset in _FINMIND:
            _finmind_report(conn, cfg.pg_schema, args, ids, log)
        else:
            _alerts_summary(conn, cfg.pg_schema, _REPORT_TABLES[args.dataset], None, log)
    log.close()
    return 0


def main() -> int:
    """解析參數並分派子指令。"""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--pg-schema", default="serving")
    common.add_argument("--log-dir", default=_LOG_DIR, help="日誌目錄（絕對路徑）")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("alerts", parents=[common], help="處置／注意公告（一年一個請求）")
    a.add_argument("--market", choices=alerts.MARKETS, required=True)
    a.add_argument("--kind", choices=alerts.KINDS, required=True)
    a.add_argument("--from-year", type=int, default=2014)
    a.add_argument("--to-year", type=int, default=now_tw().year)
    a.add_argument("--sleep", type=float, default=5.0, help="請求間隔秒（≥ 5）")
    a.add_argument("--allow-changes", action="store_true", help="與 PG 既有列內容不同時仍覆寫")
    a.set_defaults(func=cmd_alerts)

    f = sub.add_parser("finmind", parents=[common], help="FinMind 當沖／借券（逐檔全期）")
    f.add_argument("--dataset", choices=sorted(_FINMIND), required=True)
    f.add_argument("--start", default="2014-01-01")
    f.add_argument("--end", default="", help="預設今天（台北）")
    f.add_argument(
        "--stocks", default="", help="逗號分隔；省略＝prices_daily 自 --start 起的四碼股"
    )
    f.add_argument("--force", action="store_true", help="已有列的股票也重抓")
    f.add_argument("--sleep", type=float, default=6.2, help="請求間隔秒（600 次/小時留 3%% 餘裕）")
    f.add_argument("--cooldown", type=float, default=600.0, help="402 後等待秒")
    f.add_argument("--max-wait", type=float, default=7200.0, help="單檔累計等額度上限秒")
    f.add_argument("--max-consecutive-failures", type=int, default=10)
    f.add_argument("--every", type=int, default=25, help="每幾檔印一次進度")
    f.add_argument("--ratio-low", type=float, default=0.8, help="當沖均價÷收盤的合理下界（報告用）")
    f.add_argument(
        "--ratio-high", type=float, default=1.25, help="當沖均價÷收盤的合理上界（報告用）"
    )
    f.set_defaults(func=cmd_finmind)

    r = sub.add_parser("report", parents=[common], help="只讀：重印品質報告")
    r.add_argument("--dataset", choices=sorted(_REPORT_TABLES), required=True)
    r.add_argument("--stocks", default="")
    r.add_argument("--ratio-low", type=float, default=0.8)
    r.add_argument("--ratio-high", type=float, default=1.25)
    r.set_defaults(func=cmd_report)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
