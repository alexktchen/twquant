"""假的「原版模組」：介面跟網友原版被用到的部分一樣，訊號用雜湊產生（決定性）。

用途：不執行第三方程式，也能驗證「動能順勢轉接＋模擬券商」跟對照引擎逐筆相同。
訊號會讀部位裡的停損價，所以兩邊建立的部位內容只要有一點不同，後面的成交就會分岔。
"""

from __future__ import annotations

import zlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

TPE8 = timezone(timedelta(hours=8))
EXIT_HOUR = 13


class FakeBuffer:
    def __init__(self, params: Any) -> None:
        self.rows: list[dict[str, Any]] = []

    def append_sequence(self, rows: list[dict[str, Any]], params: Any) -> None:
        self.rows.extend(rows)


class FakeIntraday:
    IncrementalEventBuffer = FakeBuffer

    def __init__(self) -> None:
        self._STRATEGY_STATE_CACHE: dict[str, Any] = {}
        self.pruned = 0

    def prune_calculation_cache(self) -> None:
        self.pruned += 1

    def get_signal_context(self, **kw: Any) -> dict[str, Any]:
        rows, position = kw["quote_sequence"].rows, kw["position"]
        assert kw["candidate"]["market_relative_strength"] == 1.0
        assert kw["market_context"]["resolved"] and not kw["include_aggregates"]
        last = rows[-1]
        price, t_us = float(last["price"]), int(last["event_time"])
        h = zlib.crc32(f"{len(rows)}:{price}:{t_us}".encode())
        late = datetime.fromtimestamp(t_us / 1_000_000, tz=TPE8).hour >= EXIT_HOUR
        if position is not None:
            stop = max(position["strategy_stop_price"], position["emergency_stop_price"])
            if late:
                return {"exit_signal": True, "exit_stage": "FORCE_EXIT_TIME"}
            if price <= stop:
                return {"exit_signal": True, "exit_stage": "HIGHER_LOW_BREAK"}
            if h % 41 == 0:
                return {"exit_signal": True, "exit_stage": "EMA_TREND_BREAK"}
            if h % 43 == 0:
                return {
                    "exit_signal": True,
                    "exit_stage": "MOMENTUM_DECAY_PARTIAL",
                    "exit_fraction": 0.5,
                }
            return {}
        if late or h % 7 != 0:
            return {}
        gap = h % 60
        return {
            "entry_signal": True,
            "strategy_type": "MOMENTUM" if h % 2 else "TREND",
            "entry_setup_type": "INITIAL_BREAKOUT" if h % 3 else "TREND_RESUME",
            "strategy_stop_price": price + 5 if gap == 0 else price - gap,
            "signal_price": price - (h % 4),
            "wave_ok": h % 5 != 0,
            "momentum_ok": h % 11 != 0,
        }


class FakeTrading:
    def _entry_wave_extension_revalidation(
        self, sig: dict[str, Any], executable: float, tp: Any
    ) -> dict[str, Any]:
        return {"ok": sig["wave_ok"], "rejection_code": "WAVE_TOO_EXTENDED"}

    def _entry_momentum_revalidation(
        self, sig: dict[str, Any], price: float, tp: Any, now: datetime
    ) -> dict[str, Any]:
        assert now.utcoffset() == timedelta(hours=8)
        return {"ok": sig["momentum_ok"], "reasons": ["MOMENTUM_FADED"]}


class FakeMarket:
    def _default_trading_mode(self, state: str) -> str:
        return "AUTO"

    def _long_permission(self, state: str, mode: str) -> tuple[bool, str]:
        return True, "ALLOW"

    def _trend_permission_policy(self, context: dict[str, Any]) -> dict[str, Any]:
        return {"allowed": True}

    def resolve_strategy_market_context(
        self, context: dict[str, Any], now: datetime
    ) -> dict[str, Any]:
        return dict(context, resolved=True)


def fake_original(max_entries: int = 4) -> tuple[dict[str, Any], dict[str, Any]]:
    """回傳 (mods, params)，形狀同 load_original。"""
    mods: dict[str, Any] = {
        "intraday": FakeIntraday(),
        "trading": FakeTrading(),
        "market": FakeMarket(),
    }
    params: dict[str, Any] = {
        "intraday": SimpleNamespace(),
        "market": SimpleNamespace(snapshot=lambda: {"fake": True}),
        "trading": SimpleNamespace(
            max_consecutive_losses=2,
            loss_cooldown_minutes=20.0,
            emergency_stop_loss_pct=0.1,
            max_entries_per_symbol_per_day=max_entries,
            reentry_cooldown_minutes=3.0,
            max_stop_distance_pct=0.15,
            max_chase_pct=0.01,
        ),
    }
    return mods, params
