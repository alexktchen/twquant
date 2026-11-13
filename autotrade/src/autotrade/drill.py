"""演練程式（A-011 AC21）：用模擬券商，把歷史某一天的價格照現在的時鐘播出來，讓人從網站（或手機）實際按一次遠端緊急停止。

- 一開始買進 1 口並抱著，按下停止後才看得出「停手、平倉、確認平掉」。
- 每一筆行情都蓋上「現在」的時間：遠端指令只認今天，風控也看時間，跟真的盤中一樣在同一個時鐘上。
- 進場時段與強制平倉時間放寬到整天（只在演練裡），晚上、週末都能演練。
- 單執行緒、用時鐘推進：每一圈播該播的行情、叫 `on_timer`、叫 `ControlLoop.step`——之後真的盤中執行程式也是這個樣子。
- 只碰本機檔案：遠端指令由另一支中繼程式（`python -m autotrade relay`）拿下來。全程不碰任何券商。
"""

from __future__ import annotations

import os
import time as time_module
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from pathlib import Path

from autotrade.broker.sim import SimBroker
from autotrade.config import Config, ConfigError
from autotrade.control import ControlLoop, FileControl
from autotrade.engine import Engine, Event
from autotrade.export import dumps, snapshot
from autotrade.feed.recorded import read_recorded_day
from autotrade.feed.replay import read_taifex_day
from autotrade.journal import Journal
from autotrade.model import TAIPEI, Fill, OrderRequest, Side, Tick

DRILL_STRATEGY = "drill"
# 演練裡要印在畫面上的事件（委託、成交、停手、遠端指令、連線）
SHOWN = frozenset(
    {"order", "fill", "halt", "release", "control", "control_link", "cancel", "cancelled"}
)


@dataclass
class HoldOne:
    """演練用的策略：一開始買 1 口，之後什麼都不做。被停掉、平倉之後也不會再買。"""

    symbol: str
    sent: bool = False

    def on_tick(self, tick: Tick, position: int) -> list[OrderRequest]:
        """第一筆行情就買 1 口；只買這一次。"""
        if self.sent:
            return []
        self.sent = True
        return [OrderRequest(ts=tick.ts, symbol=self.symbol, side=Side.BUY, qty=1, reason="演練")]

    def on_fill(self, fill: Fill) -> None:
        """不用記。"""

    def on_reject(self, req: OrderRequest, reason: str) -> None:
        """沒送出去就算了（演練不重試）。"""


def drill_config(cfg: Config) -> Config:
    """演練用的設定：一定是模擬、一定要有遠端停止；進場時段與強制平倉時間放到整天。"""
    if cfg.control is None:
        raise ConfigError("設定檔沒有 [control]，沒辦法演練遠端緊急停止")
    risk = replace(
        cfg.risk,
        entry_start=time(0, 0),
        entry_end=time(23, 59, 58),
        force_flat_time=time(23, 59, 59),
        max_entries_per_day=max(cfg.risk.max_entries_per_day, 1),
    )
    return replace(cfg, mode="sim", risk=risk)


def load_prices(
    cfg: Config, ticks: Path, day: date, start: time, feed: str = "taifex"
) -> list[tuple[float, float, int]]:
    """拿那一天 start 之後的價格：（距離第一筆幾秒、價格、口數）。

    feed：taifex＝期交所盤後逐筆檔；recorded＝富邦即時錄下來的（A-013 的匯出檔）。
    """
    if feed == "recorded":
        rows = read_recorded_day(ticks, day, cfg.session.start, cfg.session.end)
    elif feed == "taifex":
        _, rows = read_taifex_day(
            ticks, cfg.contract.symbol, day, cfg.session.start, cfg.session.end
        )
    else:
        raise ValueError(f"不認得的行情來源：{feed!r}")
    picked = [t for t in rows if t.ts.time() >= start]
    if not picked:
        raise ValueError(f"{ticks} 在 {day} {start} 之後沒有 {cfg.contract.symbol} 的成交")
    first = picked[0].ts
    return [((t.ts - first).total_seconds(), t.price, t.qty) for t in picked]


@dataclass
class DrillReport:
    """演練結束時的結果。"""

    ticks: int
    position: int
    halt: str | None
    stops: int


@dataclass
class Drill:
    """演練本身。clock 與 sleep 可以換掉（測試用假的時鐘，不真的等）。"""

    cfg: Config
    prices: list[tuple[float, float, int]]
    folder: Path
    speed: float = 1.0
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(TAIPEI))
    sleep: Callable[[float], None] = time_module.sleep
    say: Callable[[str], None] = print
    status: Path | None = None
    status_every_s: float = 5.0
    tick_s: float = 0.2  # 一圈多久

    def run(self, seconds: float) -> DrillReport:
        """跑 seconds 秒（照時鐘），或價格播完為止。"""
        if not self.folder.is_dir():
            raise FileNotFoundError(f"資料夾不存在：{self.folder}")
        if self.speed <= 0:
            raise ValueError(f"speed 要大於 0：{self.speed}")
        control = self.cfg.control
        assert control is not None  # drill_config 保證有
        log = Journal(self.folder / "drill_journal.jsonl")
        engine = Engine(
            self.cfg, HoldOne(self.cfg.contract.symbol), SimBroker(self.cfg.sim), sink=log.write
        )
        shown = 0
        loop = ControlLoop(engine, FileControl(self.folder, control.stale_s), control.poll_s)
        start = self.clock()
        end = start + timedelta(seconds=seconds)
        next_status = start
        played = 0
        try:
            while True:
                now = self.clock()
                if now >= end:
                    break
                elapsed = (now - start).total_seconds() * self.speed
                while played < len(self.prices) and self.prices[played][0] <= elapsed:
                    _, price, qty = self.prices[played]
                    engine.on_tick(Tick(ts=now, price=price, qty=qty))
                    played += 1
                engine.on_timer(now)
                loop.step(now)
                shown = self._show(engine.events, shown)
                if self.status is not None and now >= next_status and engine.last_tick is not None:
                    self._write_status(engine, loop, now)
                    next_status = now + timedelta(seconds=self.status_every_s)
                if played >= len(self.prices) and engine.oms.working() == []:
                    self.say("價格播完了，演練結束。")
                    break
                self.sleep(self.tick_s)
        finally:
            log.close()
        stops = sum(1 for e in engine.events if e.kind == "control" and e.data.get("applied"))
        return DrillReport(played, engine.ledger.position, engine.risk.halt, stops)

    def _show(self, events: list[Event], shown: int) -> int:
        for event in events[shown:]:
            if event.kind in SHOWN:
                self.say(f"{event.ts:%H:%M:%S}  {event.text}")
        return len(events)

    def _write_status(self, engine: Engine, loop: ControlLoop, now: datetime) -> None:
        assert self.status is not None
        snap = snapshot(
            engine, now, source="replay", status="live", strategy=DRILL_STRATEGY, control=loop.state
        )
        tmp = self.status.with_name(self.status.name + ".tmp")
        tmp.write_text(dumps(snap), encoding="utf-8", newline="\n")
        os.replace(tmp, self.status)
