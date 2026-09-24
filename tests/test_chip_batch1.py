"""T-309／D-154：第 1 批免費籌碼——當沖、借券賣出、處置股、注意股的映射、解析與儲存。

fixture（`tests/fixtures/http/chip_batch1/`）是 2026-09-25 實測回應的裁切，結構原樣保留；
只有「總筆數」類的中繼欄位（證交所 `total`／`count`、櫃買 `subtitle`）改成裁切後的列數，
因為解析器會拿它們核對回應有沒有被截斷。測試不連網。

兩個最容易錯、而且錯了數字仍然「看起來合理」的地方：
1. **單位**：`TaiwanDailyShortSaleBalances` 是**股**；隔壁的 `TaiwanStockMarginPurchaseShortSale`
   是**張**。照抄融資券的 ×1000 會讓借券餘額大一千倍。
2. **缺值**：缺欄或 null 一律 raise／留 null，絕不當 0——0 是有意義的值（當天沒有借券賣出）。
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from twquant.config import SourceRate
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.http import HttpClient
from twquant.data.serving import schema as sch
from twquant.data.sources import alerts
from twquant.data.sources.finmind import (
    DAYTRADE_DATASET,
    SBL_DATASET,
    daytrade_rows,
    sbl_identity_violations,
    sbl_short_rows,
)
from twquant.errors import DataError, QualityGateError

_FIX = Path(__file__).parent / "fixtures" / "http" / "chip_batch1"
_D1 = dt.date(2018, 1, 2)
_D2 = dt.date(2018, 1, 3)
_BATCH1_TABLES = {"daytrade_daily", "sbl_short_daily", "disposition_stocks", "attention_stocks"}


def _load(name: str) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads((_FIX / name).read_text(encoding="utf-8"))
    return payload


def _daytrade_raw() -> list[dict[str, Any]]:
    return [dict(r) for r in _load("finmind_daytrade.json")["data"]]


def _sbl_raw() -> list[dict[str, Any]]:
    return [dict(r) for r in _load("finmind_sbl.json")["data"]]


def _by_key(rows: list[dict[str, Any]]) -> dict[tuple[str, dt.date], dict[str, Any]]:
    return {(r["stock_id"], r["date"]): r for r in rows}


# ── AC-309-1 當沖映射 ──────────────────────────────────────────────────────


def test_ac309_1_daytrade_maps_shares_and_twd() -> None:
    """Volume 是股、金額是元：2330 @ 2018-01-02 買進均價 231.98，當日收盤 232.5。"""
    assert DAYTRADE_DATASET == "TaiwanStockDayTrading"
    rows = daytrade_rows(_daytrade_raw())
    assert rows == [
        {
            "date": _D1,
            "stock_id": "2330",
            "daytrade_shares": 758_000,
            "daytrade_buy_amount": 175_838_000.0,
            "daytrade_sell_amount": 175_903_000.0,
            "sell_first_suspended": False,
        },
        {
            "date": _D2,
            "stock_id": "2330",
            "daytrade_shares": 1_778_000,
            "daytrade_buy_amount": 420_409_500.0,
            "daytrade_sell_amount": 421_344_500.0,
            "sell_first_suspended": False,
        },
    ]
    avg = rows[0]["daytrade_buy_amount"] / rows[0]["daytrade_shares"]
    assert 225.0 < avg < 240.0, "若把股當張（或反之）均價會差 1,000 倍"


@pytest.mark.parametrize(("flag", "expected"), [("", False), (" ", False), ("Y", True)])
def test_ac309_1_sell_first_flag_known_values(flag: str, expected: bool) -> None:
    raw = _daytrade_raw()[:1]
    raw[0]["BuyAfterSale"] = flag
    assert daytrade_rows(raw)[0]["sell_first_suspended"] is expected


@pytest.mark.parametrize("flag", ["N", "y", "YY", "*", None, 1])
def test_ac309_1_unknown_flag_raises(flag: object) -> None:
    """只認 ''／' '／'Y'；其他值＝來源改了語意，不猜。"""
    raw = _daytrade_raw()[:1]
    raw[0]["BuyAfterSale"] = flag
    with pytest.raises(DataError, match="BuyAfterSale"):
        daytrade_rows(raw)


@pytest.mark.parametrize("field", ["Volume", "BuyAmount", "SellAmount", "BuyAfterSale", "date"])
def test_ac309_1_missing_field_raises(field: str) -> None:
    raw = _daytrade_raw()[:1]
    del raw[0][field]
    with pytest.raises(DataError):
        daytrade_rows(raw)


@pytest.mark.parametrize("field", ["Volume", "BuyAmount", "SellAmount"])
def test_ac309_1_null_value_raises_not_zero(field: str) -> None:
    raw = _daytrade_raw()[:1]
    raw[0][field] = None
    with pytest.raises(DataError, match=field):
        daytrade_rows(raw)


@pytest.mark.parametrize("bad", [758_000.5, "758000", True])
def test_ac309_1_non_integer_shares_raise(bad: object) -> None:
    """股數必須是整數：758000.5 截成 758000 是靜默改值。"""
    raw = _daytrade_raw()[:1]
    raw[0]["Volume"] = bad
    with pytest.raises(DataError, match="Volume"):
        daytrade_rows(raw)


def test_ac309_1_frame_available_at_21_and_schema() -> None:
    """同日 21:00（比照 margin_daily，D-008）：當日 20:00 訊號截點看不到，隔日才用。"""
    df, exact_dups = alerts.build_frame("daytrade_daily", daytrade_rows(_daytrade_raw()))
    assert exact_dups == 0
    assert df.columns == list(schemas.get_schema("daytrade_daily").columns)
    assert df["available_at"].dtype == schemas.TW_DT
    assert df["available_at"].to_list() == [
        dt.datetime(2018, 1, 2, 21, 0, tzinfo=TZ),
        dt.datetime(2018, 1, 3, 21, 0, tzinfo=TZ),
    ]
    assert df["daytrade_shares"].dtype == pl.Int64
    assert df["sell_first_suspended"].dtype == pl.Boolean


# ── AC-309-2 借券映射 ──────────────────────────────────────────────────────


def test_ac309_2_sbl_maps_shares_not_lots() -> None:
    """借券是**股**，不 ×1000。

    單位證據：同一回應的 MarginShortSalesCurrentDayBalance＝2,161,000，等於本專案
    `margin_daily.short_balance`（股）2330 @ 2018-01-02 的 2,161,000。
    """
    assert SBL_DATASET == "TaiwanDailyShortSaleBalances"
    rows = sbl_short_rows(_sbl_raw())
    assert rows[0] == {
        "date": _D1,
        "stock_id": "2330",
        "sbl_prev_balance": 12_538_000,
        "sbl_sell": 45_000,
        "sbl_return": 178_000,
        "sbl_adjust": 0,
        "sbl_balance": 12_405_000,
        "sbl_next_quota": 8_386_788,
        "sbl_covering": 0,
    }
    assert rows[1]["sbl_balance"] == 12_659_000
    assert rows[1]["sbl_sell"] == 254_000


_SBL_REQUIRED = [
    "SBLShortSalesPreviousDayBalance",
    "SBLShortSalesShortSales",
    "SBLShortSalesReturns",
    "SBLShortSalesAdjustments",
    "SBLShortSalesCurrentDayBalance",
]
_SBL_NULLABLE = ["SBLShortSalesQuota", "SBLShortSalesShortCovering"]


@pytest.mark.parametrize("field", [*_SBL_REQUIRED, *_SBL_NULLABLE, "date"])
def test_ac309_2_missing_field_raises(field: str) -> None:
    """缺欄＝來源改版：連可為 null 的兩欄也一樣 raise（缺欄與「值為 null」是兩回事）。"""
    raw = _sbl_raw()[:1]
    del raw[0][field]
    with pytest.raises(DataError):
        sbl_short_rows(raw)


@pytest.mark.parametrize("field", _SBL_REQUIRED)
def test_ac309_2_required_null_raises(field: str) -> None:
    raw = _sbl_raw()[:1]
    raw[0][field] = None
    with pytest.raises(DataError, match=field):
        sbl_short_rows(raw)


def test_ac309_2_quota_and_covering_null_stay_null() -> None:
    raw = _sbl_raw()[:1]
    for field in _SBL_NULLABLE:
        raw[0][field] = None
    row = sbl_short_rows(raw)[0]
    assert row["sbl_next_quota"] is None
    assert row["sbl_covering"] is None


def test_ac309_2_identity_holds_on_real_rows_and_flags_violations() -> None:
    """TWT93U 附註：當日餘額＝前日餘額＋當日賣出－當日還券＋當日調整。"""
    df, _ = alerts.build_frame("sbl_short_daily", sbl_short_rows(_sbl_raw()))
    assert sbl_identity_violations(df).height == 0
    broken = df.with_columns(
        pl.when(pl.col("date") == _D2)
        .then(pl.col("sbl_balance") + 1_000)
        .otherwise(pl.col("sbl_balance"))
        .alias("sbl_balance")
    )
    bad = sbl_identity_violations(broken)
    assert bad["date"].to_list() == [_D2]
    assert bad.columns == df.columns


def test_ac309_2_adjust_may_be_negative_other_legs_may_not() -> None:
    raw = _sbl_raw()[:1]
    raw[0]["SBLShortSalesAdjustments"] = -5_000
    raw[0]["SBLShortSalesCurrentDayBalance"] = 12_400_000
    df, _ = alerts.build_frame("sbl_short_daily", sbl_short_rows(raw))
    assert df["sbl_adjust"].to_list() == [-5_000]
    assert sbl_identity_violations(df).height == 0

    raw[0]["SBLShortSalesShortSales"] = -1
    with pytest.raises(QualityGateError):
        alerts.build_frame("sbl_short_daily", sbl_short_rows(raw))


# ── AC-309-3 證交所處置 ────────────────────────────────────────────────────


def test_ac309_3_twse_disposition_rows() -> None:
    parsed = alerts.twse_disposition_rows(_load("twse_punish.json"))
    assert parsed.placeholders == 0
    got = _by_key(parsed.rows)
    assert set(got) == {
        ("9958", dt.date(2018, 1, 8)),
        ("9958", dt.date(2018, 1, 11)),
        ("1475", dt.date(2018, 8, 23)),
        ("03775P", dt.date(2018, 11, 5)),  # 權證也留（全部有價證券）
    }
    first = got[("9958", dt.date(2018, 1, 8))]
    assert (first["effective_start"], first["effective_end"]) == (
        dt.date(2018, 1, 9),
        dt.date(2018, 1, 22),
    )
    assert (first["level"], first["match_minutes"], first["reason"]) == (1, 5, "連續三次")

    second = got[("9958", dt.date(2018, 1, 11))]
    assert (second["level"], second["match_minutes"]) == (2, 20)

    manual = got[("1475", dt.date(2018, 8, 23))]
    assert manual["level"] is None, "人工管制撮合沒有「第幾次」，不猜"
    assert manual["match_minutes"] == 25

    warrant = got[("03775P", dt.date(2018, 11, 5))]
    assert (warrant["effective_start"], warrant["effective_end"]) == (
        dt.date(2018, 11, 6),
        dt.date(2018, 11, 19),
    )
    for r in parsed.rows:
        assert r["market"] == "twse"
        assert "<" not in r["measure"]
        assert "約每" in r["measure"], "全形括號轉半形後內容仍在"


def test_ac309_3_unknown_measure_raises() -> None:
    payload = copy.deepcopy(_load("twse_punish.json"))
    payload["data"][0][7] = "第三次處置"
    with pytest.raises(DataError, match="第三次處置"):
        alerts.twse_disposition_rows(payload)


def test_ac309_3_empty_code_raises() -> None:
    payload = copy.deepcopy(_load("twse_punish.json"))
    payload["data"][0][2] = ""
    with pytest.raises(DataError, match="證券代號"):
        alerts.twse_disposition_rows(payload)


def test_ac309_3_truncated_response_raises() -> None:
    """回應的 total 與實際列數不同＝被截斷，不得當成完整的一年。"""
    payload = copy.deepcopy(_load("twse_punish.json"))
    payload["total"] = 5
    with pytest.raises(DataError, match="total"):
        alerts.twse_disposition_rows(payload)


def test_ac309_3_period_parsing() -> None:
    assert alerts.roc_period("107/11/06～107/11/19") == (
        dt.date(2018, 11, 6),
        dt.date(2018, 11, 19),
    )
    assert alerts.roc_period("106/12/22~107/01/05") == (dt.date(2017, 12, 22), dt.date(2018, 1, 5))
    for bad in ["107/11/19～107/11/06", "107/11/06", "107/11/06～107/11/19～107/11/20", ""]:
        with pytest.raises(ValueError):
            alerts.roc_period(bad)


@pytest.mark.parametrize(
    ("text", "n"),
    [
        ("五", 5),
        ("十", 10),
        ("十二", 12),
        ("二十", 20),
        ("二十五", 25),
        ("四十五", 45),
        ("九十九", 99),
        ("5", 5),
        ("45", 45),
        ("６０", 60),
    ],
)
def test_ac309_3_chinese_numerals(text: str, n: int) -> None:
    assert alerts.chinese_number(text) == n


@pytest.mark.parametrize("text", ["", "零", "一百", "二二", "十十", "0", "100", "五分", "兩"])
def test_ac309_3_bad_numerals_raise(text: str) -> None:
    with pytest.raises(ValueError):
        alerts.chinese_number(text)


def test_ac309_3_match_minutes() -> None:
    assert alerts.match_minutes("撮合作業（約每五分鐘撮合一次）") == 5
    assert alerts.match_minutes("撮合作業(約每10分鐘撮合一次)") == 10
    assert alerts.match_minutes("以人工管制之撮合終端機執行撮合作業") is None
    with pytest.raises(ValueError, match="撮合"):
        alerts.match_minutes("約每五分鐘撮合一次；另約每二十分鐘撮合一次")


def test_ac309_3_html_stripped() -> None:
    raw = "漲幅達 14.90倍<font color='#FF0000'>﹝第九款﹞</font>。<br>第二行&amp;"
    assert alerts.clean_text(raw) == "漲幅達 14.90倍〔第九款〕。\n第二行&"
    assert alerts.clean_text("  ") is None


# ── AC-309-4 櫃買處置 ──────────────────────────────────────────────────────


def test_ac309_4_placeholder_skipped() -> None:
    parsed = alerts.tpex_disposition_rows(_load("tpex_disposal.json"))
    assert parsed.placeholders == 1
    assert [(r["stock_id"], r["date"]) for r in parsed.rows] == [
        ("3219", dt.date(2017, 12, 21)),
        ("6248", dt.date(2017, 12, 21)),
    ]


def test_ac309_4_other_empty_code_raises() -> None:
    """沒有「本日無處置資料」的空代號列不是佔位列——丟掉它就是丟掉一筆處置。"""
    payload = copy.deepcopy(_load("tpex_disposal.json"))
    payload["tables"][0]["data"][1][2] = ""
    with pytest.raises(DataError, match="證券代號"):
        alerts.tpex_disposition_rows(payload)


def test_ac309_4_ascii_period_arabic_minutes_and_level() -> None:
    got = _by_key(alerts.tpex_disposition_rows(_load("tpex_disposal.json")).rows)
    repeat = got[("3219", dt.date(2017, 12, 21))]
    assert (repeat["effective_start"], repeat["effective_end"]) == (
        dt.date(2017, 12, 22),
        dt.date(2018, 1, 5),
    )
    assert (repeat["level"], repeat["match_minutes"]) == (2, 20), "內容含「曾發布處置」＝第二次"
    first = got[("6248", dt.date(2017, 12, 21))]
    assert (first["level"], first["match_minutes"]) == (1, 5)
    assert {r["market"] for r in got.values()} == {"tpex"}


def test_ac309_4_links_stripped() -> None:
    got = _by_key(alerts.tpex_disposition_rows(_load("tpex_disposal.json")).rows)
    for r in got.values():
        assert r["reason"] == "因連續3個營業日達本中心作業要點第四條第一項第一款"
        assert ".html" not in r["measure"]


def test_ac309_4_truncated_response_raises() -> None:
    payload = copy.deepcopy(_load("tpex_disposal.json"))
    payload["tables"][0]["subtitle"] = "證券個數:2 總累計次數: 3"
    with pytest.raises(DataError, match="總累計次數"):
        alerts.tpex_disposition_rows(payload)


# ── AC-309-5 注意股兩市 ────────────────────────────────────────────────────


def test_ac309_5_twse_attention() -> None:
    parsed = alerts.twse_attention_rows(_load("twse_notice.json"))
    got = _by_key(parsed.rows)
    etn = got[("01004T", dt.date(2018, 12, 28))]
    assert etn["clauses"] == "9"
    assert etn["close"] == 12.60
    assert "<font" not in etn["info"] and "第九款" in etn["info"]
    assert got[("9958", dt.date(2018, 1, 5))]["clauses"] == "1,2,3,4"
    only3 = got[("9958", dt.date(2018, 1, 3))]
    assert (only3["clauses"], only3["close"]) == ("3", 25.60)
    assert {r["market"] for r in parsed.rows} == {"twse"}
    assert all("cum_count" not in r for r in parsed.rows), "累計次數是查詢區間的總數（前視），不存"


def test_ac309_5_tpex_attention() -> None:
    parsed = alerts.tpex_attention_rows(_load("tpex_attention.json"))
    assert parsed.placeholders == 0
    got = _by_key(parsed.rows)
    assert got[("3317", dt.date(2018, 1, 31))]["clauses"] == "7"
    two = got[("6554", dt.date(2018, 1, 2))]
    assert two["clauses"] == "1,5"
    assert "<br>" not in two["info"] and "\n" in two["info"]
    assert got[("6265", dt.date(2018, 1, 31))]["close"] == 7.35
    assert {r["market"] for r in parsed.rows} == {"tpex"}


def test_ac309_5_roc_date_both_formats() -> None:
    assert alerts.roc_date("107.12.28") == dt.date(2018, 12, 28)
    assert alerts.roc_date("107/12/28") == dt.date(2018, 12, 28)
    assert alerts.roc_date("99/01/04") == dt.date(2010, 1, 4)
    for bad in ["2018-12-28", "107/13/01", "107/12", ""]:
        with pytest.raises(ValueError):
            alerts.roc_date(bad)


def test_ac309_5_clause_list() -> None:
    assert alerts.clause_list("甲﹝第十二款﹞乙(第一款)丙(第一款)") == "1,12"
    assert alerts.clause_list("最近三十個營業日起迄兩個營業日之收盤價漲幅達120.28%") is None


def test_ac309_5_close_tokens() -> None:
    for token in ["", "--", "-----", "N/A"]:
        assert alerts.number_or_none(token) is None
    assert alerts.number_or_none("1,234.50") == 1234.5
    assert alerts.number_or_none(12) == 12.0
    with pytest.raises(ValueError):
        alerts.number_or_none("12元")


def test_ac309_5_empty_info_raises() -> None:
    payload = copy.deepcopy(_load("twse_notice.json"))
    payload["data"][0][4] = "<font color='#FF0000'></font>"
    with pytest.raises(DataError, match="注意交易資訊"):
        alerts.twse_attention_rows(payload)


# ── AC-309-6 儲存 ──────────────────────────────────────────────────────────


def _all_frames() -> dict[str, tuple[pl.DataFrame, int]]:
    disp = [
        *alerts.twse_disposition_rows(_load("twse_punish.json")).rows,
        *alerts.tpex_disposition_rows(_load("tpex_disposal.json")).rows,
    ]
    attn = [
        *alerts.twse_attention_rows(_load("twse_notice.json")).rows,
        *alerts.tpex_attention_rows(_load("tpex_attention.json")).rows,
    ]
    return {
        "daytrade_daily": alerts.build_frame("daytrade_daily", daytrade_rows(_daytrade_raw())),
        "sbl_short_daily": alerts.build_frame("sbl_short_daily", sbl_short_rows(_sbl_raw())),
        "disposition_stocks": alerts.build_frame("disposition_stocks", disp),
        "attention_stocks": alerts.build_frame("attention_stocks", attn),
    }


def test_ac309_6_all_four_frames_validate() -> None:
    frames = _all_frames()
    assert set(frames) == _BATCH1_TABLES
    for table, (df, exact_dups) in frames.items():
        assert exact_dups == 0
        assert df.columns == list(schemas.get_schema(table).columns)
        schemas.validate(table, df)
    assert frames["disposition_stocks"][0]["available_at"].to_list()[0].hour == 18
    assert frames["attention_stocks"][0].height == 6


def test_ac309_6_direct_pg_group_not_mirror() -> None:
    """直寫 PG：`serving rebuild` 會 TRUNCATE 鏡射表再從（空的）資料湖重灌——放進鏡射清單會被清空。"""
    names = {s.name for s in sch.DIRECT_PG_SPECS}
    assert names == _BATCH1_TABLES
    assert not names & set(sch.MIRROR_TABLES)
    assert names <= set(sch.ALL_TABLES)
    for spec in sch.DIRECT_PG_SPECS:
        assert spec.pk == ("stock_id", "date")
        assert ("date",) in spec.indexes
        assert spec.column_names == tuple(schemas.get_schema(spec.name).columns), (
            f"{spec.name}：PG 欄位與 pandera schema 漂移"
        )
    with pytest.raises(ValueError, match="直寫"):
        sch.rebuild_targets(["daytrade_daily"])


def test_ac309_6_duplicate_key_with_different_content_raises() -> None:
    rows = daytrade_rows(_daytrade_raw())
    twin = dict(rows[0])
    twin["daytrade_shares"] += 1_000
    with pytest.raises(DataError, match="內容不同"):
        alerts.build_frame("daytrade_daily", [*rows, twin])


def test_ac309_6_exact_duplicate_collapses_and_is_counted() -> None:
    rows = daytrade_rows(_daytrade_raw())
    df, exact_dups = alerts.build_frame("daytrade_daily", [*rows, dict(rows[0])])
    assert df.height == 2
    assert exact_dups == 1


def test_ac309_6_row_keys_must_match_schema() -> None:
    rows = daytrade_rows(_daytrade_raw())
    extra = [{**rows[0], "surprise": 1}]
    with pytest.raises(ValueError, match="surprise"):
        alerts.build_frame("daytrade_daily", extra)
    missing = [{k: v for k, v in rows[0].items() if k != "daytrade_sell_amount"}]
    with pytest.raises(ValueError, match="daytrade_sell_amount"):
        alerts.build_frame("daytrade_daily", missing)


def test_ac309_6_empty_batch_is_empty_frame() -> None:
    df, exact_dups = alerts.build_frame("attention_stocks", [])
    assert df.is_empty() and exact_dups == 0
    assert df.columns == list(schemas.get_schema("attention_stocks").columns)


def _pg_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        return psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")


def test_ac309_6_pg_roundtrip_in_scratch_schema() -> None:
    """只建這四張（ensure_tables），upsert 兩次列數不變，型別（布林、timestamptz、null）原樣回來。"""
    from twquant.data.serving.sync import upsert

    conn = _pg_or_skip()
    test_schema = "serving_test_t309"
    try:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        sch.ensure_tables(conn, sch.DIRECT_PG_SPECS, test_schema)
        sch.ensure_tables(conn, sch.DIRECT_PG_SPECS, test_schema)  # 冪等
        with conn.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                (test_schema,),
            )
            assert {r[0] for r in cur.fetchall()} == _BATCH1_TABLES, "只建直寫的四張，不建其他表"

        frames = _all_frames()
        for _ in range(2):
            with conn.cursor() as cur:
                for table, (df, _dups) in frames.items():
                    upsert(cur, test_schema, sch.spec_of(table), df)
            conn.commit()
        with conn.cursor() as cur:
            for table, (df, _dups) in frames.items():
                cur.execute(f'SELECT count(*) FROM "{test_schema}"."{table}"')
                assert cur.fetchone()[0] == df.height, table
            cur.execute(
                f'SELECT sell_first_suspended, available_at FROM "{test_schema}".daytrade_daily '
                "ORDER BY date"
            )
            assert cur.fetchall()[0] == (False, dt.datetime(2018, 1, 2, 21, 0, tzinfo=TZ))
            cur.execute(
                f'SELECT level, match_minutes FROM "{test_schema}".disposition_stocks '
                "WHERE stock_id = '1475'"
            )
            assert cur.fetchall() == [(None, 25)]
    finally:
        with conn.cursor() as cur:
            cur.execute(f'DROP SCHEMA IF EXISTS "{test_schema}" CASCADE')
        conn.commit()
        conn.close()


# ── 抓取小工具（不連網：注入 transport）────────────────────────────────────


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


@dataclass
class _Resp:
    status_code: int
    text: str

    @property
    def content(self) -> bytes:
        return self.text.encode("utf-8")


def _client(resp: _Resp, calls: list[tuple[str, dict[str, str] | None]]) -> HttpClient:
    def transport(method: str, url: str, *, params: dict[str, str] | None, timeout: float) -> _Resp:
        calls.append((url, params))
        return resp

    return HttpClient(
        source="test",
        rate=SourceRate(max_requests=10, per_seconds=1.0),
        clock=_Clock(),
        transport=transport,
    )


def test_t309_fetch_alerts_request_params_per_market() -> None:
    calls: list[tuple[str, dict[str, str] | None]] = []
    body = (_FIX / "tpex_disposal.json").read_text(encoding="utf-8")
    payload = alerts.fetch_alerts(
        _client(_Resp(200, body), calls), "tpex", "disposition", dt.date(2018, 1, 1), _D1
    )
    assert alerts.tpex_disposition_rows(payload).placeholders == 1
    assert calls == [
        (
            "https://www.tpex.org.tw/www/zh-tw/bulletin/disposal",
            {"startDate": "2018/01/01", "endDate": "2018/01/02", "response": "json"},
        )
    ]
    calls.clear()
    body = (_FIX / "twse_notice.json").read_text(encoding="utf-8")
    alerts.fetch_alerts(
        _client(_Resp(200, body), calls), "twse", "attention", dt.date(2018, 1, 1), _D1
    )
    assert calls == [
        (
            "https://www.twse.com.tw/rwd/zh/announcement/notice",
            {"startDate": "20180101", "endDate": "20180102", "response": "json"},
        )
    ]


@pytest.mark.parametrize(
    "resp",
    [_Resp(200, "<html>Please enable JavaScript</html>"), _Resp(428, "<html>challenge</html>")],
)
def test_t309_fetch_alerts_waf_page_raises(resp: _Resp) -> None:
    """WAF 挑戰頁（HTML／428）→ raise 並標 anti_bot_suspected；回補腳本見到即停，不在迴圈裡重試。"""
    with pytest.raises(DataError, match="anti_bot_suspected"):
        alerts.fetch_alerts(_client(resp, []), "twse", "disposition", dt.date(2018, 1, 1), _D1)
