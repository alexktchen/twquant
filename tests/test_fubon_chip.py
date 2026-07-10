"""T-010 / D-019：富邦DJ 券商分點來源與 canonical 轉換（離線，注入式 transport）。

fixture `broker_trades_2330.html` 由 zco.djhtm?a=2330&e=f=2026-07-08 實抓精簡而來，含
外資母公司（美林/高盛）與本土分點（新光-台中）混合、top-15 買超 + top-15 賣超共 30 列。
cell 護欄與單位/前視以合成 HTML + 小 rows 列表驗證（避開連網，CLAUDE.md §4）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from twquant.config import SourceRate
from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.http import HttpClient
from twquant.data.sources import fubon_chip
from twquant.data.warehouse import Warehouse

_RATE = SourceRate(max_requests=3, per_seconds=5.0)
_FIXTURE = Path(__file__).parent / "fixtures" / "http" / "fubon" / "broker_trades_2330.html"
_DATE = dt.date(2026, 7, 8)


@dataclass
class FakeResponse:
    status_code: int
    text: str
    content: bytes


def _t01(data_rows: str) -> bytes:
    """包成富邦DJ 的 t01 表：前 8 列表頭（會被跳過）+ 給定資料列。"""
    header = "<tr><td>h</td></tr>" * 8
    return f"<table class='t01'>{header}{data_rows}</table>".encode()


def _row(cells: list[str]) -> str:
    return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"


# ── 解析 ──────────────────────────────────────────────────────────


def test_parse_extracts_both_sides_from_real_fixture() -> None:
    rows = fubon_chip.parse_broker_rows(_FIXTURE.read_bytes())
    assert len(rows) == 30  # top-15 買超 + top-15 賣超，無 footer/合計 洩漏
    names = [r["broker_id"] for r in rows]
    assert "美林" in names  # 外資母公司
    assert "新光-台中" in names  # 本土分點（分公司）
    assert all(n for n in names)  # 無空名稱
    assert rows[0] == {"broker_id": "港商麥格理", "buy_lots": 1837, "sell_lots": 153}


def test_parse_cell_guards() -> None:
    # 買半：逗號千分位 + \xa0（→0）；賣半：--（→0）+ 前導零 012（base-10 → 12，非八進位）
    html = _t01(_row(["券商A", "1,234", "\xa0", "1234", "", "券商B", "--", "012", "-12"]))
    rows = fubon_chip.parse_broker_rows(html)
    assert rows == [
        {"broker_id": "券商A", "buy_lots": 1234, "sell_lots": 0},
        {"broker_id": "券商B", "buy_lots": 0, "sell_lots": 12},
    ]


def test_parse_skips_empty_name_half() -> None:
    # 短尾列：買半有券商、賣半名稱為 \xa0 → 只出買半
    html = _t01(_row(["尾券商", "5", "3", "2", "", "\xa0", "\xa0", "\xa0", "\xa0"]))
    rows = fubon_chip.parse_broker_rows(html)
    assert rows == [{"broker_id": "尾券商", "buy_lots": 5, "sell_lots": 3}]


def test_parse_breaks_on_footer_marker() -> None:
    # 遇「合計」列即停，其後的列不得再解析
    html = _t01(
        _row(["實券商", "9", "1", "8", "", "", "", "", ""])
        + _row(["合計買超張數", "", "合計賣超張數", "", "", "", "", "", ""])
        + _row(["幽靈券商", "99", "1", "98", "", "", "", "", ""])
    )
    rows = fubon_chip.parse_broker_rows(html)
    assert [r["broker_id"] for r in rows] == ["實券商"]


def test_parse_short_row_not_index_crash() -> None:
    html = _t01("<tr><td>只有三格</td><td>1</td><td>2</td></tr>")
    assert fubon_chip.parse_broker_rows(html) == []


def test_no_table_returns_empty() -> None:
    assert fubon_chip.parse_broker_rows(b"<html><body>no table</body></html>") == []


# ── canonical 轉換 ────────────────────────────────────────────────


def test_rows_to_canonical_units_dtypes_and_available_at() -> None:
    rows = [{"broker_id": "美林", "buy_lots": 100, "sell_lots": 50}]
    df = fubon_chip.rows_to_canonical(rows, "2330", _DATE)
    assert list(df.columns) == list(fubon_chip._CANONICAL)  # 欄序 = schema
    assert df.height == 1
    r = df.to_dicts()[0]
    assert r["stock_id"] == "2330"
    assert r["broker_id"] == "美林"
    assert r["buy_shares"] == 100_000  # 張 ×1000 → 股
    assert r["sell_shares"] == 50_000
    assert r["avg_price"] is None  # 富邦DJ 無均價
    assert r["available_at"] == dt.datetime(2026, 7, 8, 18, 0, tzinfo=TZ)  # 盤後 18:00
    assert df.schema["buy_shares"] == pl.Int64
    assert df.schema["avg_price"] == pl.Float64
    assert df.schema["available_at"] == schemas.TW_DT


def test_rows_to_canonical_dedupes_by_broker() -> None:
    rows = [
        {"broker_id": "美林", "buy_lots": 100, "sell_lots": 50},
        {"broker_id": "美林", "buy_lots": 10, "sell_lots": 5},
    ]
    df = fubon_chip.rows_to_canonical(rows, "2330", _DATE)
    assert df.height == 1  # 同名券商彙總成一列
    r = df.to_dicts()[0]
    assert r["buy_shares"] == 110_000
    assert r["sell_shares"] == 55_000


def test_rows_to_canonical_empty() -> None:
    df = fubon_chip.rows_to_canonical([], "2330", _DATE)
    assert df.height == 0
    assert list(df.columns) == list(fubon_chip._CANONICAL)
    assert df.schema["available_at"] == schemas.TW_DT


def test_fixture_end_to_end_validates() -> None:
    rows = fubon_chip.parse_broker_rows(_FIXTURE.read_bytes())
    df = fubon_chip.rows_to_canonical(rows, "2330", _DATE)
    assert df.height == 30
    assert (df["buy_shares"] % 1000 == 0).all()  # 全為股（1000 倍數）
    assert (df["sell_shares"] % 1000 == 0).all()


# ── 抓取（注入 transport）與 PIT 可見性 ───────────────────────────


def test_fetch_builds_get_params() -> None:
    seen: dict[str, object] = {}

    def transport(method: str, url: str, *, params: object, timeout: float) -> FakeResponse:
        seen["method"], seen["url"], seen["params"] = method, url, params
        return FakeResponse(200, "", _FIXTURE.read_bytes())

    client = HttpClient(source="fubon", rate=_RATE, transport=transport)
    content = fubon_chip.fetch_broker_trades(client, "2330", _DATE)
    assert seen["method"] == "GET"
    assert seen["url"] == fubon_chip._URL
    assert seen["params"] == {"a": "2330", "e": "2026-07-08", "f": "2026-07-08"}
    assert content == _FIXTURE.read_bytes()


def test_fubon_universe_filters_and_falls_back(tmp_path: Path) -> None:
    from twquant.cli import _fubon_universe

    wh = Warehouse(tmp_path)
    # 直接落 parquet（僅 stock_id 欄，_fubon_universe 只 scan 不驗證）
    meta_dir = tmp_path / "clean" / "stock_meta" / "date=_all"
    meta_dir.mkdir(parents=True)
    pl.DataFrame({"stock_id": ["2330", "0050", "00878", "1101", "123", "2330"]}).write_parquet(
        meta_dir / "part-twse.parquet"
    )
    # 四碼、排除 ETF(0050/00878)、短碼(123)，去重排序
    assert _fubon_universe(wh, r"^[1-9][0-9]{3}$") == ["1101", "2330"]

    # 無任何表 → 回 []（呼叫端據此報錯）
    assert _fubon_universe(Warehouse(tmp_path / "empty"), r"^[1-9][0-9]{3}$") == []


def test_write_and_pit_visibility(tmp_path: Path) -> None:
    # available_at=交易日18:00；D-008 as_of coerce 20:00 → 當日看得到、前一日看不到
    df = fubon_chip.rows_to_canonical(
        [{"broker_id": "美林", "buy_lots": 100, "sell_lots": 50}], "2330", _DATE
    )
    wh = Warehouse(tmp_path)
    wh.write("clean", "broker_trades", df, source="fubon")
    same = wh.view(_DATE).table("broker_trades").collect()
    prev = wh.view(_DATE - dt.timedelta(days=1)).table("broker_trades").collect()
    assert same.height == 1  # 當日 20:00 cutoff → 18:00 資料可見
    assert prev.height == 0  # 前一日 → 尚未可見
