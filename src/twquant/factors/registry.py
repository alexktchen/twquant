"""因子註冊表（SPEC-002 §1）。

@factor 裝飾器把因子純函數登記進全域註冊表；名稱須符合格式（category.name[_Nd]）、
不得重複。因子函數簽名固定為 (PitView, params) -> pl.DataFrame(date, stock_id, value)。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import polars as pl

from twquant.errors import TwquantError

if TYPE_CHECKING:
    from twquant.data.warehouse import PitView

FactorFn = Callable[["PitView", dict[str, object]], pl.DataFrame]

# 名稱格式（SPEC-000 命名 / REVIEW M14）：category.snake_name，可選 _Nd 視窗後綴
# ml：ML 排序層 meta-factor（SPEC-010 §6）——讀 ml_scores 表的薄因子，非新算因子
_CATEGORIES = ("price", "volume", "chip", "fundamental", "market", "ml")
_NAME_RE = re.compile(r"^(price|volume|chip|fundamental|market|ml)\.[a-z0-9_]+$")


@dataclass(frozen=True)
class FactorDef:
    """一個因子的定義與 metadata。"""

    name: str
    category: str
    direction: int  # 1: 值大看多; -1: 值大看空
    tables: tuple[str, ...]
    lookback_days: int
    params: dict[str, object] = field(default_factory=dict)
    anchor_invariant: bool = False
    fn: FactorFn | None = None


_REGISTRY: dict[str, FactorDef] = {}


def register(fdef: FactorDef) -> None:
    """登記因子。名稱格式不符或重複註冊 → raise。"""
    if fdef.category not in _CATEGORIES:
        raise TwquantError(f"因子類別非法：{fdef.category}（須為 {_CATEGORIES}）")
    if not _NAME_RE.match(fdef.name):
        raise TwquantError(f"因子名稱格式不符：{fdef.name}（須為 category.snake_name）")
    if not fdef.name.startswith(f"{fdef.category}."):
        raise TwquantError(f"因子名稱前綴與類別不符：{fdef.name} vs {fdef.category}")
    if fdef.name in _REGISTRY:
        raise TwquantError(f"因子已註冊：{fdef.name}")
    _REGISTRY[fdef.name] = fdef


def factor(
    *,
    name: str,
    category: str,
    tables: list[str],
    lookback_days: int,
    direction: int = 1,
    params: dict[str, object] | None = None,
    anchor_invariant: bool = False,
) -> Callable[[FactorFn], FactorFn]:
    """把純函數登記為因子的裝飾器。"""

    def deco(fn: FactorFn) -> FactorFn:
        register(
            FactorDef(
                name=name,
                category=category,
                direction=direction,
                tables=tuple(tables),
                lookback_days=lookback_days,
                params=params or {},
                anchor_invariant=anchor_invariant,
                fn=fn,
            )
        )
        return fn

    return deco


def get(name: str) -> FactorDef:
    """取因子定義；未註冊 → raise。"""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise TwquantError(f"因子未註冊：{name}") from exc


def list_factors(category: str | None = None) -> list[str]:
    """列出已註冊因子名（可依類別過濾）。"""
    names = sorted(_REGISTRY)
    if category is None:
        return names
    return [n for n in names if _REGISTRY[n].category == category]


def _clear() -> None:
    """清空註冊表（僅供測試）。"""
    _REGISTRY.clear()
