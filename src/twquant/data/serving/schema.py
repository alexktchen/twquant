"""供應層 PG schema：資料表規格與 DDL（SPEC-009 §2）。

T-031 範圍：六張鏡射表（欄位比照 data/schemas.py）+ sync_state 簿記表。
factor_values / broker_* 等隨 T-032 / T-035 加入。DDL 皆冪等（IF NOT EXISTS）。
`TableSpec` 是欄位/主鍵的單一真相——DDL 與 sync（COPY/upsert 欄位順序）都由它衍生，
避免兩處漂移。本模組**不** import psycopg——只產生 SQL 並在呼叫端連線上執行。

型別對應：Date→date、String→text、Int64→bigint、Float64→double precision、
tz-aware Datetime→timestamptz。stock_id 一律 text（保前導零）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _check_ident(name: str) -> None:
    """schema 名只允許小寫字母/數字/底線；擋 SQL 注入（雖來自受信配置，仍防呆）。"""
    if not _IDENT.match(name):
        raise ValueError(f"非法 schema 識別字：{name!r}（僅允許小寫字母/數字/底線）")


@dataclass(frozen=True)
class TableSpec:
    """一張 PG 表的欄位、主鍵與分區型態。"""

    name: str
    columns: tuple[tuple[str, str], ...]  # (欄名, PG 型別)
    pk: tuple[str, ...]
    snapshot: bool = False  # True＝無 date 分區（stock_meta，每次全量重載）
    indexes: tuple[tuple[str, ...], ...] = ()  # 額外索引，每項一組欄位（如 (("date",),)）

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c for c, _ in self.columns)

    def ddl(self, schema: str) -> str:
        cols = ", ".join(f"{c} {t}" for c, t in self.columns)
        pk = ", ".join(self.pk)
        return f'CREATE TABLE IF NOT EXISTS "{schema}"."{self.name}" ({cols}, PRIMARY KEY ({pk}))'


MIRROR_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "prices_daily",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("open", "double precision"),
            ("high", "double precision"),
            ("low", "double precision"),
            ("close", "double precision"),
            ("volume", "bigint"),
            ("amount", "double precision"),
            ("limit_up_price", "double precision"),
            ("limit_down_price", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "institutional_daily",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("foreign_net", "bigint"),
            ("trust_net", "bigint"),
            ("dealer_net", "bigint"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "margin_daily",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("margin_balance", "bigint"),
            ("short_balance", "bigint"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date"),
        indexes=(("date",),),
    ),
    TableSpec(
        "broker_trades",  # 富邦DJ 券商(母公司)買賣超 top-15±（D-019）；broker_id=券商名、avg_price=null
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("broker_id", "text"),
            ("buy_shares", "bigint"),
            ("sell_shares", "bigint"),
            ("avg_price", "double precision"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date", "broker_id"),
        indexes=(("date",),),
    ),
    TableSpec(
        "monthly_revenue",
        (
            ("date", "date"),
            ("year_month", "text"),
            ("stock_id", "text"),
            ("period_end", "date"),
            ("revenue", "double precision"),
            ("yoy", "double precision"),
            ("available_at", "timestamptz"),
            ("available_at_estimated", "boolean"),
        ),
        pk=("stock_id", "year_month"),
    ),
    TableSpec(
        "shareholding_dispersion",
        (
            ("date", "date"),
            ("stock_id", "text"),
            ("level", "integer"),
            ("holders", "bigint"),
            ("shares", "bigint"),
            ("available_at", "timestamptz"),
        ),
        pk=("stock_id", "date", "level"),
    ),
    TableSpec(
        "stock_meta",
        (
            ("stock_id", "text"),
            ("name", "text"),
            ("market", "text"),
            ("industry", "text"),
            ("list_date", "date"),
            ("delist_date", "date"),
        ),
        pk=("stock_id",),
        snapshot=True,
    ),
)

FACTOR_SPECS: tuple[TableSpec, ...] = (
    TableSpec(
        "factor_values",
        (
            ("factor", "text"),
            ("stock_id", "text"),
            ("date", "date"),
            ("value", "double precision"),
            ("z", "double precision"),
            ("pct", "double precision"),
        ),
        pk=("factor", "stock_id", "date"),
        indexes=(("factor", "date"),),
    ),
    TableSpec(
        "factor_cross_stats",
        (
            ("factor", "text"),
            ("date", "date"),
            ("mean", "double precision"),
            ("std", "double precision"),
            ("n_universe", "integer"),
        ),
        pk=("factor", "date"),
    ),
    TableSpec(
        "composite_daily",
        (
            ("stock_id", "text"),
            ("date", "date"),
            ("signed_z_mean", "double precision"),
            ("pct", "double precision"),
        ),
        pk=("stock_id", "date"),
    ),
)

SYNC_STATE_SPEC = TableSpec(
    "sync_state",
    (
        ("table_name", "text"),
        ("last_date", "date"),
        ("last_synced_at", "timestamptz"),
        ("row_count", "bigint"),
    ),
    pk=("table_name",),
)

# 富邦DJ 分點回補的抓取帳本（D-025）：一列＝一次「已 commit」的 (日, 股) 抓取（含空結果
# n_rows=0）；stock_id 可為 chip_ingest.SENTINEL 標記整日非交易。簿記表（比照 sync_state）：
# 不屬鏡射/因子表，sync/rebuild 不觸碰。
FUBON_FETCH_LOG_SPEC = TableSpec(
    "fubon_fetch_log",
    (
        ("date", "date"),
        ("stock_id", "text"),
        ("n_rows", "integer"),
        ("fetched_at", "timestamptz"),
    ),
    pk=("date", "stock_id"),
)

# 策略選股池每日快照（SPEC-007 §6 / T-041）：一列＝某日某池一名成員，記錄「當日實際
# 顯示」的池組成供回溯——之後改配置閾值不重寫歷史（首錄保留）。衍生表：由 CLI
# `screener-snapshot` 寫入，非鏡射/因子表，sync/rebuild 不觸碰。
SCREENER_DAILY_SPEC = TableSpec(
    "screener_daily",
    (
        ("date", "date"),
        ("pool", "text"),
        ("stock_id", "text"),
        ("close", "double precision"),
        ("chg", "double precision"),
        ("yoy", "double precision"),
        ("conc", "double precision"),
        ("accum_ratio", "double precision"),
        ("streak", "integer"),
        ("universe_n", "integer"),
        ("created_at", "timestamptz"),
    ),
    pk=("date", "pool", "stock_id"),
)

_ALL_SPECS: tuple[TableSpec, ...] = (
    *MIRROR_SPECS,
    *FACTOR_SPECS,
    SYNC_STATE_SPEC,
    FUBON_FETCH_LOG_SPEC,
    SCREENER_DAILY_SPEC,
)

MIRROR_TABLES: tuple[str, ...] = tuple(s.name for s in MIRROR_SPECS)
FACTOR_TABLES: tuple[str, ...] = tuple(s.name for s in FACTOR_SPECS)
ALL_TABLES: tuple[str, ...] = tuple(s.name for s in _ALL_SPECS)


def spec_of(table: str) -> TableSpec:
    """取某表的 TableSpec；未知表名 raise。"""
    for s in _ALL_SPECS:
        if s.name == table:
            return s
    raise ValueError(f"未知供應層表：{table}")


def rebuild_targets(tables: list[str] | None) -> tuple[TableSpec, ...]:
    """rebuild 要重灌的鏡射表 spec；tables=None ＝全部鏡射表。

    rebuild 只能處理**鏡射表**（自 clean 資料湖 COPY 全量重灌）。因子表
    （factor_values / factor_cross_stats / composite_daily）由 PitView 逐日物化、
    非鏡射自資料湖，故傳入這些表名（或任何非鏡射表名）一律 raise——不靜默略過無效
    targets（黃金規則 5）。因子表的錯誤訊息指引改用 `serving materialize --from/--to`。
    """
    if tables is None:
        return MIRROR_SPECS
    invalid = [t for t in tables if t not in MIRROR_TABLES]
    if invalid:
        factor = [t for t in invalid if t in FACTOR_TABLES]
        hint = (
            f" 其中 {factor} 為因子表，由 PitView 逐日物化（非鏡射自資料湖）——"
            "請改用 `python -m twquant serving materialize --from <起> --to <迄>` 重物化。"
            if factor
            else ""
        )
        raise ValueError(
            f"serving rebuild 只支援鏡射表 {list(MIRROR_TABLES)}；"
            f"收到不支援的表名 {invalid}。{hint}"
        )
    return tuple(s for s in MIRROR_SPECS if s.name in tables)


def table_ddls(schema: str) -> dict[str, str]:
    """回傳 {表名: CREATE TABLE IF NOT EXISTS ...}（schema 限定，冪等）。"""
    _check_ident(schema)
    return {s.name: s.ddl(schema) for s in _ALL_SPECS}


def index_ddls(schema: str) -> list[str]:
    """回傳所有輔助索引 DDL（每 spec 的 indexes，冪等）。"""
    _check_ident(schema)
    out: list[str] = []
    for s in _ALL_SPECS:
        for cols in s.indexes:
            iname = f"{s.name}_{'_'.join(cols)}_idx"
            out.append(
                f'CREATE INDEX IF NOT EXISTS "{iname}" ON "{schema}"."{s.name}" ({", ".join(cols)})'
            )
    return out


def ensure_schema(conn: Any, schema: str = "serving") -> None:
    """建立 serving schema 與所有 T-031 資料表＋索引（冪等）。conn 為呼叫端 PG 連線。"""
    _check_ident(schema)
    with conn.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
        for ddl in table_ddls(schema).values():
            cur.execute(ddl)
        for ddl in index_ddls(schema):
            cur.execute(ddl)
    conn.commit()
