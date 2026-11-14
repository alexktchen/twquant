"""主引擎：把行情、策略、風控、委託、帳務接起來。單執行緒、一筆行情處理到底。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

from autotrade.broker.base import Ack, Broker, BrokerEvent, Cancelled, Filled, Rejected
from autotrade.config import Config
from autotrade.ledger import Ledger
from autotrade.model import (
    ACTION_ARM,
    ACTION_FLATTEN,
    ACTION_PAUSE,
    ACTION_RESUME,
    ACTION_STANDBY,
    ACTION_STOP,
    RESULT_ARMED,
    RESULT_CANCELLING,
    RESULT_FLAT,
    RESULT_FLATTENING,
    RESULT_IGNORED,
    RESULT_LINK_DOWN,
    RESULT_PAUSED,
    RESULT_RELEASED,
    RESULT_RESUMED,
    RESULT_STUCK,
    RESULT_SUSPENDED,
    STAGE_ACK,
    STAGE_DONE,
    Command,
    CommandResult,
    OrderRequest,
    Side,
    Tick,
    require_taipei,
)
from autotrade.oms import OrderManager
from autotrade.risk import RiskManager, is_reducing
from autotrade.strategy.base import Strategy


class EndOfDayError(RuntimeError):
    """收盤時還有部位或還有沒結束的委託。"""


class RecoveryError(RuntimeError):
    """從日誌還原狀態時發現不能直接接手的情況。"""


@dataclass(frozen=True)
class Event:
    """引擎記下的一件事。text 給人看，data 給程式還原狀態用（journal 落地）。"""

    ts: datetime
    kind: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Restored:
    """從日誌還原出來、要交給引擎接手的狀態。"""

    day: date
    position: int
    avg_price: float
    realized_twd: float
    costs_twd: float
    day_start_realized: float
    entries_today: int
    halt: str | None
    open_orders: tuple[str, ...]
    # 今天處理過的遠端指令（A-011）：（編號、按下的時間、有沒有執行）。只記當天的，不跨日
    stops_today: tuple[tuple[int, str, bool], ...] = ()


@dataclass
class _StopTrack:
    """一筆遠端指令處理到哪裡了（停止與平倉要追蹤到確認沒有部位）。"""

    created_at: datetime
    applied: bool  # False＝沒有執行（不是今天的、不認得的動作）
    applied_at: datetime | None  # 重啟還原的要等第一次檢查才補上
    done: str | None = None  # 最後一次回報的收尾結果
    action: str = ACTION_STOP
    flat: bool = False  # 平倉：部位平掉的那一刻記下來（之後策略可能馬上又進場，不能等回報時才看）


@dataclass
class Engine:
    """一個商品、一個策略的交易引擎。"""

    cfg: Config
    strategy: Strategy
    broker: Broker
    ledger: Ledger = field(init=False)
    risk: RiskManager = field(init=False)
    oms: OrderManager = field(init=False)
    sink: Callable[[Event], None] | None = None
    events: list[Event] = field(default_factory=list)
    _day: date | None = None
    _day_start_realized: float = 0.0
    _cancel_sent: set[str] = field(default_factory=set)
    _strategy_orders: dict[str, OrderRequest] = field(default_factory=dict)
    _last: Tick | None = None
    _link_up: bool = True  # 跟券商的連線；斷線期間不送單也不刪單
    _strategy_off: bool = False  # 部位對不上之後策略自己記的狀態不可信，不再問它
    _stop_fired: bool = False
    _stops: dict[int, _StopTrack] = field(default_factory=dict)  # 今天處理過的遠端指令
    _stops_day: date | None = None
    _control_out: list[CommandResult] = field(default_factory=list)  # 還沒交出去的回報
    _paused: bool = False  # 暫停自動交易（A-016）：策略的進場單不送，出場照常
    _pause_noted: bool = False  # 這次暫停已經記過「略過進場」了（只記第一次，不洗版）
    _flat_asked: bool = False  # 有人要求平倉（不停手）：平掉之前策略的進場單也不送
    # 待命／運作（A-019）：待命時策略的進場單不送，出場照常。單獨跑（重播、舊的 run）一律是運作
    _armed: bool = True

    def __post_init__(self) -> None:
        self.ledger = Ledger(self.cfg.contract)
        self.risk = RiskManager(self.cfg.risk)
        self.oms = OrderManager()

    @property
    def link_up(self) -> bool:
        """跟券商的連線是不是通的。"""
        return self._link_up

    @property
    def paused(self) -> bool:
        """自動交易是不是暫停中（不再進新單）。"""
        return self._paused

    @property
    def armed(self) -> bool:
        """是不是運作中（False＝待命：不進新單）。"""
        return self._armed

    @property
    def last_tick(self) -> Tick | None:
        """最新一筆行情；還沒收到是 None。"""
        return self._last

    @property
    def last_command_id(self) -> int:
        """今天處理過的遠端指令裡最大的編號（還沒有是 0）。"""
        return max(self._stops, default=0)

    @property
    def day(self) -> date | None:
        """目前的交易日；還沒收到行情是 None。"""
        return self._day

    def _log(self, ts: datetime, kind: str, text: str, **data: Any) -> None:
        event = Event(ts, kind, text, data)
        self.events.append(event)
        if self.sink is not None:
            self.sink(event)

    def restore(self, state: Restored) -> None:
        """接手從日誌還原的狀態。還有結果不明的委託就拒絕，要先到券商確認。"""
        if state.open_orders:
            raise RecoveryError(
                f"日誌裡有結果不明的委託 {list(state.open_orders)}，先到券商確認成交或刪單再啟動"
            )
        if self.events or self.ledger.fills:
            raise RecoveryError("引擎已經開始運作，不能再還原狀態")
        self.ledger.position = state.position
        self.ledger.avg_price = state.avg_price
        self.ledger.realized_twd = state.realized_twd
        self.ledger.costs_twd = state.costs_twd
        self.risk.day = state.day
        self.risk.entries_today = state.entries_today
        self.risk.halt = state.halt
        self._day = state.day
        self._day_start_realized = state.day_start_realized
        self.ledger.reconcile(self.broker.position())
        self._stops_day = state.day
        for command_id, created_at, applied in state.stops_today:
            track = _StopTrack(datetime.fromisoformat(created_at), applied, None)
            self._stops[command_id] = track
            if not applied:
                continue
            # 當機前可能來不及回報：重啟後再報一次（網站那邊重複的回報不會覆蓋第一筆）
            code, text = self._stop_state()
            self._control_out.append(
                CommandResult(command_id, STAGE_ACK, code, f"{text}（程式重新啟動後補報）")
            )
            if state.halt is None:
                track.done = RESULT_RELEASED
                self._control_out.append(
                    CommandResult(
                        command_id, STAGE_DONE, RESULT_RELEASED, "重新啟動前已經在電腦上解除"
                    )
                )

    def day_pnl(self, last_price: float) -> float:
        """當日損益：當日已實現＋未實現。"""
        return (
            self.ledger.realized_twd - self._day_start_realized + self.ledger.open_pnl(last_price)
        )

    def _tell_reject(self, req: OrderRequest, reason: str) -> None:
        if not self._strategy_off:
            self.strategy.on_reject(req, reason)

    def _log_halt_change(self, now: datetime, before: str | None, text: str) -> None:
        if self.risk.halt != before:
            self._log(now, "halt", f"{text}（{self.risk.halt}）", halt=self.risk.halt)

    def _submit(self, req: OrderRequest, now: datetime, from_strategy: bool = False) -> bool:
        if not self._link_up:
            self._log(now, "reject", f"連線中斷，不送 {req.side.name} {req.qty} 口")
            if from_strategy:
                self._tell_reject(req, "連線中斷")
            return False
        exposure = self.ledger.position + self.oms.pending_signed_qty()
        quiet = "暫停" if self._paused else None if self._armed else "待命"
        blocked = from_strategy and (quiet is not None or self._flat_asked)
        if blocked and not is_reducing(exposure, req.signed_qty):
            why = {"暫停": "自動交易暫停中", "待命": "待命中（還沒按啟動）"}.get(
                quiet or "", "正在平倉"
            )
            if quiet is None or not self._pause_noted:
                self._pause_noted = quiet is not None
                more = f"（{quiet}期間之後的不再一一記錄）" if quiet else ""
                self._log(now, "reject", f"{why}，不送進場單 {req.side.name} {req.qty} 口{more}")
            self._tell_reject(req, why)
            return False
        before = self.risk.halt
        decision = self.risk.check(req, exposure, now)
        self._log_halt_change(now, before, "暫停送單")
        if not decision.allowed:
            self._log(now, "reject", f"風控擋下 {req.side.name} {req.qty} 口：{decision.reason}")
            if from_strategy:
                self._tell_reject(req, decision.reason)
            return False
        order_id = self.broker.submit(req)
        self.risk.on_order_sent(now)
        self.oms.add(order_id, req)
        if from_strategy:
            self._strategy_orders[order_id] = req
        entry = not is_reducing(exposure, req.signed_qty)
        if entry:
            self.risk.on_entry_sent()
        self._log(
            now,
            "order",
            f"{order_id} {req.side.name} {req.qty} 口 {req.reason}".rstrip(),
            order_id=order_id,
            side=req.side.name,
            qty=req.qty,
            limit_price=req.limit_price,
            entry=entry,
        )
        return True

    def _cancel(self, order_id: str, now: datetime, why: str) -> None:
        if self._link_up and order_id not in self._cancel_sent:
            self._cancel_sent.add(order_id)
            self.broker.cancel(order_id)
            self._log(now, "cancel", f"{order_id} 刪單：{why}", order_id=order_id)

    def _flatten(self, now: datetime, why: str) -> None:
        """刪掉還沒成交的新倉單，並把部位平掉（一次最多送單筆上限的口數）。斷線或暫停送單時不動作。"""
        if not self._link_up or self.risk.suspended:
            return
        for order in self.oms.working():
            if not is_reducing(self.ledger.position, order.remaining_signed_qty):
                self._cancel(order.order_id, now, why)
        exposure = self.ledger.position + sum(
            o.remaining_signed_qty
            for o in self.oms.working()
            if is_reducing(self.ledger.position, o.remaining_signed_qty)
        )
        if exposure != 0:
            side = Side.SELL if exposure > 0 else Side.BUY
            qty = min(abs(exposure), self.cfg.risk.max_order_qty)
            req = OrderRequest(
                ts=now, symbol=self.cfg.contract.symbol, side=side, qty=qty, reason=why
            )
            self._submit(req, now)

    def kill(self, now: datetime) -> bool:
        """緊急停止：停手並平倉，要人工解除。本機按與遠端按都走這裡。

        連線通的時候先問券商實際的部位再平：人可能已經用券商的 App 平掉了，照帳上的數字再賣會變成放空。
        回傳帳是不是被改成券商的數字。
        """
        # 先停手、再問券商：查部位出錯的話，至少已經停了
        self.risk.kill()
        self._log(now, "halt", "緊急停止", halt=self.risk.halt)
        corrected = self._link_up and not self._reconcile(now)
        self.risk.kill()  # 對不上時停手原因會被改成「對不上」；這裡是人按的緊急停止，改回來
        self._flatten(now, "緊急停止")
        return corrected

    def _position_text(self) -> str:
        position = self.ledger.position
        if position == 0:
            return "帳上沒有部位"
        return f"帳上{'多' if position > 0 else '空'} {abs(position)} 口"

    def _stop_state(self, halted: bool = True) -> tuple[str, str]:
        """停手（或要求平倉，halted=False）之後當下的狀況：（代碼、給人看的一句話）。"""
        where = self._position_text()
        if self.risk.suspended:
            return RESULT_SUSPENDED, f"程式在暫停送單，不會自動平倉，請手動處理；{where}"
        if not self._link_up:
            lead = "已停手；" if halted else ""
            return RESULT_LINK_DOWN, f"{lead}券商連線中斷，恢復後才會平倉；{where}"
        lead = "已停手，" if halted else ""
        if self.ledger.position != 0:
            return RESULT_FLATTENING, f"{lead}已送出平倉；{where}"
        if self.oms.working():
            return RESULT_CANCELLING, f"{lead}已送出刪單；{where}"
        return RESULT_FLAT, f"已停手；{where}" if halted else "本來就沒有部位、沒有委託"

    def _roll_stops(self, today: date) -> None:
        if self._stops_day != today:
            self._stops, self._stops_day = {}, today
            # 遙控指令只算當天：換日時暫停、沒做完的平倉要求都不帶過去
            self._paused = self._pause_noted = self._flat_asked = False

    def flatten_now(self, now: datetime) -> bool:
        """平倉但不停手（A-016）：刪掉還沒成交的新倉單、把部位平掉；策略照跑，平掉之前不讓它再進場。

        跟 kill 一樣先問券商實際的部位。回傳帳是不是被改成券商的數字（那種情況引擎會自己停手）。
        """
        corrected = self._link_up and not self._reconcile(now)
        self._flat_asked = True
        self._flatten(now, "遠端平倉")
        return corrected

    def pause(self) -> bool:
        """暫停自動交易（A-016）：策略的進場單不送；出場、停損、強制平倉照常。回傳是不是本來就暫停了。"""
        was, self._paused = self._paused, True
        if not was:
            self._pause_noted = False
        return was

    def resume(self) -> bool:
        """恢復自動交易。不會解除任何停手。回傳本來是不是暫停中。"""
        was, self._paused = self._paused, False
        return was

    def arm(self) -> bool:
        """啟動（A-019）：待命 → 運作。不會解除任何停手或暫停。回傳是不是本來就在運作。"""
        was, self._armed = self._armed, True
        return was

    def disarm(self) -> bool:
        """切到待命，不動部位（每天一開始用）。回傳本來是不是運作中。"""
        was, self._armed = self._armed, False
        if was:
            self._pause_noted = False
        return was

    def stand_by(self, now: datetime) -> bool:
        """停止（A-019）：先平倉、再待命。跟 flatten_now 一樣先問券商的部位；回傳帳是不是被改成券商的數字。"""
        self.disarm()
        return self.flatten_now(now)

    def _note_command(self, cmd: Command, now: datetime, what: str) -> str:
        self._log(
            now,
            "control",
            f"收到{what}（第 {cmd.id} 號，{cmd.created_at:%H:%M:%S} 按下）",
            command_id=cmd.id,
            created_at=cmd.created_at.isoformat(),
            action=cmd.action,
            applied=True,
        )
        return f"（{cmd.created_at:%H:%M:%S} 按下，{now:%H:%M:%S} 執行）"

    def _apply_flatten(self, cmd: Command, now: datetime) -> None:
        corrected = self.flatten_now(now)
        when = self._note_command(cmd, now, "遠端平倉")
        self._stops[cmd.id] = _StopTrack(cmd.created_at, True, now, action=ACTION_FLATTEN)
        code, text = self._stop_state(halted=False)
        prefix = "券商的部位跟帳上不一樣，已改用券商的數字並停手；" if corrected else ""
        self._control_out.append(CommandResult(cmd.id, STAGE_ACK, code, f"{prefix}{text}{when}"))

    def _apply_pause(self, cmd: Command, now: datetime) -> None:
        was = self.pause()
        when = self._note_command(cmd, now, "遠端暫停自動交易")
        text = "本來就在暫停中" if was else "已暫停自動交易：不再進新單，手上的部位照原本的規則出場"
        self._finish_at_once(cmd, now, RESULT_PAUSED, f"{text}；{self._position_text()}{when}")

    def _apply_resume(self, cmd: Command, now: datetime) -> None:
        was = self.resume()
        when = self._note_command(cmd, now, "遠端恢復自動交易")
        text = "已恢復自動交易" if was else "本來就沒有暫停"
        if self.risk.halt is not None:
            text += f"；但程式目前是停手狀態（{self.risk.halt}），恢復不會解除停手"
        elif self._strategy_off:
            text += "；但策略已經關閉（部位被修正過），要重新啟動程式才會再交易"
        self._finish_at_once(cmd, now, RESULT_RESUMED, f"{text}{when}")

    def _apply_arm(self, cmd: Command, now: datetime) -> None:
        was = self.arm()
        when = self._note_command(cmd, now, "遠端啟動")
        text = "本來就在運作中" if was else "已啟動：開始自動交易"
        if self.risk.halt is not None:
            text += f"；但程式目前是停手狀態（{self.risk.halt}），啟動不會解除停手"
        elif self._strategy_off:
            text += "；但策略已經關閉（部位被修正過），要重新啟動程式才會再交易"
        elif self._paused:
            text += "；但自動交易還在暫停中，要按「恢復」才會進新單"
        self._finish_at_once(cmd, now, RESULT_ARMED, f"{text}{when}")

    def _apply_standby(self, cmd: Command, now: datetime) -> None:
        corrected = self.stand_by(now)
        when = self._note_command(cmd, now, "遠端停止（平倉後待命）")
        self._stops[cmd.id] = _StopTrack(cmd.created_at, True, now, action=ACTION_STANDBY)
        code, text = self._stop_state(halted=False)
        prefix = "券商的部位跟帳上不一樣，已改用券商的數字並停手；" if corrected else ""
        self._control_out.append(
            CommandResult(cmd.id, STAGE_ACK, code, f"{prefix}已切到待命，不再進新單；{text}{when}")
        )

    def _finish_at_once(self, cmd: Command, now: datetime, code: str, text: str) -> None:
        self._stops[cmd.id] = _StopTrack(cmd.created_at, True, now, done=code, action=cmd.action)
        self._control_out.append(CommandResult(cmd.id, STAGE_ACK, code, text))
        self._control_out.append(CommandResult(cmd.id, STAGE_DONE, code, text))

    def _apply_stop(self, cmd: Command, now: datetime) -> None:
        # 先停手（日誌先有 halt），再記「這筆指令處理過了」：中間當機的話，重啟後是「還沒處理」而不是「處理了卻沒停」
        corrected = self.kill(now)
        self._log(
            now,
            "control",
            f"收到遠端緊急停止（第 {cmd.id} 號，{cmd.created_at:%H:%M:%S} 按下）",
            command_id=cmd.id,
            created_at=cmd.created_at.isoformat(),
            action=cmd.action,
            applied=True,
        )
        self._stops[cmd.id] = _StopTrack(cmd.created_at, True, now)
        code, text = self._stop_state()
        prefix = "券商的部位跟帳上不一樣，已改用券商的數字；" if corrected else ""
        when = f"（{cmd.created_at:%H:%M:%S} 按下，{now:%H:%M:%S} 執行）"
        self._control_out.append(CommandResult(cmd.id, STAGE_ACK, code, f"{prefix}{text}{when}"))

    def _ignore_command(self, cmd: Command, now: datetime, why: str) -> None:
        self._log(
            now,
            "control",
            f"遠端指令第 {cmd.id} 號{why}",
            command_id=cmd.id,
            created_at=cmd.created_at.isoformat(),
            action=cmd.action,
            applied=False,
        )
        self._stops[cmd.id] = _StopTrack(cmd.created_at, False, now, action=cmd.action)
        self._control_out.append(CommandResult(cmd.id, STAGE_ACK, RESULT_IGNORED, why))

    def _follow_stops(self, now: datetime) -> None:
        """追蹤每一筆執行過的停止，直到確認沒有部位；平不掉就回報要人處理。"""
        timeout = None if self.cfg.control is None else self.cfg.control.flatten_timeout_s
        where = self._position_text()
        for command_id, track in self._stops.items():
            if track.action not in (ACTION_STOP, ACTION_FLATTEN, ACTION_STANDBY):
                continue  # 暫停／恢復／啟動一執行就回報完了
            if not track.applied or track.done in (RESULT_FLAT, RESULT_RELEASED):
                continue
            if track.applied_at is None:
                track.applied_at = now
            waited = (now - track.applied_at).total_seconds()
            flat_now = self._link_up and self.ledger.position == 0 and not self.oms.working()
            if track.flat or flat_now:
                done, text = RESULT_FLAT, f"已確認沒有部位、沒有委託；{where}"
            elif track.done is not None:
                continue
            elif self.risk.suspended:
                done, text = RESULT_STUCK, f"程式在暫停送單，部位不會自己平掉，請手動處理；{where}"
            elif timeout is not None and waited > timeout:
                done, text = RESULT_STUCK, f"過了 {waited:.0f} 秒還沒確認平掉，請手動處理；{where}"
            else:
                continue
            track.done = done
            self._control_out.append(CommandResult(command_id, STAGE_DONE, done, text))

    def on_control(self, commands: Sequence[Command], now: datetime) -> list[CommandResult]:
        """套用遠端指令並回傳要回報給網站的結果（A-011、A-016）。停止等同 kill；另有平倉（不停手）、暫停、恢復。

        沒有任何解除停手的動作：恢復只解除「暫停」。

        要用時鐘定時呼叫（沒有新指令就傳空的）：它同時負責追蹤先前的停止有沒有真的平掉。
        """
        now = require_taipei(now, "on_control 的 now")
        self._roll_stops(now.date())
        for cmd in sorted(commands, key=lambda c: c.id):
            known = self._stops.get(cmd.id)
            if known is not None and known.created_at == cmd.created_at:
                continue  # 今天處理過了，結果已經在回報的路上
            if cmd.day != now.date():
                self._ignore_command(cmd, now, f"是 {cmd.day} 的指令，不是今天的，沒有執行")
            elif cmd.action == ACTION_STOP:
                self._apply_stop(cmd, now)
            elif cmd.action == ACTION_FLATTEN:
                self._apply_flatten(cmd, now)
            elif cmd.action == ACTION_PAUSE:
                self._apply_pause(cmd, now)
            elif cmd.action == ACTION_RESUME:
                self._apply_resume(cmd, now)
            elif cmd.action == ACTION_ARM:
                self._apply_arm(cmd, now)
            elif cmd.action == ACTION_STANDBY:
                self._apply_standby(cmd, now)
            else:
                self._ignore_command(cmd, now, f"不認得的動作 {cmd.action!r}，沒有執行")
        self._follow_stops(now)
        out, self._control_out = self._control_out, []
        # 每筆結果帶上那筆指令按下的時間：回報的時候靠它確認是同一筆
        return [
            replace(
                r,
                created_at=self._stops[r.command_id].created_at,
                action=self._stops[r.command_id].action,
                after_tick=None if self._last is None else self._last.ts,
            )
            if r.command_id in self._stops
            else r
            for r in out
        ]

    def note_control_link(self, now: datetime, *, up: bool, detail: str) -> None:
        """遠端停止這條路通不通變了（由 ControlLoop 呼叫）。只記事件，不動交易。"""
        self._log(require_taipei(now, "note_control_link 的 now"), "control_link", detail, up=up)

    def release_kill(self, now: datetime) -> None:
        """人工解除：緊急停止、部位對不上、暫停送單。只能在跑交易程式的電腦上做。"""
        self.risk.release_kill()
        self._log(now, "release", "人工解除停手", halt=self.risk.halt)
        if self.risk.halt is not None:
            return
        # 部位被修正過（例如人已經手動平掉）之後策略是關著的：解除了也不會再交易，不能說「可以再交易」
        after = (
            "但部位被修正過、策略已經關閉，要重新啟動程式才會再交易"
            if self._strategy_off
            else "程式可以再交易"
        )
        for command_id, track in self._stops.items():
            # 解除的是「停止」；平倉、暫停那幾筆的回報不動
            if track.action == ACTION_STOP and track.applied and track.done != RESULT_RELEASED:
                track.done = RESULT_RELEASED
                self._control_out.append(
                    CommandResult(
                        command_id,
                        STAGE_DONE,
                        RESULT_RELEASED,
                        f"已於 {now:%H:%M:%S} 在電腦上解除，{after}",
                    )
                )

    def _handle_events(self, events: list[BrokerEvent], now: datetime) -> bool:
        """套用券商回報；有成交回傳 True。"""
        got_fill = False
        for event in events:
            fill = self.oms.apply(event)
            if isinstance(event, Ack | Filled):
                self.risk.on_broker_ok()
            if fill is not None:
                self.ledger.apply(fill)
                if not self._strategy_off:
                    self.strategy.on_fill(fill)
                self._log(
                    now,
                    "fill",
                    f"{event.order_id} {fill.side.name} {fill.qty} 口 {fill.price}",
                    order_id=event.order_id,
                    symbol=fill.symbol,
                    side=fill.side.name,
                    qty=fill.qty,
                    price=fill.price,
                )
                got_fill = True
            elif isinstance(event, Cancelled):
                self._log(now, "cancelled", f"{event.order_id} 已刪單", order_id=event.order_id)
                if event.order_id in self._strategy_orders:
                    self._tell_reject(self._strategy_orders[event.order_id], "已刪單")
            elif isinstance(event, Rejected):
                self._log(
                    now,
                    "broker_reject",
                    f"{event.order_id} 被券商拒絕：{event.reason}",
                    order_id=event.order_id,
                )
                before = self.risk.halt
                self.risk.on_broker_reject()
                self._log_halt_change(
                    now, before, f"連續 {self.risk.broker_rejects} 筆被券商拒絕，暫停送單"
                )
                if event.order_id in self._strategy_orders:
                    self._tell_reject(self._strategy_orders[event.order_id], event.reason)
        return got_fill

    def _reconcile(self, now: datetime) -> bool:
        """跟券商部位對帳。對不上：帳改成券商的數字、停手、不再問策略，之後由停手流程平倉。"""
        broker_position = self.broker.position()
        if broker_position == self.ledger.position:
            return True
        price = self._last.price if self._last is not None else 0.0
        was = self.ledger.position
        self.ledger.adopt(broker_position, price)
        self._strategy_off = True
        self._log(
            now,
            "correction",
            f"部位對不上：帳上 {was} 口、券商 {broker_position} 口；帳改成券商的數字，停手並平倉",
            position=broker_position,
            avg_price=self.ledger.avg_price,
            ledger_position=was,
        )
        before = self.risk.halt
        self.risk.halt_mismatch()
        self._log_halt_change(now, before, "部位對不上，停手")
        return False

    def on_disconnect(self, now: datetime) -> None:
        """跟券商的連線斷了：之後不送單、不刪單，直到 on_reconnect。"""
        if self._link_up:
            self._link_up = False
            self._log(now, "link", "券商連線中斷：暫停送單與刪單", up=False)

    def on_reconnect(self, now: datetime) -> None:
        """重連：先補回漏掉的回報，再比對委託與部位。斷線前送出的委託一律不重送。"""
        self._link_up = True
        self._log(now, "link", "券商連線恢復：補回報並比對委託與部位", up=True)
        self._handle_events(self.broker.resync(), now)
        at_broker = self.broker.open_order_ids()
        mine = {o.order_id for o in self.oms.working()}
        for order_id in sorted(mine - at_broker):
            self.oms.apply(Cancelled(order_id, now))
            self._log(
                now,
                "cancelled",
                f"{order_id} 重連後券商沒有這張委託，視為沒送到（不重送）",
                order_id=order_id,
            )
            if order_id in self._strategy_orders:
                self._tell_reject(self._strategy_orders[order_id], "重連後券商沒有這張委託")
        foreign = sorted(at_broker - mine)
        for order_id in foreign:
            self.broker.cancel(order_id)
            self._log(now, "foreign_order", f"券商端有不認得的委託 {order_id}，已送刪單")
        matched = self._reconcile(now)
        if foreign and matched:
            before = self.risk.halt
            self.risk.halt_mismatch()
            self._log_halt_change(now, before, "券商端有不認得的委託，停手")
        if self.risk.halt is not None:
            # 停手中（例如斷線時收到遠端停止）：連線一恢復就平倉，不等下一筆行情
            self._flatten(now, f"停手（{self.risk.halt}）")

    def on_tick(self, tick: Tick) -> None:
        """處理一筆行情。"""
        now = tick.ts
        if tick.ts.date() != self._day:
            self._day = tick.ts.date()
            self._day_start_realized = self.ledger.realized_twd
        self._last = tick
        self.risk.on_quote(now)

        if self._handle_events(self.broker.on_tick(tick), now):
            self._reconcile(now)

        was_halted = self.risk.halt
        self.risk.update_pnl(self.day_pnl(tick.price), now)
        if self.risk.halt is not None and was_halted is None:
            self._log(
                now,
                "halt",
                f"停手（{self.risk.halt}）：當日損益 {self.day_pnl(tick.price):+.0f} 元",
                halt=self.risk.halt,
            )

        self._cancel_stale(now)

        if self.risk.halt is not None:
            self._flatten(now, f"停手（{self.risk.halt}）")
        elif now.time() >= self.cfg.risk.force_flat_time:
            self._flatten(now, "強制平倉時間")
        elif self._flat_asked:
            self._flatten(now, "遠端平倉")
        elif self._stop_hit(tick.price):
            if not self._stop_fired:
                self._stop_fired = True
                self._log(
                    now,
                    "stop",
                    f"單一部位未實現損益 {self.ledger.open_pnl(tick.price):+.0f} 元，"
                    f"達停損 {self.cfg.risk.position_stop_loss_twd} 元，系統平倉",
                )
            self._flatten(now, "單一部位停損")
        if self.ledger.position == 0:
            self._stop_fired = False
            if self._flat_asked and not self.oms.working():
                self._flat_asked = False  # 平掉了：策略可以再進場
                for track in self._stops.values():
                    if track.action in (ACTION_FLATTEN, ACTION_STANDBY) and track.done is None:
                        track.flat = True

        if not self._strategy_off:
            for req in self.strategy.on_tick(tick, self.ledger.position):
                self._submit(req, now, from_strategy=True)

    def _cancel_stale(self, now: datetime) -> None:
        """太久沒成交的委託送刪單；卡住的是平倉單就停手。"""
        for order in self.oms.stale(now, self.cfg.risk.order_timeout_s):
            if order.order_id in self._cancel_sent or not self._link_up:
                continue
            exit_order = is_reducing(self.ledger.position, order.remaining_signed_qty)
            self._cancel(order.order_id, now, f"超過 {self.cfg.risk.order_timeout_s} 秒沒成交")
            if exit_order:
                # 平倉單卡住代表券商或連線有問題：停手，刪單回報後由 _flatten 重送市價平倉
                self.risk.kill()
                self._log(
                    now, "halt", f"平倉單 {order.order_id} 逾時，緊急停止", halt=self.risk.halt
                )

    def on_timer(self, now: datetime) -> None:
        """用時鐘叫（不靠行情）：停手中或過了強制平倉時間就平倉。行情停住時靠它把停止做完。

        逾時刪單不在這裡做：委託的時間是行情的時間，這裡是電腦的時鐘，兩個鐘差幾秒就會誤刪剛送出的委託。
        """
        now = require_taipei(now, "on_timer 的 now")
        if self.risk.halt is not None:
            self._flatten(now, f"停手（{self.risk.halt}）")
        elif now.time() >= self.cfg.risk.force_flat_time:
            self._flatten(now, "強制平倉時間")
        elif self._flat_asked:
            self._flatten(now, "遠端平倉")

    def _stop_hit(self, price: float) -> bool:
        stop = self.cfg.risk.position_stop_loss_twd
        if stop is None or self.ledger.position == 0:
            return False
        return self._stop_fired or self.ledger.open_pnl(price) <= -stop

    def finish(self) -> dict[str, float | int]:
        """收盤：確認沒有部位、沒有活著的委託，回傳當日摘要。"""
        working = [o.order_id for o in self.oms.working()]
        if self.ledger.position != 0 or working:
            raise EndOfDayError(f"收盤時部位 {self.ledger.position} 口、還有委託 {working}")
        self.ledger.reconcile(self.broker.position())
        return {
            "fills": len(self.ledger.fills),
            "realized_twd": self.ledger.realized_twd - self._day_start_realized,
            "costs_twd": self.ledger.costs_twd,
            "entries": self.risk.entries_today,
        }
