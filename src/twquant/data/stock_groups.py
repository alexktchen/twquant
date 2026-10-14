"""推薦股的族群標籤（D-177）：從產業價值鏈的掛名裡，替每檔股票挑一條當「族群」。

一檔股票常掛好幾條鏈（大毅：被動元件、通信網路、電腦及週邊設備、平面顯示器），挑法寫死、
不看行情：

1. 先用官方產業別圈出候選鏈（`configs/stock_groups.yaml` 的 refine）——族群只能是官方產業別的
   細分，不會把電子零組件股標成「休閒娛樂」。
2. 候選裡取這檔掛最多段的那條（台達電在電腦及週邊設備掛 6 段、被動元件 3 段 → 電腦及週邊設備）。
3. 段數一樣多時，照 refine 列的順序取第一條（大毅每條都只掛 1 段 → 列在最前面的被動元件）。

沒有任何候選（官方產業別沒列、或這檔沒掛候選鏈）就不給族群，網站改顯示官方產業別。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import Field

from twquant.config import BaseConfig
from twquant.data.sources.tpex_chain import ChainMember
from twquant.errors import DataError


class StockGroupsConfig(BaseConfig):
    """configs/stock_groups.yaml（D-177）。"""

    base_url: str = Field(pattern=r"^https://")
    first_chain: str  # 先抓這一頁，從它的選單讀出全部產業鏈
    timeout_seconds: float = Field(gt=0)
    refresh_days: int = Field(ge=1)  # 族群表比這舊才重抓；鏈上的公司很少變
    refine: dict[str, tuple[str, ...]]  # 官方產業別 → 可細分成的產業鏈，越前面越優先


@dataclass(frozen=True)
class StockGroup:
    """一檔股票的族群：挑中的鏈、在這條鏈上掛的段、還掛了哪些別的鏈。"""

    stock_id: str
    group: str
    segments: tuple[str, ...]
    also: tuple[str, ...]


def primary_groups(
    members: Iterable[ChainMember],
    industries: Mapping[str, str | None],
    refine: Mapping[str, Sequence[str]],
    chains: Sequence[str],
) -> list[StockGroup]:
    """每檔股票挑一條鏈當族群（規則見模組說明），依代號排序。

    refine 裡寫到網站上不存在的鏈即 raise——平台改了鏈名時，不能讓那些股票悄悄掉回官方產業別。
    """
    unknown = sorted({c for cs in refine.values() for c in cs} - set(chains))
    if unknown:
        raise DataError(
            "stock_group",
            None,
            f"stock_groups.yaml 的 refine 寫到平台上沒有的產業鏈：{unknown}——平台可能改了鏈名",
        )
    order = {c: i for i, c in enumerate(chains)}
    segs: dict[str, dict[str, list[str]]] = {}
    for m in members:
        found = segs.setdefault(m.stock_id, {}).setdefault(m.chain, [])
        if m.segment not in found:  # 同名的段可能出現在上下游兩處（半導體的「生產製程及檢測設備」）
            found.append(m.segment)
    out: list[StockGroup] = []
    for sid, industry in industries.items():
        held = segs.get(sid)
        if industry is None or held is None:
            continue
        rank = {c: i for i, c in enumerate(refine.get(industry, ()))}
        candidates = [c for c in held if c in rank]
        if not candidates:
            continue
        best = min(candidates, key=lambda c: (-len(held[c]), rank[c]))
        also = sorted((c for c in held if c != best), key=lambda c: order[c])
        out.append(StockGroup(sid, best, tuple(held[best]), tuple(also)))
    out.sort(key=lambda g: g.stock_id)
    return out
