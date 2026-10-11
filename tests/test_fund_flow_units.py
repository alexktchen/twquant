"""SPEC-016 T-335：資金流設定（AC-3）、ETF 單位數解析與差分（AC-4）、PG 冪等（AC-5）、缺列整批不寫（AC-6）。

fixture：tests/fixtures/http/etf_units/ 錄自 TWSE MI_QFIIS（ETF 類）2025-06-16～18 與 FinMind 同期，
裁成 4 檔。0050 在 2025-06-18 分割（1 拆 4），單位數的跳動落在 06-17。
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
from twquant.data import schemas
from twquant.data.serving import schema as sch
from twquant.data.sources import etf_units as src
from twquant.errors import ConfigError, DataError

_FIX = Path(__file__).parent / "fixtures" / "http" / "etf_units"
_ROOT = Path(__file__).resolve().parent.parent
_D16, _D17, _D18 = dt.date(2025, 6, 16), dt.date(2025, 6, 17), dt.date(2025, 6, 18)
_D13, _D19 = dt.date(2025, 6, 13), dt.date(2025, 6, 19)
_CAL = [_D13, _D16, _D17, _D18, _D19]


def _qfiis(day: dt.date) -> dict[str, Any]:
    p = _FIX / f"twse_mi_qfiis_0099P_{day:%Y%m%d}.json"
    data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return data


def _meta(*ids: str) -> EtfMetaConfig:
    return EtfMetaConfig(
        etfs=tuple(
            EtfMetaEntry(
                etf_id=i,
                name=i,
                issuer="x",
                fund_type="國內成分證券指數股票型基金",
                is_active=i.endswith("A"),
                listed_date=dt.date(2025, 5, 27) if i == "00981A" else dt.date(2003, 6, 30),
            )
            for i in ids
        )
    )


_META4 = _meta("0050", "0056", "00878", "00981A")


# ── AC-3 設定 ───────────────────────────────────────────────────────────────


def test_ac3_repo_configs_load() -> None:
    cfg = load_config(_ROOT / "configs" / "fund_flow.yaml", FundFlowConfig)
    meta = load_config(_ROOT / cfg.etf_meta_path, EtfMetaConfig)
    assert cfg.traces.last_date == dt.date(2026, 8, 31)
    assert len(meta.etfs) >= 80
    assert all(e.etf_id.startswith("00") for e in meta.etfs)


@pytest.mark.parametrize(
    ("yaml_text", "field"),
    [
        ("bogus: 1\n", "bogus"),
        ("events:\n  flow_hi_pctl: 1.2\n", "flow_hi_pctl"),
        ("events:\n  flow_lo_pctl: -0.1\n", "flow_lo_pctl"),
        ("factors:\n  pressure_window: 0\n", "pressure_window"),
        ("traces:\n  horizons: [1, 90]\n", "horizons"),
        ("units:\n  catchup_trading_days: 0\n", "catchup_trading_days"),
    ],
)
def test_ac3_invalid_config_names_field(tmp_path: Path, yaml_text: str, field: str) -> None:
    p = tmp_path / "fund_flow.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ConfigError, match=field):
        load_config(p, FundFlowConfig)


def test_ac3_etf_meta_rejects_duplicates_and_bad_ids() -> None:
    e = _META4.etfs[0]
    with pytest.raises(ValueError, match="重複"):
        EtfMetaConfig(etfs=(e, e))
    with pytest.raises(ValueError, match="etf_id"):
        EtfMetaEntry(**{**e.model_dump(), "etf_id": "50"})


# ── AC-4 解析與差分 ──────────────────────────────────────────────────────────


def test_ac4_qfiis_parse_units_are_int_shares() -> None:
    rows = src.qfiis_units_rows(_qfiis(_D16), _D16)
    by = {r["etf_id"]: r for r in rows}
    assert set(by) == {"0050", "0056", "00878", "00981A"}
    assert by["0050"]["units"] == 3_487_500_000
    assert by["00981A"]["units"] == 271_209_000
    assert all(isinstance(r["units"], int) and r["date"] == _D16 for r in rows)


def test_ac4_qfiis_rejects_wrong_day_and_bad_numbers() -> None:
    with pytest.raises(DataError, match="不是要的那天"):
        src.qfiis_units_rows(_qfiis(_D16), _D17)
    bad = _qfiis(_D16)
    bad["data"][0][3] = "--"
    with pytest.raises(DataError, match="非負整數"):
        src.qfiis_units_rows(bad, _D16)


def test_ac4_finmind_matches_twse_same_day() -> None:
    payload = json.loads((_FIX / "finmind_shareholding_0050_202506.json").read_text("utf-8"))
    fm = {
        r["date"]: r["units"]
        for r in src.finmind_units_rows(payload["data"], "NumberOfSharesIssued")
    }
    for d in (_D16, _D17, _D18):
        tw = {r["etf_id"]: r["units"] for r in src.qfiis_units_rows(_qfiis(d), d)}
        assert fm[d] == tw["0050"], d


def _raw(*days: dt.date) -> pl.DataFrame:
    rows = [r for d in days for r in src.qfiis_units_rows(_qfiis(d), d)]
    return pl.DataFrame(rows, schema={"date": pl.Date, "etf_id": pl.String, "units": pl.Int64})


def test_ac4_units_frame_delta_split_window_and_available_at() -> None:
    null_days = ffu.split_null_days([{"stock_id": "0050", "date": _D18}], _CAL, before=1)
    assert null_days == {"0050": {_D17, _D18}}
    df = ffu.units_frame(_raw(_D16, _D17, _D18), _CAL, [_D16, _D17, _D18], null_days, 21)
    get = {(d, s): v for d, s, v in df.select("date", "etf_id", "units_delta").iter_rows()}
    # 第一天沒有前一交易日（06-13 不在輸入）→ null，不是 0
    assert get[(_D16, "0056")] is None
    assert get[(_D17, "0056")] == 13_163_534_000 - 13_158_534_000
    assert get[(_D18, "0056")] == 13_173_034_000 - 13_163_534_000
    assert get[(_D17, "00878")] == 0
    # 0050 分割：事件日與前一交易日都 null（跳動 ×4 不是申購）
    assert get[(_D17, "0050")] is None and get[(_D18, "0050")] is None
    at = df.filter(pl.col("date") == _D17)["available_at"][0]
    assert at == dt.datetime(2025, 6, 17, 21, tzinfo=TZ)
    assert df.schema["available_at"] == schemas.TW_DT
    assert df["nav"].null_count() == df.height


def test_ac4_units_frame_does_not_bridge_missing_session() -> None:
    # 06-17 缺 → 06-18 的差不能跨過去用 06-16
    df = ffu.units_frame(_raw(_D16, _D18), _CAL, [_D18], {}, 21)
    assert df["units_delta"].null_count() == df.height


def test_ac4_units_frame_rejects_off_calendar_and_duplicates() -> None:
    with pytest.raises(DataError, match="交易日曆"):
        ffu.units_frame(_raw(_D16), [_D13, _D17], [_D16], {}, 21)
    dup = pl.concat([_raw(_D16), _raw(_D16)])
    with pytest.raises(DataError, match="重複"):
        ffu.units_frame(dup, _CAL, [_D16], {}, 21)


def test_ac4_units_jumps_reports_unexplained() -> None:
    df = ffu.units_frame(_raw(_D16, _D17), _CAL, [_D17], {}, 21)  # 故意不給分割窗
    jumps = ffu.units_jumps(df, 1.5)
    assert jumps["etf_id"].to_list() == ["0050"]


def test_ac4_meta_frame_from_repo_config_validates() -> None:
    cfg = load_config(_ROOT / "configs" / "fund_flow.yaml", FundFlowConfig)
    meta = load_config(_ROOT / cfg.etf_meta_path, EtfMetaConfig)
    df = ffu.etf_meta_frame(meta)
    assert df.height == len(meta.etfs)
    row = df.filter(pl.col("etf_id") == "0050").row(0, named=True)
    assert row["is_active"] is False and row["available_at"].hour == 18


def test_ac4_pg_specs_match_pandera_columns() -> None:
    for name in sch.FUND_FLOW_TABLES:
        assert sch.spec_of(name).column_names == tuple(schemas.get_schema(name).columns), name
    assert not set(sch.FUND_FLOW_TABLES) & set(sch.MIRROR_TABLES), "rebuild 會清空直寫表"


def test_ac6_require_complete_raises_on_missing_etf() -> None:
    df = ffu.units_frame(_raw(_D16), _CAL, [_D16], {}, 21)
    ffu.require_complete(df, _META4, [_D16])
    with pytest.raises(DataError, match="0052"):
        ffu.require_complete(df, _meta("0050", "0052"), [_D16])
    # 上市當天不要求
    fresh = _meta("0050", "00999A")
    fresh = EtfMetaConfig(
        etfs=(fresh.etfs[0], fresh.etfs[1].model_copy(update={"listed_date": _D16}))
    )
    ffu.require_complete(df, fresh, [_D16])


# ── AC-5／AC-6 PG 往返（scratch schema；未設 TWQUANT_PG_DSN 即跳過）────────────────


def _pg_or_skip() -> Any:
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    import psycopg

    return psycopg.connect(dsn)


_SCHEMA = "serving_test_t335"


def _fresh_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
    conn.commit()
    sch.ensure_tables(conn, (sch.spec_of("prices_daily"), sch.spec_of("corporate_events")), _SCHEMA)
    with conn.cursor() as cur:
        cur.executemany(
            f'INSERT INTO "{_SCHEMA}".prices_daily (date, stock_id) VALUES (%s, %s)',
            [(d, "2330") for d in _CAL],
        )
        cur.execute(
            f'INSERT INTO "{_SCHEMA}".corporate_events (date, stock_id, event_type) '
            "VALUES (%s, '0050', 'split')",
            (_D18,),
        )
    conn.commit()


def _cfg() -> FundFlowConfig:
    base = load_config(_ROOT / "configs" / "fund_flow.yaml", FundFlowConfig)
    return base.model_copy(
        update={"units": base.units.model_copy(update={"catchup_trading_days": 3})}
    )


def _fetch(day: dt.date) -> list[dict[str, Any]]:
    return src.qfiis_units_rows(_qfiis(day), day)


def _dump(conn: Any) -> list[tuple[Any, ...]]:
    with conn.cursor() as cur:
        cur.execute(
            f'SELECT date, etf_id, units, units_delta, nav FROM "{_SCHEMA}".etf_units_daily '
            "ORDER BY date, etf_id"
        )
        return list(cur.fetchall())


def test_ac5_pg_ingest_is_idempotent() -> None:
    from twquant.data.serving import fund_flow_ingest as ffi

    conn = _pg_or_skip()
    try:
        _fresh_schema(conn)
        first = ffi.ingest_recent_units(conn, _SCHEMA, _cfg(), _META4, _D19, _fetch)
        assert first.fetched == [_D16, _D17, _D18]
        assert first.rows == 12
        before = _dump(conn)
        second = ffi.ingest_recent_units(conn, _SCHEMA, _cfg(), _META4, _D19, _fetch)
        assert second.fetched == [] and second.rows == 0
        assert _dump(conn) == before
        delta = {(d, s): v for d, s, _, v, _ in before}
        assert delta[(_D17, "0050")] is None and delta[(_D18, "0050")] is None
        assert delta[(_D18, "0056")] == 9_500_000
        n = ffi.write_meta(conn, _SCHEMA, _META4)
        assert n == 4 and ffi.write_meta(conn, _SCHEMA, _META4) == 4
    finally:
        conn.close()


def test_ac6_pg_missing_etf_writes_nothing() -> None:
    from twquant.data.serving import fund_flow_ingest as ffi

    conn = _pg_or_skip()
    try:
        _fresh_schema(conn)
        meta = _meta("0050", "0056", "00878", "00981A", "0052")
        with pytest.raises(DataError, match="0052"):
            ffi.ingest_recent_units(conn, _SCHEMA, _cfg(), meta, _D19, _fetch)
        assert _dump(conn) == []
    finally:
        conn.close()


def test_ac6_untracked_etf_needs_reason_and_is_not_required() -> None:
    base = _META4.etfs[0]
    with pytest.raises(ValueError, match="note"):
        EtfMetaEntry(**{**base.model_dump(), "etf_id": "0060", "units_tracked": False})
    untracked = EtfMetaEntry(
        **{**base.model_dump(), "etf_id": "0060", "units_tracked": False, "note": "來源沒有"}
    )
    meta = EtfMetaConfig(etfs=(*_META4.etfs, untracked))
    df = ffu.units_frame(_raw(_D16), _CAL, [_D16], {}, 21)
    ffu.require_complete(df, meta, [_D16])  # 0060 不追蹤 → 不算缺
    assert ffu.etf_meta_frame(meta).filter(pl.col("etf_id") == "0060")["units_tracked"][0] is False
