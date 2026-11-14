"""A-018：程式交易的遙控網站（autotrade/web，D-230）——網站那一份跟交易程式這一份對得起來。

網站的程式用 node 測（autotrade/web/tests）；這裡只守兩邊一定要一樣的東西：
資料表的定義一字不差、作廢的秒數相同、網站沒有多出不該有的寫入。
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

from autotrade.model import ADMIN_ACTIONS, ENGINE_ACTIONS
from autotrade.relay import COMMANDS_DDL, REQUESTS_DDL, PgRelayDb

WEB = Path(__file__).parents[1] / "web"
LIB = WEB / "functions" / "_lib"


def source(name: str) -> str:
    return (LIB / name).read_text(encoding="utf-8")


def ts_list(text: str, name: str) -> list[str]:
    """TS 檔裡 `export const NAME = [ ... ] as const` 的每一個字串。"""
    body = text[text.index(f"export const {name} = [") :]
    body = body[: body.index("] as const")]
    body = body[body.index("[") :]
    # 從左到右一個一個字串抓：雙引號的字串裡可以有單引號（SQL 的字面值），反過來也是
    found = re.findall(r""""((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'""", body)
    return [double or single for double, single in found]


def test_ac1_the_sites_tables_are_word_for_word_the_programs() -> None:
    assert ts_list(source("autotrade.ts"), "COMMANDS_DDL") == list(COMMANDS_DDL)
    assert ts_list(source("autotrade_remote.ts"), "REQUESTS_DDL") == list(REQUESTS_DDL)


def test_ac1_actions_and_expiry_match_the_program() -> None:
    remote = source("autotrade_remote.ts")
    assert ts_list(remote, "REMOTE_ACTIONS") == [*ENGINE_ACTIONS, *ADMIN_ACTIONS]
    ttl = re.search(r"export const REQUEST_TTL_S = (\d+)", remote)
    default = inspect.signature(PgRelayDb.__init__).parameters["request_ttl_s"].default
    assert (
        ttl is not None and float(ttl.group(1)) == default
    )  # 網站說幾秒作廢，中繼程式就幾秒不再交出去


def test_ac1_the_site_only_ever_adds_a_stop_or_a_request() -> None:
    writes: list[str] = []
    for path in sorted((WEB / "functions").rglob("*.ts")):
        text = path.read_text(encoding="utf-8")
        writes += re.findall(r"INSERT INTO [\w.]+", text)
        assert not re.search(r"DELETE FROM|TRUNCATE|DROP |UPDATE\s+web\.", text), path.name
    # D-231（2026-11-14）：使用者要「啟動／停止」也從網站下、走訊息匯流排，所以多了第三種列（只會是「指令」）
    assert sorted(writes) == [
        "INSERT INTO web.autotrade_commands",
        "INSERT INTO web.autotrade_messages",
        "INSERT INTO web.autotrade_requests",
    ]


def test_ac3_the_site_is_only_the_trading_page() -> None:
    endpoints = sorted(
        str(p.relative_to(WEB / "functions" / "api")).replace("\\", "/")
        for p in (WEB / "functions" / "api").rglob("*.ts")
    )
    assert endpoints == ["autotrade/command.ts", "autotrade/index.ts", "autotrade/stop.ts", "me.ts"]
    for path in (WEB / "functions").rglob("*.ts"):
        text = path.read_text(encoding="utf-8")
        assert "frontend/" not in text, path.name  # 不靠 twquant 網站的程式
        assert "userEmail" not in text and "SUPABASE_URL" not in text, path.name  # 不用那邊的登入
    for path in (WEB / "src").rglob("*.ts*"):
        assert "frontend/" not in path.read_text(encoding="utf-8"), path.name
