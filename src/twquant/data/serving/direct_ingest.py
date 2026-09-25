"""直寫 PG 的每日擷取（T-310）：IngestSink、當沖首次公布值、每日補抓計畫與修訂統計。

四張第 1 批表（`schema.DIRECT_PG_SPECS`）只在 PG、不在資料湖（D-154 §二.2）。每日擷取走同一個來源
註冊表與 `run_ingest`，只是 sink 換成 `PgDirectSink`——這就是 D-017「儲存可換」的用途。

寫入規則：
- 當沖／借券：upsert 覆寫（當沖到 T+2 才定案，重抓就是為了收斂到定案值）。重抓時 PG 裡該市場那天有、
  這次官方表卻沒有的列**不刪**（市場別只能從 stock_meta 推，轉市場的股票會被誤刪），記成 notes 交給呼叫端印出。
- 當沖 `keep_first=True` 時另存一份到 `daytrade_first_pub`（先到先存），量 T+2 修訂幅度用（D-154 §三）。
- 處置／注意：與 PG 既有同鍵列內容不同 → raise、一列都不寫（D-154 §二.6；確認原因後用
  `scripts/backfill_chip_batch1.py alerts --allow-changes` 覆寫）。

比照 chip_ingest.py：本模組不 import psycopg，只在呼叫端傳入的連線上操作。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import polars as pl

from twquant.config import CHIP_DAILY_TABLES, ChipDailyConfig
from twquant.data.serving import schema as sch
from twquant.data.serving.sync import upsert
from twquant.errors import DataError

ALERT_TABLES = ("disposition_stocks", "attention_stocks")
MARKETS = ("twse", "tpex")
# 來源市場 → stock_meta.market 的寫法（查「某市場那天有沒有列」與重抓時的殘留列）
STOCK_META_MARKET = {"twse": "TWSE", "tpex": "TPEX"}
_FIRST = sch.DAYTRADE_FIRST_PUB_SPEC
_DAYTRADE = "daytrade_daily"


def _q(schema: str, table: str) -> str:
    return f'"{schema}"."{table}"'


def changed_rows(cur: Any, schema: str, spec: sch.TableSpec, df: pl.DataFrame) -> pl.DataFrame:
    """df 中「PG 已有同鍵、但內容不同」的列（新值與 `<欄>_pg` 舊值並列）。available_at 由日期推得，不比。"""
    keys = list(spec.pk)
    values = [c for c in spec.column_names if c not in spec.pk and c != "available_at"]
    cols = [*keys, *values]
    if df.is_empty():
        return df.select(cols)
    select = ", ".join(f't."{c}"' for c in cols)
    cur.execute(
        f"SELECT {select} FROM {_q(schema, spec.name)} t "
        "JOIN unnest(%s::text[], %s::date[]) AS k(stock_id, date) "
        "ON t.stock_id = k.stock_id AND t.date = k.date",
        (df["stock_id"].to_list(), df["date"].to_list()),
    )
    old = pl.DataFrame(cur.fetchall(), schema={c: df.schema[c] for c in cols}, orient="row")
    joined = df.select(cols).join(old, on=keys, how="inner", suffix="_pg")
    differs = pl.any_horizontal([~pl.col(c).eq_missing(pl.col(f"{c}_pg")) for c in values])
    return joined.filter(differs)


def insert_first_pub(cur: Any, schema: str, df: pl.DataFrame) -> None:
    """當沖列另存進 daytrade_first_pub（先到先存：已有同鍵就不動）；fetched_at＝PG 的 now()。"""
    data_cols = [c for c in _FIRST.column_names if c != "fetched_at"]
    collist = ", ".join(f'"{c}"' for c in _FIRST.column_names)
    ph = ", ".join(["%s"] * len(data_cols))
    pk = ", ".join(f'"{c}"' for c in _FIRST.pk)
    cur.executemany(
        f"INSERT INTO {_q(schema, _FIRST.name)} ({collist}) VALUES ({ph}, now()) "
        f"ON CONFLICT ({pk}) DO NOTHING",
        list(df.select(data_cols).iter_rows()),
    )


def _stale_ids(
    cur: Any, schema: str, table: str, day: dt.date, meta_market: str, ids: Sequence[str]
) -> list[str]:
    """PG 裡 (table, day) 屬於該市場（依 stock_meta）、卻不在這次官方表的代號。"""
    cur.execute(
        f"SELECT t.stock_id FROM {_q(schema, table)} t "
        f"JOIN {_q(schema, 'stock_meta')} m ON m.stock_id = t.stock_id "
        "WHERE t.date = %s AND m.market = %s AND NOT (t.stock_id = ANY(%s)) ORDER BY t.stock_id",
        (day, meta_market, list(ids)),
    )
    return [r[0] for r in cur.fetchall()]


class PgDirectSink:
    """把直寫表的 frame 寫進 PG 的 IngestSink（一次 write 一個交易；失敗 rollback 後照原樣 raise）。

    market（stock_meta 的寫法，如 'TWSE'）給定時，當沖／借券多做一件事：列出 PG 那天該市場有、
    這次官方表卻沒有的代號（notes）。
    """

    def __init__(
        self, conn: Any, schema: str, *, keep_first: bool = False, market: str | None = None
    ) -> None:
        self._conn = conn
        self._schema = schema
        self._keep_first = keep_first
        self._market = market
        self.notes: list[str] = []

    def write(self, layer: str, table: str, df: pl.DataFrame, source: str | None = None) -> None:
        """寫一張直寫表（見模組說明的三條規則）。"""
        if table not in sch.DIRECT_PG_TABLES:
            raise DataError(
                table, None, f"{table} 不是直寫 PG 的表（{list(sch.DIRECT_PG_TABLES)}）"
            )
        spec = sch.spec_of(table)
        try:
            with self._conn.cursor() as cur:
                if table in ALERT_TABLES:
                    changed = changed_rows(cur, self._schema, spec, df)
                    if changed.height:
                        first = changed.row(0, named=True)
                        raise DataError(
                            table,
                            first["date"],
                            f"{changed.height} 列與 PG 既有同鍵列內容不同（D-154 §二.6：不覆寫）；"
                            f"例：{changed.head(3).rows()}。確認原因後用 "
                            "scripts/backfill_chip_batch1.py alerts --allow-changes 覆寫。",
                        )
                elif self._market is not None and not df.is_empty():
                    for (day,), sub in df.group_by(["date"]):
                        assert isinstance(day, dt.date)
                        stale = _stale_ids(
                            cur, self._schema, table, day, self._market, sub["stock_id"].to_list()
                        )
                        if stale:
                            self.notes.append(
                                f"{day} {self._market} {table}：PG 有 {len(stale)} 列不在這次的官方表"
                                f"（例 {stale[:5]}），未刪除"
                            )
                if not df.is_empty():
                    upsert(cur, self._schema, spec, df)
                    if table == _DAYTRADE and self._keep_first:
                        insert_first_pub(cur, self._schema, df)
            self._conn.commit()
        except Exception:
            self._conn.rollback()  # 讓同一條連線的下一張表還能寫；錯誤照原樣往上丟
            raise

    def partition_exists(
        self, layer: str, table: str, date: dt.date, source: str | None = None
    ) -> bool:
        """該表那天在 PG 有沒有任何一列（source 不分：直寫表沒有來源分片）。"""
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT 1 FROM {_q(self._schema, table)} WHERE date = %s LIMIT 1", (date,))
            return cur.fetchone() is not None

    def source_dates(self, table: str, source: str, layer: str = "clean") -> list[dt.date]:
        """該表在 PG 已有的日期（排序；source 不分）。"""
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT DISTINCT date FROM {_q(self._schema, table)} ORDER BY date")
            return [r[0] for r in cur.fetchall()]

    def latest_date(self, table: str, layer: str = "clean") -> dt.date | None:
        """該表在 PG 最新的日期。"""
        with self._conn.cursor() as cur:
            cur.execute(f"SELECT max(date) FROM {_q(self._schema, table)}")
            row = cur.fetchone()
            return row[0] if row else None


# ── 每日「籌碼」步驟的補抓計畫 ─────────────────────────────────────────────────


@dataclass(frozen=True)
class ChipFetch:
    """一次要抓的 (表, 市場, 日)。first＝這一次的當沖另存首次公布值；reason 印給人看。"""

    table: str
    market: str
    day: dt.date
    first: bool
    reason: str


def recent_sessions(cur: Any, schema: str, day: dt.date, n: int) -> list[dt.date]:
    """day 之前（不含）最近 n 個交易日，新到舊——交易日＝PG prices_daily 有列的日子。"""
    cur.execute(
        f"SELECT DISTINCT date FROM {_q(schema, 'prices_daily')} "
        "WHERE date < %s ORDER BY date DESC LIMIT %s",
        (day, n),
    )
    return [r[0] for r in cur.fetchall()]


def market_counts(
    cur: Any, schema: str, table: str, days: Sequence[dt.date]
) -> dict[tuple[dt.date, str], int]:
    """(日, stock_meta 市場) → 該表列數；只列有列的組合。"""
    if not days:
        return {}
    cur.execute(
        f"SELECT t.date, m.market, count(*) FROM {_q(schema, table)} t "
        f"JOIN {_q(schema, 'stock_meta')} m ON m.stock_id = t.stock_id "
        "WHERE t.date = ANY(%s) GROUP BY 1, 2",
        (list(days),),
    )
    return {(r[0], r[1]): int(r[2]) for r in cur.fetchall()}


def plan_from(
    sessions: Sequence[dt.date],
    counts: Mapping[str, Mapping[tuple[dt.date, str], int]],
    cfg: ChipDailyConfig,
    *,
    live: bool,
) -> list[ChipFetch]:
    """純函數：交易日（新到舊，sessions[k-1]＝T−k）＋各表各市場已有列數 → 要抓的清單。

    每張表每個市場：config 的 `refetch_offsets`（前第 k 個交易日，覆寫）＋最近 `catchup_trading_days`
    個交易日裡該市場一列都沒有的日子（缺日）。當沖在最小的 offset 那一次存首次公布值——只在 live
    （這一輪就是「今天」）時：手動重跑過去的日子、以及缺日補抓，抓到的可能已是定案值，混進去會把修訂幅度
    低估成 0。
    """
    plan: dict[tuple[str, str, dt.date], ChipFetch] = {}
    for table in CHIP_DAILY_TABLES:
        offsets = cfg.refetch_offsets[table]
        first_k = min(offsets)
        have = counts.get(table, {})
        for market in MARKETS:
            for k in sorted(offsets):
                if k <= len(sessions):
                    day = sessions[k - 1]
                    first = live and table == _DAYTRADE and k == first_k
                    plan[(table, market, day)] = ChipFetch(table, market, day, first, f"T-{k}")
            for day in sessions[: cfg.catchup_trading_days]:
                key = (table, market, day)
                if key not in plan and have.get((day, STOCK_META_MARKET[market]), 0) == 0:
                    plan[key] = ChipFetch(table, market, day, False, "缺日")
    return sorted(plan.values(), key=lambda f: (f.day, f.market, f.table))


def plan_chip_fetches(
    cur: Any, schema: str, day: dt.date, cfg: ChipDailyConfig, *, live: bool
) -> list[ChipFetch]:
    """查 PG 的交易日與各市場列數，產生 day 這一輪要抓的清單（見 `plan_from`）。"""
    depth = max(cfg.catchup_trading_days, *(max(v) for v in cfg.refetch_offsets.values()))
    sessions = recent_sessions(cur, schema, day, depth)
    counts = {t: market_counts(cur, schema, t, sessions) for t in CHIP_DAILY_TABLES}
    return plan_from(sessions, counts, cfg, live=live)


@dataclass(frozen=True)
class Revision:
    """某日當沖：首次公布值 vs 現在（重抓後）的差。"""

    day: dt.date
    compared: int  # 兩邊都有的列
    changed: int  # 股數、兩個金額、旗標任一不同
    shares_abs_diff: int  # |現在 − 首次| 的股數合計
    shares_total: int  # 現在的股數合計
    examples: list[str] = field(default_factory=list)


def daytrade_revision(cur: Any, schema: str, day: dt.date) -> Revision:
    """比 daytrade_first_pub 與 daytrade_daily 在 day 的差（只報不修）。"""
    diff = (
        "f.daytrade_shares IS DISTINCT FROM d.daytrade_shares "
        "OR f.daytrade_buy_amount IS DISTINCT FROM d.daytrade_buy_amount "
        "OR f.daytrade_sell_amount IS DISTINCT FROM d.daytrade_sell_amount "
        "OR f.sell_first_suspended IS DISTINCT FROM d.sell_first_suspended"
    )
    join = (
        f"FROM {_q(schema, _FIRST.name)} f JOIN {_q(schema, _DAYTRADE)} d "
        "ON d.stock_id = f.stock_id AND d.date = f.date WHERE f.date = %s"
    )
    cur.execute(
        f"SELECT count(*), count(*) FILTER (WHERE {diff}), "
        "coalesce(sum(abs(d.daytrade_shares - f.daytrade_shares)), 0), "
        f"coalesce(sum(d.daytrade_shares), 0) {join}",
        (day,),
    )
    n, changed, abs_diff, total = cur.fetchone()
    examples: list[str] = []
    if changed:
        cur.execute(
            f"SELECT f.stock_id, f.daytrade_shares, d.daytrade_shares {join} AND ({diff}) "
            "ORDER BY abs(d.daytrade_shares - f.daytrade_shares) DESC, f.stock_id LIMIT 3",
            (day,),
        )
        examples = [f"{sid} {a:,}→{b:,}" for sid, a, b in cur.fetchall()]
    return Revision(day, int(n), int(changed), int(abs_diff), int(total), examples)
