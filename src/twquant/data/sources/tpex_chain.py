"""櫃買中心「產業價值鏈資訊平台」（ic.tpex.org.tw）轉接器（D-177）：每檔股票掛在哪些產業鏈、哪一段。

官方產業別太粗（大毅、日電貿、台達電都是「電子零組件業」），這個平台把上市櫃公司細分到
產業鏈（被動元件、印刷電路板…）與鏈上的段落（電阻器、電容器…）。一家公司可同時掛很多條鏈
（台達電 20 幾條），挑哪一條當族群由 `data/stock_groups.py` 決定，這裡只忠實抓下來。

頁面是 HTML：任何一條鏈的頁面都有 `<select id="ic_option">` 列出全部鏈（代碼＋名稱）；
每條鏈的頁面每一段是 `<div id="companyList_<段代碼>" title="<段名>">`，段內公司連到
`company_basic.php?stk_code=<代號>`，到 `company-number`（「共 N 家」）為止。外國企業連到
外部網址、沒有代號，自然不收。同一段在頁面上出現兩次（桌面／手機版），去重。
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from twquant.data.http import HttpClient
from twquant.errors import DataError

_TABLE = "stock_group"
_SELECT = re.compile(r'<select id="ic_option"[^>]*>(.*?)</select>', re.S)
_OPTION = re.compile(r"<option value='([A-Z0-9]+)'[^>]*>([^<]+)</option>")
_SEGMENT = re.compile(r'<div id="companyList_([A-Z0-9]+)" title="([^"]*)"')
_STOCK = re.compile(r'company_basic\.php\?stk_code=([0-9A-Z]+)"')
_SEGMENT_END = "company-number"
# 站台在 Cloudflare 後面：除了一般的 429／5xx，52x（源站暫時連不上）實測重抓即好（2026-10-14 抓 47 頁遇到 2 次）
RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 520, 521, 522, 523, 524})


@dataclass(frozen=True)
class ChainMember:
    """某檔股票掛在某條產業鏈的某一段。"""

    stock_id: str
    chain: str
    segment: str


def parse_chain_list(page: str) -> list[tuple[str, str]]:
    """從任一條鏈的頁面讀出全部產業鏈（代碼、名稱），照頁面順序。找不到選單即 raise（版面改了）。"""
    m = _SELECT.search(page)
    chains = (
        [(code, html.unescape(name).strip()) for code, name in _OPTION.findall(m.group(1))]
        if m
        else []
    )
    if not chains:
        raise DataError(_TABLE, None, "產業價值鏈頁面找不到產業鏈選單（ic_option）——版面可能改了")
    return chains


def parse_chain_members(page: str, chain: str) -> list[ChainMember]:
    """一條鏈的頁面 → 每檔股票掛在哪幾段（去重、照頁面順序）。一段都讀不到即 raise。"""
    parts = _SEGMENT.split(page)
    seen: set[tuple[str, str]] = set()
    out: list[ChainMember] = []
    for i in range(1, len(parts), 3):
        segment = html.unescape(parts[i + 1]).strip()
        body = parts[i + 2].split(_SEGMENT_END, 1)[0]
        for sid in _STOCK.findall(body):
            if (sid, segment) not in seen:
                seen.add((sid, segment))
                out.append(ChainMember(sid, chain, segment))
    if not out:
        raise DataError(
            _TABLE, None, f"產業鏈「{chain}」的頁面讀不到任何公司——版面可能改了，或頁面沒抓完整"
        )
    return out


class ChainSource:
    """產業價值鏈平台：抓全部產業鏈的成員。整份一次抓（47 頁），非每日 Source 協定。"""

    name = "tpex_ic"

    def __init__(self, client: HttpClient, base_url: str, first_chain: str) -> None:
        self.client = client
        self.base_url = base_url.rstrip("/")
        self.first_chain = first_chain

    def _page(self, code: str) -> str:
        resp = self.client.get(f"{self.base_url}/introduce.php", params={"ic": code})
        if resp.status_code != 200:
            raise DataError(_TABLE, None, f"產業鏈 {code} 頁面 HTTP {resp.status_code}")
        try:
            return resp.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DataError(_TABLE, None, f"產業鏈 {code} 頁面不是 UTF-8：{exc.reason}") from exc

    def fetch(self) -> tuple[list[str], list[ChainMember]]:
        """回傳（全部產業鏈名稱、每檔掛在哪條鏈哪一段）。任何一頁抓不到或讀不到即 raise，不交半份。"""
        first = self._page(self.first_chain)
        chains = parse_chain_list(first)
        members: list[ChainMember] = []
        for code, chain in chains:
            page = first if code == self.first_chain else self._page(code)
            members.extend(parse_chain_members(page, chain))
        return [name for _, name in chains], members
