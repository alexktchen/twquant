"""換股進出清單：把回測結果包的 ledger 整理成「每個交易日誰進、誰出」（T-230）。

結果包（`result_bundle`）早就存了逐筆成交，但那是給機器對帳用的流水帳——
一次再平衡 40 筆買賣混在一起，看不出「這一天換掉了哪幾檔」。本模組只做重排，
**不重跑回測、不讀行情**：進出的分類、持有天數、出場報酬全部由 ledger 本身推得，
所以同一份結果包永遠得到同一份清單，也不會碰到第二保留期（D-109）的任何資料。

純函數；落地由 `scripts/build_trade_journal.py` 負責（黃金規則 10 的分工）。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from twquant.errors import DataError

ENTER = "enter"  # 部位 0 → >0
EXIT = "exit"  # 部位 >0 → 0
ADD = "add"
TRIM = "trim"


@dataclass(frozen=True)
class JournalMove:
    """單日單檔的淨動作（同日多筆成交已合併）。"""

    stock_id: str
    action: str  # enter | exit | add | trim
    shares: int  # 當日淨變動股數（賣出為負）
    price: float  # 當日成交均價（TWD）
    amount: float  # |股數| × 均價（TWD，不含費用）
    shares_after: int
    # 以下只有 exit 有值：本輪首買日、持有交易日數、價格報酬（小數，不含費用與股利）
    entry_date: str | None = None
    held_sessions: int | None = None
    ret: float | None = None
    # 出場規則造成的賣出才有值（T-231，如 stop_loss／below_ma_20）；一般再平衡為 None
    reason: str | None = None


@dataclass(frozen=True)
class JournalDay:
    """一個有成交或未成交紀錄的交易日。"""

    date: str
    moves: list[JournalMove]
    n_holdings_after: int
    unfilled: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Pos:
    shares: int = 0
    cost_shares: int = 0
    cost_amount: float = 0.0
    entry_date: str = ""


def build_journal(bundle: dict[str, Any]) -> list[JournalDay]:
    """由結果包產出依日期排序的換股清單。

    成本採本輪加權平均買進價（與引擎 `_Lot` 同口徑：加碼併入、部分出場不變均價）。
    賣超過持有股數即 raise——那代表 ledger 與引擎狀態對不上，清單不該替它圓過去。
    """
    sessions = [str(e[0]) for e in bundle["equity"]]
    session_idx = {d: i for i, d in enumerate(sessions)}
    by_day: dict[str, list[dict[str, Any]]] = {}
    for t in bundle["ledger"]:
        by_day.setdefault(str(t["date"]), []).append(t)
    unfilled_by_day: dict[str, list[dict[str, Any]]] = {}
    for u in bundle.get("unfilled", []):
        unfilled_by_day.setdefault(str(u["date"]), []).append(dict(u))

    # T-168：分割改股數但不是交易，不在 ledger 裡。不套用的話這裡會把
    # 「11,000 股分割成 220,000 後賣掉 205,000」判成賣超而 raise。
    # 套用的是**股數與成本股數**（cost_amount 不動）——同一筆錢換到更多股，
    # 均價自然縮放，出場報酬才算得對。
    splits_by_day: dict[str, list[dict[str, Any]]] = {}
    for sp in bundle.get("splits", []):
        splits_by_day.setdefault(str(sp["date"]), []).append(dict(sp))

    book: dict[str, _Pos] = {}
    days: list[JournalDay] = []
    for day in sorted(set(by_day) | set(unfilled_by_day) | set(splits_by_day)):
        if day not in session_idx:
            raise DataError("trade_journal", None, f"{day} 有成交紀錄卻不在權益曲線的交易日內")
        for sp in splits_by_day.get(day, []):
            pos = book.get(str(sp["stock_id"]))
            if pos is not None and pos.shares > 0:
                adj = float(sp["adj_factor"])
                pos.shares = int(sp["shares_after"])
                pos.cost_shares = round(pos.cost_shares / adj) if adj > 0 else pos.cost_shares
        moves = [
            _net_move(book, sid, trades, day, session_idx)
            for sid, trades in sorted(_group(by_day.get(day, [])).items())
        ]
        order = {ENTER: 0, EXIT: 1, ADD: 2, TRIM: 3}
        moves.sort(key=lambda m: (order[m.action], -m.amount, m.stock_id))
        days.append(
            JournalDay(
                date=day,
                moves=moves,
                n_holdings_after=sum(1 for p in book.values() if p.shares > 0),
                unfilled=sorted(
                    unfilled_by_day.get(day, []), key=lambda u: (u["stock_id"], u["side"])
                ),
            )
        )
    return days


def _group(trades: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        out.setdefault(str(t["stock_id"]), []).append(t)
    return out


def _net_move(
    book: dict[str, _Pos],
    sid: str,
    trades: list[dict[str, Any]],
    day: str,
    session_idx: dict[str, int],
) -> JournalMove:
    pos = book.setdefault(sid, _Pos())
    before, entry_before = pos.shares, pos.entry_date
    avg_before = pos.cost_amount / pos.cost_shares if pos.cost_shares else 0.0
    gross = 0.0
    volume = 0
    reason: str | None = None
    for t in trades:
        reason = t.get("reason") or reason
        qty, price = int(t["shares"]), float(t["price"])
        if t["side"] == "buy":
            if pos.shares == 0:
                pos.entry_date, pos.cost_shares, pos.cost_amount = day, 0, 0.0
            pos.shares += qty
            pos.cost_shares += qty
            pos.cost_amount += qty * price
        elif t["side"] == "sell":
            if qty > pos.shares:
                raise DataError(
                    "trade_journal", None, f"{day} {sid} 賣出 {qty} 股，但帳上只有 {pos.shares} 股"
                )
            pos.shares -= qty
        else:
            raise DataError("trade_journal", None, f"{day} {sid} 未知的買賣別：{t['side']!r}")
        gross += qty * price
        volume += qty
    delta = pos.shares - before
    price = gross / volume if volume else 0.0
    if before == 0:
        action = ENTER
    elif pos.shares == 0:
        action = EXIT
    else:
        action = ADD if delta > 0 else TRIM
    exit_fields: dict[str, Any] = {}
    if action == EXIT:
        exit_fields = {
            "entry_date": entry_before,
            "held_sessions": session_idx[day] - session_idx[entry_before],
            "ret": price / avg_before - 1.0 if avg_before > 0 else None,
        }
    return JournalMove(
        stock_id=sid,
        action=action,
        shares=delta,
        price=price,
        amount=abs(delta) * price,
        shares_after=pos.shares,
        reason=reason,
        **exit_fields,
    )


def journal_to_dicts(days: list[JournalDay]) -> list[dict[str, Any]]:
    """轉成可 JSON 序列化的結構（給報告頁內嵌）。"""
    return [asdict(d) for d in days]
