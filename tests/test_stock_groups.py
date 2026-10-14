"""D-177／T-352：推薦股的族群標籤——櫃買中心產業價值鏈的解析、挑族群的規則、PG 族群表。

頁面 fixture 是 2026-10-14 實抓頁面裁出的最小結構（tests/fixtures/http/tpex_ic/），不連網。
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from twquant.config import SourceRate, load_config
from twquant.conventions import TZ
from twquant.data.http import HttpClient
from twquant.data.sources.finmind import _INDUSTRY_ALIASES, _INDUSTRY_EXACT
from twquant.data.sources.tpex_chain import (
    RETRY_STATUS,
    ChainMember,
    ChainSource,
    parse_chain_list,
    parse_chain_members,
)
from twquant.data.stock_groups import StockGroup, StockGroupsConfig, primary_groups
from twquant.errors import DataError

_FIX = Path(__file__).parent / "fixtures" / "http" / "tpex_ic"
_ROOT = Path(__file__).resolve().parent.parent
_CHAINS = ["半導體", "被動元件", "電腦及週邊設備"]


def _page(code: str) -> str:
    return (_FIX / f"introduce_{code}.html").read_text(encoding="utf-8")


# ── 解析 ────────────────────────────────────────────────────────────────────


def test_d177_ac1_chain_list_comes_from_the_page_menu() -> None:
    assert parse_chain_list(_page("J000")) == [
        ("D000", "半導體"),
        ("J000", "被動元件"),
        ("F000", "電腦及週邊設備"),
    ]
    with pytest.raises(DataError, match="ic_option"):
        parse_chain_list("<html><body>維護中</body></html>")


def test_d177_ac1_members_dedupe_doubled_blocks_and_skip_foreign_companies() -> None:
    got = parse_chain_members(_page("J000"), "被動元件")
    # 每段在頁面上出現兩次（桌面／手機版）只算一次；外國企業沒有代號不收
    assert got == [
        ChainMember("2327", "被動元件", "電阻器"),
        ChainMember("2478", "被動元件", "電阻器"),
        ChainMember("2308", "被動元件", "電阻器"),
        ChainMember("2327", "被動元件", "電容器"),
        ChainMember("3090", "被動元件", "電容器"),
    ]
    with pytest.raises(DataError, match="讀不到任何公司"):
        parse_chain_members("<html>空的</html>", "被動元件")


def test_d177_ac1_segment_stops_at_its_company_count() -> None:
    """段落的範圍到「共 N 家」為止：最後一段之後的頁尾連結不能算進去。"""
    page = _page("F000").replace(
        "網站地圖</a>", '網站地圖</a><a href="company_basic.php?stk_code=9999">x</a>'
    )
    assert "9999" not in {m.stock_id for m in parse_chain_members(page, "電腦及週邊設備")}


# ── 抓取 ────────────────────────────────────────────────────────────────────


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


@dataclass
class _Bytes:
    raw: bytes
    status_code: int = 200

    @property
    def text(self) -> str:
        return ""

    @property
    def content(self) -> bytes:
        return self.raw


def _client(replies: dict[str, list[_Resp]], calls: list[str]) -> HttpClient:
    def transport(method: str, url: str, *, params: dict[str, str] | None, timeout: float) -> _Resp:
        code = (params or {})["ic"]
        calls.append(code)
        return replies[code].pop(0)

    return HttpClient(
        source="test",
        rate=SourceRate(max_requests=100, per_seconds=1.0),
        clock=_Clock(),
        transport=transport,
        retry_status=RETRY_STATUS,
    )


def test_d177_ac2_fetch_reads_every_chain_once_and_retries_cloudflare_520() -> None:
    calls: list[str] = []
    replies = {
        "D000": [_Resp(200, _page("D000"))],
        "J000": [_Resp(520, "error code: 520"), _Resp(200, _page("J000"))],  # 實際遇過
        "F000": [_Resp(200, _page("F000"))],
    }
    chains, members = ChainSource(
        _client(replies, calls), "https://ic.tpex.org.tw/", "D000"
    ).fetch()
    assert chains == _CHAINS
    assert calls == ["D000", "J000", "J000", "F000"]  # 第一頁不重抓
    assert {m.chain for m in members} == set(_CHAINS)


def test_d177_ac2_a_page_that_never_comes_back_fails_the_whole_fetch() -> None:
    """不交半份：少一條鏈，那條鏈的股票會悄悄掉回官方產業別。"""
    replies = {
        "D000": [_Resp(200, _page("D000"))],
        "J000": [_Resp(404, "not found")],
        "F000": [_Resp(200, _page("F000"))],
    }
    with pytest.raises(DataError, match="J000"):
        ChainSource(_client(replies, []), "https://ic.tpex.org.tw", "D000").fetch()
    garbled = HttpClient(
        source="test",
        rate=SourceRate(max_requests=100, per_seconds=1.0),
        clock=_Clock(),
        transport=lambda method, url, *, params, timeout: _Bytes(b"\xff\xfe"),
    )
    with pytest.raises(DataError, match="D000 頁面不是 UTF-8"):
        ChainSource(garbled, "https://ic.tpex.org.tw", "D000").fetch()


# ── 挑族群 ──────────────────────────────────────────────────────────────────

_REFINE = {
    "電子零組件業": ["被動元件", "電腦及週邊設備", "半導體"],
    "電腦及週邊設備業": ["電腦及週邊設備"],
    "半導體業": ["半導體"],
    "玻璃陶瓷": [],
}
_SPLIT = {
    "電腦及週邊設備": {"散熱片、風扇馬達、散熱模組": "散熱", "電源供應器": "電源供應器"},
    "半導體": {"IC/晶圓製造": "晶圓製造", "IC設計": "IC設計"},
}


def _members() -> list[ChainMember]:
    out: list[ChainMember] = []
    for code, chain in (("D000", "半導體"), ("J000", "被動元件"), ("F000", "電腦及週邊設備")):
        out.extend(parse_chain_members(_page(code), chain))
    return out


def _groups(
    industries: dict[str, str | None],
    members: list[ChainMember] | None = None,
    min_members: int = 1,
) -> dict[str, StockGroup]:
    got = primary_groups(
        _members() if members is None else members,
        industries,
        _REFINE,
        _CHAINS,
        _SPLIT,
        min_members,
    )
    return {g.stock_id: g for g in got}


def test_d177_ac3_dayi_and_nichidenbo_are_passive_components() -> None:
    """使用者的例子：大毅、日電貿是被動元件。大毅每條鏈都只掛一段 → 照 refine 順序取被動元件。"""
    g = _groups({"2478": "電子零組件業", "3090": "電子零組件業"})
    assert g["2478"] == StockGroup("2478", "被動元件", "被動元件", ("電阻器",), ("電腦及週邊設備",))
    assert g["3090"] == StockGroup("3090", "被動元件", "被動元件", ("電容器",), ())


def test_d177_ac3_the_chain_with_more_segments_wins() -> None:
    """台達電：電腦及週邊設備 2 段、半導體 2 段（上下游同名的段只算一次）、被動元件 1 段。

    先比段數（排第一的被動元件出局），一樣多才照 refine 順序（電腦及週邊設備排在半導體前面）。
    電腦及週邊設備雖然拆段，台達電橫跨電源供應器與散熱兩段 → 維持整條鏈。
    """
    g = _groups({"2308": "電子零組件業"})["2308"]
    assert (g.group, g.chain) == ("電腦及週邊設備", "電腦及週邊設備")
    assert g.segments == ("電源供應器", "散熱片、風扇馬達、散熱模組")
    assert g.also == ("半導體", "被動元件")  # 照平台上鏈的順序
    more = [*_members(), ChainMember("2308", "半導體", "IC封裝測試")]
    best = _groups({"2308": "電子零組件業"}, more)["2308"]
    assert (best.chain, best.segments) == ("半導體", ("IC設計", "生產製程及檢測設備", "IC封裝測試"))
    assert best.group == "IC設計"  # 沒列在 split 的段不算：列著的只有 IC設計 一段


def test_d177_ac6_big_chains_split_into_segments() -> None:
    """使用者：半導體、電腦及週邊設備太大，散熱和伺服器要分得開。只落在一個列出的段 → 族群是那一段的顯示名。"""
    g = _groups({"3017": "電腦及週邊設備業", "2330": "半導體業", "2454": "半導體業"})
    assert g["3017"] == StockGroup(
        "3017", "散熱", "電腦及週邊設備", ("散熱片、風扇馬達、散熱模組",), ()
    )
    assert (g["2330"].group, g["2454"].group) == ("晶圓製造", "IC設計")
    # 另外掛一段沒列在 split 的（「其他…」）不影響（廣達：筆電＋其他零組件 → 筆電）
    extra = [*_members(), ChainMember("3017", "電腦及週邊設備", "其他電腦及週邊設備之零組件")]
    assert _groups({"3017": "電腦及週邊設備業"}, extra)["3017"].group == "散熱"


def test_d177_ac6_small_segments_stay_with_the_chain() -> None:
    """段太小（平台上不到 split_min_members 家）不自成族群：fixture 的晶圓製造只有台積電一家。"""
    g = _groups({"3017": "電腦及週邊設備業", "2330": "半導體業"}, min_members=2)
    assert g["3017"].group == "散熱"  # 台達電＋奇鋐 2 家
    assert (g["2330"].group, g["2330"].chain) == ("半導體", "半導體")


def test_d177_ac3_no_candidate_chain_means_the_official_industry() -> None:
    """半導體業的股票就算掛在被動元件，也只能是半導體；沒有候選、產業別沒列、平台上沒有 → 族群是官方產業別。"""
    g = _groups(
        {
            "2330": "半導體業",
            "2327": "半導體業",  # 只掛被動元件 → 沒有候選
            "2454": None,  # 沒有產業別 → 不給
            "3017": "玻璃陶瓷",
            "3090": "未知產業",
            "9999": "電子零組件業",  # 平台上沒有
        }
    )
    assert set(g) == {"2330", "2327", "3017", "3090", "9999"}
    assert g["2330"] == StockGroup("2330", "晶圓製造", "半導體", ("IC/晶圓製造",), ())
    assert g["2327"] == StockGroup("2327", "半導體業", None, (), ())
    assert g["9999"] == StockGroup("9999", "電子零組件業", None, (), ())
    assert (g["3017"].group, g["3090"].group) == ("玻璃陶瓷", "未知產業")


def test_d177_ac3_a_renamed_chain_or_segment_raises_instead_of_silently_falling_back() -> None:
    def run(refine: dict[str, list[str]], split: dict[str, dict[str, str]]) -> None:
        primary_groups(_members(), {"2478": "電子零組件業"}, refine, _CHAINS, split, 1)

    with pytest.raises(DataError, match="被動元件產業"):
        run({"電子零組件業": ["被動元件產業"]}, {})
    with pytest.raises(DataError, match="電腦週邊"):
        run(_REFINE, {"電腦週邊": {"散熱": "散熱"}})
    with pytest.raises(DataError, match="電腦及週邊設備／散熱模組"):
        run(_REFINE, {"電腦及週邊設備": {"散熱模組": "散熱"}})


def test_d177_ac3_output_is_sorted_and_deterministic() -> None:
    ind: dict[str, str | None] = {
        "3090": "電子零組件業",
        "2478": "電子零組件業",
        "2330": "半導體業",
    }
    first = list(_groups(ind).values())
    assert [g.stock_id for g in first] == ["2330", "2478", "3090"]
    assert list(_groups(dict(reversed(ind.items()))).values()) == first


# ── 配置 ────────────────────────────────────────────────────────────────────


def test_d177_ac4_config_lists_every_official_industry() -> None:
    """refine 要把官方 34 個產業別都列出來（沒有對應的鏈就寫空），新加的產業別才不會被漏掉沒人想過。"""
    cfg = load_config(_ROOT / "configs" / "stock_groups.yaml", StockGroupsConfig)
    official = set(_INDUSTRY_EXACT) | set(_INDUSTRY_ALIASES.values())
    assert set(cfg.refine) == official
    for industry, chains in cfg.refine.items():
        assert len(set(chains)) == len(chains), f"{industry} 的鏈重複"
    assert cfg.refine["電子零組件業"][0] == "被動元件"
    # 拆段的鏈都是 refine 裡的鏈；被動元件是使用者要的族群，不拆
    assert set(cfg.split) <= {c for cs in cfg.refine.values() for c in cs}
    assert "被動元件" not in cfg.split
    assert cfg.split["電腦及週邊設備"]["散熱片、風扇馬達、散熱模組"] == "散熱"


def test_d177_ac4_step_runs_in_the_cloud_too() -> None:
    """族群標籤是網站要的，雲端 pipeline 不能略過（靜態）。"""
    from twquant.config import ServingConfig

    source = (_ROOT / "src" / "twquant" / "cli.py").read_text(encoding="utf-8")
    assert 'PipelineStep("族群標籤", _stock_groups_step)' in source
    skips = load_config(_ROOT / "configs" / "serving.yaml", ServingConfig).cloud.skip_pipeline_steps
    assert "族群標籤" not in skips


# ── PG 族群表（無 DSN 則 skip）────────────────────────────────────────────────

_SCHEMA = "serving_test_stock_group"
_AT = dt.datetime(2026, 10, 14, 18, 0, tzinfo=TZ)


def _connect_or_skip() -> Any:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("TWQUANT_PG_DSN")
    if not dsn:
        pytest.skip("TWQUANT_PG_DSN 未設，跳過 PG 整合測試")
    try:
        conn = psycopg.connect(dsn)
    except psycopg.Error as exc:  # pragma: no cover - 依環境而定
        pytest.skip(f"PG 不可達，跳過：{exc}")
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
        cur.execute(f'CREATE SCHEMA "{_SCHEMA}"')
        cur.execute(
            f'CREATE TABLE "{_SCHEMA}".stock_meta (stock_id text PRIMARY KEY, industry text)'
        )
        cur.execute(
            f'INSERT INTO "{_SCHEMA}".stock_meta VALUES (%s, %s), (%s, %s)',
            ("2478", "電子零組件業", "0050", None),
        )
    conn.commit()
    return conn


def _drop(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(f'DROP SCHEMA IF EXISTS "{_SCHEMA}" CASCADE')
    conn.commit()
    conn.close()


def test_d177_ac5_table_is_replaced_whole_and_its_age_drives_refresh() -> None:
    from twquant.data.serving.groups_store import (
        groups_updated_at,
        read_group_map,
        read_industries,
        replace_groups,
    )

    conn = _connect_or_skip()
    try:
        assert read_industries(conn, _SCHEMA) == {"2478": "電子零組件業", "0050": None}
        assert groups_updated_at(conn, _SCHEMA) is None  # 表還沒建 → 要抓
        old = [
            StockGroup("2330", "晶圓製造", "半導體", ("IC/晶圓製造",), ()),
            StockGroup("2478", "通信網路", "通信網路", ("x",), ()),
        ]
        replace_groups(conn, _SCHEMA, old, _AT - dt.timedelta(days=8))
        new = [
            StockGroup("1802", "玻璃陶瓷", None, (), ()),
            StockGroup("2478", "被動元件", "被動元件", ("電阻器",), ("通信網路", "電腦及週邊設備")),
        ]
        replace_groups(conn, _SCHEMA, new, _AT)
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT stock_id, grp, chain, segments, also FROM "{_SCHEMA}".stock_group ORDER BY 1'
            )
            assert cur.fetchall() == [
                ("1802", "玻璃陶瓷", None, [], []),
                ("2478", "被動元件", "被動元件", ["電阻器"], ["通信網路", "電腦及週邊設備"]),
            ]
        assert groups_updated_at(conn, _SCHEMA) == _AT
        assert read_group_map(conn, _SCHEMA) == {"1802": "玻璃陶瓷", "2478": "被動元件"}
        # 沒有一檔對上產業鏈＝來源壞了：raise，舊表留著
        with pytest.raises(DataError, match="沒有一檔對上產業鏈"):
            replace_groups(conn, _SCHEMA, [StockGroup("1802", "玻璃陶瓷", None, (), ())], _AT)
        with pytest.raises(DataError, match="沒有一檔對上產業鏈"):
            replace_groups(conn, _SCHEMA, [], _AT)
        with pytest.raises(ValueError, match="時區"):
            replace_groups(conn, _SCHEMA, new, dt.datetime(2026, 10, 14))  # 故意 naive
        with conn.cursor() as cur:
            cur.execute(f'SELECT count(*) FROM "{_SCHEMA}".stock_group')
            assert cur.fetchone() == (2,)
    finally:
        _drop(conn)
