"""紙上交易帳本（D-142 事前登記，T-277）：append-only、雜湊鏈、拒絕回填。

**這個模組存在的唯一理由是防止我自己作弊。** 一份「事後可以重新產生」的紙上交易紀錄
和一次回測沒有差別——同樣落在已被探勘過的資料上，同樣會被選擇偏誤吃掉（D-141 §七）。
所以帳本的價值不在「會算報酬」，而在**寫下去就改不動**：

* 每一筆事件帶 `prev_hash` 與 `hash`（SHA-256 鏈）。改掉中間任何一筆，它之後所有事件的
  雜湊都對不起來，`verify_chain` 會指出第一個斷點。
* 寫入端硬性拒絕：日期倒退、早於開軌日、記未來、同日同類型重複，
  以及**選股事件的補記超過時效**（知道後來漲跌才寫下的名單不是紙上交易，是回測）。
* 缺資料記 `skipped` 並附原因——不補、不猜、不沿用前一天（黃金規則 5）。
  排程器已知的兩個靜默缺口（T-275／T-276）造成的洞，會以洞的形式留在帳本裡。

本模組只做「記錄與重播」：事件內容（成交、標記、平倉）由呼叫端算好傳進來，
這裡不讀行情、不連 PG、不算策略。落地在 `reporting/`（黃金規則 10 的分工）。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from twquant.config import BaseConfig
from twquant.conventions import TZ
from twquant.errors import DataError

_LEDGER_DIR = "paper_trading"
_TRACK_CODE = re.compile(r"^[A-Z][A-Z0-9-]{1,31}$")
# 鏈首的 prev_hash。不是魔術數字，是「前面沒有東西」的編碼。
GENESIS_HASH = "0" * 64


def _as_stock_id(value: object) -> str:
    """黃金規則 4：代號一律字串、保留前導零。int 進來代表上游已經弄丟了前導零。"""
    if not isinstance(value, str) or not value:
        raise ValueError("stock_id 必須是非空字串（不得為數字型別）")
    return value


StockId = Annotated[str, BeforeValidator(_as_stock_id)]


class FillRow(BaseModel):
    """一筆成交。欄位與回測結果包的 `ledger` 同名同義，讓兩邊可以直接對齊。"""

    stock_id: StockId
    side: Literal["buy", "sell"]
    shares: int = Field(gt=0)
    price: float = Field(gt=0)
    commission: float = Field(ge=0)
    tax: float = Field(ge=0)
    slippage: float = Field(ge=0)
    reason: str = "rebalance"


class OrderRow(BaseModel):
    """一張待執行的單：訊號日收盤排定，**隔日開盤**成交（E0 的 `next_open`）。"""

    stock_id: StockId
    target_weight: float = Field(ge=0.0, le=1.0)


class HoldingRow(BaseModel):
    """收盤標記時的一檔持股——這一檔目前的**完整狀態**，不只是市值。

    `close` 為當日收盤；停牌沿用最後一次已知收盤並列入 `stale_marks`。
    `realized_*` 是本輪已經減碼賣掉的那部分：E0 每月再平衡會把留下來的部位加碼或減碼，
    所以一筆來回的報酬必須把中途的進出一起算，否則「每筆期望值」會只看到最後一段。
    """

    stock_id: StockId
    shares: int = Field(gt=0)
    close: float = Field(gt=0)
    cost_basis: float = Field(ge=0)  # 目前持股的總成本（含手續費與滑價）
    realized_cost: float = Field(default=0.0, ge=0)  # 本輪已賣出部分的原始成本
    realized_proceeds: float = Field(default=0.0, ge=0)  # 本輪已賣出部分的淨收入
    held_sessions: int = Field(default=0, ge=0)  # 進場日之後經過的交易日數


class ClosedTrade(BaseModel):
    """一筆已平倉的來回。`net_return` 是**扣完所有費用**的報酬（D-142 §五 的判準口徑）。"""

    stock_id: StockId
    opened_on: dt.date
    closed_on: dt.date
    shares: int = Field(gt=0)
    cost: float = Field(gt=0)  # 買進總支出（含手續費、滑價）
    proceeds: float = Field(ge=0)  # 賣出總收入（扣掉手續費、證交稅、滑價）
    net_return: float
    held_sessions: int = Field(ge=0)

    @model_validator(mode="after")
    def _return_matches_cashflow(self) -> ClosedTrade:
        # 報酬不是另外算出來的一個數字，而是現金流的函數。兩者不一致代表上游算錯了，
        # 不是四捨五入——差 1e-9 就 raise，讓它在寫入之前爆掉。
        expected = self.proceeds / self.cost - 1.0
        if abs(expected - self.net_return) > 1e-9:
            raise ValueError(
                f"{self.stock_id}：net_return {self.net_return:.10f} 與現金流推得的 "
                f"{expected:.10f} 不符（cost={self.cost}, proceeds={self.proceeds}）"
            )
        if self.closed_on < self.opened_on:
            raise ValueError(f"{self.stock_id}：平倉日早於進場日")
        return self


class TrackOpened(BaseModel):
    """開軌事件（鏈首）：把「這條軌照什麼規則跑」釘死在鏈上。

    `strategy_fingerprint` 與 `cost_fingerprint` 是配置檔內容的 SHA-256。
    之後任何一天若算出不同的指紋，就代表有人改了規則——那要另開新軌（D-142 §六.1），
    不是在這條軌上繼續記。
    """

    kind: Literal["track_opened"] = "track_opened"
    code: str
    label: str
    registration: str  # 事前登記的裁決編號，例：D-142
    strategy: str
    strategy_fingerprint: str = Field(min_length=16)
    cost_fingerprint: str = Field(min_length=16)
    model_id: str
    capital_twd: float = Field(gt=0)
    start_date: dt.date
    credit_dividends: bool


class DayRecord(BaseModel):
    """一個交易日的完整紀錄：開盤成交 → 收盤標記 → 排定明天的單。

    一天一筆，順序即真實順序。`orders_queued` 非空＝這天做了選股決策，
    補記時效因此比純標記日嚴格（D-142 §四.4）。
    """

    kind: Literal["day"] = "day"
    fills: tuple[FillRow, ...] = ()
    closed: tuple[ClosedTrade, ...] = ()
    holdings: tuple[HoldingRow, ...] = ()
    orders_queued: tuple[OrderRow, ...] = ()
    cash: float
    equity: float
    # 當日沒有行情、以最後已知收盤標記的持股。空＝所有持股都有當日價。
    stale_marks: tuple[str, ...] = ()
    # 想買卻沒買成的（漲停、參與率、金額不足一張）。**不得靜默**（T-135 的教訓）。
    unfilled: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _accounting_identity(self) -> DayRecord:
        # 會計恆等：權益 = 現金 + 持倉市值（SPEC-004 AC-6）。容忍度是浮點累加誤差，不是放寬。
        market_value = sum(h.shares * h.close for h in self.holdings)
        if abs(self.cash + market_value - self.equity) > 1e-6 * max(1.0, abs(self.equity)):
            raise ValueError(
                f"會計恆等不成立：現金 {self.cash:.2f} + 持倉 {market_value:.2f} "
                f"≠ 權益 {self.equity:.2f}"
            )
        if self.cash < -1e-9:
            raise ValueError(f"現金為負（{self.cash:.2f}）——引擎不允許融資")
        weights = sum(o.target_weight for o in self.orders_queued)
        if weights > 1.0 + 1e-9:
            raise ValueError(f"待執行單的權重和 {weights:.6f} 超過 1")
        return self


class SkippedDay(BaseModel):
    """這天記不成，原因寫在這裡。**洞要留在帳本裡**，不得事後補平。"""

    kind: Literal["skipped"] = "skipped"
    reason: str = Field(min_length=1)


class LedgerNote(BaseModel):
    """修正紀錄。程式錯了就修，但已寫入的事件不重算——修正只能往前記（D-142 §四.6）。"""

    kind: Literal["note"] = "note"
    text: str = Field(min_length=1)


EventBody = Annotated[
    TrackOpened | DayRecord | SkippedDay | LedgerNote, Field(discriminator="kind")
]


class LedgerEvent(BaseModel):
    """鏈上的一筆。`hash` 由其餘欄位（含 `prev_hash`）決定，故改一個字就對不起來。"""

    schema_version: Literal[1] = 1
    seq: int = Field(ge=0)
    date: dt.date
    recorded_at: dt.datetime
    prev_hash: str = Field(min_length=64, max_length=64)
    hash: str = Field(min_length=64, max_length=64)
    body: EventBody

    @field_validator("recorded_at")
    @classmethod
    def _timestamp_is_taipei(cls, value: dt.datetime) -> dt.datetime:
        if value.tzinfo is None:
            raise ValueError("recorded_at 必須 tz-aware（黃金規則 2）")
        return value.astimezone(TZ)

    def core_bytes(self) -> bytes:
        """參與雜湊的決定性序列化：除了 `hash` 本身以外的每一個欄位。"""
        payload = self.model_dump(mode="json")
        payload.pop("hash", None)
        return json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def recorded_lag_days(self) -> int:
        """這筆是當天記的還是補記的（0＝當天）。負值代表記了未來，寫入端會拒絕。"""
        return (self.recorded_at.astimezone(TZ).date() - self.date).days


def _digest(event: LedgerEvent) -> str:
    return hashlib.sha256(event.core_bytes()).hexdigest()


def seal(
    *, seq: int, day: dt.date, recorded_at: dt.datetime, prev_hash: str, body: EventBody
) -> LedgerEvent:
    """組出一筆已封印的事件（先以占位雜湊建模驗證，再把真雜湊填回去）。"""
    draft = LedgerEvent(
        seq=seq,
        date=day,
        recorded_at=recorded_at,
        prev_hash=prev_hash,
        hash=GENESIS_HASH,
        body=body,
    )
    return draft.model_copy(update={"hash": _digest(draft)})


# ---------------------------------------------------------------- 配置（D-142）


class TrackConfig(BaseConfig):
    """一條紙上交易軌。兩條軌吃同一份名單、只差資本，故策略與模型不在這裡選。"""

    code: str
    label: str
    capital_twd: float = Field(gt=0)
    note: str = ""

    @field_validator("code")
    @classmethod
    def _code_is_safe(cls, value: str) -> str:
        # 軌別代號會變成檔案路徑，故只收大寫英數與連字號——不接受任何路徑片段。
        if _TRACK_CODE.fullmatch(value) is None:
            raise ValueError(f"軌別代號格式錯誤：{value!r}（只收 A-Z 0-9 -，2~32 字）")
        return value


class VerdictRule(BaseConfig):
    """判讀門檻（D-142 §五）。**看到數字之後不得修改**——事前登記的意義就在這裡。"""

    min_signal_days: int = Field(gt=0)
    min_closed_trades: int = Field(gt=0)
    min_months: int = Field(gt=0)
    forward_rank_ic: float
    month_block_t: float


class PaperTradingConfig(BaseConfig):
    """configs/paper_trading.yaml 的模型。"""

    schema_version: Literal[1] = 1
    registration: str = Field(min_length=1)
    strategy: str = Field(min_length=1)  # configs/strategies/<strategy>.yaml
    model_id: str = Field(min_length=1)
    credit_dividends: bool = False
    max_participation: float = Field(gt=0, le=1)
    # 選股事件的補記時效：超過就拒絕。知道後來漲跌才寫下的名單不是紙上交易。
    max_record_lag_days: int = Field(ge=0)
    # 純標記日的補記時效較寬：標記不含任何決策，晚記不會改變它的值。
    max_mark_lag_days: int = Field(ge=0)
    tracks: tuple[TrackConfig, ...]
    verdict: VerdictRule

    @model_validator(mode="after")
    def _tracks_are_usable(self) -> PaperTradingConfig:
        if not self.tracks:
            raise ValueError("至少要有一條軌")
        codes = [t.code for t in self.tracks]
        dup = sorted({c for c in codes if codes.count(c) > 1})
        if dup:
            raise ValueError(f"軌別代號重複：{dup}")
        if self.max_mark_lag_days < self.max_record_lag_days:
            raise ValueError("純標記日的時效不得比選股事件還嚴（設定寫反了）")
        return self


# ---------------------------------------------------------------- 讀寫


def ledger_path(data_dir: str | Path, code: str) -> Path:
    """已驗證的軌別代號 → 固定路徑（不接受任何路徑片段）。"""
    if not isinstance(code, str) or _TRACK_CODE.fullmatch(code) is None:
        raise DataError("paper_ledger", None, f"軌別代號格式錯誤：{code!r}")
    return Path(data_dir) / _LEDGER_DIR / code / "events.jsonl"


def read_events(path: str | Path) -> list[LedgerEvent]:
    """讀出整條鏈。檔案不存在＝還沒開軌，回空串列（那是合法狀態，不是錯誤）。"""
    p = Path(path)
    if not p.exists():
        return []
    events: list[LedgerEvent] = []
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            events.append(LedgerEvent.model_validate_json(line))
        except (ValidationError, ValueError) as exc:
            raise DataError("paper_ledger", None, f"{p.name} 第 {lineno} 行不是合法事件") from exc
    return events


def verify_chain(events: list[LedgerEvent]) -> None:
    """逐筆重算雜湊並檢查鏈結。不合就指出**第一個**斷點——後面的都是它的後果。"""
    prev = GENESIS_HASH
    for i, ev in enumerate(events):
        if ev.seq != i:
            raise DataError("paper_ledger", ev.date, f"序號斷裂：第 {i} 筆的 seq={ev.seq}")
        if ev.prev_hash != prev:
            raise DataError(
                "paper_ledger",
                ev.date,
                f"鏈結斷裂於 seq={ev.seq}：prev_hash={ev.prev_hash[:12]}… "
                f"但前一筆的 hash={prev[:12]}…",
            )
        actual = _digest(ev)
        if actual != ev.hash:
            raise DataError(
                "paper_ledger",
                ev.date,
                f"seq={ev.seq} 的內容被改過：重算雜湊 {actual[:12]}… ≠ 記錄的 {ev.hash[:12]}…",
            )
        prev = ev.hash


def _guard(
    events: list[LedgerEvent],
    body: EventBody,
    day: dt.date,
    recorded_at: dt.datetime,
    cfg: PaperTradingConfig,
) -> None:
    """寫入前的硬性檢查（D-142 §四）。每一條都 raise，沒有一條是警告。"""
    if recorded_at.tzinfo is None:
        raise DataError("paper_ledger", day, "recorded_at 必須 tz-aware（黃金規則 2）")
    opened = isinstance(body, TrackOpened)
    if opened != (not events):
        raise DataError(
            "paper_ledger",
            day,
            "開軌事件只能是鏈首一筆" if opened else "這條軌還沒開，第一筆必須是 track_opened",
        )
    lag = (recorded_at.astimezone(TZ).date() - day).days
    if lag < 0:
        raise DataError("paper_ledger", day, f"不得記未來：{day} 比記錄當下還晚 {-lag} 天")
    if opened:
        return

    genesis = events[0].body
    if not isinstance(genesis, TrackOpened):  # verify_chain 之後不該發生
        raise DataError("paper_ledger", day, "鏈首不是開軌事件，帳本結構已損毀")
    # 先問「早於開軌日嗎」再問「倒退嗎」：兩條都會擋住回填，但前者才說得出**為什麼**
    # 不准回填。順序反過來的話，所有回填嘗試都只會看到「日期倒退」。
    if day < genesis.start_date:
        raise DataError(
            "paper_ledger",
            day,
            f"{day} 早於開軌日 {genesis.start_date}——帳本不帶歷史（D-142 §四.2）",
        )
    head = events[-1]
    if day < head.date:
        raise DataError(
            "paper_ledger",
            day,
            f"日期倒退：帳本已記到 {head.date}，不接受 {day}。"
            "帳本是 append-only——要補的洞已經是洞了（D-142 §四.1）",
        )
    if isinstance(body, DayRecord | SkippedDay) and any(
        e.date == day and isinstance(e.body, DayRecord | SkippedDay) for e in events
    ):
        raise DataError("paper_ledger", day, f"{day} 已經記過了，同一天不得記兩次")
    if isinstance(body, DayRecord):
        picked = bool(body.orders_queued)
        limit = cfg.max_record_lag_days if picked else cfg.max_mark_lag_days
        if lag > limit:
            what = "選股" if picked else "標記"
            raise DataError(
                "paper_ledger",
                day,
                f"{what}事件補記了 {lag} 天（上限 {limit}）。"
                + (
                    "知道後來漲跌才寫下的名單不是紙上交易，是回測（D-142 §四.4）"
                    if picked
                    else "這天的洞留成 skipped，不要補平"
                ),
            )


def append_event(
    data_dir: str | Path,
    code: str,
    body: EventBody,
    *,
    day: dt.date,
    recorded_at: dt.datetime,
    cfg: PaperTradingConfig,
) -> LedgerEvent:
    """驗鏈 → 過閘門 → 封印 → 附加一行。任何一步不過就什麼都不寫。"""
    path = ledger_path(data_dir, code)
    events = read_events(path)
    verify_chain(events)  # 先確認既有的鏈沒被動過，再往上疊
    _guard(events, body, day, recorded_at, cfg)
    event = seal(
        seq=len(events),
        day=day,
        recorded_at=recorded_at,
        prev_hash=events[-1].hash if events else GENESIS_HASH,
        body=body,
    )
    line = event.model_dump_json() + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # "a" 的單行寫入在 POSIX 與 Windows 上都是附加；fsync 讓斷電時不會留半行。
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise DataError("paper_ledger", day, f"帳本 {path.name} 無法附加") from exc
    return event


# ---------------------------------------------------------------- 重播


@dataclass
class PositionState:
    """一檔持股目前的樣子。`cost_basis` 只涵蓋**還沒賣掉**的那些股。"""

    shares: int = 0
    cost_basis: float = 0.0
    opened_on: dt.date | None = None
    last_close: float = 0.0
    realized_cost: float = 0.0
    realized_proceeds: float = 0.0
    held_sessions: int = 0


@dataclass
class TrackState:
    """把整條鏈折成「現在的狀態」。純函數的結果——同一條鏈永遠折出同一個狀態。"""

    code: str = ""
    label: str = ""
    start_date: dt.date | None = None
    capital_twd: float = 0.0
    cash: float = 0.0
    equity: float = 0.0
    positions: dict[str, PositionState] = field(default_factory=dict)
    pending: tuple[OrderRow, ...] = ()
    closed: list[ClosedTrade] = field(default_factory=list)
    last_date: dt.date | None = None
    last_hash: str = GENESIS_HASH
    seq: int = 0
    skipped: list[tuple[dt.date, str]] = field(default_factory=list)
    notes: list[tuple[dt.date, str]] = field(default_factory=list)

    @property
    def opened(self) -> bool:
        return self.start_date is not None


def replay(events: list[LedgerEvent]) -> TrackState:
    """重播整條鏈。**不驗雜湊**（那是 `verify_chain` 的事），只折疊已記錄的事實。"""
    state = TrackState()
    for ev in events:
        body = ev.body
        state.seq = ev.seq + 1
        state.last_hash = ev.hash
        state.last_date = ev.date
        if isinstance(body, TrackOpened):
            state.code = body.code
            state.label = body.label
            state.start_date = body.start_date
            state.capital_twd = body.capital_twd
            state.cash = body.capital_twd
            state.equity = body.capital_twd
        elif isinstance(body, DayRecord):
            state.cash = body.cash
            state.equity = body.equity
            state.pending = body.orders_queued
            state.positions = {
                h.stock_id: PositionState(
                    shares=h.shares,
                    cost_basis=h.cost_basis,
                    opened_on=state.positions.get(h.stock_id, PositionState()).opened_on or ev.date,
                    last_close=h.close,
                    realized_cost=h.realized_cost,
                    realized_proceeds=h.realized_proceeds,
                    held_sessions=h.held_sessions,
                )
                for h in body.holdings
            }
            state.closed.extend(body.closed)
        elif isinstance(body, SkippedDay):
            state.skipped.append((ev.date, body.reason))
        else:
            state.notes.append((ev.date, body.text))
    return state


def load_track(data_dir: str | Path, code: str) -> TrackState:
    """讀鏈 → 驗鏈 → 重播。**每次讀都驗**：被動過的帳本不該還能拿來算報酬。"""
    events = read_events(ledger_path(data_dir, code))
    verify_chain(events)
    return replay(events)
