"""推薦股的族群（D-177）：從產業價值鏈的掛名裡，替每檔股票挑一個族群，網站顯示、也拿來算族群資金。

一檔股票常掛好幾條鏈（大毅：被動元件、通信網路、電腦及週邊設備、平面顯示器），挑法寫死、不看行情：

1. 先用官方產業別圈出候選鏈（`configs/stock_groups.yaml` 的 refine）——族群只能是官方產業別的
   細分，不會把電子零組件股標成「休閒娛樂」。
2. 候選裡取這檔掛最多段的那條（台達電在電腦及週邊設備掛 6 段、被動元件 3 段 → 電腦及週邊設備）。
3. 段數一樣多時，照 refine 列的順序取第一條（大毅每條都只掛 1 段 → 列在最前面的被動元件）。
4. 大鏈拆到段（D-177 修訂，refine 之後才看）：挑中的鏈列在 `split`，而且這檔在這條鏈上只落在
   **一個**列出的段、那段至少 `split_min_members` 家 → 族群是那一段（奇鋐 → 散熱、緯穎 → 伺服器）；
   橫跨好幾段的（鴻海、台達電）維持整條鏈。沒列在 split 的段（「其他…」、太小的段）不算。

沒有任何候選（官方產業別沒列、或這檔沒掛候選鏈）→ 族群就是官方產業別（`chain` 為 None）。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import Field

from twquant.config import BaseConfig
from twquant.data.sources.tpex_chain import ChainMember
from twquant.errors import DataError

_TABLE = "stock_group"


class GroupFlowConfig(BaseConfig):
    """族群資金（D-177 修訂）：每天每個族群的成交比重對自己過去平均，與漲跌家數。"""

    avg_sessions: int = Field(ge=1)  # 資金倍數的分母：前幾個交易日的平均成交比重（不含當天）
    hot_ratio: float = Field(gt=1.0)  # 資金倍數至少這麼多……
    hot_breadth: float = Field(gt=0.0, le=1.0)  # ……而且上漲家數比例至少這麼多，才標「資金流入」
    hot_min_members: int = Field(
        ge=1
    )  # 當天有成交的成員少於這個數不標（兩三檔的族群一檔漲停就翻倍）
    max_members: int = Field(ge=1)  # 族群排行每個族群列出成交金額最大的前幾檔


class StockGroupsConfig(BaseConfig):
    """configs/stock_groups.yaml（D-177）。"""

    base_url: str = Field(pattern=r"^https://")
    first_chain: str  # 先抓這一頁，從它的選單讀出全部產業鏈
    timeout_seconds: float = Field(gt=0)
    refresh_days: int = Field(ge=1)  # 族群表比這舊才重抓；鏈上的公司很少變
    refine: dict[str, tuple[str, ...]]  # 官方產業別 → 可細分成的產業鏈，越前面越優先
    split_min_members: int = Field(ge=1)  # 段至少幾家（平台上的上市櫃＋興櫃）才自成一個族群
    split: dict[
        str, dict[str, str]
    ]  # 拆到段的大鏈 → {段名: 顯示名}；不同段可給同一個顯示名（合成一個族群）
    flow: GroupFlowConfig


@dataclass(frozen=True)
class StockGroup:
    """一檔股票的族群。

    group：網站顯示、族群資金統計用的名字（散熱、被動元件、玻璃陶瓷）；chain：挑中的產業鏈，
    用官方產業別時為 None；segments：在這條鏈上掛的段；also：還掛了哪些別的鏈。
    """

    stock_id: str
    group: str
    chain: str | None
    segments: tuple[str, ...]
    also: tuple[str, ...]


def _check_config(
    refine: Mapping[str, Sequence[str]],
    split: Mapping[str, Mapping[str, str]],
    chains: Sequence[str],
    sizes: Mapping[tuple[str, str], int],
) -> None:
    """設定檔寫到平台上沒有的鏈或段即 raise——平台改名時，不能讓那些股票悄悄掉回大鏈或官方產業別。"""
    unknown = sorted(({c for cs in refine.values() for c in cs} | set(split)) - set(chains))
    if unknown:
        raise DataError(
            _TABLE, None, f"stock_groups.yaml 寫到平台上沒有的產業鏈：{unknown}——平台可能改了鏈名"
        )
    missing = sorted(f"{c}／{s}" for c, segs in split.items() for s in segs if (c, s) not in sizes)
    if missing:
        raise DataError(
            _TABLE,
            None,
            f"stock_groups.yaml 的 split 寫到平台上沒有的段：{missing}——平台可能改了段名",
        )


def primary_groups(
    members: Iterable[ChainMember],
    industries: Mapping[str, str | None],
    refine: Mapping[str, Sequence[str]],
    chains: Sequence[str],
    split: Mapping[str, Mapping[str, str]],
    split_min_members: int,
) -> list[StockGroup]:
    """每檔有官方產業別的股票挑一個族群（規則見模組說明），依代號排序。"""
    members = list(members)
    sizes = Counter(
        (m.chain, m.segment)
        for m in {(m.stock_id, m.chain, m.segment): m for m in members}.values()
    )
    _check_config(refine, split, chains, sizes)
    order = {c: i for i, c in enumerate(chains)}
    segs: dict[str, dict[str, list[str]]] = {}
    for m in members:
        found = segs.setdefault(m.stock_id, {}).setdefault(m.chain, [])
        if m.segment not in found:  # 同名的段可能出現在上下游兩處（半導體的「生產製程及檢測設備」）
            found.append(m.segment)
    out: list[StockGroup] = []
    for sid, industry in industries.items():
        if industry is None:
            continue
        held = segs.get(sid, {})
        rank = {c: i for i, c in enumerate(refine.get(industry, ()))}
        candidates = [c for c in held if c in rank]
        if not candidates:
            out.append(StockGroup(sid, industry, None, (), ()))
            continue
        best = min(candidates, key=lambda c: (-len(held[c]), rank[c]))
        labels = split.get(best, {})
        own = {
            labels[s] for s in held[best] if s in labels and sizes[(best, s)] >= split_min_members
        }
        group = next(iter(own)) if len(own) == 1 else best
        also = sorted((c for c in held if c != best), key=lambda c: order[c])
        out.append(StockGroup(sid, group, best, tuple(held[best]), tuple(also)))
    out.sort(key=lambda g: g.stock_id)
    return out
