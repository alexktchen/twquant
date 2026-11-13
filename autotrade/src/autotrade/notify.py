"""把引擎事件變成給人看的手機通知：白話、分級、同一件事不洗版。這裡不連網路，送出在 delivery。"""

from __future__ import annotations

import logging
import re
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import Enum

from autotrade.config import Config, ConfigError, ContractConfig, RiskConfig
from autotrade.delivery import Failure, Outbox, Outgoing
from autotrade.engine import Event, Restored
from autotrade.ledger import Ledger
from autotrade.model import Fill, Side, require_taipei
from autotrade.risk import (
    HALT_BROKER_REJECT,
    HALT_DAILY_LOSS,
    HALT_KILL,
    HALT_MISMATCH,
    HALT_ORDER_RATE,
    SUSPENDED,
)

log = logging.getLogger(__name__)


class Level(Enum):
    """通知的輕重。數字越大越要緊。"""

    INFO = 0  # 一般：成交、解除、連線恢復
    WARN = 1  # 注意：出了狀況，程式自己處理完了
    ACT = 2  # 要處理：程式停下來等人，或人要去看部位


MARK = {Level.INFO: "", Level.WARN: "⚠️ 注意", Level.ACT: "🚨 要處理"}
MODE_TEXT = {"sim": "模擬", "live": "真單"}
SIDE_TEXT = {"BUY": "買進", "SELL": "賣出"}
# 停手原因（risk.py 的代碼）→ 白話。網站另有一份（frontend/src/cloud/autotrade.ts 的 HALT_SHORT）
HALT_SHORT = {
    HALT_DAILY_LOSS: "當日虧損到上限",
    HALT_KILL: "緊急停止",
    HALT_MISMATCH: "跟券商對不上",
    HALT_ORDER_RATE: "送單太頻繁",
    HALT_BROKER_REJECT: "連續被券商拒絕",
}
# 引擎事件的種類 → 白話名稱（通知內容產生失敗時，至少說得出是哪一種事）
KIND_LABEL = {
    "fill": "成交",
    "halt": "停手",
    "release": "解除",
    "stop": "停損",
    "correction": "部位修正",
    "link": "券商連線",
    "broker_reject": "被券商拒絕",
    "foreign_order": "不認得的委託",
    "reject": "沒有送出的委託",
    "order": "送出委託",
    "cancel": "送出刪單",
    "cancelled": "刪單完成",
    "control": "遠端緊急停止",
    "control_link": "遠端緊急停止的連線",
}
UNLOCK = "要在電腦上解除，程式才會再交易。"

_CODE = re.compile(r"（([a-z_]+)）")
_SIDE = re.compile(r"\b(BUY|SELL)\b")
_POINT_ZERO = re.compile(r"(\d)\.0(?!\d)")
_ASCII = re.compile(r"[A-Za-z]")
_VARYING = re.compile(r"[\d.:]+")
_DAY_PNL = re.compile(r"當日損益 ([+-]?[\d.]+) 元")
_REJECT_COUNT = re.compile(r"連續 (\d+) 筆被券商拒絕")
_STALE_EXIT = re.compile(r"平倉單 \S+ 逾時")
_STOP = re.compile(r"未實現損益 ([+-]?[\d.]+) 元，達停損 ([\d.]+) 元")
_BROKER_SAID = re.compile(r"被券商拒絕：(.*)$", re.S)
_FOREIGN_ID = re.compile(r"不認得的委託 (\S+?)，")
_UNSENT = re.compile(r"連線中斷，不送 (BUY|SELL) (\d+) 口")
_BLOCKED = re.compile(r"風控擋下 (BUY|SELL) (\d+) 口：(.*)$", re.S)
_ORDER_WHY = re.compile(r"^\S+ (?:BUY|SELL) \d+ 口 ?(.*)$", re.S)
_CANCEL_WHY = re.compile(r"刪單：(.*)$", re.S)


def plain(text: str) -> str:
    """把引擎事件文字裡的內部代碼換成白話；不認得的代碼留著並標明是代碼。"""

    def code(match: re.Match[str]) -> str:
        known = HALT_SHORT.get(match[1])
        return f"（{known}）" if known else f"（代碼 {match[1]}，通知還不認得）"

    text = _CODE.sub(code, text)
    text = _SIDE.sub(lambda m: SIDE_TEXT[m[1]], text)
    return _POINT_ZERO.sub(r"\1", text)


def num(value: float) -> str:
    """數字：千分位，小數最多兩位、不留多餘的 0。"""
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def signed(value: float) -> str:
    """帶正負號的金額；0 就寫 0。"""
    value = round(value, 2)
    if value == 0:
        return "0"
    return ("+" if value > 0 else "-") + num(abs(value))


def position_text(position: int) -> str:
    """部位：空手／多 n 口／空 n 口。"""
    if position == 0:
        return "空手"
    return f"{'多' if position > 0 else '空'} {abs(position)} 口"


def where_of(cfg: Config, replay: bool) -> str:
    """每則通知開頭的標籤，例：「【模擬】微台」；重播的會標出來。"""
    if cfg.notify is None:
        raise ConfigError("設定檔沒有 [notify]，不能開通知")
    return f"【{MODE_TEXT[cfg.mode]}{'重播' if replay else ''}】{cfg.notify.name}"


@dataclass(frozen=True)
class Message:
    """一則通知。key 相同算同一類（節流用）；None＝每一則都送（成交）。"""

    ts: datetime
    kind: str
    level: Level
    key: str | None
    once: bool  # 同一類在視窗內只即時通知一次（被擋下這種會一直重複的事）
    where: str
    headline: str
    lines: tuple[str, ...]
    merged: int = 1  # 這一則代表幾件事（合併過的大於 1）

    @property
    def title(self) -> str:
        """第一行：手機鎖定畫面上看到的那一句。"""
        return f"{MARK[self.level]}{self.where}｜{self.headline}"

    @property
    def text(self) -> str:
        """整則通知的文字。"""
        return "\n".join([self.title, f"時間 {self.ts:%m/%d %H:%M:%S}", *self.lines])


def sample(now: datetime, level: Level, where: str) -> Message:
    """notify-test 用的測試通知。"""
    lines = {
        Level.INFO: (
            "你看到這一則，代表程式交易的通知送得到這支手機。",
            "一般的通知（例如成交）長這樣。",
        ),
        Level.WARN: (
            "出了狀況、但程式自己處理完的事，第一行會像這樣標「注意」。",
            "這一則是測試，不用處理。",
        ),
        Level.ACT: (
            "要你處理的事，第一行會像這樣標「要處理」。",
            "這一則是測試，不用處理。",
        ),
    }[level]
    return Message(now, "test", level, None, False, where, "測試通知", lines)


@dataclass(frozen=True)
class OrderInfo:
    """一張還活著的委託（從 order 事件記下來的）。"""

    side: str
    qty: int
    entry: bool  # 新倉單（不是只減少部位的單）
    limit_price: float | None


@dataclass(frozen=True)
class Seen:
    """一件事發生之後，寫通知要用到的帳上數字。"""

    position: int
    avg_price: float
    day_realized_twd: float  # 當日已實現損益（含成本）
    corrected: bool  # 今天部位有沒有被修正過
    open_orders: int  # 還沒有結果的委託張數
    open_exits: int  # 其中平倉單（只減少部位的單）的張數
    order: OrderInfo | None = None  # 這件事講的那張委託（認得的話）
    pnl_twd: float | None = None  # 成交：這筆之後多出來的已實現損益（含這筆的成本）
    cost_twd: float | None = None  # 成交：這筆的成本
    closing: bool = False  # 成交：有沒有平掉原本的部位
    trip_twd: float | None = None  # 成交後回到空手：從進場到出場這一趟的淨損益（成本都扣了）


class Book:
    """通知用的小帳：跟著事件記部位、損益與還活著的委託，才寫得出「這筆賺賠」「帳上還有沒有部位」。

    跟 journal.recover 用同一種算法（成交照記、部位修正照改），所以跟引擎的帳一致。
    """

    def __init__(self, contract: ContractConfig, restored: Restored | None = None) -> None:
        self.ledger = Ledger(contract)
        self.day: date | None = None
        self.day_start_realized = 0.0
        self.corrected_on: date | None = None
        self.orders: dict[str, OrderInfo] = {}
        self._left: dict[str, int] = {}
        # 這一趟（空手 → 有部位 → 空手）開始前的已實現損益；不知道從哪裡起算時是 None
        self._trip_start: float | None = None
        if restored is not None:
            self.ledger.position = restored.position
            self.ledger.avg_price = restored.avg_price
            self.ledger.realized_twd = restored.realized_twd
            self.ledger.costs_twd = restored.costs_twd
            self.day = restored.day
            self.day_start_realized = restored.day_start_realized

    @property
    def day_realized(self) -> float:
        """當日已實現損益（含成本）。"""
        return self.ledger.realized_twd - self.day_start_realized

    def apply(self, event: Event) -> Seen:
        """記一件事，回傳這件事之後的帳上數字。"""
        day = event.ts.date()
        if day != self.day:
            self.day = day
            self.day_start_realized = self.ledger.realized_twd
        data = event.data
        order: OrderInfo | None = None
        pnl: float | None = None
        cost: float | None = None
        trip: float | None = None
        closing = False
        if event.kind == "order":
            limit = data["limit_price"]
            order = OrderInfo(
                side=str(data["side"]),
                qty=int(data["qty"]),
                entry=bool(data["entry"]),
                limit_price=None if limit is None else float(limit),
            )
            self.orders[data["order_id"]] = order
            self._left[data["order_id"]] = order.qty
        elif event.kind == "fill":
            fill = Fill(
                ts=event.ts,
                symbol=data["symbol"],
                side=Side[data["side"]],
                qty=int(data["qty"]),
                price=float(data["price"]),
            )
            was = self.ledger.position
            closing = was * fill.signed_qty < 0
            cost = self.ledger.cost_of(fill)
            before = self.ledger.realized_twd
            if was == 0:
                self._trip_start = before
            self.ledger.apply(fill)
            pnl = self.ledger.realized_twd - before
            if self.ledger.position == 0:
                if self._trip_start is not None:
                    trip = self.ledger.realized_twd - self._trip_start
                self._trip_start = None
            elif was * self.ledger.position < 0:
                # 一筆成交直接反手：舊的一趟與新的一趟的成本混在同一筆裡，分不乾淨，就不講「這一趟」
                self._trip_start = None
            order = self.orders.get(data["order_id"])
            if order is not None:
                self._left[data["order_id"]] -= fill.qty
                if self._left[data["order_id"]] <= 0:
                    del self.orders[data["order_id"]], self._left[data["order_id"]]
        elif event.kind in ("cancelled", "broker_reject"):
            order = self.orders.pop(data["order_id"], None)
            self._left.pop(data["order_id"], None)
        elif event.kind == "cancel":
            order = self.orders.get(data["order_id"])
        elif event.kind == "correction":
            self.ledger.adopt(int(data["position"]), float(data["avg_price"]))
            self.corrected_on = day
            self._trip_start = None  # 部位被改過，這一趟的損益沒有可靠的起點
        return Seen(
            position=self.ledger.position,
            avg_price=self.ledger.avg_price,
            day_realized_twd=self.day_realized,
            corrected=self.corrected_on == day,
            open_orders=len(self.orders),
            open_exits=sum(1 for o in self.orders.values() if not o.entry),
            order=order,
            pnl_twd=pnl,
            cost_twd=cost,
            closing=closing,
            trip_twd=trip,
        )


@dataclass(frozen=True)
class Context:
    """寫通知要用到的固定資訊。"""

    where: str
    risk: RiskConfig


# 一則通知的草稿：（等級、節流的 key、是不是只即時通知一次、標題、其餘各行）
Draft = tuple[Level, str | None, bool, str, list[str]]


def _holding(seen: Seen) -> str:
    if seen.position == 0:
        return "空手"
    return f"{position_text(seen.position)}（均價 {num(seen.avg_price)}）"


def _order_words(order: OrderInfo | None) -> str:
    return "一筆委託" if order is None else f"{SIDE_TEXT[order.side]} {order.qty} 口"


def _gain(amount: float) -> str:
    amount = round(amount, 2)
    if amount == 0:
        return "不賺不賠"
    return f"{'賺' if amount > 0 else '賠'} {num(abs(amount))} 元"


def _fill(event: Event, seen: Seen, ctx: Context) -> Draft:
    data = event.data
    if seen.pnl_twd is None or seen.cost_twd is None:
        raise ValueError(f"成交事件沒有算出損益：{event!r}")
    head = f"{SIDE_TEXT[data['side']]} {int(data['qty'])} 口，成交 {num(float(data['price']))}"
    lines = [f"帳上部位：{_holding(seen)}"]
    if not seen.closing:
        return Level.INFO, None, False, head, [*lines, f"這筆的成本 {num(seen.cost_twd)} 元"]
    if seen.trip_twd is None:
        head += f"｜這筆{_gain(seen.pnl_twd)}"
    else:
        # 回到空手：標題寫整趟（進場到出場）的結果，下一行交代它怎麼由各筆加起來
        head += f"｜這一趟{_gain(seen.trip_twd)}"
        earlier = seen.trip_twd - seen.pnl_twd
        lines.append(
            f"這一趟＝這一筆 {signed(seen.pnl_twd)} 元＋這一趟前面的 {signed(earlier)} 元（成本都扣了）"
        )
    lines.append(f"今天已實現 {signed(seen.day_realized_twd)} 元（已扣成本）")
    if seen.corrected:
        lines.append("今天部位被修正過，這筆損益是估算。")
    return Level.INFO, None, False, head, lines


def _halt(event: Event, seen: Seen, ctx: Context) -> Draft:
    code, text = event.data.get("halt"), event.text
    holding = f"帳上部位：{_holding(seen)}"
    if code == HALT_DAILY_LOSS:
        lines = []
        match = _DAY_PNL.search(text)
        if match:
            limit = ctx.risk.daily_loss_limit_twd
            cap = "" if limit is None else f"（上限 {num(limit)} 元）"
            lines.append(f"當日損益 {signed(float(match[1]))} 元{cap}。")
        lines += ["程式會自動平倉，今天不再進場，明天自動恢復。", "不用處理。"]
        return Level.WARN, "halt", False, "停手：今天虧損到上限", lines
    if code in SUSPENDED:
        if _STALE_EXIT.search(text):
            head = "平倉單一直沒成交，而且程式在暫停送單中"
            first = f"暫停的原因：{HALT_SHORT[code]}。平倉單已送出刪單，但不會重送。"
        elif "緊急停止" in text:
            head = "緊急停止沒有作用：程式在暫停送單中"
            first = f"暫停的原因：{HALT_SHORT[code]}。"
        elif code == HALT_ORDER_RATE:
            rate = ctx.risk.max_orders_per_minute
            head = f"暫停送單：{HALT_SHORT[code]}"
            first = f"一分鐘內送出的委託達到上限{'' if rate is None else f'（{rate} 筆）'}。"
        else:
            count = _REJECT_COUNT.search(text)
            head = f"暫停送單：{HALT_SHORT[code]}"
            first = f"連續 {count[1] if count else '好幾'} 筆委託被券商拒絕。"
        left = (
            "帳上空手。"
            if seen.position == 0
            else f"帳上還有部位：{_holding(seen)}——程式不會自己平倉，請馬上用元大 App 處理。"
        )
        lines = [first, "現在連平倉單都不送。", left, "處理完要在電腦上解除，程式才會再送單。"]
        return Level.ACT, "halt", False, head, lines
    if code == HALT_KILL:
        if _STALE_EXIT.search(text):
            timeout = num(ctx.risk.order_timeout_s)
            lines = [
                f"平倉單送出超過 {timeout} 秒沒成交，可能是券商或連線有問題。",
                "程式已送出刪單，刪掉後會改送市價平倉。",
                holding,
                f"請馬上打開元大 App 確認部位；處理完{UNLOCK}",
            ]
            return Level.ACT, "halt", False, "緊急停止：平倉單一直沒成交", lines
        return Level.ACT, "halt", False, "緊急停止", ["程式會把部位平掉並停手。", holding, UNLOCK]
    if code == HALT_MISMATCH:
        if "不認得的委託" in text:
            lines = [
                "程式已送出刪單並停手；帳上有部位的話會平掉。",
                holding,
                f"請打開元大 App 核對委託與部位；確認後{UNLOCK}",
            ]
            return Level.ACT, "halt", False, "停手：券商那邊有不是程式送的委託", lines
        lines = [
            "程式已改用券商的數字，會把部位平掉。",
            holding,
            f"請打開元大 App 核對；確認後{UNLOCK}",
        ]
        return Level.ACT, "halt", False, "停手：部位跟券商對不上", lines
    lines = [plain(text), holding, "請到電腦上看程式的狀態。"]
    return Level.ACT, "halt", False, "停手（原因通知還不認得）", lines


def _release(event: Event, seen: Seen, ctx: Context) -> Draft:
    code = event.data.get("halt")
    if code is None:
        lines = ["程式恢復正常運作。", f"帳上部位：{_holding(seen)}"]
        return Level.INFO, "release", False, "已解除停手", lines
    why = HALT_SHORT.get(code, f"代碼 {code}，通知還不認得")
    after = (
        "這種停手不能人工解除，明天自動恢復。"
        if code == HALT_DAILY_LOSS
        else "請到電腦上看程式的狀態。"
    )
    return Level.WARN, "release", False, "解除沒有生效：還在停手", [f"原因：{why}。", after]


def _stop(event: Event, seen: Seen, ctx: Context) -> Draft:
    match = _STOP.search(event.text)
    first = (
        f"這個部位的未實現損益 {signed(float(match[1]))} 元，達到停損金額 {num(float(match[2]))} 元。"
        if match
        else plain(event.text)
    )
    lines = [
        first,
        "程式已送出市價平倉，結果看接下來的成交通知。",
        "不用處理；程式沒有停手，今天照常運作。",
    ]
    return Level.WARN, "stop", False, "停損：程式正在平倉", lines


def _correction(event: Event, seen: Seen, ctx: Context) -> Draft:
    was = position_text(int(event.data["ledger_position"]))
    now = position_text(int(event.data["position"]))
    lines = [
        "程式已改用券商的數字，接著會停手並把部位平掉。",
        "可能是在別的地方下了單，或有成交回報漏掉。",
        f"請打開元大 App 核對；確認後{UNLOCK}",
    ]
    return Level.ACT, "correction", False, f"部位對不上：帳上{was}、券商{now}", lines


def _link(event: Event, seen: Seen, ctx: Context) -> Draft:
    if event.data["up"]:
        lines = ["程式會補回斷線期間的回報，並跟券商核對委託與部位；對不上的會另外通知。"]
        return Level.INFO, "link", False, "券商連線恢復", lines
    lines = ["斷線期間程式不送單、也不刪單。"]
    if seen.position == 0:
        lines.append("帳上空手；連線恢復前不會進場。")
    else:
        lines.append(
            f"帳上還有部位：{_holding(seen)}——程式現在沒辦法平倉，"
            "請打開元大 App 盯著，必要時自己平倉。"
        )
    if seen.open_orders:
        lines.append(
            f"還有 {seen.open_orders} 張委託沒有結果（可能已經成交），連線恢復後會跟券商核對。"
        )
    at_risk = seen.position != 0 or seen.open_orders > 0
    return (Level.ACT if at_risk else Level.WARN), "link", False, "券商連線中斷", lines


def _broker_reject(event: Event, seen: Seen, ctx: Context) -> Draft:
    order = seen.order
    match = _BROKER_SAID.search(event.text)
    said = match[1].strip() if match else ""
    lines = [f"券商的說明：{said or '（沒有說明）'}"]
    urgent = (order is None or not order.entry) and seen.position != 0
    if urgent:
        what = "這是平倉單，部位還在" if order is not None else "帳上還有部位"
        lines.append(f"{what}：{_holding(seen)}。")
        lines.append("程式會再判斷要不要重送；請打開元大 App 確認部位。")
    elif order is not None and order.entry:
        lines.append("這是新倉單，沒有成交，部位不變。")
    limit = ctx.risk.max_consecutive_broker_rejects
    if limit is not None:
        lines.append(f"連續被拒絕 {limit} 筆，程式就會暫停送單。")
    lines.append(f"委託編號 {event.data['order_id']}")
    head = "有一筆委託被券商拒絕" if order is None else f"委託被券商拒絕：{_order_words(order)}"
    return (Level.ACT if urgent else Level.WARN), "broker_reject", False, head, lines


def _foreign_order(event: Event, seen: Seen, ctx: Context) -> Draft:
    match = _FOREIGN_ID.search(event.text)
    lines = [
        "程式已送出刪單，接著會停手。",
        "可能是在別的地方下了單。請打開元大 App 核對委託與部位。",
        f"委託編號 {match[1]}" if match else plain(event.text),
    ]
    return Level.ACT, "foreign_order", False, "券商那邊有不是程式送的委託", lines


def _reject(event: Event, seen: Seen, ctx: Context) -> Draft:
    text = event.text
    unsent, blocked = _UNSENT.search(text), _BLOCKED.search(text)
    match = unsent or blocked
    if match is None:
        key = f"reject:{_VARYING.sub('#', text)}"
        return Level.INFO, key, True, "有一筆委託沒有送出", [plain(text)]
    words = f"{SIDE_TEXT[match[1]]} {match[2]} 口"
    if unsent:
        why, head = "連線中斷", f"連線中斷，沒有送出：{words}"
        lines = ["連線恢復後，策略會重新判斷要不要下單。"]
    else:
        why = _VARYING.sub("#", match[3])  # 數字不同（次數、秒數）仍算同一個原因
        head, lines = f"風控擋下：{words}", [f"原因：{plain(match[3])}"]
    # 沒送出去的是平倉方向的單（有多單時的賣出、有空單時的買進），而且沒有別張平倉單在途：部位還在，要人去看
    against = seen.position != 0 and (match[1] == "SELL") == (seen.position > 0)
    if not against or seen.open_exits > 0:
        return Level.INFO, f"reject:{why}", True, head, lines
    lines.append(f"這是平倉方向的單，部位還在：{_holding(seen)}。請打開元大 App，必要時自己平倉。")
    return Level.ACT, f"reject-exit:{why}", True, head, lines


def _order(event: Event, seen: Seen, ctx: Context) -> Draft:
    order = seen.order
    if order is None:
        raise ValueError(f"送出委託的事件沒有委託內容：{event!r}")
    price = "市價" if order.limit_price is None else f"限價 {num(order.limit_price)}"
    lines = ["新倉單" if order.entry else "平倉單"]
    match = _ORDER_WHY.match(event.text)
    why = plain(match[1].strip()) if match else ""
    if why and _ASCII.search(why) is None:  # 策略自己的理由常帶英文代號，那種就不寫
        lines.append(f"原因：{why}")
    lines.append(f"委託編號 {event.data['order_id']}")
    return Level.INFO, "order", False, f"送出委託：{_order_words(order)}（{price}）", lines


def _cancel(event: Event, seen: Seen, ctx: Context) -> Draft:
    match = _CANCEL_WHY.search(event.text)
    why = plain(match[1].strip() if match else event.text)
    level = Level.WARN if "沒成交" in why else Level.INFO
    lines = [f"原因：{why}"]
    if seen.order is not None and seen.order.entry:
        lines.append("這是新倉單，沒成交的部分不會進場。")
    elif seen.order is not None:
        lines.append("這是平倉單，部位還在；接下來怎麼處理看下一則通知。")
    lines.append(f"委託編號 {event.data['order_id']}")
    return level, "cancel", False, f"送出刪單：{_order_words(seen.order)}", lines


def _cancelled(event: Event, seen: Seen, ctx: Context) -> Draft:
    words, tail = _order_words(seen.order), f"委託編號 {event.data['order_id']}"
    if "券商沒有這張委託" in event.text:
        lines = ["程式不會自動重送；策略會重新判斷要不要下單。", tail]
        return Level.WARN, "cancelled", False, f"斷線前送出的委託沒有送到券商：{words}", lines
    return Level.INFO, "cancelled", False, f"刪單完成：{words}", [tail]


def _control(event: Event, seen: Seen, ctx: Context) -> Draft:
    """網站送來的遠端指令（A-011）。事件文字帶編號與內部用字，這裡不照抄。"""
    if not event.data.get("applied"):
        lines = [
            "收到一筆遠端指令，但沒有執行：不是今天的，或是這一版程式不認得的動作。",
            "交易不受影響。",
        ]
        return Level.WARN, "control", False, "遠端指令沒有執行", lines
    lines = [
        "有人在網站上按了緊急停止。程式已經停手並開始平倉（另有一則「緊急停止」的通知）。",
        f"帳上部位：{_holding(seen)}",
        UNLOCK,
    ]
    return Level.WARN, "control", False, "收到遠端緊急停止", lines


def _control_link(event: Event, seen: Seen, ctx: Context) -> Draft:
    """網站上的緊急停止到不到得了這台電腦。原因的細節（檔名、錯誤訊息）留在日誌，通知只講結論。"""
    if event.data["up"]:
        lines = ["網站上的緊急停止又到得了這台電腦了。"]
        return Level.INFO, "control_link", False, "遠端緊急停止恢復可以用", lines
    lines = [
        "網站上的緊急停止現在到不了這台電腦（讀不到停止指令，或結果回報不出去）。",
        "交易照常進行。要停請到電腦前，或用元大 App。",
        f"帳上部位：{_holding(seen)}",
    ]
    return Level.WARN, "control_link", False, "遠端緊急停止現在到不了", lines


_WRITERS: dict[str, Callable[[Event, Seen, Context], Draft]] = {
    "fill": _fill,
    "halt": _halt,
    "release": _release,
    "stop": _stop,
    "correction": _correction,
    "link": _link,
    "broker_reject": _broker_reject,
    "foreign_order": _foreign_order,
    "reject": _reject,
    "order": _order,
    "cancel": _cancel,
    "cancelled": _cancelled,
    "control": _control,
    "control_link": _control_link,
}


def compose(event: Event, seen: Seen, ctx: Context) -> Message:
    """一件引擎事件 → 一則通知。純函數：同樣的輸入一定得到同樣的文字。"""
    level, key, once, headline, lines = _WRITERS[event.kind](event, seen, ctx)
    return Message(event.ts, event.kind, level, key, once, ctx.where, headline, tuple(lines))


def _span(seconds: float) -> str:
    whole_minutes = seconds >= 60 and seconds % 60 == 0
    return f"{int(seconds // 60)} 分鐘" if whole_minutes else f"{num(seconds)} 秒"


@dataclass
class _Held:
    """同一類、被壓著等合併的通知。"""

    first: datetime
    last: Message
    count: int
    level: Level
    limit: int
    heads: dict[str, int]


class Throttle:
    """同一類通知在視窗內最多即時送幾則；超過的合併成一則，視窗空出來時送出。不丟任何一則。"""

    def __init__(self, window_s: float, burst: int) -> None:
        self._window = timedelta(seconds=window_s)
        self._span = _span(window_s)
        self._burst = burst
        self._sent: dict[str, deque[datetime]] = {}
        self._held: dict[str, _Held] = {}

    def _room(self, key: str, limit: int, now: datetime) -> bool:
        times = self._sent.setdefault(key, deque())
        while times and now - times[0] >= self._window:
            times.popleft()
        return len(times) < limit

    def offer(self, message: Message) -> list[Message]:
        """交一則進來，回傳現在該送的（可能含到期的合併通知，也可能是空的）。"""
        out = self.due(message.ts)
        key = message.key
        if key is None:
            return [*out, message]
        if message.level is Level.ACT and not message.once:
            # 要人處理的事絕不壓著等：先把同一類壓著的合併送出（順序才對），再立刻送這一則
            self._room(key, self._burst, message.ts)
            waiting = self._held.pop(key, None)
            if waiting is not None:
                self._sent[key].append(message.ts)
                out.append(self._merge(waiting))
            self._sent[key].append(message.ts)
            return [*out, message]
        held = self._held.get(key)
        if held is not None:
            held.last, held.count = message, held.count + 1
            held.level = max(held.level, message.level, key=lambda level: level.value)
            held.heads[message.headline] = held.heads.get(message.headline, 0) + 1
            return out
        limit = 1 if message.once else self._burst
        if not self._room(key, limit, message.ts):
            self._held[key] = _Held(
                message.ts, message, 1, message.level, limit, {message.headline: 1}
            )
            return out
        self._sent[key].append(message.ts)
        if len(self._sent[key]) == limit:  # 用掉最後一個即時名額：先講接下來會合併
            note = (
                f"同樣的原因 {self._span}內不重複通知；還在發生的話，到時候合併成一則。"
                if message.once
                else f"這類通知 {self._span}內已經 {limit} 則，接下來的會合併成一則。"
            )
            message = replace(message, lines=(*message.lines, note))
        return [*out, message]

    def due(self, now: datetime) -> list[Message]:
        """視窗空出來的類別：把壓著的合併成一則交出來。"""
        out: list[Message] = []
        for key in list(self._held):
            if self._room(key, self._held[key].limit, now):
                self._sent[key].append(now)
                out.append(self._merge(self._held.pop(key)))
        return out

    def flush(self) -> list[Message]:
        """結束時：還壓著的全部交出來（不管視窗）。"""
        out = [self._merge(held) for held in self._held.values()]
        self._held.clear()
        return out

    @staticmethod
    def _merge(held: _Held) -> Message:
        last, headline = held.last, held.last.headline
        span = f"{held.first:%H:%M:%S}～{last.ts:%H:%M:%S}"
        if held.count == 1:
            note = f"（這則是 {last.ts:%H:%M:%S} 發生的；跟前面的通知同一類，所以晚了一點才送。）"
        elif len(held.heads) == 1:
            headline = f"{headline}（共 {held.count} 次）"
            note = f"（{span} 同樣的事共 {held.count} 次，合併成這一則。）"
        else:
            headline = f"{headline}（合併 {held.count} 則）"
            heads = "、".join(f"{head} {count} 次" for head, count in held.heads.items())
            note = (
                f"（{span} 共 {held.count} 則同類通知合併成這一則：{heads}。"
                "上面是最後一則的內容。）"
            )
        return replace(
            last,
            level=held.level,
            headline=headline,
            lines=(*last.lines, note),
            merged=held.count,
        )


@dataclass(frozen=True)
class Channel:
    """另一個送出管道（例如網站推播的一台裝置）：只收 min_level 以上的通知，自己一條佇列。"""

    name: str
    outbox: Outbox
    min_level: Level


@dataclass(frozen=True)
class ChannelReport:
    """一個額外管道結束時的統計。"""

    name: str
    sent: int
    lost: int
    failures: tuple[Failure, ...]


@dataclass(frozen=True)
class Report:
    """通知結束時的統計。sent／lost／failures 是主管道（Telegram）的；其他管道在 channels。"""

    sent: int  # 送出去的則數
    lost: int  # 確定沒送出去的則數
    failures: tuple[Failure, ...]
    channels: tuple[ChannelReport, ...] = ()


class Notifier:
    """接在 Engine.sink 上：事件 → 白話通知 → 交給佇列。這裡出任何錯都不會回到引擎。"""

    def __init__(
        self,
        cfg: Config,
        outbox: Outbox,
        *,
        replay: bool = False,
        restored: Restored | None = None,
        extra: Sequence[Channel] = (),
    ) -> None:
        if cfg.notify is None:
            raise ConfigError("設定檔沒有 [notify]，不能開通知")
        self._cfg = cfg.notify
        self._kinds = frozenset(cfg.notify.kinds)
        self._ctx = Context(where=where_of(cfg, replay), risk=cfg.risk)
        self._outbox = outbox
        self._extra = tuple(extra)
        self._throttle = Throttle(cfg.notify.throttle_s, cfg.notify.burst)
        self._clock: datetime | None = None  # 最後一個合格的事件時間
        self.book = Book(cfg.contract, restored)

    def on_event(self, event: Event) -> None:
        """引擎每記一件事就呼叫一次。只把通知放進佇列，不等網路。"""
        try:
            event = replace(event, ts=require_taipei(event.ts, "通知的事件時間"))
            self._clock = event.ts
            seen = self.book.apply(event)
            if event.kind not in self._kinds:
                return
            self._hand_over(self._throttle.offer(compose(event, seen, self._ctx)))
        except Exception as exc:
            # 通知自己出錯不能拖累交易：記下來，改送一則「有事沒辦法轉成通知」
            log.exception("通知內容產生失敗（%s）", event.kind)
            self._broken(event, exc)

    def poll(self, now: datetime) -> None:
        """定時呼叫（每筆行情之後）：把視窗空出來的合併通知送出去。"""
        try:
            self._hand_over(self._throttle.due(require_taipei(now, "poll 的時間")))
        except Exception:
            log.exception("送出合併的通知時出錯")

    def close(self) -> Report:
        """結束：壓著的合併通知全部送出，等佇列送完（最多 close_wait_s 秒），回傳統計。"""
        self._hand_over(self._throttle.flush())
        self._outbox.close(self._cfg.close_wait_s)
        for ch in self._extra:
            ch.outbox.close(self._cfg.close_wait_s)
        channels = tuple(
            ChannelReport(ch.name, ch.outbox.sent, ch.outbox.lost, ch.outbox.failures)
            for ch in self._extra
        )
        return Report(self._outbox.sent, self._outbox.lost, self._outbox.failures, channels)

    def _hand_over(self, messages: list[Message]) -> None:
        for message in messages:
            silent = self._cfg.quiet_routine and message.level is Level.INFO
            item = Outgoing(message.ts, message.title, message.text, silent)
            self._outbox.put(item)
            for ch in self._extra:
                if message.level.value >= ch.min_level.value:
                    ch.outbox.put(item)

    def _broken(self, event: Event, exc: Exception) -> None:
        label = KIND_LABEL.get(event.kind, "其他")
        reason = f"通知內容產生失敗：{type(exc).__name__}: {exc}"
        title = f"（沒辦法轉成通知的事）{label}"
        self._outbox.record(Failure(event.ts, title, reason, 0, True))
        lines = (f"種類：{label}", "請到電腦上看程式的紀錄。")
        head = "有一件事沒辦法轉成通知"
        # 事件自己的時間可能就是壞的：用最後一個合格的時間；連一個都沒有就不節流、直接送
        ts = self._clock if self._clock is not None else event.ts
        key = f"broken:{event.kind}" if self._clock is not None else None
        fallback = Message(ts, event.kind, Level.ACT, key, True, self._ctx.where, head, lines)
        try:
            self._hand_over(self._throttle.offer(fallback))
        except Exception:
            log.exception("替代的通知也產生失敗（%s）", event.kind)
