"""T-066：purged walk-forward 切分器（SPEC-010 §3，AC-1）。

契約（由本測試凍結）：
- `walk_forward_splits(trading_days, *, train_min_days, test_days, embargo_days, vault_days)`
  回傳 `list[WalkSplit(train_start, train_end, test_start, test_end)]`。
- 擴張訓練窗：train_start 恆為首日；embargo＝train_end 與 test_start 之間的交易日數；
  測試段連續、不重疊、聯集覆蓋首段訓練＋embargo 之後的全部可用日（尾段可短）。
- 金庫期（vault_days）：最後 N 個交易日不得出現在任何切分。
- 輸入未嚴格遞增、或長度不足以構成第一組切分 → raise（失敗要大聲）。
"""

from __future__ import annotations

import datetime as dt

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from twquant.ml.splits import WalkSplit, walk_forward_splits


def _days(n: int, start: dt.date = dt.date(2022, 1, 3)) -> list[dt.date]:
    """n 個連續平日（合成交易日曆）。"""
    out: list[dt.date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def _check_invariants(
    days: list[dt.date],
    splits: list[WalkSplit],
    *,
    train_min_days: int,
    test_days: int,
    embargo_days: int,
    vault_days: int,
) -> None:
    """AC-1 的全部不變量，供例示測試與 hypothesis 共用。"""
    assert splits, "至少要有一組切分"
    usable = days[: len(days) - vault_days] if vault_days else days
    idx = {d: i for i, d in enumerate(days)}
    covered: list[dt.date] = []
    prev_train_end_i = -1
    for s in splits:
        # 擴張窗：train_start 恆為首日、train_end 嚴格遞增
        assert s.train_start == usable[0]
        assert idx[s.train_end] > prev_train_end_i
        prev_train_end_i = idx[s.train_end]
        # embargo：train_end 與 test_start 之間恰好隔 embargo_days 個交易日
        assert idx[s.test_start] - idx[s.train_end] - 1 == embargo_days
        # 零重疊：測試段全在訓練段之後
        assert s.test_start > s.train_end
        assert s.test_end >= s.test_start
        # 金庫期不可觸碰
        assert idx[s.test_end] < len(usable)
        covered.extend(days[idx[s.test_start] : idx[s.test_end] + 1])
    # 首組訓練窗長度 = train_min_days
    assert idx[splits[0].train_end] == train_min_days - 1
    # 測試段連續、不重疊、覆蓋首段訓練＋embargo 之後的全部可用日
    first_test_i = train_min_days + embargo_days
    assert covered == usable[first_test_i:]
    # 尾段以外的測試段長度 = test_days
    for s in splits[:-1]:
        assert idx[s.test_end] - idx[s.test_start] + 1 == test_days


def test_ac1_example_shape() -> None:
    days = _days(30)
    splits = walk_forward_splits(days, train_min_days=10, test_days=5, embargo_days=2, vault_days=3)
    _check_invariants(days, splits, train_min_days=10, test_days=5, embargo_days=2, vault_days=3)
    # 30 日 − 金庫 3 = 27 可用；首測試日 index 12；測試日共 15 → 3 段（5/5/5）
    assert len(splits) == 3


def test_ac1_partial_tail_block() -> None:
    days = _days(28)
    splits = walk_forward_splits(days, train_min_days=10, test_days=5, embargo_days=2, vault_days=3)
    # 25 可用；測試日 13 → 5/5/3，尾段可短
    assert len(splits) == 3
    _check_invariants(days, splits, train_min_days=10, test_days=5, embargo_days=2, vault_days=3)


def test_ac1_deterministic() -> None:
    days = _days(60)
    a = walk_forward_splits(days, train_min_days=20, test_days=7, embargo_days=3, vault_days=5)
    b = walk_forward_splits(days, train_min_days=20, test_days=7, embargo_days=3, vault_days=5)
    assert a == b


def test_ac1_unsorted_raises() -> None:
    days = _days(20)
    days[3], days[4] = days[4], days[3]
    with pytest.raises(ValueError, match="遞增"):
        walk_forward_splits(days, train_min_days=5, test_days=3, embargo_days=1)


def test_ac1_insufficient_days_raises() -> None:
    with pytest.raises(ValueError, match="不足"):
        walk_forward_splits(_days(12), train_min_days=10, test_days=3, embargo_days=2)


def test_ac1_bad_params_raise() -> None:
    days = _days(30)
    for kw in (
        {"train_min_days": 0, "test_days": 5, "embargo_days": 0},
        {"train_min_days": 10, "test_days": 0, "embargo_days": 0},
        {"train_min_days": 10, "test_days": 5, "embargo_days": -1},
        {"train_min_days": 10, "test_days": 5, "embargo_days": 0, "vault_days": -1},
    ):
        with pytest.raises(ValueError):
            walk_forward_splits(days, **kw)  # type: ignore[arg-type]


@settings(max_examples=50, deadline=None)
@given(
    n=st.integers(min_value=30, max_value=300),
    train_min=st.integers(min_value=5, max_value=60),
    test=st.integers(min_value=1, max_value=20),
    embargo=st.integers(min_value=0, max_value=10),
    vault=st.integers(min_value=0, max_value=15),
)
def test_ac1_property_invariants(
    n: int, train_min: int, test: int, embargo: int, vault: int
) -> None:
    if n - vault < train_min + embargo + 1:
        return  # 不可構成切分的組合由 raise 測試覆蓋
    days = _days(n)
    splits = walk_forward_splits(
        days, train_min_days=train_min, test_days=test, embargo_days=embargo, vault_days=vault
    )
    _check_invariants(
        days,
        splits,
        train_min_days=train_min,
        test_days=test,
        embargo_days=embargo,
        vault_days=vault,
    )
