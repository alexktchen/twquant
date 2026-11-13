"""風控：送單前檢查與停手狀態。平倉（只減少部位的委託）放行，只有「暫停送單」例外。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from autotrade.config import RiskConfig
from autotrade.model import OrderRequest, require_taipei

HALT_DAILY_LOSS = "daily_loss"
HALT_KILL = "kill"
HALT_MISMATCH = "mismatch"  # 部位或委託跟券商對不上：停手並平倉，要人工解除
HALT_ORDER_RATE = "order_rate"  # 送單太頻繁：暫停送單
HALT_BROKER_REJECT = "broker_reject"  # 連續被券商拒絕：暫停送單
# 暫停送單：連平倉單都不送（程式或券商端有異常，再送只會更糟），要人工解除
SUSPENDED = frozenset({HALT_ORDER_RATE, HALT_BROKER_REJECT})
MANUAL_RELEASE = frozenset({HALT_KILL, HALT_MISMATCH}) | SUSPENDED
RATE_WINDOW = timedelta(minutes=1)


@dataclass(frozen=True)
class Decision:
    """風控的回答。allowed 為 False 時 reason 一定有字。"""

    allowed: bool
    reason: str = ""


def is_reducing(position: int, signed_qty: int) -> bool:
    """這筆委託是不是只會減少部位（不反手、不加碼）。"""
    after = position + signed_qty
    return abs(after) < abs(position) and after * position >= 0


@dataclass
class RiskManager:
    """單一商品的風控狀態。"""

    cfg: RiskConfig
    day: date | None = None
    entries_today: int = 0
    halt: str | None = None
    last_quote_ts: datetime | None = None
    broker_rejects: int = 0
    sent: deque[datetime] = field(default_factory=deque)

    @property
    def suspended(self) -> bool:
        """是不是在「暫停送單」（連平倉單都不送）。"""
        return self.halt in SUSPENDED

    def roll_day(self, now: datetime) -> None:
        """換日：進場次數與每日上限造成的停手歸零；其他停手都要人工解除。"""
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
        """人工緊急停止：之後只放行平倉，直到 release_kill。暫停送單中不降級。"""
        if not self.suspended:
            self.halt = HALT_KILL

    def halt_mismatch(self) -> None:
        """部位或委託跟券商對不上：停手（只放行平倉）。暫停送單中不降級。"""
        if not self.suspended:
            self.halt = HALT_MISMATCH

    def release_kill(self) -> None:
        """人工解除：緊急停止、對不上、暫停送單都由這裡解除。"""
        if self.halt in MANUAL_RELEASE:
            self.halt = None
            self.broker_rejects = 0

    def update_pnl(self, day_pnl_twd: float, now: datetime) -> None:
        """餵當日損益（已實現＋未實現）；碰到每日虧損上限就停手。"""
        self.roll_day(now)
        limit = self.cfg.daily_loss_limit_twd
        if limit is not None and day_pnl_twd <= -limit and self.halt is None:
            self.halt = HALT_DAILY_LOSS

    def on_order_sent(self, now: datetime) -> None:
        """每送出一筆委託（新倉或平倉）記一次，給下單頻率上限用。"""
        self.sent.append(require_taipei(now, "on_order_sent.now"))

    def on_broker_reject(self) -> None:
        """券商拒絕一筆委託；連續達上限就暫停送單。"""
        self.broker_rejects += 1
        limit = self.cfg.max_consecutive_broker_rejects
        if limit is not None and self.broker_rejects >= limit:
            self.halt = HALT_BROKER_REJECT

    def on_broker_ok(self) -> None:
        """券商正常回報（收到委託或成交）：連續被拒的計數歸零。"""
        self.broker_rejects = 0

    def _sent_in_window(self, now: datetime) -> int:
        while self.sent and now - self.sent[0] >= RATE_WINDOW:
            self.sent.popleft()
        return len(self.sent)

    def check(self, req: OrderRequest, position: int, now: datetime) -> Decision:
        """送單前檢查。position 是送單前的實際部位。"""
        now = require_taipei(now, "check.now")
        self.roll_day(now)
        if self.suspended:
            return Decision(False, f"暫停送單（{self.halt}），要人工解除")
        rate = self.cfg.max_orders_per_minute
        if rate is not None and self._sent_in_window(now) >= rate:
            self.halt = HALT_ORDER_RATE
            return Decision(
                False, f"一分鐘內已送 {len(self.sent)} 筆委託，達上限 {rate} 筆，暫停送單"
            )
        if req.qty > self.cfg.max_order_qty:
            return Decision(False, f"單筆 {req.qty} 口超過上限 {self.cfg.max_order_qty} 口")
        if is_reducing(position, req.signed_qty):
            return Decision(True)
        after = position + req.signed_qty
        if not self.cfg.min_position <= after <= self.cfg.max_position:
            return Decision(
                False,
                f"送出後部位 {after} 口超出範圍 {self.cfg.min_position}～{self.cfg.max_position}",
            )
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
