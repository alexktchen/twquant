"""SPEC-016 T-336：ETF 每日持股——基準日與可用時點（AC-7）、權重和（AC-8）、冪等與缺資料（AC-9）。

fixture：tests/fixtures/http/etf_pcf/ 錄自元大 PCF/Daily（0050，查詢日 2026-09-01 → 基準日 08-31）、
統一 GetPCF（00981A，查詢日 115/09/01 → 基準日 08-31）與統一清單頁的基金代碼表（裁剪）。
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.config import EtfMetaConfig, EtfMetaEntry, FundFlowConfig, load_config
from twquant.conventions import TZ
from twquant.data import fund_flow_units as ffu
from twquant.data.serving import schema as sch
from twquant.data.sources import etf_pcf
from twquant.errors import DataError

_FIX = Path(__file__).parent / "fixtures" / "http" / "etf_pcf"
_ROOT = Path(__file__).resolve().parent.parent
_B = dt.date(2026, 8, 31)


def _load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((_FIX / name).read_text(encoding="utf-8"))
    return data


def _yuanta() -> etf_pcf.PcfSnapshot:
    return etf_pcf.yuanta_snapshot(_load("yuanta_pcf_0050_q20260901.json"), "0050")


def _ez() -> etf_pcf.PcfSnapshot:
    return etf_pcf.ezmoney_snapshot(_load("ezmoney_getpcf_49YTW_1150901.json"), "00981A")


# ── AC-7 基準日與可用時點 ─────────────────────────────────────────────────────


def test_ac7_yuanta_base_date_is_trandate_not_query_date() -> None:
    s = _yuanta()
    assert s.base_date == _B  # 查詢日是 09-01
    assert s.published_at == dt.datetime(2026, 8, 31, 15, 58, 39, tzinfo=TZ)
    assert len(s.rows) == 50
    top = {sid: (sh, w) for sid, sh, w in s.rows}["2330"]
    assert isinstance(top[0], int) and 0.5 < top[1] < 0.7  # 權重換成小數


def test_ac7_ezmoney_base_date_and_stocks_only() -> None:
    s = _ez()
    assert s.base_date == _B
    assert s.published_at.date() == _B and s.published_at.tzinfo is not None
    assert len(s.rows) == 50  # 期貨（TX）不算個股持股
    assert all(sid.isdigit() for sid, _, _ in s.rows)


def test_ac7_ezmoney_fund_codes_from_page() -> None:
    codes = etf_pcf.ezmoney_fund_codes(
        (_FIX / "ezmoney_pcf_fundlist.html").read_text(encoding="utf-8")
    )
    assert codes["00981A"] == "49YTW"
    assert codes["00939"] == "46YTW"
    assert etf_pcf.roc(dt.date(2026, 9, 1)) == "115/09/01"


def test_ac7_available_at_is_max_of_cutoff_and_publication() -> None:
    s = _yuanta()
    df = ffu.holdings_frame([s], available_hour=18, weight_sum_max=1.02)
    assert set(df["date"].to_list()) == {_B}
    assert df["available_at"][0] == dt.datetime(2026, 8, 31, 18, tzinfo=TZ)
    late = etf_pcf.PcfSnapshot(
        s.etf_id, s.base_date, dt.datetime(2026, 9, 1, 9, 30, tzinfo=TZ), s.rows
    )
    df2 = ffu.holdings_frame([late], available_hour=18, weight_sum_max=1.02)
    assert df2["available_at"][0] == dt.datetime(2026, 9, 1, 9, 30, tzinfo=TZ)


def test_ac7_empty_query_raises_pcf_empty() -> None:
    with pytest.raises(etf_pcf.PcfEmpty):
        etf_pcf.yuanta_snapshot({"PCF": None, "FundWeights": None}, "0056")
    with pytest.raises(etf_pcf.PcfEmpty):
        etf_pcf.ezmoney_snapshot({"asset": [{"AssetCode": "ST", "Details": []}]}, "00981A")
    with pytest.raises(etf_pcf.PcfEmpty):  # 假日：整個資產清單是空的
        etf_pcf.ezmoney_snapshot({"asset": []}, "00981A")
    two = {"asset": [{"AssetCode": "ST", "Details": [{}]}, {"AssetCode": "ST", "Details": [{}]}]}
    with pytest.raises(DataError, match="一個股票資產段"):
        etf_pcf.ezmoney_snapshot(two, "00981A")


def test_ac7_rejects_bad_rows() -> None:
    p = _load("yuanta_pcf_0050_q20260901.json")
    p["FundWeights"]["StockWeights"][0]["qty"] = 1.5
    with pytest.raises(DataError, match="非負整數"):
        etf_pcf.yuanta_snapshot(p, "0050")
    p = _load("yuanta_pcf_0050_q20260901.json")
    p["FundWeights"]["StockWeights"][1]["code"] = p["FundWeights"]["StockWeights"][0]["code"]
    with pytest.raises(DataError, match="重複"):
        etf_pcf.yuanta_snapshot(p, "0050")


# ── AC-8 權重和 ─────────────────────────────────────────────────────────────


def test_ac8_weight_sum_bounds() -> None:
    s = _ez()
    total = sum(w for _, _, w in s.rows)
    assert 0.9 < total < 0.98  # 主動式持現金與期貨：低於 0.98 是正常的
    ffu.holdings_frame([s], 18, 1.02)
    over = etf_pcf.PcfSnapshot(
        s.etf_id, s.base_date, s.published_at, tuple((a, b, w * 1.2) for a, b, w in s.rows)
    )
    with pytest.raises(DataError, match="權重和"):
        ffu.holdings_frame([over], 18, 1.02)
    with pytest.raises(DataError, match="兩份"):
        ffu.holdings_frame([s, s], 18, 1.02)


# ── 查詢日推算 ───────────────────────────────────────────────────────────────


def test_ac9_snapshot_for_base_probes_only_on_empty() -> None:
    from twquant.data.serving import fund_flow_ingest as ffi

    s = _yuanta()
    calls: list[dt.date | None] = []

    def fetch(etf_id: str, q: dt.date | None) -> etf_pcf.PcfSnapshot:
        calls.append(q)
        if q is not None and q.weekday() >= 5:
            raise etf_pcf.PcfEmpty("etf_holdings_daily", q, "weekend")
        return s

    # 基準日 2026-08-28（週五）之後未知 → 試 08-29、08-30（週末空）、08-31 → 回 08-31 的清單 ≠ 08-28
    with pytest.raises(etf_pcf.PcfMissingBase):
        ffi.snapshot_for_base(fetch, "0050", dt.date(2026, 8, 28), None, 7)
    assert calls == [dt.date(2026, 8, 29), dt.date(2026, 8, 30), dt.date(2026, 8, 31)]
    calls.clear()
    assert ffi.snapshot_for_base(fetch, "0050", _B, dt.date(2026, 9, 1), 7) is s
    assert calls == [dt.date(2026, 9, 1)]

    def boom(etf_id: str, q: dt.date | None) -> etf_pcf.PcfSnapshot:
        raise DataError("etf_holdings_daily", q, "格式改版")

    with pytest.raises(DataError, match="格式改版"):  # 不是 PcfEmpty → 不換日重試
        ffi.snapshot_for_base(boom, "0050", _B, None, 7)


# ── AC-9 PG 往返（scratch schema；未設 TWQUANT_PG_DSN 即跳過）────────────────────

_SCHEMA = "serving_test_t336"
_CAL = [dt.date(2026, 8, 27), dt.date(2026, 8, 28), _B, dt.date(2026, 9, 1)]


def _pg_or_skip() -> Any:
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    import psycopg

    return psycopg.connect(dsn)


def _meta(*pairs: tuple[str, str, bool]) -> EtfMetaConfig:
    return EtfMetaConfig(
        etfs=tuple(
            EtfMetaEntry(
                etf_id=i,
                name=i,
                issuer=iss,
                fund_type="x",
                is_active=act,
                listed_date=dt.date(2020, 1, 2),
            )
            for i, iss, act in pairs
        )
    )


def _cfg() -> FundFlowConfig:
    base = load_config(_ROOT / "configs" / "fund_flow.yaml", FundFlowConfig)
    return base.model_copy(
        update={"holdings": base.holdings.model_copy(update={"catchup_trading_days": 2})}
    )


def _fresh(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
    conn.commit()
    sch.ensure_tables(conn, (sch.spec_of("prices_daily"),), _SCHEMA)
    with conn.cursor() as cur:
        cur.executemany(
            f'INSERT INTO "{_SCHEMA}".prices_daily (date, stock_id) VALUES (%s, %s)',
            [(d, "2330") for d in _CAL],
        )
    conn.commit()


def _fake(snap: etf_pcf.PcfSnapshot) -> Any:
    """依查詢日回「前一交易日」為基準日的清單（模擬來源語意）。"""

    def fetch(etf_id: str, q: dt.date | None) -> etf_pcf.PcfSnapshot:
        assert q is not None
        if q.weekday() >= 5:
            raise etf_pcf.PcfEmpty("etf_holdings_daily", q, "weekend")
        base = max(d for d in _CAL if d < q)
        return etf_pcf.PcfSnapshot(
            etf_id, base, dt.datetime.combine(base, dt.time(16), TZ), snap.rows
        )

    return fetch


def test_ac9_pg_ingest_idempotent_and_replaces_whole_snapshot() -> None:
    from twquant.data.serving import fund_flow_ingest as ffi

    conn = _pg_or_skip()
    try:
        _fresh(conn)
        meta = _meta(("0050", "元大", False), ("00981A", "統一", True))
        fetchers = {"元大": _fake(_yuanta()), "統一": _fake(_ez())}
        day = dt.date(2026, 9, 2)
        r1 = ffi.ingest_recent_holdings(conn, _SCHEMA, _cfg(), meta, day, fetchers)
        assert sorted(b for _, b in r1.fetched) == [
            _B,
            _B,
            dt.date(2026, 9, 1),
            dt.date(2026, 9, 1),
        ]
        assert r1.rows == 200
        r2 = ffi.ingest_recent_holdings(conn, _SCHEMA, _cfg(), meta, day, fetchers)
        assert r2.fetched == [] and r2.rows == 0
        # 整份替換：同一 (ETF, 基準日) 重寫只剩新清單，舊股票不殘留
        s = _yuanta()
        small = etf_pcf.PcfSnapshot("0050", _B, s.published_at, s.rows[:3])
        ffi.write_holdings(conn, _SCHEMA, ffu.holdings_frame([small], 18, 1.02))
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT count(*) FROM "{_SCHEMA}".etf_holdings_daily WHERE etf_id=%s AND date=%s',
                ("0050", _B),
            )
            assert cur.fetchone()[0] == 3
    finally:
        conn.close()


def test_ac9_pg_missing_snapshot_writes_nothing() -> None:
    from twquant.data.serving import fund_flow_ingest as ffi

    conn = _pg_or_skip()
    try:
        _fresh(conn)
        meta = _meta(("0050", "元大", False), ("00981A", "統一", True))

        def lagging(etf_id: str, q: dt.date | None) -> etf_pcf.PcfSnapshot:
            s = _ez()  # 永遠回 08-31：09-01 的基準日來源沒有
            return etf_pcf.PcfSnapshot(etf_id, _B, s.published_at, s.rows)

        fetchers = {"元大": _fake(_yuanta()), "統一": lagging}
        with pytest.raises(etf_pcf.PcfMissingBase):
            ffi.ingest_recent_holdings(conn, _SCHEMA, _cfg(), meta, dt.date(2026, 9, 2), fetchers)
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{_SCHEMA}".etf_holdings_daily')
            assert cur.fetchone()[0] == 0
        with pytest.raises(DataError, match="抓取函數"):
            ffi.ingest_recent_holdings(conn, _SCHEMA, _cfg(), meta, dt.date(2026, 9, 2), {})
    finally:
        conn.close()


def test_ac9_holdings_spec_matches_pandera() -> None:
    from twquant.data import schemas

    assert sch.spec_of("etf_holdings_daily").column_names == tuple(
        schemas.get_schema("etf_holdings_daily").columns
    )
    df = ffu.holdings_frame([_yuanta()], 18, 1.02)
    assert df.schema["stock_id"] == pl.String and df.height == 50
