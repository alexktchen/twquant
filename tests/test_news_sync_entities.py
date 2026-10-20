"""T-402／T-404：新聞庫實體同步腳本——排名、設定解析、差異、SQL 組字串與跳脫。離線：不連主庫、不連 Management API。"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import urllib.error
import urllib.request
from email.message import Message
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "news_sync_entities.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("news_sync_entities", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["news_sync_entities"] = mod
    spec.loader.exec_module(mod)
    return mod


m: Any = _load()

ENTITIES_DOC: dict[str, Any] = {
    "us": [{"ticker": "NVDA", "label": "輝達", "aliases": ["Nvidia", "NVIDIA", "輝達", "Nvidia"]}],
    "companies": [
        {"id": "samsung", "label": "三星", "country": "韓國", "aliases": ["Samsung", "三星電子"]}
    ],
    "themes": [{"id": "memory", "label": "記憶體", "aliases": ["記憶體", "DRAM"]}],
}
EDGES_DOC: dict[str, Any] = {
    "supplier": [{"src": "us:NVDA", "tw": ["2382", "2317"], "status": "draft"}],
    "theme": [{"src": "th:memory", "grp": ["半導體（記憶體）"], "tw": ["2408"], "status": "draft"}],
    "peer": [{"src": "co:samsung", "tw": ["2408"], "label": "同業", "status": "draft"}],
}
META = [
    ("2317", "鴻海"),
    ("2382", "廣達"),
    ("2408", "南亞科"),
    ("2344", "華邦電"),
    ("0050", "元大台灣50"),
]
GROUPS = [("2408", "半導體（記憶體）"), ("2344", "半導體（記憶體）"), ("2317", "電腦及週邊設備")]
AMOUNTS: dict[str, float | None] = {"2408": 5e9, "2344": 8e9, "2317": 2e10}


# ── sql_literal ──────────────────────────────────────────────────────────────


def test_sql_literal_escapes_single_quotes() -> None:
    assert m.sql_literal("O'Reilly") == "'O''Reilly'"
    assert m.sql_literal("'; drop table news.entities; --") == "'''; drop table news.entities; --'"


def test_sql_literal_keeps_backslash_and_cjk_literal() -> None:
    assert m.sql_literal("a\\b") == "'a\\b'"
    assert m.sql_literal("半導體（記憶體）") == "'半導體（記憶體）'"


def test_sql_literal_scalars() -> None:
    assert m.sql_literal(None) == "null"
    assert m.sql_literal(True) == "true"
    assert m.sql_literal(False) == "false"
    assert m.sql_literal(3) == "3"


def test_sql_literal_arrays() -> None:
    assert m.sql_literal(["Nvidia", "輝達"]) == "array['Nvidia', '輝達']::text[]"
    assert m.sql_literal(["It's"]) == "array['It''s']::text[]"
    assert m.sql_literal([]) == "array[]::text[]"
    assert m.sql_literal(("a",)) == "array['a']::text[]"


def test_sql_literal_rejects_other_types() -> None:
    with pytest.raises(TypeError):
        m.sql_literal(1.5)
    with pytest.raises(TypeError):
        m.sql_literal([1, 2])
    with pytest.raises(ValueError, match="NUL"):
        m.sql_literal("a\x00b")


# ── SQL 組字串 ───────────────────────────────────────────────────────────────


def test_entity_upsert_sql() -> None:
    e = m.Entity("us:NVDA", "us", "輝達", "NVDA", ("Nvidia", "It's"), "config")
    sql = m.entity_upsert_sql([e])
    assert sql.startswith(
        "insert into news.entities (id, type, label, sub, aliases, source) values"
    )
    assert "('us:NVDA', 'us', '輝達', 'NVDA', array['Nvidia', 'It''s']::text[], 'config')" in sql
    assert sql.endswith(
        "on conflict (id) do update set label = excluded.label, sub = excluded.sub, "
        "aliases = excluded.aliases, source = excluded.source, valid_to = null"
    )


def test_entity_upsert_sql_null_sub_and_empty_aliases() -> None:
    e = m.Entity("grp:散熱", "group", "散熱", None, (), "stock_group")
    assert (
        "('grp:散熱', 'group', '散熱', null, array[]::text[], 'stock_group')"
        in m.entity_upsert_sql([e])
    )


def test_edge_insert_and_close_sql() -> None:
    e = m.Edge("tw:2408", "grp:半導體（記憶體）", "belongs", "approved", None, 2, "stock_group")
    ins = m.edge_insert_sql([e])
    assert ins.startswith(
        "insert into news.graph_edges (src, dst, type, status, label, rank, source) values"
    )
    assert (
        "('tw:2408', 'grp:半導體（記憶體）', 'belongs', 'approved', null, 2, 'stock_group')" in ins
    )
    close = m.edge_close_sql([("us:NVDA", "tw:2382", "supplier"), ("a'b", "c", "peer")])
    assert close.startswith(
        "update news.graph_edges set valid_to = now() where valid_to is null and (src, dst, type) in (values"
    )
    assert "('us:NVDA', 'tw:2382', 'supplier')" in close
    assert "('a''b', 'c', 'peer')" in close


def test_entity_close_sql() -> None:
    sql = m.entity_close_sql(["tw:1234", "co:o'neil"])
    assert sql == (
        "update news.entities set valid_to = now() where valid_to is null and id in ('tw:1234', 'co:o''neil')"
    )


def test_empty_batches_raise() -> None:
    for fn in (m.entity_upsert_sql, m.entity_close_sql, m.edge_close_sql, m.edge_insert_sql):
        with pytest.raises(ValueError, match="空批次"):
            fn([])


def test_batched_respects_limit() -> None:
    assert [len(b) for b in m.batched(list(range(1201)), 500)] == [500, 500, 201]
    assert list(m.batched([], 500)) == []
    with pytest.raises(ValueError):
        list(m.batched([1], 0))


def test_plan_statements_batches_at_most_500_rows() -> None:
    ents = [
        m.Entity(f"tw:{i:04d}", "tw", f"股{i}", f"{i:04d}", (f"股{i}",), "stock_meta")
        for i in range(1001)
    ]
    plan = m.Plan(upsert=ents, close_entities=[], close_edges=[], insert_edges=[])
    stmts = m.plan_statements(plan)
    assert len(stmts) == 3
    assert all(s.count("'stock_meta')") <= m.BATCH_ROWS for s in stmts)
    assert m.BATCH_ROWS <= 500


def test_plan_statements_order() -> None:
    ent = m.Entity("tw:2408", "tw", "南亞科", "2408", ("南亞科",), "stock_meta")
    edge = m.Edge("tw:2408", "grp:x", "belongs", "approved", None, 1, "stock_group")
    plan = m.Plan(
        upsert=[ent], close_entities=["tw:9999"], close_edges=[edge.key()], insert_edges=[edge]
    )
    s = m.plan_statements(plan)
    assert [x.split()[0] + " " + x.split()[1] + " " + x.split()[2] for x in s] == [
        "insert into news.entities",
        "update news.graph_edges set",
        "insert into news.graph_edges",
        "update news.entities set",
    ]


# ── 主庫 → 實體與 belongs 線 ─────────────────────────────────────────────────


def test_stock_entities_keep_leading_zero() -> None:
    (e,) = m.stock_entities([("0050", "元大台灣50")])
    assert (e.id, e.type, e.label, e.sub, e.aliases, e.source) == (
        "tw:0050",
        "tw",
        "元大台灣50",
        "0050",
        ("元大台灣50",),
        "stock_meta",
    )


def test_stock_entities_reject_bad_rows() -> None:
    with pytest.raises(m.DataError, match="沒有 name"):
        m.stock_entities([("2330", " ")])
    with pytest.raises(m.DataError, match="stock_id"):
        m.stock_entities([(2330, "台積電")])


def test_rank_groups_by_amount_desc_missing_last() -> None:
    groups = [("A001", "g"), ("A002", "g"), ("A003", "g"), ("A004", "g"), ("B001", "h")]
    amounts: dict[str, float | None] = {"A001": 10.0, "A002": 30.0, "A003": None, "B001": 1.0}
    got = {(e.src, e.dst): e.rank for e in m.rank_groups(groups, amounts)}
    assert got == {
        ("tw:A002", "grp:g"): 1,
        ("tw:A001", "grp:g"): 2,
        ("tw:A003", "grp:g"): 3,  # amount null
        ("tw:A004", "grp:g"): 4,  # 當天沒有那一列；同為缺值依代號
        ("tw:B001", "grp:h"): 1,
    }
    assert all(
        e.type == "belongs" and e.status == "approved" for e in m.rank_groups(groups, amounts)
    )


def test_rank_groups_ties_by_stock_id_and_deterministic() -> None:
    groups = [("2002", "g"), ("2001", "g")]
    a = m.rank_groups(groups, {"2001": 5.0, "2002": 5.0})
    assert [(e.src, e.rank) for e in a] == [("tw:2001", 1), ("tw:2002", 2)]
    assert a == m.rank_groups(list(reversed(groups)), {"2001": 5.0, "2002": 5.0})


def test_rank_groups_rejects_duplicate_and_blank() -> None:
    with pytest.raises(m.DataError, match="兩次"):
        m.rank_groups([("2330", "a"), ("2330", "b")], {})
    with pytest.raises(m.DataError, match="grp"):
        m.rank_groups([("2330", "")], {})


def test_group_entities() -> None:
    edges = m.rank_groups(GROUPS, AMOUNTS)
    got = m.group_entities(edges)
    assert [(e.id, e.type, e.label, e.sub) for e in got] == [
        ("grp:半導體（記憶體）", "group", "半導體（記憶體）", "族群"),
        ("grp:電腦及週邊設備", "group", "電腦及週邊設備", "族群"),
    ]


# ── 設定 ─────────────────────────────────────────────────────────────────────


def test_load_yaml_missing_file_has_clear_error(tmp_path: Path) -> None:
    with pytest.raises(m.ConfigError, match=r"找不到.*news_entities\.yaml"):
        m.load_yaml(tmp_path / "news_entities.yaml")


def test_load_yaml_top_level_must_be_mapping(tmp_path: Path) -> None:
    p = tmp_path / "news_edges.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(m.ConfigError, match="mapping"):
        m.load_yaml(p)


def test_config_entities() -> None:
    got = {e.id: e for e in m.config_entities(ENTITIES_DOC)}
    assert set(got) == {"us:NVDA", "co:samsung", "th:memory"}
    nv = got["us:NVDA"]
    assert (nv.type, nv.label, nv.sub, nv.aliases, nv.source) == (
        "us",
        "輝達",
        "NVDA",
        ("Nvidia", "NVIDIA", "輝達"),  # 重複的別名只留一次、順序不變
        "config",
    )
    assert (got["co:samsung"].type, got["co:samsung"].sub) == ("company", "韓國")
    assert (got["th:memory"].type, got["th:memory"].sub) == ("theme", "主題")


@pytest.mark.parametrize(
    ("doc", "msg"),
    [
        ({"stocks": []}, "頂層鍵"),
        ({"us": [{"ticker": "nvda", "label": "輝達"}]}, "ticker"),
        ({"us": [{"ticker": "NVDA"}]}, "缺欄位"),
        ({"us": [{"ticker": "NVDA", "label": "輝達", "alias": ["x"]}]}, "不認得的欄位"),
        ({"us": [{"ticker": "NVDA", "label": "輝達", "aliases": "Nvidia"}]}, "字串清單"),
        ({"companies": [{"id": "Samsung", "label": "三星", "country": "韓國"}]}, "小寫"),
        ({"themes": "ai"}, "清單"),
    ],
)
def test_config_entities_rejects_bad_entries(doc: dict[str, Any], msg: str) -> None:
    with pytest.raises(m.ConfigError, match=msg):
        m.config_entities(doc)


def test_config_edges() -> None:
    got = {(e.type, e.src, e.dst): e for e in m.config_edges(EDGES_DOC)}
    assert set(got) == {
        ("supplier", "us:NVDA", "tw:2382"),
        ("supplier", "us:NVDA", "tw:2317"),
        ("theme", "th:memory", "tw:2408"),
        ("theme", "th:memory", "grp:半導體（記憶體）"),
        ("peer", "co:samsung", "tw:2408"),
    }
    assert got[("peer", "co:samsung", "tw:2408")].label == "同業"
    assert all(
        e.status == "draft" and e.rank is None and e.source == "config" for e in got.values()
    )


@pytest.mark.parametrize(
    ("doc", "msg"),
    [
        ({"supplier": [{"src": "us:NVDA", "tw": [2382], "status": "draft"}]}, "加引號"),
        ({"supplier": [{"src": "us:NVDA", "tw": ["2382"], "status": "ok"}]}, "status"),
        ({"supplier": [{"src": "us:NVDA", "tw": ["2382"]}]}, "缺欄位"),
        ({"peer": [{"src": "co:samsung", "tw": ["2408"], "status": "draft"}]}, "缺欄位"),
        ({"theme": [{"src": "th:ai", "status": "draft"}]}, "沒有任何終點"),
        ({"theme": [{"src": "th:ai", "grps": ["x"], "status": "draft"}]}, "不認得的欄位"),
        ({"member": []}, "頂層鍵"),
    ],
)
def test_config_edges_rejects_bad_entries(doc: dict[str, Any], msg: str) -> None:
    with pytest.raises(m.ConfigError, match=msg):
        m.config_edges(doc)


# ── 合併、檢查、差異 ─────────────────────────────────────────────────────────


def test_build_new_skips_group_rows_of_inactive_stocks() -> None:
    groups = [*GROUPS, ("9999", "電腦及週邊設備")]  # 9999 不在未下市清單
    ents, edges, skipped = m.build_new(META, groups, AMOUNTS, ENTITIES_DOC, EDGES_DOC)
    assert skipped == 1
    assert "tw:9999" not in {e.src for e in edges}
    assert sum(e.type == "tw" for e in ents) == len(META)
    belongs = {(e.src, e.dst): e.rank for e in edges if e.type == "belongs"}
    assert belongs == {
        ("tw:2344", "grp:半導體（記憶體）"): 1,
        ("tw:2408", "grp:半導體（記憶體）"): 2,
        ("tw:2317", "grp:電腦及週邊設備"): 1,
    }
    m.make_plan(ents, edges, [], [])  # 端點都齊


def test_check_edges_unknown_endpoint() -> None:
    meta = [("2317", "鴻海")]  # 2382、2408 不在 → 設定的線指到不存在的台股
    ents, edges, _ = m.build_new(meta, [], {}, ENTITIES_DOC, EDGES_DOC)
    with pytest.raises(m.ConfigError, match="端點不在實體裡"):
        m.make_plan(ents, edges, [], [])


def test_check_edges_unknown_group_name() -> None:
    doc = {"theme": [{"src": "th:memory", "grp": ["記憶體族群"], "status": "draft"}]}
    ents, edges, _ = m.build_new(META, GROUPS, AMOUNTS, ENTITIES_DOC, doc)
    with pytest.raises(m.ConfigError, match="grp:記憶體族群"):
        m.make_plan(ents, edges, [], [])


def test_check_edges_duplicate_key() -> None:
    doc = {
        "supplier": [
            {"src": "us:NVDA", "tw": ["2382"], "status": "draft"},
            {"src": "us:NVDA", "tw": ["2382"], "status": "approved"},
        ]
    }
    ents, edges, _ = m.build_new(META, GROUPS, AMOUNTS, ENTITIES_DOC, doc)
    with pytest.raises(m.ConfigError, match="不只一次"):
        m.make_plan(ents, edges, [], [])


def test_check_entities_duplicate_id() -> None:
    e = m.Entity("us:NVDA", "us", "輝達", "NVDA", (), "config")
    with pytest.raises(m.ConfigError, match="重複"):
        m.check_entities([e, e])


def _edge(src: str, dst: str, typ: str = "supplier", **kw: Any) -> Any:
    base: dict[str, Any] = {"status": "draft", "label": None, "rank": None, "source": "config"}
    base.update(kw)
    return m.Edge(src, dst, typ, base["status"], base["label"], base["rank"], base["source"])


def test_diff_edges_unchanged_added_removed_changed() -> None:
    same = _edge("us:NVDA", "tw:2382")
    gone = _edge("us:NVDA", "tw:3231")
    rank_old = _edge("tw:2408", "grp:g", "belongs", status="approved", rank=1, source="stock_group")
    rank_new = _edge("tw:2408", "grp:g", "belongs", status="approved", rank=2, source="stock_group")
    approved = _edge("us:AAPL", "tw:2317", status="approved")
    was_draft = _edge("us:AAPL", "tw:2317")
    added = _edge("co:samsung", "tw:2408", "peer", label="同業")
    close, insert = m.diff_edges(
        [same, gone, rank_old, was_draft], [same, rank_new, approved, added]
    )
    assert close == sorted([gone.key(), rank_old.key(), was_draft.key()])
    assert set(insert) == {rank_new, approved, added}
    assert same not in insert


def test_diff_edges_idempotent() -> None:
    edges = [_edge("us:NVDA", "tw:2382"), _edge("us:NVDA", "tw:2317")]
    assert m.diff_edges(edges, edges) == ([], [])


def test_diff_edges_duplicate_active_rows_are_rebuilt() -> None:
    e = _edge("us:NVDA", "tw:2382")
    close, insert = m.diff_edges([e, e], [e])
    assert close == [e.key()]
    assert insert == [e]


def test_diff_entities() -> None:
    new = [m.Entity("tw:2330", "tw", "台積電", "2330", ("台積電",), "stock_meta")]
    assert m.diff_entities(["tw:2330", "tw:1234", "co:old"], new) == ["co:old", "tw:1234"]
    assert m.diff_entities([], new) == []


def test_make_plan_and_summary() -> None:
    ents, edges, _ = m.build_new(META, GROUPS, AMOUNTS, ENTITIES_DOC, EDGES_DOC)
    plan = m.make_plan(ents, edges, ["tw:1234"], [])
    assert plan.close_entities == ["tw:1234"]
    assert len(plan.insert_edges) == len(edges)
    text = "\n".join(m.summary(plan, ents, edges))
    assert "tw 5" in text and "group 2" in text and "us 1" in text
    assert "新增 8" in text


def test_edge_from_row() -> None:
    row = {
        "src": "a",
        "dst": "b",
        "type": "belongs",
        "status": "approved",
        "label": None,
        "rank": 3,
        "source": "stock_group",
    }
    assert m.edge_from_row(row) == m.Edge("a", "b", "belongs", "approved", None, 3, "stock_group")


# ── .env 與 Management API（假的 opener，不連網） ────────────────────────────


def test_load_env_missing_key_lists_names_not_values(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "TWQUANT_SUPABASE_DSN=postgresql://secret\nSUPABASE_NEWS_ACCESS_TOKEN=\n", encoding="utf-8"
    )
    with pytest.raises(m.ConfigError) as ei:
        m.load_env(p)
    msg = str(ei.value)
    assert "SUPABASE_NEWS_ACCESS_TOKEN" in msg and "TWQUANT_NEWS_PROJECT_REF" in msg
    assert "secret" not in msg


def test_load_env_reads_quoted(tmp_path: Path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "# x\nTWQUANT_SUPABASE_DSN='dsn'\nSUPABASE_NEWS_ACCESS_TOKEN=\"tok\"\nTWQUANT_NEWS_PROJECT_REF=ref\n",
        encoding="utf-8",
    )
    assert m.load_env(p) == {
        "TWQUANT_SUPABASE_DSN": "dsn",
        "SUPABASE_NEWS_ACCESS_TOKEN": "tok",
        "TWQUANT_NEWS_PROJECT_REF": "ref",
    }


class _Resp(io.BytesIO):
    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        self.close()


def test_management_query_request_shape() -> None:
    seen: dict[str, Any] = {}

    def opener(req: urllib.request.Request, timeout: float) -> _Resp:
        seen["url"] = req.full_url
        seen["method"] = req.get_method()
        seen["headers"] = {k.lower(): v for k, v in req.header_items()}
        assert isinstance(req.data, bytes)
        seen["body"] = json.loads(req.data.decode("utf-8"))
        return _Resp(json.dumps([{"id": "tw:2330"}]).encode("utf-8"))

    rows = m.management_query("abc123", "tok", "select 'O''x'", opener=opener)
    assert rows == [{"id": "tw:2330"}]
    assert seen["url"] == "https://api.supabase.com/v1/projects/abc123/database/query"
    assert seen["method"] == "POST"
    assert seen["headers"]["authorization"] == "Bearer tok"
    assert seen["headers"]["content-type"] == "application/json"
    assert seen["body"] == {"query": "select 'O''x'"}


def test_management_query_http_error_has_context_without_token() -> None:
    def opener(req: urllib.request.Request, timeout: float) -> _Resp:
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            Message(),
            io.BytesIO(b'{"message":"relation does not exist"}'),
        )

    with pytest.raises(m.NewsApiError) as ei:
        m.management_query("abc123", "sekret-token", "select 1 from news.entities", opener=opener)
    msg = str(ei.value)
    assert "HTTP 400" in msg and "relation does not exist" in msg and "news.entities" in msg
    assert "sekret-token" not in msg


def test_management_query_non_list_response() -> None:
    def opener(req: urllib.request.Request, timeout: float) -> _Resp:
        return _Resp(b'{"message":"x"}')

    with pytest.raises(m.NewsApiError, match="陣列"):
        m.management_query("r", "t", "select 1", opener=opener)


def test_rank_only_change_updates_in_place() -> None:
    old = [m.Edge("tw:2330", "grp:半導體", "belongs", "approved", None, 1, "stock_group")]
    new = [m.Edge("tw:2330", "grp:半導體", "belongs", "approved", None, 2, "stock_group")]
    close, insert, rerank = m.diff_edges_rerank(old, new)
    assert (close, insert, rerank) == ([], [], new)
    sql = m.edge_rerank_sql(rerank)
    assert "update news.graph_edges" in sql and "('tw:2330', 'grp:半導體', 'belongs', 2)" in sql
    # 狀態變了 → 照樣關掉重插
    changed = [m.Edge("tw:2330", "grp:半導體", "belongs", "draft", None, 2, "stock_group")]
    close2, insert2, rerank2 = m.diff_edges_rerank(old, changed)
    assert close2 == [("tw:2330", "grp:半導體", "belongs")] and insert2 == changed and rerank2 == []


def test_apply_reviews_follows_latest_decision_for_drafts_only() -> None:
    """T-403：核可頁核可的草稿同步後維持 approved、退回的不再長回來；設定檔本來就 approved 的線不受影響。"""
    e = m.Edge
    edges = [
        e("us:NVDA", "tw:2330", "supplier", "draft", None, None, "config"),
        e("us:NVDA", "tw:2382", "supplier", "draft", None, None, "config"),
        e("us:NVDA", "tw:3231", "supplier", "draft", None, None, "config"),
        e("tw:2330", "grp:半導體", "belongs", "approved", None, 1, "stock_group"),
    ]
    decisions = {
        "us:NVDA|tw:2330|supplier": "approved",
        "us:NVDA|tw:2382|supplier": "rejected",
        "tw:2330|grp:半導體|belongs": "rejected",
    }
    got = m.apply_reviews(edges, decisions)
    assert [(x.dst, x.status) for x in got] == [
        ("tw:2330", "approved"),
        ("tw:3231", "draft"),
        ("grp:半導體", "approved"),
    ]
    # 核可後同步：跟新聞庫裡核可頁插的那條（approved、同說明同來源）沒有差異 → 不關不插
    existing = [e("us:NVDA", "tw:2330", "supplier", "approved", None, None, "config")]
    close, insert, _ = m.diff_edges_rerank(existing, got[:1])
    assert close == [] and insert == []


def test_tw_en_adds_english_aliases_by_stock_name() -> None:
    """台股英文名（2026-10-20）：照主庫股名加進別名；股名對不到或重名就停下來。"""
    tw = m.stock_entities([("2409", "友達"), ("3008", "大立光"), ("2330", "台積電")])
    got = m.apply_tw_en(
        tw, m.config_tw_en({"友達": ["AUO", "AU Optronics", "AUO"], "大立光": ["Largan"]})
    )
    assert [(e.id, e.aliases) for e in got] == [
        ("tw:2409", ("友達", "AUO", "AU Optronics")),
        ("tw:3008", ("大立光", "Largan")),
        ("tw:2330", ("台積電",)),
    ]
    with pytest.raises(m.ConfigError, match="國巨"):
        m.apply_tw_en(tw, {"國巨": ("Yageo",)})
    dup = m.stock_entities([("1111", "同名"), ("2222", "同名")])
    with pytest.raises(m.ConfigError, match="同名"):
        m.apply_tw_en(dup, {"同名": ("Same",)})
    bads: list[dict[str, Any]] = [{"友達": []}, {"友達": "AUO"}, {"友達": [" AUO"]}, {"": ["X"]}]
    for bad in bads:
        with pytest.raises(m.ConfigError):
            m.config_tw_en(bad)


def test_build_new_uses_tw_en_when_given() -> None:
    meta = [("2409", "友達")]
    ents, _, _ = m.build_new(meta, [], {}, ENTITIES_DOC, EDGES_DOC, {"友達": ["AUO"]})
    assert next(e for e in ents if e.id == "tw:2409").aliases == ("友達", "AUO")


def test_add_base_names_strips_suffixes_but_keeps_real_names() -> None:
    """股名後綴（2026-10-20）：國巨*、臻鼎-KY、奧義賽博-KY創、鴻華先進-創 → 加去掉後綴的名字；矽創、群創的「創」是名字不動；
    拉丁字、單字、撞到別人、兩檔同名的不加。"""
    tw = m.stock_entities(
        [
            ("2327", "國巨*"),
            ("4958", "臻鼎-KY"),
            ("7823", "奧義賽博-KY創"),
            ("2258", "鴻華先進-創"),
            ("8016", "矽創"),
            ("3481", "群創"),
            ("6706", "TPK-KY"),
            ("1111", "台南-KY"),
            ("2222", "同名-KY"),
            ("3333", "同名*"),
            ("4444", "鑫-KY"),
        ]
    )
    got = {
        e.id: e.aliases for e in m.add_base_names(tw, {"台南"} | {a for e in tw for a in e.aliases})
    }
    assert got["tw:2327"] == ("國巨*", "國巨")
    assert got["tw:4958"] == ("臻鼎-KY", "臻鼎")
    assert got["tw:7823"] == ("奧義賽博-KY創", "奧義賽博")
    assert got["tw:2258"] == ("鴻華先進-創", "鴻華先進")
    assert got["tw:8016"] == ("矽創",) and got["tw:3481"] == ("群創",)
    assert got["tw:6706"] == ("TPK-KY",), "拉丁字的交給 news_tw_en.yaml"
    assert got["tw:1111"] == ("台南-KY",), "撞到別的實體不加"
    assert got["tw:2222"] == ("同名-KY",) and got["tw:3333"] == ("同名*",), "兩檔去掉後綴同名都不加"
    assert got["tw:4444"] == ("鑫-KY",), "只剩一個字不加"
