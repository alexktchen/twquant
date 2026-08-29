"""Purged walk-forward 切分器（SPEC-010 §3）。純函數、無 IO，受 IO 靜態閘門掃描。"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise


@dataclass(frozen=True)
class WalkSplit:
    """一組切分的日期邊界（皆為含端點的交易日）：擴張訓練窗＋embargo＋連續測試段。"""

    train_start: dt.date
    train_end: dt.date
    test_start: dt.date
    test_end: dt.date


def walk_forward_splits(
    trading_days: Sequence[dt.date],
    *,
    train_min_days: int,
    test_days: int,
    embargo_days: int,
    vault_days: int = 0,
) -> list[WalkSplit]:
    """擴張視窗 purged walk-forward 切分。

    首窗訓練 train_min_days 個交易日，隔 embargo_days 個交易日（清除標籤前向視窗
    與測試期的重疊，embargo 應 ≥ label horizon，由配置層把關）取 test_days 的測試段，
    之後訓練窗逐段擴張前進；測試段聯集恰覆蓋首窗＋embargo 之後的全部可用日（尾段可短）。
    最後 vault_days 個交易日為金庫期（SPEC-010 §7），不出現在任何切分。
    """
    if train_min_days < 1:
        raise ValueError(f"train_min_days 須 ≥ 1，得到 {train_min_days}")
    if test_days < 1:
        raise ValueError(f"test_days 須 ≥ 1，得到 {test_days}")
    if embargo_days < 0:
        raise ValueError(f"embargo_days 須 ≥ 0，得到 {embargo_days}")
    if vault_days < 0:
        raise ValueError(f"vault_days 須 ≥ 0，得到 {vault_days}")
    days = list(trading_days)
    if any(b <= a for a, b in pairwise(days)):
        raise ValueError("trading_days 須嚴格遞增（含不得重複）")
    usable = days[: len(days) - vault_days] if vault_days else days
    first_test = train_min_days + embargo_days
    if first_test >= len(usable):
        raise ValueError(
            f"交易日不足：金庫期扣除後可用 {len(usable)} 日，"
            f"無法構成首窗 {train_min_days} + embargo {embargo_days} + 測試 ≥ 1 日"
        )
    out: list[WalkSplit] = []
    for test_lo in range(first_test, len(usable), test_days):
        test_hi = min(test_lo + test_days - 1, len(usable) - 1)
        out.append(
            WalkSplit(
                train_start=usable[0],
                train_end=usable[test_lo - embargo_days - 1],
                test_start=usable[test_lo],
                test_end=usable[test_hi],
            )
        )
    return out
