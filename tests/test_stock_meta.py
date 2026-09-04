"""stock_meta（中文名 / 產業別族群）清洗 + 個股因子體檢卡（monitoring.snapshot）測試。

clean_stock_meta 以全形空格切代號/中文名、產業別=族群、濾非四碼；stock_meta_map 供中文名
查詢；scorecard 的白名單防注入與無行情降級。完整因子計算屬整合層（smoke + 對真實資料湖手驗）。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

import polars as pl
import pytest
from fastapi import HTTPException

from twquant.conventions import TZ
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


# ── T-111：prices 有、stock_meta 沒有的股票（多為已下市）────────────────────


def test_ac1_factors_silently_skip_stock_missing_from_meta(tmp_path: Path) -> None:
    """有行情但不在 stock_meta 的股票，三個依賴 stock_meta 的因子會**靜默**略過它。

    這是 T-111 的缺陷本體：2,056 檔有行情的股票中有 82 檔不在 stock_meta（來源 ISIN 只列現存上市），
    其中 price.residual_reversal_5d 是 25 個 pin 特徵之一。沒有任何錯誤或警告。
    """
    import datetime as dt

    import twquant.factors.builtins  # noqa: F401
    from twquant.data import schemas
    from twquant.factors import compute as fc

    days = [dt.date(2025, 3, d) for d in (3, 4, 5, 6, 7, 10, 11, 12)]
    px = pl.DataFrame(
        [
            {
                "date": d,
                "stock_id": s,
                "open": 100.0,
                "high": 100.0,
                "low": 100.0,
                "close": 100.0 + i,
                "volume": 1000,
                "amount": 100_000.0,
                "limit_up_price": None,
                "limit_down_price": None,
                "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
            }
            for i, d in enumerate(days)
            for s in ("1101", "1102", "9999")
        ]
    ).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )
    meta = pl.DataFrame(
        [
            {
                "stock_id": "1101",
                "name": "甲",
                "market": "TWSE",
                "industry": "水泥工業",
                "list_date": None,
                "delist_date": None,
            },
            {
                "stock_id": "1102",
                "name": "乙",
                "market": "TWSE",
                "industry": "水泥工業",
                "list_date": None,
                "delist_date": None,
            },
        ]
    ).with_columns(pl.col("list_date").cast(pl.Date), pl.col("delist_date").cast(pl.Date))

    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", px, source="twse")
    wh.write("clean", "stock_meta", meta, source="twse")
    out = fc.compute(["price.residual_reversal_5d"], wh.view(days[-1]), dates=[days[-1]])
    got = set(out["stock_id"].to_list())
    assert "1101" in got and "1102" in got
    assert "9999" not in got, "有行情卻不在 stock_meta 的股票被靜默略過（T-111 的缺陷）"


def test_ac2_stock_info_rows_maps_industry_aliases() -> None:
    """FinMind 用舊版產業分類，名稱與 ISIN 不同（『建材營造』vs『建材營造業』）。
    直接寫入會把同一個產業拆成兩組，讓既有股票的產業均值失真，故必須對映。"""
    from twquant.data.sources.finmind import stock_info_rows

    rows = [
        {"stock_id": "1101", "stock_name": "台泥", "industry_category": "水泥工業", "type": "twse"},
        {
            "stock_id": "2542",
            "stock_name": "興富發",
            "industry_category": "建材營造",
            "type": "twse",
        },
        {"stock_id": "2903", "stock_name": "遠百", "industry_category": "貿易百貨", "type": "twse"},
        {"stock_id": "2801", "stock_name": "彰銀", "industry_category": "金融保險", "type": "twse"},
        {"stock_id": "5871", "stock_name": "中租", "industry_category": "金融業", "type": "twse"},
        {"stock_id": "2731", "stock_name": "雄獅", "industry_category": "觀光事業", "type": "tpex"},
    ]
    got = {r["stock_id"]: r for r in stock_info_rows(rows)}
    assert got["1101"]["industry"] == "水泥工業"
    assert got["2542"]["industry"] == "建材營造業"
    assert got["2903"]["industry"] == "貿易百貨業"
    assert got["2801"]["industry"] == "金融保險業"
    assert got["5871"]["industry"] == "金融保險業"
    assert got["2731"]["industry"] == "觀光餐旅"
    assert got["1101"]["market"] == "TWSE" and got["2731"]["market"] == "TPEX"
    assert got["1101"]["name"] == "台泥"


def test_ac2_stock_info_rows_leaves_unmappable_industry_null() -> None:
    """舊的大分類（『電子工業』『化學生技醫療』）在現行分類已拆成多個子產業，
    硬歸到其中一個會污染那個族群 → 留 null，不猜。"""
    from twquant.data.sources.finmind import stock_info_rows

    rows = [
        {"stock_id": "2499", "stock_name": "東貝", "industry_category": "電子工業", "type": "twse"},
        {
            "stock_id": "4152",
            "stock_name": "台微體",
            "industry_category": "化學生技醫療",
            "type": "tpex",
        },
        {
            "stock_id": "9188",
            "stock_name": "某存託",
            "industry_category": "存託憑證",
            "type": "twse",
        },
    ]
    got = {r["stock_id"]: r for r in stock_info_rows(rows)}
    assert all(got[s]["industry"] is None for s in ("2499", "4152", "9188"))
    assert got["2499"]["market"] == "TWSE"  # 市場別仍要保留


def test_ac2_stock_info_rows_raises_on_unknown_industry() -> None:
    """來源新增未知分類時必須 raise——靜默補 null 會讓因子悄悄少一批股票（黃金規則 5）。"""
    from twquant.data.sources.finmind import stock_info_rows

    with pytest.raises(DataError, match="未知產業別"):
        stock_info_rows(
            [
                {
                    "stock_id": "1234",
                    "stock_name": "X",
                    "industry_category": "太空採礦業",
                    "type": "twse",
                }
            ]
        )


def test_ac3_delisting_rows_parse_delist_date() -> None:
    """下市日取自 TaiwanStockDelisting（權威），不用『最後交易日』推導——
    實測兩者差 5~200 天（2499：最後交易 2020-04-06、實際下市 2020-11-10）。"""
    import datetime as dt

    from twquant.data.sources.finmind import delisting_rows

    got = delisting_rows(
        [
            {"date": "2020-11-10", "stock_id": "2499", "stock_name": "東貝光電股份有限公司"},
            {"date": "2022-04-21", "stock_id": "1507", "stock_name": "永大機電工業股份有限公司"},
        ]
    )
    assert {r["stock_id"]: r["delist_date"] for r in got} == {
        "2499": dt.date(2020, 11, 10),
        "1507": dt.date(2022, 4, 21),
    }


def test_ac2_reconcile_adds_missing_and_keeps_existing(tmp_path: Path) -> None:
    """補列規則：既有列一律不覆蓋（除了原本永遠是 null 的 delist_date）；
    行情有、meta 沒有的補進來；兩個來源都沒有市場別的股票不得偽造。"""
    import datetime as dt

    from twquant.data.clean import reconcile_stock_meta

    existing = pl.DataFrame(
        [
            {
                "stock_id": "1101",
                "name": "台泥",
                "market": "TWSE",
                "industry": "水泥工業",
                "list_date": dt.date(1962, 2, 9),
                "delist_date": None,
            }
        ]
    ).with_columns(pl.col("list_date").cast(pl.Date), pl.col("delist_date").cast(pl.Date))
    info: list[Mapping[str, object]] = [
        {"stock_id": "2499", "name": "東貝", "market": "TWSE", "industry": None},
        {"stock_id": "1507", "name": "永大", "market": "TWSE", "industry": "電機機械"},
    ]
    delistings = [
        {"stock_id": "2499", "delist_date": dt.date(2020, 11, 10)},
        {"stock_id": "1507", "delist_date": dt.date(2022, 4, 21)},
        {"stock_id": "8913", "delist_date": dt.date(2020, 1, 20)},  # 無 info → 不得補列
    ]
    out = reconcile_stock_meta(
        existing, present_ids=["1101", "2499", "1507", "8913"], info=info, delistings=delistings
    )
    by = {r["stock_id"]: r for r in out.to_dicts()}
    assert by["1101"]["industry"] == "水泥工業" and by["1101"]["list_date"] == dt.date(1962, 2, 9)
    assert by["1507"]["industry"] == "電機機械"
    assert by["1507"]["delist_date"] == dt.date(2022, 4, 21)
    assert by["2499"]["industry"] is None and by["2499"]["delist_date"] == dt.date(2020, 11, 10)
    assert "8913" not in by, "缺市場別的股票不得偽造補列（market 為必填）"


def test_ac3_reconcile_fills_delist_date_for_existing_rows() -> None:
    """已在 stock_meta 但實際已下市的股票，delist_date 要補上（原本一律寫死 null）。"""
    import datetime as dt

    from twquant.data.clean import reconcile_stock_meta

    existing = pl.DataFrame(
        [
            {
                "stock_id": "2456",
                "name": "奇力新",
                "market": "TWSE",
                "industry": "電子零組件業",
                "list_date": None,
                "delist_date": None,
            }
        ]
    ).with_columns(pl.col("list_date").cast(pl.Date), pl.col("delist_date").cast(pl.Date))
    out = reconcile_stock_meta(
        existing,
        present_ids=["2456"],
        info=[],
        delistings=[{"stock_id": "2456", "delist_date": dt.date(2022, 1, 5)}],
    )
    assert out.to_dicts()[0]["delist_date"] == dt.date(2022, 1, 5)
