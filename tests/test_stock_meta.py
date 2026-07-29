"""stock_meta（中文名 / 產業別族群）清洗 + 個股因子體檢卡（monitoring.snapshot）測試。

clean_stock_meta 以全形空格切代號/中文名、產業別=族群、濾非四碼；stock_meta_map 供中文名
查詢；scorecard 的白名單防注入與無行情降級。完整因子計算屬整合層（smoke + 對真實資料湖手驗）。
"""

from __future__ import annotations

import os
from pathlib import Path

import polars as pl
import pytest
from fastapi import HTTPException

from twquant.data.clean import clean_stock_meta
from twquant.data.sources import raw_frame
from twquant.data.warehouse import Warehouse
from twquant.errors import DataError
from twquant.monitoring import api
from twquant.monitoring import snapshot as snap

_FIELDS = ["code_name", "isin", "list_date", "market", "industry", "cfi", "note"]


def _isin(rows: list[list[str]]) -> pl.DataFrame:
    return raw_frame(_FIELDS, rows)


def test_clean_stock_meta_split_and_industry() -> None:
    raw = _isin(
        [
            ["2330　台積電", "TW0002330008", "1994/09/05", "上市", "半導體業", "ESVUFR", ""],
            ["2317　鴻海", "TW0002317005", "1991/06/18", "上市", "其他電子業", "ESVUFR", ""],
            ["合計 xyz", "", "", "上市", "", "", ""],  # 無全形空格 → 代號非法 → 丟棄
        ]
    )
    out = clean_stock_meta(raw, "TWSE")
    assert out.height == 2  # 合計列被濾
    d = {r["stock_id"]: r for r in out.to_dicts()}
    assert d["2330"]["name"] == "台積電"
    assert d["2330"]["industry"] == "半導體業"  # 產業別=族群
    assert d["2330"]["market"] == "TWSE"
    assert str(d["2330"]["list_date"]) == "1994-09-05"


def test_clean_stock_meta_blank_industry_is_null() -> None:
    out = clean_stock_meta(
        _isin([["0050　元大台灣50", "x", "2003/06/25", "上市", "", "y", ""]]), "TWSE"
    )
    row = out.to_dicts()[0]
    assert row["name"] == "元大台灣50"
    assert row["industry"] is None  # 空產業別 → null


def _meta_wh(tmp_path: Path) -> Warehouse:
    wh = Warehouse(tmp_path)
    wh.write(
        "clean",
        "stock_meta",
        clean_stock_meta(
            _isin([["2330　台積電", "x", "1994/09/05", "上市", "半導體業", "y", ""]]), "TWSE"
        ),
        source="twse",
    )
    return wh


def test_stock_meta_map(tmp_path: Path) -> None:
    m = snap.stock_meta_map(_meta_wh(tmp_path))
    assert m["2330"]["name"] == "台積電"
    assert m["2330"]["industry"] == "半導體業"


def test_scorecard_joins_name_without_prices(tmp_path: Path) -> None:
    # 有 stock_meta、無行情 → 降級：帶中文名/族群、factors 空、不報錯
    sc = snap.stock_factor_scorecard(_meta_wh(tmp_path), "2330")
    assert sc["sid"] == "2330"
    assert sc["name"] == "台積電"
    assert sc["industry"] == "半導體業"
    assert sc["factors"] == []


def test_scorecard_rejects_injection(tmp_path: Path) -> None:
    with pytest.raises(DataError):
        snap.stock_factor_scorecard(Warehouse(tmp_path), "2330'; DROP TABLE prices_daily;--")


def test_api_stock_factors_rejects_injection(tmp_path: Path) -> None:
    """非法代號在碰 PG 之前就被 `_check` 擋下 → 400（不需要 PG，任何環境都該過）。"""
    with pytest.raises(HTTPException) as ei:
        api.get_stock_factors("bad'; --", _meta_wh(tmp_path))
    assert ei.value.status_code == 400


def test_api_stock_meta_reads_pg(tmp_path: Path) -> None:
    """/api/stock-meta 走 PG（D-035 起無 DuckDB 降級）；無 PG 環境跳過而非假裝有資料。"""
    pytest.importorskip("psycopg")
    if not os.environ.get("TWQUANT_PG_DSN"):
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    meta = api.get_stock_meta(_meta_wh(tmp_path))["meta"]
    assert isinstance(meta, dict)


def test_sector_flows_empty_when_missing(tmp_path: Path) -> None:
    # 缺 stock_meta / institutional / prices 任一 → 空結構、不報錯
    f = snap.sector_flows(Warehouse(tmp_path))
    assert f["sectors"] == []
    assert f["dates"] == []
    assert f["as_of"] is None


def test_sector_flows_aggregates_by_industry(snapshot_wh: Warehouse) -> None:
    # snapshot_wh 有 prices(twse)+institutional；補 stock_meta 後聚合出族群資金流
    snapshot_wh.write(
        "clean",
        "stock_meta",
        clean_stock_meta(
            _isin(
                [
                    ["2330　台積電", "x", "1994/09/05", "上市", "半導體業", "y", ""],
                    ["2317　鴻海", "x", "1991/06/18", "上市", "其他電子業", "y", ""],
                ]
            ),
            "TWSE",
        ),
        source="twse",
    )
    f = snap.sector_flows(snapshot_wh, window=20)
    sectors = f["sectors"]
    assert isinstance(sectors, list)
    inds = {s["industry"] for s in sectors}
    assert "半導體業" in inds
    assert "其他電子業" in inds
    assert f["as_of"] == "2026-06-30"
    assert isinstance(f["daily"], dict)
