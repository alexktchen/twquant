"""風控：送單前檢查與停手狀態。平倉（只減少部位的委託）永遠放行。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from autotrade.config import RiskConfig
from autotrade.model import OrderRequest, require_taipei

HALT_DAILY_LOSS = "daily_loss"
HALT_KILL = "kill"


@dataclass(frozen=True)
class Decision:
    """風控的回答。allowed 為 False 時 reason 一定有字。"""

    allowed: bool
    reason: str = ""


@dataclass
class RiskManager:
    """單一商品的風控狀態。"""

    cfg: RiskConfig
    day: date | None = None
    entries_today: int = 0
    halt: str | None = None
    last_quote_ts: datetime | None = None

    def roll_day(self, now: datetime) -> None:
        """換日：進場次數與每日上限造成的停手歸零；人工緊急停止不解除。"""
        today = require_taipei(now, "roll_day.now").date()
        if today != self.day:
            self.day = today
            self.entries_today = 0
            if self.halt == HALT_DAILY_LOSS:
                self.halt = None

    def on_quote(self, ts: datetime) -> None:
        """記下最後一筆行情的時間。"""
        self.last_quote_ts = require_taipei(ts, "on_quote.ts")

    def kill(self) -> None:
        """人工緊急停止：之後只放行平倉，直到 release_kill。"""
        self.halt = HALT_KILL

    def release_kill(self) -> None:
        """人工解除緊急停止。"""
        if self.halt == HALT_KILL:
            self.halt = None

    def update_pnl(self, day_pnl_twd: float, now: datetime) -> None:
        """餵當日損益（已實現＋未實現）；碰到每日虧損上限就停手。"""
        self.roll_day(now)
        limit = self.cfg.daily_loss_limit_twd
        if limit is not None and day_pnl_twd <= -limit and self.halt is None:
            self.halt = HALT_DAILY_LOSS

    def check(self, req: OrderRequest, position: int, now: datetime) -> Decision:
        """送單前檢查。position 是送單前的實際部位。"""
        now = require_taipei(now, "check.now")
        self.roll_day(now)
        if req.qty > self.cfg.max_order_qty:
            return Decision(False, f"單筆 {req.qty} 口超過上限 {self.cfg.max_order_qty} 口")
        after = position + req.signed_qty
        if not self.cfg.min_position <= after <= self.cfg.max_position:
            return Decision(
                False,
                f"送出後部位 {after} 口超出範圍 {self.cfg.min_position}～{self.cfg.max_position}",
            )
        reducing = abs(after) < abs(position) and after * position >= 0
        if reducing:
            return Decision(True)
        if self.halt is not None:
            return Decision(False, f"停手中（{self.halt}），只接受平倉")
        if not self.cfg.entry_start <= now.time() < self.cfg.entry_end:
            return Decision(
                False, f"{now.time()} 不在進場時段 {self.cfg.entry_start}～{self.cfg.entry_end}"
            )
        if self.entries_today >= self.cfg.max_entries_per_day:
            return Decision(False, f"今天已進場 {self.entries_today} 次，達上限")
        if self.last_quote_ts is None:
            return Decision(False, "還沒收到行情")
        age = (now - self.last_quote_ts).total_seconds()
        if age > self.cfg.max_quote_age_s:
            return Decision(False, f"行情已 {age:.1f} 秒沒更新，超過 {self.cfg.max_quote_age_s} 秒")
        return Decision(True)

    def on_entry_sent(self) -> None:
        """一筆新倉委託送出後計一次進場。"""
        self.entries_today += 1
