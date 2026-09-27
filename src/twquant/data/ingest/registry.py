"""來源註冊表（D-017）——比照 factors/registry.py 慣用法。

新增資料來源 = 一個 SourcePort + 一筆 `register(SourceSpec(...))`，不改編排核心。
`cadence` 讓每日迴圈只取 daily 來源、週/月頻來源各由專屬指令驅動。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from twquant.errors import TwquantError

if TYPE_CHECKING:
    from twquant.config import SystemConfig
    from twquant.data.ingest.ports import SourcePort

Cadence = Literal["daily", "weekly", "monthly"]
Store = Literal["lake", "pg"]


@dataclass(frozen=True)
class SourceSpec:
    """一個來源的註冊項：儲存標籤、建構器（注入 config → SourcePort）、擷取頻率。

    store（T-310）：`lake`＝寫資料湖，供應層步驟再鏡射同步進 PG；`pg`＝直寫 PG
    （`serving.schema.DIRECT_PG_SPECS`，資料湖沒有這些表）。
    proves_trading（T-310）：這個來源抓成功＝當天有交易。`cli._ingest_date` 靠它分辨
    「休市」與「真的沒抓到」（T-276）；公告類（處置／注意）休市日也查得到東西，故為 False。
    """

    source_tag: str
    build: Callable[[SystemConfig], SourcePort]
    cadence: Cadence = "daily"
    store: Store = "lake"
    proves_trading: bool = True


_REGISTRY: dict[str, SourceSpec] = {}


def register(spec: SourceSpec) -> None:
    """登記來源。重複 source_tag → raise（防兩處各定義一次的漂移）。"""
    if spec.source_tag in _REGISTRY:
        raise TwquantError(f"來源已註冊：{spec.source_tag}")
    _REGISTRY[spec.source_tag] = spec


def get(source_tag: str) -> SourceSpec:
    """取來源規格；未註冊 → raise。"""
    try:
        return _REGISTRY[source_tag]
    except KeyError as exc:
        raise TwquantError(f"來源未註冊：{source_tag}") from exc


def list_specs(cadence: Cadence | None = None) -> list[SourceSpec]:
    """列出已註冊來源（可依 cadence 過濾）。"""
    return [s for s in _REGISTRY.values() if cadence is None or s.cadence == cadence]


def _clear() -> None:
    """清空註冊表（僅供測試）。"""
    _REGISTRY.clear()
