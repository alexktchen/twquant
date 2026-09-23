"""日頻回測引擎（SPEC-004 §1/§3）。

執行模型：訊號日 t 收盤產生目標權重 → t+1 開盤成交（next_open）。整數張、價格相依
成本、漲跌停不成交（記 unfilled）、參與率上限。會計恆等：權益 = 現金 + 持倉市值，
成本於成交時自現金扣除，全程現金 ≥ 0（AC-6）。純運算：不寫檔（落地由 reporting）。
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import polars as pl

from twquant.backtest.costs import CostConfig, limit_down_proxy, limit_up_proxy, trade_cost
from twquant.backtest.exits import HeldPosition, entry_blocked, exit_reasons, ma_column
from twquant.backtest.fill import decide_fill
from twquant.backtest.metrics import Metrics, compute_metrics
from twquant.strategy.overlays import _realized_vol

if TYPE_CHECKING:
    from twquant.data.warehouse import TableSource
    from twquant.strategy.config import StrategyConfig

_LOT = 1000
_Row = dict[str, Any]  # 單日單股行情列（open/close/limit/_prev_close/_adv）


@dataclass(frozen=True)
class EquityPoint:
    date: dt.date
    equity: float
    cash: float
    positions_value: float


@dataclass(frozen=True)
class Trade:
    date: dt.date
    stock_id: str
    side: str  # buy | sell
    shares: int
    price: float
    commission: float
    tax: float
    slippage: float
    reason: str = "rebalance"  # 出場規則造成的成交記規則名（T-231）


@dataclass(frozen=True)
class SplitAdjustment:
    """分割造成的股數變動（T-168）。**不是交易，故不進 ledger**——但也不能不記：
    所有從 ledger 重建部位的下游（診斷、換股清單、判定）都會看到「買 11,000 卻賣 205,000」
    而 raise。比照股利獨立成表，讓它可稽核也可重建。"""

    date: dt.date
    stock_id: str
    adj_factor: float
    shares_before: int
    shares_after: int


@dataclass(frozen=True)
class Dividend:
    """除息／除權當日的現金入帳一筆（D-085 選項 A）。

    `per_share` 由 `corporate_events.adj_factor` 反推：`前收 × (1 − adj_factor)`。
    **股票股利與權息一律折算成現金**——不是真實現金流，但總報酬正確，
    且避免產生非整股部位（那會撐破 `PaperBroker` 的 `tol_cash`，擋住實盤）。
    """

    date: dt.date
    stock_id: str
    shares: int
    per_share: float
    cash: float
    event_type: str


@dataclass(frozen=True)
class Unfilled:
    date: dt.date
    stock_id: str
    side: str
    shares: int
    reason: str  # limit_up | limit_down | participation | cash | lot（不足一張，T-135）


@dataclass
class _Lot:
    """本輪持有批次（部位 0→>0 起算；加碼併入、部分出場不變均價、歸零即移除）。"""

    entry_date: dt.date
    cost_shares: int  # 本輪累計買入股數
    cost_amount: float  # 本輪累計買入價金（不含費用）
    min_close: float  # 持有期間最低收盤（含進場日）
    max_close: float = 0.0  # 持有期間最高收盤（trailing_ma 用，T-247）
    sessions_held: int = 0  # 買進後經過的交易日數（買進日＝0；time_stop 用，T-256）
    dividends: float = 0.0  # 本輪已收到的股利現金（D-085）；歸零出場時隨 lot 一起消失


@dataclass(frozen=True)
class Holding:
    """期末持倉一檔（SPEC-004 §4 持倉快照的期末切片）。"""

    stock_id: str
    shares: int
    last_close: float
    value: float
    weight: float  # value / final_equity
    entry_date: dt.date | None = None  # 本輪首買日
    avg_cost: float = 0.0  # 本輪加權平均買進價（不含費用）
    # D-085：含息報酬。未計股利時除息會顯示成虧損——那是價格跳空，不是賠錢。
    dividends: float = 0.0  # 本輪已收到的股利現金
    ret: float = 0.0  # (last_close + 每股已收股利) / avg_cost − 1
    max_loss: float = 0.0  # 持有期間最低收盤 vs 成本（≤0，MAE）


@dataclass(frozen=True)
class BacktestResult:
    equity: list[EquityPoint]
    ledger: list[Trade]
    unfilled: list[Unfilled]
    metrics: Metrics
    initial_capital: float
    final_equity: float
    final_holdings: list[Holding] = field(default_factory=list)
    dividends: list[Dividend] = field(default_factory=list)  # D-085：可稽核的股利明細
    splits: list[SplitAdjustment] = field(default_factory=list)  # T-168：可稽核的分割調整
    # 這次是不是含息口徑。**必須帶在結果裡**——不然含息與不含息的結果包長得一模一樣，
    # 誰也分不出手上這份是哪一種，而那正是最容易造成靜默不一致的地方。
    credit_dividends: bool = True


@dataclass
class _State:
    cash: float
    holdings: dict[str, int] = field(default_factory=dict)  # stock_id -> shares
    last_close: dict[str, float] = field(default_factory=dict)
    lots: dict[str, _Lot] = field(default_factory=dict)  # 本輪持有批次（成本/首買日/MAE）
    ledger: list[Trade] = field(default_factory=list)
    unfilled: list[Unfilled] = field(default_factory=list)
    splits: list[SplitAdjustment] = field(default_factory=list)
    dividends: list[Dividend] = field(default_factory=list)
    traded_notional: float = 0.0
    exposure_mult: float = 1.0  # T-286：每日曝險目標的倍數；無 exposure_target 時恆為 1.0


def _price_panel(
    wh: TableSource, start: dt.date, end: dt.date, ma_windows: tuple[int, ...] = ()
) -> dict[dt.date, dict[str, _Row]]:
    """載入 [start,end] 的日行情，附前收與 20 日 ADV，組成 {date: {stock_id: row}}。"""
    lf = (
        wh.scan("prices_daily")
        .select("date", "stock_id", "open", "close", "amount", "limit_up_price", "limit_down_price")
        .sort(["stock_id", "date"])
        .with_columns(
            _prev_close=pl.col("close").shift(1).over("stock_id", order_by="date"),
            _adv=pl.col("amount").rolling_median(20).over("stock_id"),
            # T-231：均線用**未還原**收盤（含當日）——與看盤軟體上的均線同一條；
            # 除息日的跳空因此可能觸發跌破，這是規則本身的性質，不在此修飾。
            **{ma_column(w): pl.col("close").rolling_mean(w).over("stock_id") for w in ma_windows},
        )
        .filter((pl.col("date") >= start) & (pl.col("date") <= end))
    )
    by_date: dict[dt.date, dict[str, _Row]] = {}
    for r in lf.collect().to_dicts():
        by_date.setdefault(r["date"], {})[r["stock_id"]] = r
    return by_date


# D-085：只有這三種是「配發給股東的東西」，可折算成現金。
# **分割／反分割不在此列**：1:4 分割的 adj_factor 是 0.25，照同一條公式會把
# 七成五的部位價值當成現金發下去——那是憑空生錢。分割要改的是**股數**，
# 屬另一個缺陷（T-168），本函數明確不碰。
_DIVIDEND_EVENTS = frozenset({"cash_dividend", "cash_and_stock_dividend", "stock_dividend"})
# T-168：分割／反分割改的是**股數**不是現金。不處理的話 1:20 分割當天價格掉 95%，
# D-029 的跳價防護會把部位強制出清並記成 −94% 的真實虧損（D-143 實測 5314 與 6919
# 正是這樣各賠掉一格）。標籤那一側早就還原了（`attach_adjustment` 連乘全部 adj_factor），
# 只有回測沒跟上——模型用乾淨資料訓練、回測卻吃假虧損，是不對稱的失效。
_SPLIT_EVENTS = frozenset({"split", "reverse_split"})


def _dividends_by_date(
    wh: TableSource, start: dt.date, end: dt.date
) -> dict[dt.date, list[tuple[str, float, str]]]:
    """{除息日: [(stock_id, adj_factor, event_type)]}。查無 `corporate_events` → 空表。

    `adj_factor >= 1` 的列一律略過：那不是配發（實測 11 列，多為反分割與資料異常），
    照公式會算出負的股利。
    """
    try:
        lf = wh.scan("corporate_events")
    except Exception:  # noqa: BLE001 - 供應層沒有這張表時退回「無股利」，由呼叫端的旗標把關
        return {}
    cols = lf.collect_schema().names()
    if not {"date", "stock_id", "adj_factor"} <= set(cols):
        return {}
    et = pl.col("event_type") if "event_type" in cols else pl.lit("unknown")
    df = (
        lf.select("date", "stock_id", "adj_factor", et.alias("event_type"))
        .filter(
            pl.col("date").is_between(start, end)
            & pl.col("adj_factor").is_not_null()
            & (pl.col("adj_factor") > 0.0)
            & (pl.col("adj_factor") < 1.0)
            & pl.col("event_type").is_in(list(_DIVIDEND_EVENTS))
        )
        .collect()
    )
    out: dict[dt.date, list[tuple[str, float, str]]] = {}
    for r in df.to_dicts():
        out.setdefault(r["date"], []).append(
            (str(r["stock_id"]), float(r["adj_factor"]), str(r["event_type"]))
        )
    return out


def _splits_by_date(
    wh: TableSource, start: dt.date, end: dt.date
) -> dict[dt.date, list[tuple[str, float]]]:
    """{分割日: [(stock_id, adj_factor)]}。查無 `corporate_events` → 空表。

    與 `_dividends_by_date` 的差別：**不限 adj_factor < 1**——反分割的因子大於 1
    （實測 2.0~7.0），那正是要調整的另一個方向。
    """
    try:
        lf = wh.scan("corporate_events")
    except Exception:  # noqa: BLE001 - 供應層沒有這張表時退回「無分割」，與股利同一處置
        return {}
    cols = lf.collect_schema().names()
    if not {"date", "stock_id", "adj_factor", "event_type"} <= set(cols):
        return {}
    df = (
        lf.select("date", "stock_id", "adj_factor", "event_type")
        .filter(
            pl.col("date").is_between(start, end)
            & pl.col("adj_factor").is_not_null()
            & (pl.col("adj_factor") > 0.0)
            & pl.col("event_type").is_in(list(_SPLIT_EVENTS))
        )
        .collect()
    )
    out: dict[dt.date, list[tuple[str, float]]] = {}
    for r in df.to_dicts():
        out.setdefault(r["date"], []).append((str(r["stock_id"]), float(r["adj_factor"])))
    return out


def _apply_splits(state: _State, day: dt.date, events: list[tuple[str, float]]) -> None:
    """分割日調整股數與成本基礎（T-168）。**必須在當日收盤價更新之前呼叫**，
    否則 D-029 的跳價防護會先把部位當成資料異常清掉。

    1:20 分割的 `adj_factor` 是 0.05：持股與成本股數 × 1/0.05、持有期高低點 × 0.05、
    `last_close` × 0.05。同步縮放，權益在分割前後連續（`cost_amount` 不動——
    同一筆錢換到更多股，均價自然變成原來的 0.05 倍）。

    股數取整到最近的整數：台股分割配發整股，不足一股的畸零部分實務上折現金，
    這裡直接忽略（誤差 < 1 股，對 15 檔部位的權益影響在 1e-6 量級）。
    """
    for sid, adj in events:
        if adj <= 0.0:
            continue
        prev_close = state.last_close.get(sid)
        if prev_close is not None:
            state.last_close[sid] = prev_close * adj
        shares = state.holdings.get(sid)
        if shares:
            after = round(shares / adj)
            state.holdings[sid] = after
            state.splits.append(
                SplitAdjustment(
                    date=day,
                    stock_id=sid,
                    adj_factor=adj,
                    shares_before=shares,
                    shares_after=after,
                )
            )
        lot = state.lots.get(sid)
        if lot is not None:
            # 均價是 cost_amount / cost_shares 推出來的：同一筆錢換到更多股，
            # 故調的是股數、不是金額（調金額會讓成本憑空變動）
            lot.cost_shares = round(lot.cost_shares / adj)
            lot.max_close *= adj
            lot.min_close *= adj


def _credit_dividends(state: _State, day: dt.date, events: list[tuple[str, float, str]]) -> None:
    """除息日把現金入帳。**必須在當日收盤價更新之前呼叫**——每股金額要用
    除息**前**的收盤（`state.last_close` 此刻仍是昨收）。用除息後的價格會少算。"""
    for sid, adj, etype in events:
        shares = state.holdings.get(sid)
        if not shares:
            continue
        prev_close = state.last_close.get(sid)
        if prev_close is None or prev_close <= 0.0:
            continue
        per_share = prev_close * (1.0 - adj)
        cash = per_share * shares
        state.cash += cash
        lot = state.lots.get(sid)
        if lot is not None:
            lot.dividends += cash
        state.dividends.append(
            Dividend(
                date=day,
                stock_id=sid,
                shares=shares,
                per_share=per_share,
                cash=cash,
                event_type=etype,
            )
        )


def _limit_up(row: _Row) -> float | None:
    if row.get("limit_up_price") is not None:
        return float(row["limit_up_price"])
    prev = row.get("_prev_close")
    return limit_up_proxy(float(prev)) if prev is not None else None


def _limit_down(row: _Row) -> float | None:
    if row.get("limit_down_price") is not None:
        return float(row["limit_down_price"])
    prev = row.get("_prev_close")
    return limit_down_proxy(float(prev)) if prev is not None else None


def _portfolio_value(state: _State, day_px: dict[str, _Row]) -> float:
    mv = 0.0
    for sid, sh in state.holdings.items():
        px = day_px.get(sid, {}).get("open")
        ref = float(px) if px is not None else state.last_close.get(sid, 0.0)
        mv += sh * ref
    return state.cash + mv


def _execute(
    state: _State,
    targets: dict[str, float],
    day: dt.date,
    day_px: dict[str, _Row],
    cost_cfg: CostConfig,
    max_participation: float,
) -> None:
    """在 day 開盤把持倉調到 targets（先賣後買、買單受現金約束）。"""
    pv = _portfolio_value(state, day_px)
    names = sorted(set(state.holdings) | set(targets))  # set 順序不定 → 排序確保決定性
    deltas: dict[str, int] = {}
    for sid in names:
        row = day_px.get(sid)
        cur = state.holdings.get(sid, 0)
        if row is None or row.get("open") is None:  # 停牌：凍結
            continue
        w = targets.get(sid, 0.0)
        o = float(row["open"])
        desired = int((w * pv) // (o * _LOT)) * _LOT if (w > 0 and o > 0) else 0
        if w > 0 and o > 0 and desired == 0:
            # T-135：目標金額不足一張 → 整張捨去歸零。先前**靜默**——不記未成交、不報錯，
            # 結果是「40 檔等權、10M 資金」實測曝險只有 61%，且檔數越多越嚴重，把跨檔數的
            # 比較整個混淆掉（黃金規則 5）。記成 unfilled 讓它在 CLI 的「未成交」與結果包裡現形；
            # 成交行為本身不變，只是不再靜默。
            _unfill(state, sid, "buy", int(w * pv / o), day, "lot")
        if desired != cur:
            deltas[sid] = desired - cur

    # 次序鍵 sid：同 delta/同金額時決定性排序（現金約束下先後影響成交結果）
    sells = sorted((s for s in deltas if deltas[s] < 0), key=lambda s: (deltas[s], s))
    buys = sorted(
        (s for s in deltas if deltas[s] > 0), key=lambda s: (-deltas[s] * day_px[s]["open"], s)
    )
    for sid in sells:
        _fill(state, sid, deltas[sid], day, day_px[sid], cost_cfg, max_participation)
    for sid in buys:
        _fill(state, sid, deltas[sid], day, day_px[sid], cost_cfg, max_participation)


def _fill(
    state: _State,
    sid: str,
    delta: int,
    day: dt.date,
    row: _Row,
    cost_cfg: CostConfig,
    max_participation: float,
    reason: str = "rebalance",
) -> None:
    side = "buy" if delta > 0 else "sell"
    price = float(row["open"])
    prev = row.get("_prev_close")
    # D-029 防衛：非漲跌停可解釋的跳價（停牌復牌/減資/資料錯誤）→ 不成交
    if prev and abs(price / float(prev) - 1.0) > cost_cfg.data_guard.jump_ratio:
        _unfill(state, sid, side, abs(delta), day, "price_jump")
        return
    adv = row.get("_adv")
    d = decide_fill(
        side=side,
        price=price,
        shares=abs(delta),
        limit_up=_limit_up(row),
        limit_down=_limit_down(row),
        adv=float(adv) if adv else None,
        max_participation=max_participation,
        available_cash=state.cash,
        cost_cfg=cost_cfg,
    )
    # 迴圈變數不得叫 reason：那會**蓋掉本函數的 `reason` 參數**，
    # 於是部分成交（如撞到參與率上限）的再平衡單會被記成 `participation`，
    # 在結果包裡長得跟出場規則造成的成交一模一樣（T-231 實測 E0 有 8 筆）。
    for sh, why in d.unfilled:
        _unfill(state, sid, side, sh, day, why)
    if d.filled_shares <= 0 or d.cost is None:
        return

    qty, c = d.filled_shares, d.cost
    if side == "buy":
        state.cash -= price * qty + c.total
        state.holdings[sid] = state.holdings.get(sid, 0) + qty
        day_close = row.get("close")
        floor0 = min(price, float(day_close)) if day_close is not None else price
        lot = state.lots.get(sid)
        if lot is None:  # 本輪首買（部位 0→>0）
            state.lots[sid] = _Lot(
                entry_date=day,
                cost_shares=qty,
                cost_amount=price * qty,
                min_close=floor0,
                max_close=max(price, float(day_close)) if day_close is not None else price,
            )
        else:  # 加碼併入均價
            lot.cost_shares += qty
            lot.cost_amount += price * qty
            lot.min_close = min(lot.min_close, floor0)
    else:
        state.cash += price * qty - c.total
        new = state.holdings.get(sid, 0) - qty
        if new <= 0:
            state.holdings.pop(sid, None)
            state.lots.pop(sid, None)  # 本輪結束
        else:
            state.holdings[sid] = new

    state.traded_notional += price * qty
    state.ledger.append(
        Trade(
            date=day,
            stock_id=sid,
            side=side,
            shares=qty,
            price=price,
            commission=c.commission,
            tax=c.tax,
            slippage=c.slippage,
            reason=reason,
        )
    )


def _held(state: _State) -> dict[str, HeldPosition]:
    """把持股與本輪成本攤成出場判斷要的形狀。"""
    out: dict[str, HeldPosition] = {}
    for sid, sh in state.holdings.items():
        lot = state.lots.get(sid)
        avg = lot.cost_amount / lot.cost_shares if lot and lot.cost_shares > 0 else 0.0
        dps = lot.dividends / sh if lot and sh > 0 else 0.0
        out[sid] = HeldPosition(
            shares=sh,
            avg_cost=avg,
            dividends_per_share=dps,
            peak_close=lot.max_close if lot else 0.0,
            sessions_held=lot.sessions_held if lot else 0,
        )
    return out


def _unfill(state: _State, sid: str, side: str, shares: int, day: dt.date, reason: str) -> None:
    state.unfilled.append(Unfilled(date=day, stock_id=sid, side=side, shares=shares, reason=reason))


def _forced_exit(state: _State, sid: str, price: float, day: dt.date, cost_cfg: CostConfig) -> None:
    """D-029 防衛：持倉的價格序列出現非漲跌停可解釋的跳價（停牌復牌跳空/減資/資料
    錯誤）→ 以 min(跳價前, 跳價後) 全數出場——幻影跳價不得記成損益。"""
    qty = state.holdings.pop(sid, 0)
    state.lots.pop(sid, None)
    if qty <= 0 or price <= 0:
        return
    c = trade_cost(side="sell", price=price, shares=qty, participation=0.0, cfg=cost_cfg)
    state.cash += price * qty - c.total
    state.traded_notional += price * qty
    state.ledger.append(
        Trade(
            date=day,
            stock_id=sid,
            side="sell",
            shares=qty,
            price=price,
            commission=c.commission,
            tax=c.tax,
            slippage=c.slippage,
        )
    )


def run_backtest(
    strategy_cfg: StrategyConfig,
    cost_cfg: CostConfig,
    start: dt.date,
    end: dt.date,
    warehouse: TableSource,
    initial_capital: float = 10_000_000.0,
    max_participation: float = 0.05,
    credit_dividends: bool = True,
) -> BacktestResult:
    """跑回測，回傳 BacktestResult（權益曲線、ledger、unfilled、指標）。

    `credit_dividends=False` 關掉 D-085 的股利入帳，回到**價格報酬**口徑。
    存在的理由是 D-086：含息與不含息兩次相減，才分離得出「股利本身」與
    「部位變大之後撞參與率上限」——後者實測占了 A 軌 47.3M 期末差裡的 13.6M。
    **不是預設**：真實投資人收得到股利，關掉它只是為了做對照。
    """
    from twquant.data import calendar as cal
    from twquant.strategy.build import build_targets, signal_dates
    from twquant.strategy.slots import slot_candidates

    calendar = cal.load(warehouse)
    sessions = calendar.between(start, end)
    if not sessions:
        raise ValueError(f"[{start},{end}] 無交易日")
    slot_cfg = strategy_cfg.slots
    # 每日補位模式沒有「再平衡日」：進出都由每天收盤的判斷決定（T-247）
    sig_dates = (
        set()
        if slot_cfg is not None
        else set(signal_dates(strategy_cfg.rebalance, calendar, start, end))
    )
    exit_rules = strategy_cfg.exit_rules
    # 進場濾網的均線也要算，否則欄位不存在、濾網靜默失效
    ma_windows = tuple(
        sorted(
            {
                int(r.window)
                for r in (*exit_rules, *strategy_cfg.entry_filters)
                if r.window is not None
            }
        )
    )
    by_date = _price_panel(warehouse, start, end, ma_windows)

    state = _State(cash=initial_capital)
    equity_curve: list[EquityPoint] = []
    pending: dict[str, float] | None = None
    exits_pending: dict[str, str] = {}
    trims_pending: dict[str, int] = {}  # T-286：明早要減碼的股數
    entries_pending: list[str] = []

    jump_ratio = cost_cfg.data_guard.jump_ratio
    div_by_date = _dividends_by_date(warehouse, start, end) if credit_dividends else {}
    # 分割與 `credit_dividends` 無關：不調股數就是錯的會計，沒有「要不要」的選項
    split_by_date = _splits_by_date(warehouse, start, end)
    for day in sessions:
        day_px = by_date.get(day, {})
        # D-085：**先入帳再更新收盤**。每股金額要用除息前的收盤，而 `state.last_close`
        # 此刻仍是昨收；等下面的迴圈跑完就變成除息後的價格了，會少算。
        todays = div_by_date.get(day)
        if todays:
            _credit_dividends(state, day, todays)
        # T-168：分割在收盤更新之前調整股數，否則跳價防護會先把部位清掉
        todays_split = split_by_date.get(day)
        if todays_split:
            _apply_splits(state, day, todays_split)
        for sid, row in day_px.items():  # 更新最後收盤（停牌沿用）
            if row.get("close") is None:
                continue
            new_c = float(row["close"])
            prev_c = state.last_close.get(sid)
            if (
                sid in state.holdings
                and prev_c is not None
                and prev_c > 0
                and abs(new_c / prev_c - 1.0) > jump_ratio
            ):
                _forced_exit(state, sid, min(prev_c, new_c), day, cost_cfg)
            lot = state.lots.get(sid)
            if lot is not None:
                lot.min_close = min(lot.min_close, new_c)
                lot.max_close = max(lot.max_close, new_c)
                if lot.entry_date != day:  # 買進當天不算；之後每個有行情的交易日 +1
                    lot.sessions_held += 1
            state.last_close[sid] = new_c

        if pending is not None:
            _execute(state, pending, day, day_px, cost_cfg, max_participation)
            pending = None
            exits_pending = {}  # 再平衡日由選股全權決定，前一晚的出場旗標作廢
        elif exits_pending:
            for sid, why in sorted(exits_pending.items()):
                qty = state.holdings.get(sid, 0)
                px_row = day_px.get(sid)
                if qty > 0 and px_row is not None and px_row.get("open") is not None:
                    _fill(state, sid, -qty, day, px_row, cost_cfg, max_participation, why)
            exits_pending = {}  # 沒賣掉的（跌停／量不足）今晚會再被判一次

        if slot_cfg is not None and trims_pending:  # T-286：減碼賣出，走與出場同一條成交路徑
            for sid, q in sorted(trims_pending.items()):
                have = state.holdings.get(sid, 0)
                px_row = day_px.get(sid)
                q = min(q, have)
                if q > 0 and px_row is not None and px_row.get("open") is not None:
                    _fill(
                        state, sid, -q, day, px_row, cost_cfg, max_participation, "exposure_target"
                    )
            trims_pending = {}  # 沒賣掉的（跌停／量不足）今晚會再算一次

        if slot_cfg is not None and entries_pending:  # 先賣後買：賣出的現金當天就能用
            pv = _portfolio_value(state, day_px)
            for sid in entries_pending:
                if len(state.holdings) >= slot_cfg.n:
                    break
                px_row = day_px.get(sid)
                if sid in state.holdings or px_row is None or px_row.get("open") is None:
                    continue
                o = float(px_row["open"])
                per_slot = pv * state.exposure_mult / slot_cfg.n  # T-286：倍數 1.0 時即既有行為
                want = int(per_slot // (o * _LOT)) * _LOT if o > 0 else 0
                if want == 0:  # 一格的金額買不起一張：記下來，不靜默略過（T-135 同理）
                    _unfill(state, sid, "buy", int(per_slot / o) if o > 0 else 0, day, "lot")
                    continue
                _fill(state, sid, want, day, px_row, cost_cfg, max_participation, "entry")
            entries_pending = []

        pos_value = sum(sh * state.last_close.get(sid, 0.0) for sid, sh in state.holdings.items())
        equity_curve.append(
            EquityPoint(
                date=day,
                equity=state.cash + pos_value,
                cash=state.cash,
                positions_value=pos_value,
            )
        )

        if day in sig_dates:
            # SPEC-012：banding 需要「昨天在不在裡面」；狀態留在引擎，build_targets 保持純函數
            tdf = build_targets(strategy_cfg, warehouse, day, held=frozenset(state.holdings))
            pending = {r["stock_id"]: float(r["target_weight"]) for r in tdf.to_dicts()}

        if exit_rules and state.holdings and pending is None:
            exits_pending = exit_reasons(exit_rules, _held(state), day_px, warehouse, day)

        if slot_cfg is not None and slot_cfg.exposure_target is not None:
            # T-286／D-145 §二：每日曝險目標。倍數用 V1 同一個波動估計（持股等權組合），
            # 空手時沒有東西可估 → 1.0（新進場全額）。減碼與出場同一天判、隔日開盤執行。
            et = slot_cfg.exposure_target
            held_ids = sorted(sid for sid, sh in state.holdings.items() if sh > 0)
            mult = 1.0
            if held_ids:
                realized = _realized_vol(warehouse.view(day), day, held_ids, et.window)
                if realized > 0.0:
                    mult = max(et.min_mult, min(1.0, et.target_vol / realized))
            state.exposure_mult = mult
            pos_now = sum(sh * state.last_close.get(sid, 0.0) for sid, sh in state.holdings.items())
            pv_now = state.cash + pos_now
            if pv_now > 0.0 and pos_now > 0.0 and pos_now / pv_now > mult + 1.0 / slot_cfg.n:
                sell_frac = 1.0 - (mult * pv_now) / pos_now
                for sid in held_ids:
                    if sid in exits_pending:  # 整檔要出場的，不再另外減碼
                        continue
                    q = int((state.holdings[sid] * sell_frac) // _LOT) * _LOT
                    if q > 0:
                        trims_pending[sid] = q

        if slot_cfg is not None:
            # 空位只算「現在真的空著的」：今晚才判定要賣的，明天賣不賣得掉還不知道（跌停）
            free = slot_cfg.n - len(state.holdings)
            if free > 0:
                cands = [
                    sid
                    for sid, _ in slot_candidates(strategy_cfg, warehouse, day)
                    if sid not in state.holdings
                ]
                blocked = entry_blocked(
                    [*exit_rules, *strategy_cfg.entry_filters], cands, day_px, warehouse, day
                )
                entries_pending = [sid for sid in cands if sid not in blocked][:free]

    equity_series = [e.equity for e in equity_curve]
    mean_equity = sum(equity_series) / len(equity_series)
    n_days = len(equity_series)
    single_side = state.traded_notional / 2.0
    turnover_ann = (
        (single_side / mean_equity) * (252.0 / n_days) if mean_equity > 0 and n_days else 0.0
    )
    metrics = compute_metrics(equity_series, turnover_ann)
    final_equity = equity_series[-1] if equity_series else initial_capital

    def _holding(sid: str, sh: int) -> Holding:
        px = state.last_close.get(sid, 0.0)
        lot = state.lots.get(sid)
        avg = lot.cost_amount / lot.cost_shares if lot and lot.cost_shares > 0 else 0.0
        div = lot.dividends if lot else 0.0
        # D-085：每股已收股利攤回單價再比成本；否則除息會顯示成虧損（那是價格跳空）
        dps = div / sh if sh > 0 else 0.0
        return Holding(
            stock_id=sid,
            shares=sh,
            last_close=px,
            value=sh * px,
            weight=(sh * px) / final_equity if final_equity > 0 else 0.0,
            entry_date=lot.entry_date if lot else None,
            avg_cost=avg,
            dividends=div,
            ret=((px + dps) / avg - 1.0) if avg > 0 else 0.0,
            max_loss=min(0.0, lot.min_close / avg - 1.0) if lot and avg > 0 else 0.0,
        )

    final_holdings = sorted(
        (_holding(sid, sh) for sid, sh in state.holdings.items()),
        key=lambda h: (-h.value, h.stock_id),
    )
    return BacktestResult(
        equity=equity_curve,
        ledger=state.ledger,
        unfilled=state.unfilled,
        splits=state.splits,
        metrics=metrics,
        initial_capital=initial_capital,
        final_equity=final_equity,
        final_holdings=final_holdings,
        dividends=state.dividends,
        credit_dividends=credit_dividends,
    )
