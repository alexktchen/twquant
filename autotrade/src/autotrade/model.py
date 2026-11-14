"""資料型別：逐筆、委託、成交、遠端指令。時間一律 tz-aware Asia/Taipei。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def require_taipei(ts: datetime, what: str) -> datetime:
    """確認時間帶時區並轉成 Asia/Taipei；naive datetime 直接 raise。"""
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError(f"{what} 是沒有時區的時間：{ts!r}（一律要 tz-aware Asia/Taipei）")
    return ts.astimezone(TAIPEI)


class Side(Enum):
    """買賣別；value 是部位變動的方向。"""

    BUY = 1
    SELL = -1


@dataclass(frozen=True)
class Tick:
    """一筆成交與當下的買賣價。price 單位是點，qty 單位是口。"""

    ts: datetime
    price: float
    qty: int
    bid: float | None = None
    ask: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts", require_taipei(self.ts, "Tick.ts"))
        if self.price <= 0 or self.qty <= 0:
            raise ValueError(f"Tick 價量要大於 0：price={self.price} qty={self.qty} ts={self.ts}")


@dataclass(frozen=True)
class OrderRequest:
    """策略想送的委託。limit_price 為 None 表示市價。"""

    ts: datetime
    symbol: str
    side: Side
    qty: int
    limit_price: float | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts", require_taipei(self.ts, "OrderRequest.ts"))
        if self.qty <= 0:
            raise ValueError(f"委託口數要大於 0：{self.qty}（{self.symbol} {self.side.name}）")

    @property
    def signed_qty(self) -> int:
        """帶方向的口數：買為正、賣為負。"""
        return self.side.value * self.qty


@dataclass(frozen=True)
class Fill:
    """一筆成交回報。"""

    ts: datetime
    symbol: str
    side: Side
    qty: int
    price: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "ts", require_taipei(self.ts, "Fill.ts"))
        if self.qty <= 0 or self.price <= 0:
            raise ValueError(f"成交價量要大於 0：price={self.price} qty={self.qty} {self.symbol}")

    @property
    def signed_qty(self) -> int:
        """帶方向的口數：買為正、賣為負。"""
        return self.side.value * self.qty


# 遠端指令。A-011／D-225 只有「停止」；A-016／D-229 起多了五種（使用者要網站也能平倉、暫停、啟動）
ACTION_STOP = "stop"  # 緊急停止：停手並平倉，所有程式都執行，今天之內都有效
ACTION_FLATTEN = "flatten"  # 平倉但不停手
ACTION_PAUSE = "pause"  # 暫停自動交易：不再進新單
ACTION_RESUME = "resume"  # 恢復自動交易
ACTION_START = "start"  # 啟動交易程式（由本機後台執行）
ACTION_QUIT = "quit"  # 結束交易程式（由本機後台執行）
ENGINE_ACTIONS = (ACTION_FLATTEN, ACTION_PAUSE, ACTION_RESUME)  # 由 target 那一支交易程式執行
ADMIN_ACTIONS = (ACTION_START, ACTION_QUIT)  # 由後台執行

# 一筆停止會回報兩個階段：先是「收到了、當下怎麼處理」（ack），之後是「最後的結果」（done）
STAGE_ACK = "ack"
STAGE_DONE = "done"

# 停止指令的處理結果代碼（網站用它決定要不要提醒使用者手動處理）
RESULT_FLAT = "flat"  # 沒有部位、沒有委託（ack：本來就空手；done：確認平掉了）
RESULT_CANCELLING = "cancelling"  # 已停手，已送出刪單
RESULT_FLATTENING = "flattening"  # 已停手，已送出平倉
RESULT_LINK_DOWN = "link_down"  # 已停手，但券商連線中斷，恢復後才會平倉
RESULT_SUSPENDED = "suspended"  # 程式在暫停送單，不會自動平倉
RESULT_IGNORED = "ignored"  # 沒有執行（不是今天的、或不認得的指令）
RESULT_STUCK = "stuck"  # done：停手了但部位沒平掉（暫停送單、或等太久），要人處理
RESULT_RELEASED = "released"  # done：已經在電腦上解除，程式可以再交易
RESULT_PAUSED = "paused"  # 已暫停自動交易
RESULT_RESUMED = "resumed"  # 已恢復自動交易
RESULT_ACCEPTED = "accepted"  # ack：後台收到了，正在做
RESULT_STARTED = "started"  # done：交易程式啟動了
RESULT_QUIT = "quit"  # done：交易程式結束了（或本來就沒有在跑）
RESULT_REFUSED = "refused"  # done：沒有執行（原因在 note）


@dataclass(frozen=True)
class Command:
    """網站送來的一筆遠端指令。day 是按下那一天的台北日期；target 是對哪一種模式（停止沒有，所有程式都執行）。"""

    id: int
    action: str
    day: date
    created_at: datetime
    target: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "created_at", require_taipei(self.created_at, "Command.created_at")
        )
        if self.id <= 0:
            raise ValueError(f"指令編號要大於 0：{self.id}")


@dataclass(frozen=True)
class CommandResult:
    """一筆遠端指令的處理結果，回報給網站。stage 是 ack 或 done；code 給程式判斷，note 給人看。"""

    command_id: int
    stage: str
    code: str
    note: str
    # 那筆指令按下的時間：指令表重建、編號重來時，靠它確認回報的是同一筆（不參與相等比較）
    created_at: datetime | None = field(default=None, compare=False)
    # 那筆指令是什麼動作（寫進回報檔：重開之後靠它知道今天做過什麼。不參與相等比較）
    action: str = field(default=ACTION_STOP, compare=False)
    # 執行的當下，交易程式處理到哪一筆行情（還沒收到行情是 None）。電腦的時鐘比行情快一點，
    # 重開之後要照「接在哪一筆行情後面」套用，結果才會一模一樣（不參與相等比較）
    after_tick: datetime | None = field(default=None, compare=False)
