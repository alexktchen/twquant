"""T-079 AC-2：擷取層資料完整性閘門（黃金規則 5）。

這三個檢查來自 2026-09-01 實際發生的資料事故——`prices_daily` 有 43 個交易日、
`institutional_daily` 有 2 個日期被寫入重複／陳舊快照，存在至少三年未被發現，
因為**沒有任何一關在檢查**。每條測試都以當時的真實數據形狀構造，確保這些檢查
當初就會攔下來。
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from twquant.data.quality import (
    assert_no_duplicate_snapshots,
    assert_no_sparse_dates,
    duplicate_snapshot_dates,
    phantom_dates,
    sparse_dates,
)
from twquant.errors import QualityGateError

_D = [dt.date(2024, 5, 1 + i) for i in range(6)]


def _panel(rows: list[tuple[dt.date, str, float, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "stock_id": pl.String, "close": pl.Float64, "volume": pl.Int64},
        orient="row",
    )


def _day(d: dt.date, n: int, *, seed: int) -> list[tuple[dt.date, str, float, int]]:
    """一日 n 檔的合成行情；seed 不同 → 價量不同。"""
    return [(d, f"{1100 + i}", 10.0 + i + seed, 1_000 * (i + 1) + seed) for i in range(n)]


def test_ac2_detects_duplicated_snapshot_between_two_dates() -> None:
    """AC-2：兩個日期共用同一份快照 → 必須被抓出。

    真實案例：2024-05-06 與 2024-05-20 有 1,234 檔的 (close, volume) 完全相同，
    包含 2618 長榮航 close 36.40／volume 238,615,417——精確到股數。
    """
    rows = _day(_D[0], 50, seed=0) + _day(_D[1], 50, seed=1)
    # _D[2] 是 _D[0] 的複製，只換日期
    rows += [(_D[2], sid, c, v) for (_, sid, c, v) in _day(_D[0], 50, seed=0)]
    hits = duplicate_snapshot_dates(
        _panel(rows), key=["stock_id"], values=["close", "volume"], max_rate=0.05
    )
    flagged = {h.date for h in hits}
    assert _D[0] in flagged and _D[2] in flagged, "共用快照的兩個日期都應被標記"
    assert _D[1] not in flagged, "獨立的日期不得誤報"
    top = max(hits, key=lambda h: h.rate)
    assert top.rate == pytest.approx(1.0), "完全複製的日期重複率應為 1.0"


def test_ac2_genuine_coincidence_stays_below_threshold() -> None:
    """AC-2：真實巧合不得誤報。實測乾淨資料的最大重複率是 1.64%，門檻 5% 有充分餘裕。"""
    rows = _day(_D[0], 100, seed=0) + _day(_D[1], 100, seed=1)
    # 只有 2 檔碰巧價量相同（2%）——低於門檻
    rows += [(_D[1], "9001", 10.0, 1_000), (_D[0], "9001", 10.0, 1_000)]
    rows += [(_D[1], "9002", 20.0, 2_000), (_D[0], "9002", 20.0, 2_000)]
    hits = duplicate_snapshot_dates(
        _panel(rows), key=["stock_id"], values=["close", "volume"], max_rate=0.05
    )
    assert hits == [], f"2% 的巧合不應觸發，實得 {[(h.date, h.rate) for h in hits]}"


def test_ac2_assert_raises_with_actionable_context() -> None:
    """AC-2：檢出即 raise（黃金規則 5），訊息須含表名、日期、重複率與對象日期。"""
    rows = _day(_D[0], 40, seed=0)
    rows += [(_D[1], sid, c, v) for (_, sid, c, v) in _day(_D[0], 40, seed=0)]
    with pytest.raises(QualityGateError) as exc:
        assert_no_duplicate_snapshots(
            _panel(rows),
            table="prices_daily",
            key=["stock_id"],
            values=["close", "volume"],
            max_rate=0.05,
        )
    msg = str(exc.value)
    assert "prices_daily" in msg
    assert str(_D[0]) in msg or str(_D[1]) in msg
    assert "%" in msg, "訊息須含重複率，否則無法判斷嚴重程度"


def test_ac2_clean_panel_does_not_raise() -> None:
    """AC-2：乾淨資料不得誤攔——閘門會誤報就沒人敢開。"""
    rows = _day(_D[0], 80, seed=0) + _day(_D[1], 80, seed=7) + _day(_D[2], 80, seed=13)
    assert_no_duplicate_snapshots(
        _panel(rows),
        table="prices_daily",
        key=["stock_id"],
        values=["close", "volume"],
        max_rate=0.05,
    )


def test_ac2_detects_phantom_date_not_on_trading_calendar() -> None:
    """AC-2：資料表出現非交易日 → 抓出。

    真實案例：`institutional_daily` 有 2025-01-24（2025 農曆年封關、非交易日）
    共 908 列，內容是 2022-02-09 的複製。
    """
    calendar = [_D[0], _D[1], _D[3]]
    present = [_D[0], _D[1], _D[2], _D[3]]
    assert phantom_dates(present, calendar) == [_D[2]]
    assert phantom_dates(calendar, calendar) == []


def test_ac2_duplicate_rate_is_relative_to_that_days_row_count() -> None:
    """AC-2：重複率須以「該日自身列數」為分母。

    汙染日的列數常與正常日不同（複製自別日 → 上市檔數不同），用全域平均當分母
    會低估小日子的嚴重度。
    """
    # _D[0] 只有 10 檔且全被複製到 _D[1]；_D[1] 有 100 檔
    small = _day(_D[0], 10, seed=0)
    rows = small + _day(_D[1], 100, seed=5)
    rows += [(_D[1], sid, c, v) for (_, sid, c, v) in small]
    hits = {
        h.date: h.rate
        for h in duplicate_snapshot_dates(
            _panel(rows), key=["stock_id"], values=["close", "volume"], max_rate=0.05
        )
    }
    assert hits[_D[0]] == pytest.approx(1.0), "小日子被全數複製 → 重複率 100%"
    assert hits[_D[1]] == pytest.approx(10 / 110), "大日子的分母是它自己的列數"


# ── 稀疏日偵測（2026-09-02 事故）─────────────────────────────────────────────
# 重複快照偵測器抓「多出來的列」，抓不到「少掉的列」。實際踩到：8 個交易日只有
# TWSE 一條腿、整個 TPEX（上櫃 774~839 檔）完全缺席，而該日仍被記為擷取成功，
# 三年間沒有任何一關發現。那 8 天列數 1,189~1,308，鄰近正常日 5,537——差 4 倍以上。


def _counts_panel(counts: dict[str, int]) -> pl.DataFrame:
    """依 {日期: 列數} 造一張只有 date/stock_id 的面板。"""
    rows = [
        {"date": dt.date.fromisoformat(d), "stock_id": f"{1000 + i}"}
        for d, n in counts.items()
        for i in range(n)
    ]
    return pl.DataFrame(rows)


def test_ac2_detects_date_missing_one_source_leg() -> None:
    """單一來源缺席＝列數塌陷到鄰近日的一小部分，須被抓出。

    數字取自真實事故：正常 5,537 列、壞日 1,189 列（只剩 TWSE，TPEX 全缺）。
    """
    counts = {f"2024-06-{d:02d}": 5537 for d in range(3, 15)}
    counts["2024-06-10"] = 1189
    hits = sparse_dates(_counts_panel(counts), min_ratio=0.5, window=5)
    assert [h.date for h in hits] == [dt.date(2024, 6, 10)]
    assert hits[0].n_rows == 1189
    assert hits[0].expected == pytest.approx(5537)


def test_ac2_normal_daily_variation_is_not_flagged() -> None:
    """正常日列數會小幅浮動（新上市／下市／暫停交易），不得誤報。"""
    counts = {f"2024-06-{d:02d}": n for d, n in zip(range(3, 15), range(5500, 5512), strict=True)}
    assert sparse_dates(_counts_panel(counts), min_ratio=0.5, window=5) == []


def test_ac2_sparse_uses_local_window_not_global_median() -> None:
    """比較基準必須是**鄰近日**：宇宙隨年份成長，用全期中位數會把早期整段誤報。

    此處前段 2,000 列、後段 6,000 列都屬正常，只有中間插入的塌陷日該被抓。
    """
    counts = {f"2020-06-{d:02d}": 2000 for d in range(1, 13)}
    counts.update({f"2026-06-{d:02d}": 6000 for d in range(1, 13)})
    counts["2026-06-07"] = 1200
    hits = sparse_dates(_counts_panel(counts), min_ratio=0.5, window=5)
    assert [h.date for h in hits] == [dt.date(2026, 6, 7)], "早期低列數日不得被誤報"


def test_ac2_assert_sparse_raises_with_actionable_context() -> None:
    """訊息要指出哪一天、幾列、鄰近日幾列——否則使用者無從判斷該重抓哪一天。"""
    counts = {f"2024-06-{d:02d}": 5537 for d in range(3, 15)}
    counts["2024-06-10"] = 1189
    with pytest.raises(QualityGateError) as exc:
        assert_no_sparse_dates(_counts_panel(counts), table="prices_daily", min_ratio=0.5, window=5)
    msg = str(exc.value)
    assert "2024-06-10" in msg and "1,189" in msg


def test_ac2_short_history_is_not_flagged() -> None:
    """鄰近窗不足時寧可不報也不誤報（首次回補的頭幾天）。"""
    panel = _counts_panel({"2024-06-03": 100, "2024-06-04": 5000})
    assert sparse_dates(panel, min_ratio=0.5, window=5) == []
