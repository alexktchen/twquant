"""T-259：籌碼載入器要寫哪些日期——**湖與 PG 各自算**（2026-09-20 的靜默失敗）。

當天實際發生：`lake_rebuild` 已有 2005~2026 的 5,329 天，PG 只有其中 1,620 天。
載入器只用「湖有沒有」判斷，於是湖有、PG 沒有的 3,709 天被整個跳過，
而它回報「已寫入湖與 PG：2,660,877 列」——**成功訊息與實際狀態不符**。
後果：2020~2024 的籌碼資料在 PG 缺席，而 D-127 的籌碼特徵正要用那幾年。
"""

from __future__ import annotations

import datetime as dt

from scripts.load_finmind_chip import write_targets


def _d(*days: int) -> list[dt.date]:
    return [dt.date(2021, 1, d) for d in days]


def test_ac1_lake_and_pg_are_decided_separately() -> None:
    """湖有、PG 沒有的日期**必須寫進 PG**；反之亦然。"""
    t = write_targets(days=_d(1, 2, 3, 4), lake_has=set(_d(1, 2)), pg_has=set(_d(1, 3)))
    assert t.to_lake == _d(3, 4)
    assert t.to_pg == _d(2, 4)
    assert t.overlap == _d(1, 2)  # 一致性比對仍以湖為準（那裡才有官方資料）


def test_ac2_nothing_to_do_is_reported_as_such() -> None:
    t = write_targets(days=_d(1, 2), lake_has=set(_d(1, 2)), pg_has=set(_d(1, 2)))
    assert t.to_lake == [] and t.to_pg == []
    assert t.nothing_to_write


def test_ac3_empty_stores_write_everything() -> None:
    t = write_targets(days=_d(1, 2, 3), lake_has=set(), pg_has=set())
    assert t.to_lake == _d(1, 2, 3) and t.to_pg == _d(1, 2, 3)
    assert t.overlap == []
    assert not t.nothing_to_write


def test_ac4_the_2026_09_20_shape_would_have_been_caught() -> None:
    """湖有全部、PG 只有一部分——舊邏輯會回「無新增日期可寫」。"""
    days = _d(1, 2, 3, 4, 5)
    t = write_targets(days=days, lake_has=set(days), pg_has=set(_d(1)))
    assert t.to_lake == []
    assert t.to_pg == _d(2, 3, 4, 5)  # 舊邏輯這裡是空的
    assert not t.nothing_to_write
