"""T-402／T-404（SPEC-017 §5～§7、D-200）：台股、族群、國外公司、主題 → 新聞庫 news.entities／news.graph_edges。

    uv run --all-groups python scripts/news_sync_entities.py [--dry-run] [--env-file D:/project/twquant/.env]

介面契約：docs/HANDOFF-2026-10-19-news-graph-real.md §6。
- 主庫（psycopg，TWQUANT_SUPABASE_DSN）：serving.stock_meta（未下市）→ tw:<代號>；
  serving.stock_group ＋ serving.prices_daily 最新一天的成交金額 → 族群 grp:<族群> 與 belongs 線（族群內成交金額名次，1 最大）。
- configs/news_entities.yaml → us:<ticker>／co:<id>／th:<id> 實體；configs/news_edges.yaml → 草擬的 supplier／theme／peer 線。
- 新聞庫：Management API（POST /v1/projects/<ref>/database/query），每批 ≤ BATCH_ROWS 列。
  實體 upsert；現役（valid_to is null）卻不在新集合裡的實體 → valid_to = now()。
  線照 (src, dst, type) 比對：新集合沒有、或狀態／說明／名次／來源變了 → 舊的 valid_to = now()，再插新的一條。
- --dry-run：兩邊照讀、算差異、只印統計，不寫。

SQL 字串一律經 sql_literal() 跳脫（單引號加倍、陣列 array['…']::text[]）；.env 的值不印出來。
每一批是 Management API 的一次呼叫（各自一個交易）：中途失敗時重跑即可——差異是重新算的，不會重複插線。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTITIES_CFG = ROOT / "configs" / "news_entities.yaml"
EDGES_CFG = ROOT / "configs" / "news_edges.yaml"
# 0.2 兩個 data/ 的陷阱同理：.env 只有主 checkout 那份，worktree 裡沒有
DEFAULT_ENV = pathlib.Path("D:/project/twquant/.env")
ENV_KEYS = ("TWQUANT_SUPABASE_DSN", "SUPABASE_NEWS_ACCESS_TOKEN", "TWQUANT_NEWS_PROJECT_REF")

API_URL = "https://api.supabase.com/v1/projects/{ref}/database/query"
USER_AGENT = "twquant-news-sync/1.0"  # 不帶 UA 的 Python-urllib 可能被 Cloudflare 擋
HTTP_TIMEOUT_S = 120  # 一批 500 列的 upsert 在免費方案上通常 1～3 秒
BATCH_ROWS = 500  # 契約 §6：每批 ≤ 500 列
MAIN_SCHEMA = "serving"  # 同 configs/serving.yaml 的 pg_schema（雲端主庫）

ENTITY_TYPES = ("tw", "us", "company", "group", "theme")
EDGE_TYPES = ("supplier", "belongs", "member", "peer", "theme")
STATUSES = ("approved", "draft")
_LOWER_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_TICKER = re.compile(r"^[A-Z0-9][A-Z0-9.\-]*$")
_STOCK_ID = re.compile(r"^[0-9A-Z]{4,6}$")


# 例外自己定義、不 import twquant.errors：`mypy scripts/news_sync_entities.py` 單獨檢查時 twquant 沒有 py.typed，
# 匯入它會讓 mypy 報 import-untyped（scripts/ 其他檔同樣會）。訊息規格照 SPEC-000：含表名與期望 vs 實際。
class SyncError(Exception):
    """這支同步腳本的例外根。"""


class ConfigError(SyncError):
    """.env、news_entities.yaml、news_edges.yaml 缺檔或格式不對。"""


class DataError(SyncError):
    """主庫資料不合預期（哪張表、哪檔、期望 vs 實際）。"""

    def __init__(self, table: str, detail: str) -> None:
        self.table = table
        self.detail = detail
        super().__init__(f"[{table}] {detail}")


class NewsApiError(SyncError):
    """新聞庫 Management API 呼叫失敗（HTTP 錯誤或回應不是 JSON 陣列）。"""


@dataclass(frozen=True)
class Entity:
    """news.entities 的一列（valid_from／valid_to 由資料庫管）。"""

    id: str
    type: str
    label: str
    sub: str | None
    aliases: tuple[str, ...]
    source: str


@dataclass(frozen=True)
class Edge:
    """news.graph_edges 的一列（valid_from／valid_to 由資料庫管）。"""

    src: str
    dst: str
    type: str
    status: str
    label: str | None
    rank: int | None
    source: str

    def key(self) -> tuple[str, str, str]:
        """同一條線的身分：(src, dst, type)；現役的同一身分只能有一條。"""
        return (self.src, self.dst, self.type)


# ── SQL 組字串 ─────────────────────────────────────────────────────────────────


def sql_literal(value: object) -> str:
    """Python 值 → PostgreSQL 字面值。只認 None／bool／int／str／字串序列；其他型別 raise。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        if "\x00" in value:
            raise ValueError(f"字串含 NUL 字元，PostgreSQL 存不了：{value!r}")
        # standard_conforming_strings 預設 on：反斜線照字面，只要把單引號加倍
        return "'" + value.replace("'", "''") + "'"
    if isinstance(value, list | tuple):
        if not all(isinstance(v, str) for v in value):
            raise TypeError(f"陣列只收字串：{value!r}")
        if not value:
            return "array[]::text[]"
        return "array[" + ", ".join(sql_literal(v) for v in value) + "]::text[]"
    raise TypeError(f"sql_literal 不支援的型別 {type(value).__name__}：{value!r}")


def _values(rows: Iterable[Sequence[object]]) -> str:
    return ",\n".join("(" + ", ".join(sql_literal(v) for v in row) + ")" for row in rows)


def entity_upsert_sql(entities: Sequence[Entity]) -> str:
    """一批實體的 insert … on conflict (id) do update（契約 §6 第 4 步）。"""
    if not entities:
        raise ValueError("entity_upsert_sql：空批次")
    rows = [(e.id, e.type, e.label, e.sub, list(e.aliases), e.source) for e in entities]
    return (
        "insert into news.entities (id, type, label, sub, aliases, source) values\n"
        + _values(rows)
        + "\non conflict (id) do update set label = excluded.label, sub = excluded.sub, "
        "aliases = excluded.aliases, source = excluded.source, valid_to = null"
    )


def entity_close_sql(ids: Sequence[str]) -> str:
    """把一批現役實體關掉（valid_to = now()）。"""
    if not ids:
        raise ValueError("entity_close_sql：空批次")
    return (
        "update news.entities set valid_to = now() where valid_to is null and id in ("
        + ", ".join(sql_literal(i) for i in ids)
        + ")"
    )


def edge_close_sql(keys: Sequence[tuple[str, str, str]]) -> str:
    """把一批現役的線關掉（valid_to = now()），以 (src, dst, type) 指定。"""
    if not keys:
        raise ValueError("edge_close_sql：空批次")
    return (
        "update news.graph_edges set valid_to = now() where valid_to is null "
        "and (src, dst, type) in (values\n" + _values(keys) + ")"
    )


def edge_rerank_sql(edges: Sequence[Edge]) -> str:
    """一批現役線只改 rank（以 (src, dst, type) 指定）。"""
    if not edges:
        raise ValueError("edge_rerank_sql：空批次")
    rows = [(e.src, e.dst, e.type, e.rank) for e in edges]
    return (
        "update news.graph_edges g set rank = v.rank::int from (values\n"
        + _values(rows)
        + "\n) as v(src, dst, type, rank) where g.valid_to is null "
        "and g.src = v.src and g.dst = v.dst and g.type = v.type"
    )


def edge_insert_sql(edges: Sequence[Edge]) -> str:
    """一批新線的 insert（valid_from 用資料庫預設 now()）。"""
    if not edges:
        raise ValueError("edge_insert_sql：空批次")
    rows = [(e.src, e.dst, e.type, e.status, e.label, e.rank, e.source) for e in edges]
    return (
        "insert into news.graph_edges (src, dst, type, status, label, rank, source) values\n"
        + _values(rows)
    )


EXISTING_ENTITIES_SQL = "select id from news.entities where valid_to is null"
EXISTING_EDGES_SQL = (
    "select src, dst, type, status, label, rank, source from news.graph_edges "
    "where valid_to is null"
)


def batched[T](items: Sequence[T], size: int) -> Iterator[list[T]]:
    """依序切成每批最多 size 個。"""
    if size < 1:
        raise ValueError(f"批次大小要 ≥ 1，拿到 {size}")
    for i in range(0, len(items), size):
        yield list(items[i : i + size])


# ── 主庫 → 實體與 belongs 線 ─────────────────────────────────────────────────


def stock_entities(meta: Iterable[tuple[str, str | None]]) -> list[Entity]:
    """(stock_id, name)（已只含未下市）→ tw:<代號>；label＝name、sub＝代號、aliases＝[name]。"""
    out: list[Entity] = []
    for sid, name in meta:
        if not isinstance(sid, str) or not _STOCK_ID.match(sid):
            raise DataError(f"{MAIN_SCHEMA}.stock_meta", f"stock_id 應是 4～6 碼字串，拿到 {sid!r}")
        if not name or not name.strip():
            raise DataError(f"{MAIN_SCHEMA}.stock_meta", f"{sid} 沒有 name")
        label = name.strip()
        out.append(Entity(f"tw:{sid}", "tw", label, sid, (label,), "stock_meta"))
    return out


def rank_groups(groups: Iterable[tuple[str, str]], amounts: dict[str, float | None]) -> list[Edge]:
    """(stock_id, grp) ＋ 最新一天成交金額 → belongs 線；族群內依金額由大到小排名（1 最大）。

    當天沒成交（停牌、沒有那一列、amount 為 null）的排在最後；同金額依代號排，結果是決定性的。
    """
    by_grp: dict[str, list[str]] = {}
    seen: set[str] = set()
    for sid, grp in groups:
        if not grp or not grp.strip():
            raise DataError(f"{MAIN_SCHEMA}.stock_group", f"{sid} 的 grp 是空的")
        if sid in seen:
            raise DataError(f"{MAIN_SCHEMA}.stock_group", f"{sid} 出現兩次（主鍵應是 stock_id）")
        seen.add(sid)
        by_grp.setdefault(grp.strip(), []).append(sid)

    def order(sid: str) -> tuple[int, float, str]:
        amt = amounts.get(sid)
        return (1, 0.0, sid) if amt is None else (0, -amt, sid)

    out: list[Edge] = []
    for grp in sorted(by_grp):
        for rank, sid in enumerate(sorted(by_grp[grp], key=order), start=1):
            out.append(
                Edge(f"tw:{sid}", f"grp:{grp}", "belongs", "approved", None, rank, "stock_group")
            )
    return out


def group_entities(belongs: Iterable[Edge]) -> list[Entity]:
    """belongs 線的終點 → 族群實體 grp:<族群>，sub＝「族群」。

    aliases 留空：mention.ts 的 buildIndex 本來就把 label 當別名。
    """
    names = sorted({e.dst.removeprefix("grp:") for e in belongs})
    return [Entity(f"grp:{g}", "group", g, "族群", (), "stock_group") for g in names]


# ── configs/news_entities.yaml、news_edges.yaml ──────────────────────────────


def load_yaml(path: pathlib.Path) -> dict[str, Any]:
    """讀一份 news_* 設定；檔案不存在或頂層不是 mapping → ConfigError。"""
    if not path.exists():
        raise ConfigError(
            f"找不到 {path}：實體與草擬線的設定（契約 docs/HANDOFF-2026-10-19-news-graph-real.md §6）還沒寫好"
        )
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ConfigError(f"{path.name}：頂層應是 mapping，拿到 {type(doc).__name__}")
    return doc


def _section(doc: dict[str, Any], key: str, where: str) -> list[dict[str, Any]]:
    raw = doc.get(key, [])
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise ConfigError(f"{where}：{key} 應是清單，拿到 {type(raw).__name__}")
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ConfigError(f"{where}：{key}[{i}] 應是 mapping，拿到 {item!r}")
    return raw


def _check_keys(item: dict[str, Any], allowed: set[str], required: set[str], where: str) -> None:
    extra = sorted(set(item) - allowed)
    if extra:
        raise ConfigError(f"{where}：不認得的欄位 {extra}（可用：{sorted(allowed)}）")
    missing = sorted(required - set(item))
    if missing:
        raise ConfigError(f"{where}：缺欄位 {missing}")


def _text(item: dict[str, Any], key: str, where: str) -> str:
    v = item.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ConfigError(f"{where}：{key} 應是非空字串，拿到 {v!r}")
    return v.strip()


def _str_list(item: dict[str, Any], key: str, where: str) -> list[str]:
    v = item.get(key, [])
    if v is None:
        v = []
    if not isinstance(v, list):
        raise ConfigError(f"{where}：{key} 應是字串清單，拿到 {v!r}")
    out: list[str] = []
    for x in v:
        if not isinstance(x, str) or not x.strip():
            # YAML 不加引號的 0050 會變成整數 50（黃金規則 4）
            raise ConfigError(f"{where}：{key} 裡的 {x!r} 不是非空字串（代號要加引號）")
        out.append(x.strip())
    return out


def _dedup(xs: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(xs))


def config_entities(doc: dict[str, Any], where: str = "news_entities.yaml") -> list[Entity]:
    """news_entities.yaml（us／companies／themes）→ us:<ticker>、co:<id>、th:<id> 實體。"""
    unknown = sorted(set(doc) - {"us", "companies", "themes"})
    if unknown:
        raise ConfigError(f"{where}：不認得的頂層鍵 {unknown}（可用：us、companies、themes）")
    out: list[Entity] = []
    for i, it in enumerate(_section(doc, "us", where)):
        w = f"{where} us[{i}]"
        _check_keys(it, {"ticker", "label", "aliases"}, {"ticker", "label"}, w)
        ticker = _text(it, "ticker", w)
        if not _TICKER.match(ticker):
            raise ConfigError(f"{w}：ticker 應是大寫英數（可含 . -），拿到 {ticker!r}")
        aliases = _dedup(_str_list(it, "aliases", w))
        out.append(Entity(f"us:{ticker}", "us", _text(it, "label", w), ticker, aliases, "config"))
    for i, it in enumerate(_section(doc, "companies", where)):
        w = f"{where} companies[{i}]"
        _check_keys(it, {"id", "label", "country", "aliases"}, {"id", "label", "country"}, w)
        cid = _text(it, "id", w)
        if not _LOWER_ID.match(cid):
            raise ConfigError(f"{w}：id 應是小寫英數（可含 _ -），拿到 {cid!r}")
        aliases = _dedup(_str_list(it, "aliases", w))
        out.append(
            Entity(
                f"co:{cid}",
                "company",
                _text(it, "label", w),
                _text(it, "country", w),
                aliases,
                "config",
            )
        )
    for i, it in enumerate(_section(doc, "themes", where)):
        w = f"{where} themes[{i}]"
        _check_keys(it, {"id", "label", "aliases"}, {"id", "label"}, w)
        tid = _text(it, "id", w)
        if not _LOWER_ID.match(tid):
            raise ConfigError(f"{w}：id 應是小寫英數（可含 _ -），拿到 {tid!r}")
        aliases = _dedup(_str_list(it, "aliases", w))
        out.append(Entity(f"th:{tid}", "theme", _text(it, "label", w), "主題", aliases, "config"))
    return out


_EDGE_KEYS: dict[str, tuple[set[str], set[str]]] = {
    # 區段 → (可用欄位, 必填欄位)
    "supplier": ({"src", "tw", "status", "label"}, {"src", "tw", "status"}),
    "theme": ({"src", "grp", "tw", "status", "label"}, {"src", "status"}),
    "peer": ({"src", "tw", "status", "label"}, {"src", "tw", "status", "label"}),
}


def config_edges(doc: dict[str, Any], where: str = "news_edges.yaml") -> list[Edge]:
    """news_edges.yaml（supplier／theme／peer）→ 線；tw 代號 → tw:<代號>、grp 族群名 → grp:<族群>。"""
    unknown = sorted(set(doc) - set(_EDGE_KEYS))
    if unknown:
        raise ConfigError(f"{where}：不認得的頂層鍵 {unknown}（可用：{sorted(_EDGE_KEYS)}）")
    out: list[Edge] = []
    for kind, (allowed, required) in _EDGE_KEYS.items():
        for i, it in enumerate(_section(doc, kind, where)):
            w = f"{where} {kind}[{i}]"
            _check_keys(it, allowed, required, w)
            src = _text(it, "src", w)
            status = _text(it, "status", w)
            if status not in STATUSES:
                raise ConfigError(f"{w}：status 應是 {STATUSES} 之一，拿到 {status!r}")
            label = _text(it, "label", w) if "label" in it else None
            dsts = [f"tw:{s}" for s in _str_list(it, "tw", w)]
            dsts += [f"grp:{g}" for g in _str_list(it, "grp", w)]
            if not dsts:
                raise ConfigError(f"{w}：沒有任何終點（tw／grp 都是空的）")
            out.extend(Edge(src, d, kind, status, label, None, "config") for d in dsts)
    return out


# ── 合併、檢查、差異 ─────────────────────────────────────────────────────────


def check_entities(entities: Sequence[Entity]) -> None:
    """id 不得重複、型別要在清單裡。"""
    dup = sorted(i for i, n in Counter(e.id for e in entities).items() if n > 1)
    if dup:
        raise ConfigError(f"實體 id 重複：{dup[:20]}（共 {len(dup)} 個）")
    bad = sorted({e.type for e in entities} - set(ENTITY_TYPES))
    if bad:
        raise ConfigError(f"實體型別不在 {ENTITY_TYPES} 裡：{bad}")


def check_edges(edges: Sequence[Edge], known: set[str]) -> None:
    """兩端都要是這次的實體；(src, dst, type) 不得重複；不得自己連自己。"""
    missing = sorted(
        {f"{e.type} {e.src} → {e.dst}" for e in edges if e.src not in known or e.dst not in known}
    )
    if missing:
        raise ConfigError(
            f"有 {len(missing)} 條線的端點不在實體裡（台股要未下市、族群名要照主庫 stock_group）：{missing[:20]}"
        )
    selfloop = sorted({e.src for e in edges if e.src == e.dst})
    if selfloop:
        raise ConfigError(f"線的起點與終點相同：{selfloop[:20]}")
    dup = sorted(k for k, n in Counter(e.key() for e in edges).items() if n > 1)
    if dup:
        raise ConfigError(f"同一條線 (src, dst, type) 出現不只一次：{dup[:20]}（共 {len(dup)} 條）")
    bad = sorted({e.type for e in edges} - set(EDGE_TYPES))
    if bad:
        raise ConfigError(f"線的型別不在 {EDGE_TYPES} 裡：{bad}")


@dataclass(frozen=True)
class Plan:
    """這次要寫進新聞庫的東西。"""

    upsert: list[Entity]
    close_entities: list[str]
    close_edges: list[tuple[str, str, str]]
    insert_edges: list[Edge]
    # 只有族群名次變了的 belongs 線：原地更新 rank（名次每天跟著成交金額變，關掉重插會讓表每天長一批）
    rerank_edges: list[Edge] = field(default_factory=list)


def diff_entities(existing_ids: Iterable[str], new: Sequence[Entity]) -> list[str]:
    """現役但不在新集合裡的實體 id（要 valid_to = now()），排序過。"""
    keep = {e.id for e in new}
    return sorted(set(existing_ids) - keep)


def diff_edges(
    existing: Sequence[Edge], new: Sequence[Edge]
) -> tuple[list[tuple[str, str, str]], list[Edge]]:
    """現役的線 vs 新集合 → (要關的 (src, dst, type), 要插的線)。

    內容（狀態、說明、名次、來源）完全一樣的不動；變了的先關舊的再插新的。
    現役裡同一身分有不只一條（不該發生）→ 全部關掉、重插一條。
    """
    count = Counter(e.key() for e in existing)
    new_set = set(new)
    close = {e.key() for e in existing if e not in new_set or count[e.key()] > 1}
    alive = {e for e in existing if e.key() not in close}
    insert = [e for e in new if e not in alive]
    return sorted(close), sorted(insert, key=lambda e: (e.type, e.src, e.dst))


def diff_edges_rerank(
    existing: Sequence[Edge], new: Sequence[Edge]
) -> tuple[list[tuple[str, str, str]], list[Edge], list[Edge]]:
    """同 diff_edges，但「只有 rank 不同」的線不關不插，改列進第三個清單原地更新。"""
    close, insert = diff_edges(existing, new)
    old = {e.key(): e for e in existing}
    rerank = [
        e
        for e in insert
        if e.key() in old and e.key() in set(close) and replace(old[e.key()], rank=e.rank) == e
    ]
    keys = {e.key() for e in rerank}
    return (
        [k for k in close if k not in keys],
        [e for e in insert if e.key() not in keys],
        rerank,
    )


def make_plan(
    new_entities: Sequence[Entity],
    new_edges: Sequence[Edge],
    existing_ids: Iterable[str],
    existing_edges: Sequence[Edge],
) -> Plan:
    """檢查新集合、算差異。"""
    check_entities(new_entities)
    check_edges(new_edges, {e.id for e in new_entities})
    close_edges, insert_edges, rerank = diff_edges_rerank(existing_edges, new_edges)
    return Plan(
        upsert=sorted(new_entities, key=lambda e: e.id),
        close_entities=diff_entities(existing_ids, new_entities),
        close_edges=close_edges,
        insert_edges=insert_edges,
        rerank_edges=rerank,
    )


def plan_statements(plan: Plan, batch: int = BATCH_ROWS) -> list[str]:
    """依序要執行的 SQL：先 upsert 實體 → 關線 → 插線 → 關實體（先關後插，現役同一身分始終只有一條）。"""
    stmts = [entity_upsert_sql(b) for b in batched(plan.upsert, batch)]
    stmts += [edge_close_sql(b) for b in batched(plan.close_edges, batch)]
    stmts += [edge_rerank_sql(b) for b in batched(plan.rerank_edges, batch)]
    stmts += [edge_insert_sql(b) for b in batched(plan.insert_edges, batch)]
    stmts += [entity_close_sql(b) for b in batched(plan.close_entities, batch)]
    return stmts


def edge_from_row(row: dict[str, Any]) -> Edge:
    """Management API 回的一列 → Edge。"""
    rank = row.get("rank")
    return Edge(
        str(row["src"]),
        str(row["dst"]),
        str(row["type"]),
        str(row["status"]),
        None if row.get("label") is None else str(row["label"]),
        None if rank is None else int(rank),
        str(row["source"]),
    )


# ── IO：.env、主庫、Management API ───────────────────────────────────────────


def load_env(path: pathlib.Path, keys: Sequence[str] = ENV_KEYS) -> dict[str, str]:
    """讀 .env，回傳 keys 這幾個；缺任何一個（或值是空的）→ ConfigError，訊息只列鍵名、不列值。"""
    if not path.exists():
        raise ConfigError(f"找不到 {path}")
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    missing = [k for k in keys if not env.get(k)]
    if missing:
        raise ConfigError(f"{path} 缺少或是空值：{missing}")
    return {k: env[k] for k in keys}


Opener = Callable[[urllib.request.Request, float], Any]


def _default_open(req: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(req, timeout=timeout)


def management_query(
    ref: str, token: str, query: str, *, opener: Opener = _default_open
) -> list[dict[str, Any]]:
    """在新聞庫跑一段 SQL（Management API），回傳結果列（DML 回 []）。HTTP 錯誤 → NewsApiError。"""
    req = urllib.request.Request(
        API_URL.format(ref=ref),
        data=json.dumps({"query": query}).encode("utf-8"),
        headers={
            "authorization": f"Bearer {token}",
            "content-type": "application/json",
            "user-agent": USER_AGENT,
        },
        method="POST",
    )
    head = query[:80].replace("\n", " ")
    try:
        with opener(req, HTTP_TIMEOUT_S) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise NewsApiError(f"新聞庫查詢失敗 HTTP {e.code}（{head}…）：{detail}") from e
    try:
        rows = json.loads(body)
    except json.JSONDecodeError as e:
        raise NewsApiError(f"新聞庫回應不是 JSON（{head}…）：{body[:200]}") from e
    if not isinstance(rows, list):
        raise NewsApiError(f"新聞庫回應應是陣列（{head}…）：{body[:200]}")
    return rows


def read_main(
    dsn: str,
) -> tuple[list[tuple[str, str | None]], list[tuple[str, str]], dict[str, float | None], str]:
    """主庫：未下市的 (stock_id, name)、(stock_id, grp)、最新一天的成交金額、那一天（字串）。"""
    import psycopg  # 延後匯入：測試不需要 psycopg

    s = MAIN_SCHEMA
    with psycopg.connect(dsn, prepare_threshold=None, connect_timeout=20) as conn:
        meta = conn.execute(
            f"select stock_id, name from {s}.stock_meta where delist_date is null order by stock_id"
        ).fetchall()
        groups = conn.execute(
            f"select stock_id, grp from {s}.stock_group order by stock_id"
        ).fetchall()
        row = conn.execute(f"select max(date) from {s}.prices_daily").fetchone()
        if row is None or row[0] is None:
            raise DataError(f"{s}.prices_daily", "沒有任何一天的行情，排不出族群名次")
        day = row[0]
        amounts = conn.execute(
            f"select stock_id, amount from {s}.prices_daily where date = %s", (day,)
        ).fetchall()
    return (
        [(str(a), b) for a, b in meta],
        [(str(a), str(b)) for a, b in groups],
        {str(a): (None if b is None else float(b)) for a, b in amounts},
        day.isoformat(),
    )


# ── 主流程 ───────────────────────────────────────────────────────────────────


def build_new(
    meta: Sequence[tuple[str, str | None]],
    groups: Sequence[tuple[str, str]],
    amounts: dict[str, float | None],
    entities_doc: dict[str, Any],
    edges_doc: dict[str, Any],
) -> tuple[list[Entity], list[Edge], int]:
    """主庫資料＋兩份設定 → (實體, 線, 族群表裡不是未下市台股而略過的列數)。"""
    config = config_entities(entities_doc)
    # 跟國外公司或主題同名的台股（例：5007 三星、8424 惠普）不進實體表：不然「三星電子」的新聞會連到鋼鐵股
    reserved = {a for e in config for a in (e.label, *e.aliases)}
    tw = [e for e in stock_entities(meta) if e.label not in reserved]
    active = {e.sub for e in tw}
    kept = [(sid, g) for sid, g in groups if sid in active]
    belongs = rank_groups(kept, amounts)
    entities = tw + group_entities(belongs) + config
    edges = belongs + config_edges(edges_doc)
    return entities, edges, len(groups) - len(kept)


def summary(plan: Plan, new_entities: Sequence[Entity], new_edges: Sequence[Edge]) -> list[str]:
    """要印的統計行（不含任何金鑰）。"""

    def by(types: Iterable[str]) -> str:
        c = Counter(types)
        return "、".join(f"{t} {c[t]}" for t in EDGE_TYPES if c.get(t))

    def n_by(n: int, types: Iterable[str]) -> str:
        return f"{n}（{by(types)}）" if n else "0"

    drafts = sum(e.status == "draft" for e in new_edges)
    closing = plan.close_entities
    return [
        f"實體 {len(new_entities)}："
        + "、".join(f"{t} {sum(e.type == t for e in new_entities)}" for t in ENTITY_TYPES),
        f"  要關掉的實體 {len(closing)}" + (f"（例：{closing[:5]}）" if closing else ""),
        f"線 {len(new_edges)}：{by(e.type for e in new_edges)}",
        f"  草擬 {drafts}、核可 {len(new_edges) - drafts}",
        "  新增 " + n_by(len(plan.insert_edges), (e.type for e in plan.insert_edges)),
        "  關閉 " + n_by(len(plan.close_edges), (k[2] for k in plan.close_edges)),
    ]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="台股／族群／國外公司／主題 → 新聞庫 news.entities、news.graph_edges"
    )
    ap.add_argument("--dry-run", action="store_true", help="只讀兩邊、印統計，不寫新聞庫")
    ap.add_argument(
        "--env-file", default=str(DEFAULT_ENV), help="主 checkout 的 .env（值不會印出來）"
    )
    args = ap.parse_args(argv)
    t0 = time.monotonic()

    env = load_env(pathlib.Path(args.env_file))
    entities_doc = load_yaml(ENTITIES_CFG)
    edges_doc = load_yaml(EDGES_CFG)
    meta, groups, amounts, day = read_main(env["TWQUANT_SUPABASE_DSN"])
    new_entities, new_edges, skipped = build_new(meta, groups, amounts, entities_doc, edges_doc)
    print(
        f"主庫：未下市 {len(meta)} 檔、族群表 {len(groups)} 列（略過非未下市 {skipped}）、成交金額取 {day}"
    )

    ref, token = env["TWQUANT_NEWS_PROJECT_REF"], env["SUPABASE_NEWS_ACCESS_TOKEN"]

    def query(sql: str) -> list[dict[str, Any]]:
        return management_query(ref, token, sql)

    existing_ids = [str(r["id"]) for r in query(EXISTING_ENTITIES_SQL)]
    existing_edges = [edge_from_row(r) for r in query(EXISTING_EDGES_SQL)]
    print(f"新聞庫現役：實體 {len(existing_ids)}、線 {len(existing_edges)}")

    plan = make_plan(new_entities, new_edges, existing_ids, existing_edges)
    for line in summary(plan, new_entities, new_edges):
        print(line)
    stmts = plan_statements(plan)
    if args.dry_run:
        print(f"--dry-run：不寫（會執行 {len(stmts)} 批 SQL）；花 {time.monotonic() - t0:.1f} 秒")
        return 0
    for i, stmt in enumerate(stmts, start=1):
        query(stmt)
        print(f"  批 {i}/{len(stmts)} 完成", flush=True)
    print(f"完成：{len(stmts)} 批；花 {time.monotonic() - t0:.1f} 秒")
    return 0


if __name__ == "__main__":
    sys.exit(main())
