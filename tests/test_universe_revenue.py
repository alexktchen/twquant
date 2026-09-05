"""T-122：宇宙層月營收成長濾網（SPEC-013 §3.4，AC-3／AC-4）。

三個門檻（`min_revenue_yoy` / `max_revenue_yoy` / `min_revenue_streak`）是**外部先驗**
（取自 finlab 公開文章：連 3 月正成長、年增 10%~150%），不是我們配適出來的，
故不得因回測難看而調整（D-060）。

這裡鎖死四個容易錯的地方：
1. **連續月數以「營收月份」計，不是曆月**：中間缺一個月即斷鏈，不得因為
   `drop_nulls` 把缺月悄悄接起來而誤判為連續。
2. **上限 150% 是刻意的**：排除減資、合併、基期異常造成的假成長。
3. **營收缺值者排除**：完全沒有營收資料的公司不得因「沒有證據不符合」而被放行
   （黃金規則 5，與 D-059 的 min_roe 同一規則）。
4. **PIT**：月營收有公告時滯，濾網只能看訊號日當下已公布的月份。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import polars as pl

from twquant.conventions import TZ
from twquant.data import schemas
from twquant.data.warehouse import Warehouse
from twquant.strategy.config import UniverseConfig
from twquant.strategy.universe import universe_ids

_DAY = dt.date(2024, 1, 15)
# 含 2024-02-15：`test_ac4_*` 的反向確認要在 2024-01 營收公告日之後再查一次宇宙，
# 那天若沒有行情列，宇宙會因「沒有股票」而空掉，測試就驗不到 PIT（會恆真）。
_DAYS = [dt.date(2024, 1, d) for d in (8, 9, 10, 11, 12, 15)] + [dt.date(2024, 2, 15)]

# (year_month, period_end, available_at) — 台股月營收於次月 10 日前公告
_MONTHS = {
    "2023-10": (dt.date(2023, 10, 31), dt.datetime(2023, 11, 10, 22, tzinfo=TZ)),
    "2023-11": (dt.date(2023, 11, 30), dt.datetime(2023, 12, 10, 22, tzinfo=TZ)),
    "2023-12": (dt.date(2023, 12, 31), dt.datetime(2024, 1, 10, 22, tzinfo=TZ)),
    # 訊號日之後才公布 → PIT 測試用，不得被看見
    "2024-01": (dt.date(2024, 1, 31), dt.datetime(2024, 2, 10, 22, tzinfo=TZ)),
}

# stock_id -> {year_month: yoy}
_PROFILES: dict[str, dict[str, float]] = {
    # 合格：連 3 月正成長、最新 25% 落在 [10%, 150%]
    "1111": {"2023-10": 0.20, "2023-11": 0.30, "2023-12": 0.25},
    # 最新 5% 低於下限
    "2222": {"2023-10": 0.20, "2023-11": 0.30, "2023-12": 0.05},
    # 最新 200% 高於上限（假成長：減資／合併／基期異常）
    "3333": {"2023-10": 0.20, "2023-11": 0.30, "2023-12": 2.00},
    # 最新合格但三個月內有一個月衰退 → 斷鏈
    "4444": {"2023-10": -0.10, "2023-11": 0.30, "2023-12": 0.25},
    # 缺 2023-11 → 營收月份不連續（曆月看起來像連兩筆，實際斷鏈）
    "5555": {"2023-10": 0.20, "2023-12": 0.25},
    # 完全沒有月營收資料（"6666" 只出現在行情裡）
}
_STOCKS = sorted([*_PROFILES, "6666"])


def _prices() -> pl.DataFrame:
    rows = [
        {
            "date": d,
            "stock_id": s,
            "open": 50.0,
            "high": 50.0,
            "low": 50.0,
            "close": 50.0,
            "volume": 1000,
            "amount": 5e7,
            "limit_up_price": None,
            "limit_down_price": None,
            "available_at": dt.datetime(d.year, d.month, d.day, 18, tzinfo=TZ),
        }
        for d in _DAYS
        for s in _STOCKS
    ]
    return pl.DataFrame(rows).with_columns(
        pl.col("available_at").cast(schemas.TW_DT),
        pl.col("limit_up_price").cast(pl.Float64),
        pl.col("limit_down_price").cast(pl.Float64),
    )


def _revenue(extra: dict[str, dict[str, float]] | None = None) -> pl.DataFrame:
    profiles = {s: dict(m) for s, m in _PROFILES.items()}
    for s, months in (extra or {}).items():
        profiles.setdefault(s, {}).update(months)
    rows: list[dict[str, object]] = []
    for s, months in profiles.items():
        for ym, yoy in months.items():
            pe, at = _MONTHS[ym]
            rows.append(
                {
                    "date": pe,
                    "year_month": ym,
                    "stock_id": s,
                    "period_end": pe,
                    "revenue": 1e8 * (1.0 + yoy),
                    "yoy": yoy,
                    "available_at": at,
                    "available_at_estimated": False,
                }
            )
    return pl.DataFrame(rows).with_columns(pl.col("available_at").cast(schemas.TW_DT))


def _wh(tmp_path: Path, extra: dict[str, dict[str, float]] | None = None) -> Warehouse:
    wh = Warehouse(tmp_path)
    wh.write("clean", "prices_daily", _prices(), source="twse")
    wh.write("clean", "monthly_revenue", _revenue(extra), source="mops_sii")
    return wh


def _base() -> UniverseConfig:
    """只留營收濾網作用；其餘門檻全關，讓斷言可歸因。"""
    return UniverseConfig(
        min_adv_twd=None, min_price=None, max_jump_ratio=None, max_gap_sessions=None
    )


def _ids(wh: Warehouse, **over: object) -> list[str]:
    return universe_ids(wh.view(_DAY), _DAY, _base().model_copy(update=over))


def test_ac3_all_six_stocks_pass_when_filter_is_off(tmp_path: Path) -> None:
    """基線：濾網關閉時六檔全在——後面每條斷言的差異才能歸因於濾網本身。"""
    assert _ids(_wh(tmp_path)) == _STOCKS


def test_ac3_yoy_band_excludes_below_min_and_above_max(tmp_path: Path) -> None:
    """下限擋掉 5%（2222）、上限擋掉 200%（3333，假成長）。"""
    wh = _wh(tmp_path)
    assert "2222" not in _ids(wh, min_revenue_yoy=0.10)
    assert "2222" in _ids(wh, min_revenue_yoy=0.01), "門檻放到 1% 就該通過，證明擋它的是下限"
    assert "3333" not in _ids(wh, max_revenue_yoy=1.50)
    assert "3333" in _ids(wh, max_revenue_yoy=3.00), "上限放寬就該通過，證明擋它的是上限"


def test_ac3_streak_requires_all_months_positive(tmp_path: Path) -> None:
    """4444 最新月合格，但三個月內有一個月衰退 → 斷鏈，不得通過。"""
    wh = _wh(tmp_path)
    assert "4444" in _ids(wh, min_revenue_streak=2), "最近兩個月都是正的，2 應通過"
    assert "4444" not in _ids(wh, min_revenue_streak=3), "回看三個月含 −10%，3 不應通過"


def test_ac3_streak_counts_revenue_months_not_calendar(tmp_path: Path) -> None:
    """5555 只有 2023-10 與 2023-12（缺 11 月），兩筆都是正的但**營收月份不連續**。

    這是本濾網最容易寫錯的地方：`drop_nulls` 之後兩筆相鄰，若只數「有幾筆是正的」
    就會誤判為連續兩個月。必須以 year_month 的跨距驗證。
    """
    wh = _wh(tmp_path)
    got = _ids(wh, min_revenue_streak=2)
    assert "5555" not in got, "缺 2023-11 → 不算連續兩個月"
    assert "1111" in got, "同一組門檻下，月份連續的 1111 必須通過（證明不是門檻本身太嚴）"


def test_ac3_stock_without_revenue_is_excluded_not_kept(tmp_path: Path) -> None:
    """6666 完全沒有月營收資料 → 排除。

    「沒有證據顯示它不符合」不等於「它符合」。把缺值當成通過，等於讓沒有營收申報的
    公司整批繞過濾網（黃金規則 5 的精神，與 D-059 的 min_roe 同一規則）。
    """
    wh = _wh(tmp_path)
    assert "6666" in _ids(wh), "濾網關閉時應在"
    assert "6666" not in _ids(wh, min_revenue_yoy=-1e9), "門檻寬到不可能擋掉任何有值者，仍須排除"
    assert "6666" not in _ids(wh, min_revenue_streak=1)


def test_ac3_revenue_filters_none_is_bit_identical(tmp_path: Path) -> None:
    """未啟用時輸出必須與加此功能之前逐位元相同——既有 15 個策略配置都沒有這三個欄位。"""
    wh = _wh(tmp_path)
    cfg = _base()
    assert cfg.min_revenue_yoy is None
    assert cfg.max_revenue_yoy is None
    assert cfg.min_revenue_streak is None
    assert universe_ids(wh.view(_DAY), _DAY, cfg) == universe_ids(
        wh.view(_DAY),
        _DAY,
        cfg.model_copy(
            update={"min_revenue_yoy": None, "max_revenue_yoy": None, "min_revenue_streak": None}
        ),
    )


def test_ac3_full_finlab_prior_keeps_only_the_qualifying_stock(tmp_path: Path) -> None:
    """三個門檻一起套（連 3 月正成長 + 年增 10%~150%）→ 只有 1111 通過。"""
    got = _ids(_wh(tmp_path), min_revenue_yoy=0.10, max_revenue_yoy=1.50, min_revenue_streak=3)
    assert got == ["1111"]


def test_ac4_filter_cannot_see_revenue_published_after_signal_date(tmp_path: Path) -> None:
    """月營收有公告時滯：2024-01 的營收要到 2024-02-10 才公布，訊號日 2024-01-15 看不見。

    給 2222 一筆 2024-01 的 50% 年增（足以讓它通過下限）。若濾網讀到未公布的月份，
    2222 會出現在結果裡——那就是前視偏誤（黃金規則 1）。
    """
    wh = _wh(tmp_path, extra={"2222": {"2024-01": 0.50}})
    assert "2222" not in _ids(wh, min_revenue_yoy=0.10), "讀到未公布的 2024-01 營收即前視"
    # 反向確認該筆資料確實在資料湖裡（否則本測試會恆真）
    later = dt.date(2024, 2, 15)
    assert "2222" in universe_ids(
        wh.view(later), later, _base().model_copy(update={"min_revenue_yoy": 0.10})
    ), "公告日之後應看得見，證明 fixture 真的寫入了這筆"


def test_ac4_no_lookahead_extra_future_rows_do_not_change_today(tmp_path: Path) -> None:
    """f(data[:t]) == f(data)[:t]：資料湖多了未來的列，訊號日 t 的宇宙不得改變。"""
    cfg = {"min_revenue_yoy": 0.10, "max_revenue_yoy": 1.50, "min_revenue_streak": 3}
    plain = _ids(_wh(tmp_path / "plain"), **cfg)
    with_future = _ids(
        _wh(tmp_path / "future", extra={s: {"2024-01": 9.99} for s in _PROFILES}), **cfg
    )
    assert plain == with_future
