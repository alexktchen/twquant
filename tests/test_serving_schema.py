"""T-031 / SPEC-009 AC-1：serving schema DDL 冪等。

需要真 PG（TWQUANT_PG_DSN + 可連線）；無則 skip，故無 PG 的 CI 仍綠（AC-8）。
用獨立 test schema，跑完 DROP，不碰正式 serving schema。
"""

from __future__ import annotations

import os
from typing import Any

import pytest

_MIRROR = {
    "prices_daily",
    "institutional_daily",
    "margin_daily",
    "monthly_revenue",
    "shareholding_dispersion",
    "stock_meta",
    "index_daily",  # T-082：先前遺漏，導致 price.rs_market_20d 因 PG 無指數而永遠算不出來
}
_ALL = _MIRROR | {"sync_state"}


def _conn_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        return psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")


def test_ac1_ddl_idempotent() -> None:
    from twquant.data.serving import schema as sch

    conn = _conn_or_skip()
    test_schema = "serving_test_ac1"
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()

        sch.ensure_schema(conn, test_schema)
        sch.ensure_schema(conn, test_schema)  # 第二次不得報錯（冪等）

        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                (test_schema,),
            )
            got = {row[0] for row in cur.fetchall()}
        assert got >= _ALL, f"缺表：{_ALL - got}"
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        conn.close()


def test_ddl_rejects_bad_schema_ident() -> None:
    from twquant.data.serving import schema as sch

    with pytest.raises(ValueError, match="識別字"):
        sch.table_ddls("bad-schema; DROP")


def test_rebuild_targets_none_selects_all_mirror() -> None:
    """tables=None → 全部鏡射表（rebuild 預設全量重灌）。"""
    from twquant.data.serving import schema as sch

    assert sch.rebuild_targets(None) == sch.MIRROR_SPECS


def test_rebuild_targets_selects_named_mirror() -> None:
    """指定鏡射表名 → 只回那些表的 spec，順序依 MIRROR_SPECS。"""
    from twquant.data.serving import schema as sch

    got = sch.rebuild_targets(["institutional_daily", "prices_daily"])
    assert [s.name for s in got] == ["prices_daily", "institutional_daily"]


def test_rebuild_targets_rejects_factor_table() -> None:
    """composite_daily 等因子表非鏡射，rebuild 不得靜默略過——須 raise 並指引 materialize
    （黃金規則 5）。這正是 2026-07-11 composite_daily 殘留舊演算法值的排查根因。"""
    from twquant.data.serving import schema as sch

    with pytest.raises(ValueError, match="materialize") as exc:
        sch.rebuild_targets(["composite_daily"])
    assert "composite_daily" in str(exc.value)


def test_rebuild_targets_rejects_all_factor_tables() -> None:
    """三張因子表（factor_values / factor_cross_stats / composite_daily）皆非鏡射，全數 raise。"""
    from twquant.data.serving import schema as sch

    for table in sch.FACTOR_TABLES:
        with pytest.raises(ValueError, match="materialize"):
            sch.rebuild_targets([table])


def test_rebuild_targets_rejects_mixed_mirror_and_factor() -> None:
    """混入合法鏡射表與因子表時，仍整批 raise——不得只重灌鏡射表、靜默略過因子表。"""
    from twquant.data.serving import schema as sch

    with pytest.raises(ValueError, match="composite_daily"):
        sch.rebuild_targets(["prices_daily", "composite_daily"])


def test_rebuild_targets_rejects_unknown_table() -> None:
    """未知表名 raise（非鏡射即拒，不靜默產生空 targets）。"""
    from twquant.data.serving import schema as sch

    with pytest.raises(ValueError, match="不支援"):
        sch.rebuild_targets(["does_not_exist"])


def test_t082_index_daily_is_mirrored_so_rs_market_20d_can_compute() -> None:
    """T-082：`index_daily` 必須在 MIRROR_SPECS 中。

    缺陷歷史：它不在鏡射清單裡 → `serving sync` 不同步 → PG 只有來源不明的 52 天；
    而因子物化在 D-026 之後改讀 PG，故 `price.rs_market_20d`
    （宣告 tables=[prices_daily, index_daily]）在 Phase 1 模型中 split=0、gain=0.00%，
    完全未被使用；Phase 2 的 12 特徵清單仍含它，等於一欄全空。
    """
    from twquant.data.serving import schema as sch

    assert "index_daily" in sch.MIRROR_TABLES
    spec = next(s for s in sch.MIRROR_SPECS if s.name == "index_daily")
    assert spec.pk == ("date", "index_id"), "主鍵須與既有 PG 表一致，否則 upsert 會失敗"
    assert not spec.snapshot, "index_daily 是日頻事實表，非快照表"
    assert spec.column_names == (
        "date",
        "index_id",
        "open",
        "high",
        "low",
        "close",
        "available_at",
    ), "欄位順序與名稱須與 clean 層 parquet 及既有 PG 表一致"
