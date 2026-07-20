"""技術型態事件因子（STRATEGY §4b，T-042）。

樞紐點（swing high/low）以前後 k 根 K 棒極值定義，在 p+k 日才「可知」（確認延遲，
反前視核心）；t 日的因子值只能使用確認日 ≤ t 的樞紐。型態成立日 = 所有構成條件
（含頸線突破）首次全部可觀察的交易日，成立日起連續 active_days 個交易日輸出同一
value，無型態不回傳列。事件稀疏因子不進 composite、不走橫斷面標準化
（STRATEGY §4b.3）。價格欄位比照 price.py（目前 adj == close，corporate_events
補齊後自動生效）。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import polars as pl

from twquant.factors.registry import factor

if TYPE_CHECKING:
    from collections.abc import Sequence

    from twquant.data.warehouse import PitView

_OUT_SCHEMA: dict[str, type[pl.DataType]] = {
    "date": pl.Date,
    "stock_id": pl.String,
    "value": pl.Float64,
}


@dataclass(frozen=True)
class Pivot:
    """已確認樞紐點：idx 為所在 K 棒；confirmed_idx = idx + k，該日起才可用。"""

    idx: int
    price: float
    kind: str  # "high" | "low"
    confirmed_idx: int


def confirmed_pivots(highs: Sequence[float], lows: Sequence[float], k: int) -> list[Pivot]:
    """找 swing high/low：嚴格高/低於前 k 根、不劣於後 k 根（同值取最早的 p）。"""
    n = len(highs)
    out: list[Pivot] = []
    for p in range(k, n - k):
        h = highs[p]
        if all(h > highs[j] for j in range(p - k, p)) and all(
            h >= highs[j] for j in range(p + 1, p + k + 1)
        ):
            out.append(Pivot(idx=p, price=h, kind="high", confirmed_idx=p + k))
        lo = lows[p]
        if all(lo < lows[j] for j in range(p - k, p)) and all(
            lo <= lows[j] for j in range(p + 1, p + k + 1)
        ):
            out.append(Pivot(idx=p, price=lo, kind="low", confirmed_idx=p + k))
    return out


def _fnum(params: dict[str, object], key: str, default: float) -> float:
    raw = params.get(key, default)
    return float(raw) if isinstance(raw, int | float | str) else default


def _fint(params: dict[str, object], key: str, default: int) -> int:
    raw = params.get(key, default)
    return int(raw) if isinstance(raw, int | float | str) else default


@dataclass(frozen=True)
class _Candidate:
    """待突破的型態候選：activate_idx 起生效，close 觸及 fire_* 成立、觸及 invalid_* 作廢。"""

    activate_idx: int  # 全部構成條件首次可知的 bar（最後一個樞紐的確認日）
    anchor_idx: int  # 視窗計齡起點（型態起點）
    value: float  # 成立時輸出的型態深度/幅度
    fire_above: float | None = None  # close > 此價 → 成立（多頭突破）
    fire_below: float | None = None  # close < 此價 → 成立（空頭跌破）
    invalid_above: float | None = None  # close > 此價 → 作廢
    invalid_below: float | None = None  # close < 此價 → 作廢


def _scan_events(
    closes: Sequence[float], candidates: list[_Candidate], *, window: int, active_days: int
) -> list[tuple[int, float]]:
    """逐根掃描候選，回傳 (成立日 idx, value)。所有判定只用確認日 ≤ t 的資訊。"""
    pending = sorted(candidates, key=lambda c: (c.activate_idx, c.anchor_idx, c.value))
    events: list[tuple[int, float]] = []
    active: list[_Candidate] = []
    pi = 0
    cooldown_until = -1
    for t in range(len(closes)):
        while pi < len(pending) and pending[pi].activate_idx <= t:
            active.append(pending[pi])
            pi += 1
        close = closes[t]
        # 過期（超出視窗）與作廢先剔除
        active = [
            c
            for c in active
            if t - c.anchor_idx <= window
            and not (c.invalid_below is not None and close < c.invalid_below)
            and not (c.invalid_above is not None and close > c.invalid_above)
        ]
        if t <= cooldown_until or not active:
            continue
        fired = [
            c
            for c in active
            if (c.fire_above is not None and close > c.fire_above)
            or (c.fire_below is not None and close < c.fire_below)
        ]
        if fired:
            best = max(fired, key=lambda c: c.value)
            events.append((t, best.value))
            active = []  # 型態消耗；active 期間不再觸發（STRATEGY §4b.2）
            cooldown_until = t + active_days - 1
    return events


def _double_bottom_events(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    *,
    window: int,
    k: int,
    low_tol: float,
    min_gap: int,
    buffer: float,
    min_depth: float,
    active_days: int,
) -> list[tuple[int, float]]:
    """W 底：雙低配對 + 頸線突破。頸線 = 兩低點之間最高的樞紐高點（其確認日必早於
    low2 確認日，故無前視）。"""
    pivots = confirmed_pivots(highs, lows, k)
    plows = [p for p in pivots if p.kind == "low"]
    phighs = [p for p in pivots if p.kind == "high"]
    candidates: list[_Candidate] = []
    for j, low2 in enumerate(plows):
        for low1 in plows[:j]:
            if low2.idx - low1.idx < min_gap:
                continue
            if abs(low1.price - low2.price) / min(low1.price, low2.price) > low_tol:
                continue
            necks = [h.price for h in phighs if low1.idx < h.idx < low2.idx]
            if not necks:
                continue
            neckline = max(necks)
            depth = (neckline - max(low1.price, low2.price)) / neckline
            if depth < min_depth:
                continue
            candidates.append(
                _Candidate(
                    activate_idx=low2.confirmed_idx,
                    anchor_idx=low1.idx,
                    value=depth,
                    fire_above=neckline * (1.0 + buffer),
                    invalid_below=min(low1.price, low2.price),
                )
            )
    return _scan_events(closes, candidates, window=window, active_days=active_days)


def _hs_top_events(
    closes: Sequence[float],
    highs: Sequence[float],
    lows: Sequence[float],
    *,
    window: int,
    k: int,
    low_tol: float,
    shoulder_margin: float,
    buffer: float,
    active_days: int,
) -> list[tuple[int, float]]:
    """頭肩頂：連續三個樞紐高點（中峰高於兩肩 ≥ margin、兩肩差 ≤ low_tol），
    頸線 = 兩谷（各段間最低樞紐低點）均值，close 跌破頸線成立；漲破中峰作廢。"""
    pivots = confirmed_pivots(highs, lows, k)
    plows = [p for p in pivots if p.kind == "low"]
    phighs = [p for p in pivots if p.kind == "high"]
    candidates: list[_Candidate] = []
    for h1, h2, h3 in zip(phighs, phighs[1:], phighs[2:], strict=False):
        if h2.price < h1.price * (1.0 + shoulder_margin):
            continue
        if h2.price < h3.price * (1.0 + shoulder_margin):
            continue
        if abs(h1.price - h3.price) / min(h1.price, h3.price) > low_tol:
            continue
        t1 = [lo.price for lo in plows if h1.idx < lo.idx < h2.idx]
        t2 = [lo.price for lo in plows if h2.idx < lo.idx < h3.idx]
        if not t1 or not t2:
            continue
        neckline = (min(t1) + min(t2)) / 2.0
        candidates.append(
            _Candidate(
                activate_idx=h3.confirmed_idx,
                anchor_idx=h1.idx,
                value=(h2.price - neckline) / neckline,
                fire_below=neckline * (1.0 - buffer),
                invalid_above=h2.price,
            )
        )
    return _scan_events(closes, candidates, window=window, active_days=active_days)


def _price_panel(view: PitView) -> pl.DataFrame:
    """載入 (date, stock_id, high, low, close)，剔除缺值與非正價列（停牌/無成交，
    比照 price.py 的 > 0 防衛），每股依日排序。"""
    return (
        view.table("prices_daily")
        .select("date", "stock_id", "high", "low", "close")
        .drop_nulls(["high", "low", "close"])
        .filter((pl.col("high") > 0) & (pl.col("low") > 0) & (pl.col("close") > 0))
        .sort(["stock_id", "date"])
        .collect()
    )


_EventFn = Callable[[list[float], list[float], list[float]], list[tuple[int, float]]]


def _emit_events(px: pl.DataFrame, event_fn: _EventFn, active_days: int) -> pl.DataFrame:
    """逐股跑事件偵測，成立日起 active_days 個交易日輸出同一 value。"""
    rows: list[tuple[dt.date, str, float]] = []
    for part in px.partition_by("stock_id", maintain_order=True):
        sid = str(part["stock_id"][0])
        dates = part["date"].to_list()
        events = event_fn(part["close"].to_list(), part["high"].to_list(), part["low"].to_list())
        for t_idx, value in events:
            for j in range(t_idx, min(t_idx + active_days, len(dates))):
                rows.append((dates[j], sid, value))
    if not rows:
        return pl.DataFrame(schema=_OUT_SCHEMA)
    return pl.DataFrame(rows, schema=_OUT_SCHEMA, orient="row").sort(["date", "stock_id"])


@factor(
    name="price.double_bottom_120d",
    category="price",
    direction=1,
    tables=["prices_daily"],
    lookback_days=140,
    params={
        "window": 120,
        "confirm_bars": 5,
        "low_tol": 0.03,
        "min_gap_bars": 15,
        "break_buffer": 0.005,
        "min_depth": 0.03,
        "active_days": 10,
    },
    anchor_invariant=True,
)
def double_bottom_120d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """W 底突破事件：雙低點（樞紐確認延遲）＋頸線突破，成立日起輸出型態深度。"""
    window = _fint(params, "window", 120)
    k = _fint(params, "confirm_bars", 5)
    low_tol = _fnum(params, "low_tol", 0.03)
    min_gap = _fint(params, "min_gap_bars", 15)
    buffer = _fnum(params, "break_buffer", 0.005)
    min_depth = _fnum(params, "min_depth", 0.03)
    active_days = _fint(params, "active_days", 10)

    def _events(
        closes: list[float], highs: list[float], lows: list[float]
    ) -> list[tuple[int, float]]:
        return _double_bottom_events(
            closes,
            highs,
            lows,
            window=window,
            k=k,
            low_tol=low_tol,
            min_gap=min_gap,
            buffer=buffer,
            min_depth=min_depth,
            active_days=active_days,
        )

    return _emit_events(_price_panel(view), _events, active_days)


@factor(
    name="price.hs_top_120d",
    category="price",
    direction=-1,
    tables=["prices_daily"],
    lookback_days=140,
    params={
        "window": 120,
        "confirm_bars": 5,
        "low_tol": 0.03,
        "shoulder_margin": 0.02,
        "break_buffer": 0.005,
        "active_days": 10,
    },
    anchor_invariant=True,
)
def hs_top_120d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """頭肩頂跌破事件：三樞紐高點＋頸線跌破（dir=−1），成立日起輸出頭至頸線深度。"""
    window = _fint(params, "window", 120)
    k = _fint(params, "confirm_bars", 5)
    low_tol = _fnum(params, "low_tol", 0.03)
    margin = _fnum(params, "shoulder_margin", 0.02)
    buffer = _fnum(params, "break_buffer", 0.005)
    active_days = _fint(params, "active_days", 10)

    def _events(
        closes: list[float], highs: list[float], lows: list[float]
    ) -> list[tuple[int, float]]:
        return _hs_top_events(
            closes,
            highs,
            lows,
            window=window,
            k=k,
            low_tol=low_tol,
            shoulder_margin=margin,
            buffer=buffer,
            active_days=active_days,
        )

    return _emit_events(_price_panel(view), _events, active_days)


@factor(
    name="price.box_breakout_60d",
    category="price",
    direction=1,
    tables=["prices_daily"],
    lookback_days=70,
    params={"window": 60, "box_width": 0.12, "break_buffer": 0.005, "active_days": 10},
    anchor_invariant=True,
)
def box_breakout_60d(view: PitView, params: dict[str, object]) -> pl.DataFrame:
    """箱型突破事件：截至 t−1 的 window 日高低區間寬 ≤ box_width 且 close 突破區間高，
    成立日起輸出突破幅度 close/區間高 − 1。"""
    window = _fint(params, "window", 60)
    box_width = _fnum(params, "box_width", 0.12)
    buffer = _fnum(params, "break_buffer", 0.005)
    active_days = _fint(params, "active_days", 10)

    px = _price_panel(view)
    if px.is_empty():
        return pl.DataFrame(schema=_OUT_SCHEMA)
    enriched = px.with_columns(
        pl.col("high").rolling_max(window).shift(1).over("stock_id").alias("_hi"),
        pl.col("low").rolling_min(window).shift(1).over("stock_id").alias("_lo"),
        pl.col("date").cum_count().over("stock_id").alias("_tix"),  # 每股 1-based 序號
    )
    fired = enriched.filter(
        (pl.col("_tix") > window)  # 箱體須滿 window 根
        & pl.col("_hi").is_not_null()
        & pl.col("_lo").is_not_null()
        & ((pl.col("_hi") - pl.col("_lo")) / pl.col("_lo") <= box_width)
        & (pl.col("close") > pl.col("_hi") * (1.0 + buffer))
    )
    if fired.is_empty():
        return pl.DataFrame(schema=_OUT_SCHEMA)
    rows: list[tuple[dt.date, str, float]] = []
    fired_sids = fired["stock_id"].unique().to_list()
    for part in px.filter(pl.col("stock_id").is_in(fired_sids)).partition_by(
        "stock_id", maintain_order=True
    ):
        sid = str(part["stock_id"][0])
        dates = part["date"].to_list()
        pos = {d: i for i, d in enumerate(dates)}
        f = fired.filter(pl.col("stock_id") == sid).sort("date")
        cooldown_until = -1
        for d, close, hi in zip(
            f["date"].to_list(), f["close"].to_list(), f["_hi"].to_list(), strict=True
        ):
            t_idx = pos[d]
            if t_idx <= cooldown_until:  # active 期間不再觸發（STRATEGY §4b.2）
                continue
            value = float(close) / float(hi) - 1.0
            for j in range(t_idx, min(t_idx + active_days, len(dates))):
                rows.append((dates[j], sid, value))
            cooldown_until = t_idx + active_days - 1
    if not rows:
        return pl.DataFrame(schema=_OUT_SCHEMA)
    return pl.DataFrame(rows, schema=_OUT_SCHEMA, orient="row").sort(["date", "stock_id"])
