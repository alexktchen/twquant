"""D-159 第十五輪（逐筆）：三種移動停利 vs 抱滿 21 天。**參數照 D-159 §二 寫死，不得依結果改。**

    TWQUANT_PG_DSN=... uv run --all-groups python scripts/d159_trailing_exits.py

部位＝D-156 同一批 A20 虛擬部位（池內前 15 名、20 列內不重買、開盤漲停不進），第 0 天開盤買，
還原價，每筆扣來回 0.40%。第 k 天收盤觸發 → 第 k+1 天開盤賣；最長第 121 天開盤賣。
只留「進場後第 121 個交易日 ≤ 2026-04-30」的部位（依市場日曆機械篩，與結果無關）；
股票在那之前就沒有行情 → 以最後一個開盤價出場並計數，不剔除。

| 臂 | 出場 |
|---|---|
| S0 | 第 21 天開盤（無停損） |
| S0L | S0 ＋ 下軌停損（參考，不判定） |
| T-MA | 下軌停損；20 日均線 > 買進價時收盤 < 均線 |
| T-ATR | 下軌停損；線 = 持有以來最高收盤 − 3 × ATR14，線 > 買進價時收盤 < 線 |
| T-BRK | 下軌停損；主力成本階梯（2022-04 起，見 `broker_cost`／`step_floor`） |

下軌＝還原收盤 20 日均線 − 2 × 20 日母體標準差。
"""

from __future__ import annotations

import datetime as dt
import importlib
import io
import os
import pathlib
import sys
from typing import Any

import numpy as np
import polars as pl

# ── D-159 §二 的參數（寫死）──────────────────────────────────────────────────
BB_N, BB_K = 20, 2.0
MA_N = 20
ATR_N, ATR_K = 14, 3.0
BRK_WINDOW = 20  # 訊號日前 20 個交易日（t−20 ~ t−1）
BRK_STEP = 0.5  # 目標價 C × (1 + 0.5 j)
BRK_FROM = dt.date(2022, 4, 1)  # 分點資料 2022-03 起；成本窗需要前 20 個交易日
MAX_EXIT = 121  # 最長第 121 天開盤賣
S0_EXIT = 21
EVAL_FROM = dt.date(2019, 10, 1)
PATH_END = dt.date(2026, 4, 30)  # D-156 §一：路徑不得越過
ROUND_TRIP = 0.004
BIG_LOSS, BIG_WIN = -0.10, 0.50
_S = "stock_id"


# ── 純函數（tests/test_d159_trailing.py 守著）────────────────────────────────


def first_true(cond: np.ndarray) -> np.ndarray:
    """每列第一個 True 的位置；沒有 → -1。"""
    hit = cond.any(axis=1)
    return np.where(hit, cond.argmax(axis=1), -1)


def exit_day(trigger: np.ndarray, last: np.ndarray, cap: int) -> np.ndarray:
    """第一個觸發日 k → 第 k+1 天開盤；沒觸發 → cap；股票先沒行情 → 最後一個開盤（last）。

    trigger 形狀 (n, H)：第 0~H−1 天收盤是否觸發（沒有行情的格子必須是 False）。
    """
    k = first_true(trigger)
    ex = np.where(k >= 0, k + 1, cap)
    out: np.ndarray = np.minimum(np.minimum(ex, cap), last)
    return out


def lock_trigger(close: np.ndarray, line: np.ndarray, entry: np.ndarray) -> np.ndarray:
    """停利線高於買進價時，收盤 < 線（D-159 §二：停利線只管鎖利）。NaN 一律不觸發。"""
    with np.errstate(invalid="ignore"):
        return (line > entry[:, None]) & (close < line)


def atr_line(close: np.ndarray, atr: np.ndarray, k: float) -> np.ndarray:
    """持有以來最高收盤 − k × ATR（close 的 NaN 不會拉高最高價）。"""
    runmax = np.fmax.accumulate(close, axis=1)
    return runmax - k * atr


def broker_cost(net: np.ndarray, vwap: np.ndarray) -> np.ndarray:
    """主力成本 C = Σ max(N_d, 0) × VWAP_d ÷ Σ max(N_d, 0)；沒有一天淨買超 > 0 → NaN。

    net、vwap 形狀 (n, 20)；缺資料的格子給 NaN，視為沒有淨買超。
    """
    w = np.where(np.isfinite(net) & np.isfinite(vwap), np.maximum(net, 0.0), 0.0)
    num = (w * np.where(np.isfinite(vwap), vwap, 0.0)).sum(axis=1)
    den = w.sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0, num / den, np.nan)


def step_floor(close: np.ndarray, cost: np.ndarray, step: float) -> np.ndarray:
    """階梯停利線：最高收盤到過的最高一階 J（G_j = C(1 + step·j)）；J ≥ 1 時線 = G_{J−1}，否則 NaN。"""
    runmax = np.fmax.accumulate(close, axis=1)
    c = cost[:, None]
    with np.errstate(invalid="ignore"):
        j = np.floor((runmax / c - 1.0) / step + 1e-12)
        return np.where(j >= 1, c * (1.0 + step * (j - 1)), np.nan)


def summarize(tag: str, net: np.ndarray, days: np.ndarray) -> str:
    """一臂一行：每筆、每持有日、虧損尾巴、大賺的比例。"""
    top = np.sort(net)[-max(1, len(net) // 10) :]
    return (
        f"  {tag:<6} 筆數 {len(net):,}　每筆淨 平均 {net.mean():+.2%}／中位 {np.median(net):+.2%}　"
        f"勝率 {(net > 0).mean():.1%}　每持有日 {net.sum() / days.sum():+.4%}　平均持有 {days.mean():.1f}　"
        f"虧 >10% {(net < BIG_LOSS).mean():.1%}　最差 5% {np.percentile(net, 5):+.1%}　"
        f"淨 ≥ +50% {(net >= BIG_WIN).mean():.2%}　最好 10% 平均 {top.mean():+.1%}"
    )


# ── 資料 ─────────────────────────────────────────────────────────────────────


def _sibling(name: str) -> Any:
    """同目錄的研究腳本（d151_report、d156_exit_model）；它們不是套件，執行期才載入。"""
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    return importlib.import_module(name)


def _market(conn: Any) -> pl.DataFrame:
    mk: pl.DataFrame = _sibling("d151_report").load_market(conn).sort(_S, "date")
    a = pl.col("adj")
    ac, ah, al = pl.col("close") / a, pl.col("high") / a, pl.col("low") / a
    prev = ac.shift(1).over(_S)
    tr = pl.max_horizontal(ah - al, (ah - prev).abs(), (al - prev).abs())
    seasoned = pl.int_range(pl.len()).over(_S) >= ATR_N  # 同 price.atr_14 的閘門
    return mk.with_columns(
        ac=ac,
        ao=pl.col("open") / a,
        lower=ac.rolling_mean(BB_N).over(_S) - BB_K * ac.rolling_std(BB_N, ddof=0).over(_S),
        ma=ac.rolling_mean(MA_N).over(_S),
        atr=pl.when(seasoned).then(tr.ewm_mean(alpha=1.0 / ATR_N, ignore_nulls=True).over(_S)),
        vwap=pl.when(pl.col("volume") > 0).then(pl.col("amount") / pl.col("volume") / a),
    )


def _broker_net(conn: Any, ids: list[str]) -> pl.DataFrame:
    """(date, stock_id, net)：top-15 分點（買 − 賣）加總。分年查，避免一次掃 5,770 萬列。"""
    schema = _sibling("d151_report").SCHEMA

    out = []
    lo = BRK_FROM - dt.timedelta(days=60)
    for year in range(lo.year, PATH_END.year + 1):
        a, b = max(lo, dt.date(year, 1, 1)), min(PATH_END, dt.date(year, 12, 31))
        with conn.cursor() as cur:
            cur.execute(
                f"""select date, stock_id, sum(buy_shares - sell_shares)::float8
                    from "{schema}".broker_trades
                    where date between %s and %s and stock_id = any(%s)
                    group by date, stock_id""",
                (a, b, ids),
            )
            out.extend(cur.fetchall())
    return pl.DataFrame(out, schema=["date", _S, "net"], orient="row").with_columns(
        pl.col("date").cast(pl.Date)
    )


def _month_t(
    month: np.ndarray, a: tuple[np.ndarray, np.ndarray], b: tuple[np.ndarray, np.ndarray]
) -> str:
    m, t, n = _sibling("d156_exit_model")._month_t(month, a, b)
    return f"月區塊差 {m:+.4%}（t {t:+.2f}，{n} 個月）"


def main() -> None:
    import psycopg

    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    d156 = _sibling("d156_exit_model")

    with psycopg.connect(os.environ["TWQUANT_PG_DSN"]) as conn:
        mk = _market(conn)
        pos = d156.build_positions(mk, d156._a20_scores())
        cal = np.sort(mk["date"].unique().to_numpy())
        entry_ix = np.searchsorted(cal, pos["entry_date"].to_numpy())
        horizon_ok = entry_ix + MAX_EXIT < len(cal)
        horizon_ok[horizon_ok] = cal[entry_ix[horizon_ok] + MAX_EXIT] <= np.datetime64(PATH_END)
        pos = pos.filter(pl.Series(horizon_ok) & (pl.col("entry_date") >= EVAL_FROM))
        broker = _broker_net(conn, sorted(pos[_S].unique().to_list()))

    sid = mk[_S].to_numpy()
    cut = np.flatnonzero(sid[1:] != sid[:-1]) + 1
    stock_end = np.empty(len(sid), dtype=np.int64)
    for a, b in zip(np.r_[0, cut], np.r_[cut, len(sid)], strict=True):
        stock_end[a:b] = b - 1
    r0 = pos["r0"].to_numpy()
    last = np.minimum(stock_end[r0] - r0, MAX_EXIT)  # 最後一個有開盤的天數
    k = np.arange(MAX_EXIT)
    rows = np.minimum(r0[:, None] + k[None, :], stock_end[r0][:, None])
    valid = k[None, :] <= (stock_end[r0] - r0)[:, None]

    def grab(col: str) -> np.ndarray:
        v = mk[col].to_numpy().astype(float)[rows]
        out: np.ndarray = np.where(valid, v, np.nan)
        return out

    close, lower, ma, atr = grab("ac"), grab("lower"), grab("ma"), grab("atr")
    orow = np.minimum(r0[:, None] + np.arange(MAX_EXIT + 1)[None, :], stock_end[r0][:, None])
    opens = mk["ao"].to_numpy().astype(float)[orow]
    entry = opens[:, 0]
    with np.errstate(invalid="ignore"):
        stop = close < lower
    ix = np.arange(len(r0))

    def net_of(ex: np.ndarray) -> np.ndarray:
        out: np.ndarray = opens[ix, ex] / entry - 1.0 - ROUND_TRIP
        return out

    locks = {
        "T-MA": lock_trigger(close, ma, entry),
        "T-ATR": lock_trigger(close, atr_line(close, atr, ATR_K), entry),
    }
    arms: dict[str, np.ndarray] = {
        "S0": np.full(len(r0), S0_EXIT),
        "S0L": exit_day(stop[:, :S0_EXIT], last, S0_EXIT),
        **{tag: exit_day(stop | lk, last, MAX_EXIT) for tag, lk in locks.items()},
    }
    forced = last < MAX_EXIT
    month = pos["entry_date"].dt.strftime("%Y-%m").to_numpy()
    print("**D-159 第十五輪（逐筆）：事前登記的判讀，參數寫死。**")
    print(
        f"部位 {len(r0):,} 筆（進場 {pos['entry_date'].min()} ~ {pos['entry_date'].max()}）；"
        f"第 121 天前沒行情、以最後開盤出場的 {int(forced.sum()):,} 筆"
    )
    base = (net_of(arms["S0"]), arms["S0"].astype(float))
    for tag, ex in arms.items():
        net = net_of(ex)
        print(summarize(tag, net, ex.astype(float)))
        if tag != "S0":
            kk = np.clip(ex - 1, 0, MAX_EXIT - 1)
            fired_stop, fired_lock = stop[ix, kk], locks.get(tag, np.zeros_like(stop))[ix, kk]
            reason = np.where(
                fired_stop,
                "下軌",
                np.where(
                    fired_lock,
                    "停利線",
                    np.where((ex == last) & (last < ex.max()), "沒行情", "時間到"),
                ),
            )
            vals, cnt = np.unique(reason, return_counts=True)
            print(
                "         出場："
                + "、".join(f"{v} {c / len(ex):.1%}" for v, c in zip(vals, cnt, strict=True))
            )
            print("         對 S0 " + _month_t(month, (net, ex.astype(float)), base))

    # T-BRK：2022-04 起的子集，與同子集的 S0、T-ATR 並列
    sub = pos["entry_date"].to_numpy() >= np.datetime64(BRK_FROM)
    net_px = mk.select("date", _S).with_row_index("r").join(broker, on=["date", _S], how="left")
    net_by_row = net_px.sort("r")["net"].to_numpy().astype(float)
    win = r0[:, None] - 1 - np.arange(BRK_WINDOW, 0, -1)[None, :]  # t−20 ~ t−1（t = r0−1）
    same = sid[np.clip(win, 0, None)] == sid[r0][:, None]
    nets = np.where(same, net_by_row[np.clip(win, 0, None)], np.nan)
    vwap = np.where(same, mk["vwap"].to_numpy().astype(float)[np.clip(win, 0, None)], np.nan)
    cost = broker_cost(nets, vwap)
    floor = step_floor(close, cost, BRK_STEP)
    with np.errstate(invalid="ignore"):
        brk_trig = stop | (np.isfinite(floor) & (close < floor))
    brk = exit_day(brk_trig, last, MAX_EXIT)
    print(
        f"\n── T-BRK（進場 {BRK_FROM} 起，{int(sub.sum()):,} 筆；沒有主力成本的 {int((sub & ~np.isfinite(cost)).sum()):,} 筆）"
    )
    reached = np.isfinite(floor).any(axis=1)
    print(
        f"  碰到 1.5C 的 {reached[sub].mean():.1%}；成本 ÷ 買進價 中位 {np.nanmedian(cost[sub] / entry[sub]):.3f}"
    )
    sub_month = month[sub]
    sub_base = (net_of(arms["S0"])[sub], arms["S0"][sub].astype(float))
    atr_ex = arms["T-ATR"][sub]
    for tag, ex in (("S0", arms["S0"][sub]), ("T-ATR", atr_ex), ("T-BRK", brk[sub])):
        net = opens[ix[sub], ex] / entry[sub] - 1.0 - ROUND_TRIP
        print(summarize(tag, net, ex.astype(float)))
        if tag == "T-BRK":
            print("         對 S0 " + _month_t(sub_month, (net, ex.astype(float)), sub_base))
            atr_net = opens[ix[sub], atr_ex] / entry[sub] - 1.0 - ROUND_TRIP
            print(
                "         對 T-ATR "
                + _month_t(sub_month, (net, ex.astype(float)), (atr_net, atr_ex.astype(float)))
            )
    print(
        f"\n（第 121 天判定窗止於 {PATH_END}；最長持有 {MAX_EXIT} 天；還原價；來回 {ROUND_TRIP:.2%}）"
    )


if __name__ == "__main__":
    main()
