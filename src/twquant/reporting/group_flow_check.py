"""族群資金流入有沒有用（D-178，事前登記）：名單逐筆依「進名單那天族群是否資金流入」分兩組比報酬。

主要（決定結論）：名單績效的逐筆（D-176：每一檔新進名單一筆、隔天開盤買、抱 20 個交易日、扣成本含股利、
同一檔同時只有一筆），只取已賣出的。每一筆看它進名單那天（signal_date）所屬族群有沒有標「資金流入」
（D-177 修訂的定義，原封不動），比兩組的平均報酬。同一天進名單的幾筆會一起漲跌，所以信賴區間用
「整天一起抽」的 bootstrap（cluster bootstrap by signal date）。判斷門檻在 configs/group_flow_check.yaml。

次要（只當參考，不決定結論）：全市場每一天，資金流入族群的股票 vs 其他股票，之後 20 個交易日的收盤報酬差。

純函數：資料由呼叫端讀好傳進來。
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
import polars as pl
from pydantic import Field

from twquant.config import BaseConfig

_TINY = 1e-12  # 比這小的標準差當成 0（浮點誤差）


class GroupFlowCheckConfig(BaseConfig):
    """configs/group_flow_check.yaml（D-178 事前登記；看到結果之後不得修改）。"""

    registration: str
    start: dt.date  # 名單績效開戶日；這天以後進名單的逐筆
    flow_fingerprint: str  # 「資金流入」定義（stock_groups.yaml flow）的指紋：定義被改就不算數
    min_hot_trades: int = Field(ge=1)
    min_hot_dates: int = Field(ge=1)
    min_diff: float  # 兩組平均報酬差至少這麼多（小數）
    ci_level: float = Field(gt=0.0, lt=1.0)
    bootstrap_reps: int = Field(ge=100)
    seed: int
    consistency_min_trades: int = Field(ge=1)  # 某條線資金流入組至少幾筆，才要求那條線的差也 > 0
    market_horizon: int = Field(ge=1)  # 次要：全市場看之後幾個交易日
    market_step: int = Field(ge=1)  # 次要：每隔幾個交易日取一天算 t（不重疊）


@dataclass(frozen=True)
class TradeObs:
    """一筆已賣出的逐筆：哪條線、哪天進名單、哪檔、族群、那天族群有沒有資金流入、報酬、是不是即時公布。"""

    line: str
    signal: dt.date
    sid: str
    group: str | None
    hot: bool
    ret: float
    live: bool


@dataclass(frozen=True)
class GroupStats:
    n: int
    dates: int
    mean: float | None
    median: float | None
    win: float | None


@dataclass(frozen=True)
class CheckResult:
    """主要檢定的結果。verdict：有用（開發期支持）／沒有用／樣本不足。"""

    hot: GroupStats
    cold: GroupStats
    diff: float | None
    ci: tuple[float, float] | None
    by_line: dict[str, tuple[GroupStats, GroupStats, float | None]]
    verdict: str
    reasons: tuple[str, ...]


def label_trades(
    rows: Sequence[Mapping[str, object]],
    line: str,
    groups: Mapping[str, str],
    hot_by_date: Mapping[dt.date, frozenset[str]],
    start: dt.date,
) -> list[TradeObs]:
    """名單績效的逐筆列（網站 /api/scorecard/trades 的 rows）→ 已賣出、start 以後的逐筆，標上族群與資金流入。

    族群照網站（族群表，沒有就官方產業別，由呼叫端放進 groups）；那天族群資金沒算到（沒有那天的資料）即 raise。
    """
    out: list[TradeObs] = []
    for r in rows:
        if r.get("status") != "closed" or r.get("ret") is None:
            continue
        signal = dt.date.fromisoformat(str(r["signal"]))
        if signal < start:
            continue
        if signal not in hot_by_date:
            raise ValueError(f"{line} {r['sid']}：{signal} 沒有族群資金，不能分組")
        sid = str(r["sid"])
        group = groups.get(sid)
        ret = float(r["ret"])  # type: ignore[arg-type]
        if not math.isfinite(ret):
            raise ValueError(f"{line} {sid} {signal}：報酬不是有限數 {ret}")
        out.append(
            TradeObs(
                line,
                signal,
                sid,
                group,
                group is not None and group in hot_by_date[signal],
                ret,
                bool(r.get("live")),
            )
        )
    return out


def _stats(obs: Sequence[TradeObs]) -> GroupStats:
    if not obs:
        return GroupStats(0, 0, None, None, None)
    rets = [o.ret for o in obs]
    return GroupStats(
        n=len(rets),
        dates=len({o.signal for o in obs}),
        mean=float(np.mean(rets)),
        median=float(np.median(rets)),
        win=sum(1 for r in rets if r > 0) / len(rets),
    )


def _diff(obs: Sequence[TradeObs]) -> float | None:
    hot = [o.ret for o in obs if o.hot]
    cold = [o.ret for o in obs if not o.hot]
    return float(np.mean(hot) - np.mean(cold)) if hot and cold else None


def cluster_bootstrap(
    obs: Sequence[TradeObs], reps: int, seed: int, level: float
) -> tuple[float, float] | None:
    """兩組平均差的信賴區間：每次整天整天地抽（同一天進名單的一起抽回來），抽 reps 次取百分位。

    某次抽到的日子裡沒有任一組 → 那次不算；能算的不到一半 → None（樣本太少）。固定 seed，結果可重現。
    """
    days = sorted({o.signal for o in obs})
    if not days:
        return None
    by_day = {d: [o for o in obs if o.signal == d] for d in days}
    # 每天兩組的（加總、筆數），抽樣時只要加總，不用逐筆重算
    hs = np.array([sum(o.ret for o in by_day[d] if o.hot) for d in days])
    hn = np.array([sum(1 for o in by_day[d] if o.hot) for d in days])
    cs = np.array([sum(o.ret for o in by_day[d] if not o.hot) for d in days])
    cn = np.array([sum(1 for o in by_day[d] if not o.hot) for d in days])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(days), size=(reps, len(days)))
    n_hot, n_cold = hn[idx].sum(axis=1), cn[idx].sum(axis=1)
    ok = (n_hot > 0) & (n_cold > 0)
    if ok.sum() < reps / 2:
        return None
    diffs = hs[idx].sum(axis=1)[ok] / n_hot[ok] - cs[idx].sum(axis=1)[ok] / n_cold[ok]
    tail = (1.0 - level) / 2.0
    return float(np.quantile(diffs, tail)), float(np.quantile(diffs, 1.0 - tail))


def evaluate(obs: Sequence[TradeObs], cfg: GroupFlowCheckConfig) -> CheckResult:
    """照事前登記的門檻下結論（D-178 §二）。"""
    hot = [o for o in obs if o.hot]
    cold = [o for o in obs if not o.hot]
    hs, cs = _stats(hot), _stats(cold)
    diff = _diff(obs)
    by_line: dict[str, tuple[GroupStats, GroupStats, float | None]] = {}
    for line in sorted({o.line for o in obs}):
        part = [o for o in obs if o.line == line]
        by_line[line] = (
            _stats([o for o in part if o.hot]),
            _stats([o for o in part if not o.hot]),
            _diff(part),
        )
    if hs.n < cfg.min_hot_trades or hs.dates < cfg.min_hot_dates or cs.n < cfg.min_hot_trades:
        reason = (
            f"資金流入組 {hs.n} 筆、{hs.dates} 天（門檻 {cfg.min_hot_trades} 筆、{cfg.min_hot_dates} 天）；"
            f"其他組 {cs.n} 筆"
        )
        return CheckResult(hs, cs, diff, None, by_line, "樣本不足", (reason,))
    ci = cluster_bootstrap(obs, cfg.bootstrap_reps, cfg.seed, cfg.ci_level)
    reasons: list[str] = []
    passed = True
    if diff is None or diff < cfg.min_diff:
        passed = False
        reasons.append(f"報酬差 {_pct(diff)} 未達 {_pct(cfg.min_diff)}")
    if ci is None or ci[0] <= 0.0:
        passed = False
        reasons.append(
            f"{cfg.ci_level:.0%} 信賴區間下限 {_pct(ci[0]) if ci else '算不出來'} 沒有大於 0"
        )
    for line, (h, _, d) in by_line.items():
        if h.n >= cfg.consistency_min_trades and (d is None or d <= 0.0):
            passed = False
            reasons.append(f"{line} 資金流入組 {h.n} 筆，報酬差 {_pct(d)} 沒有大於 0")
    if passed:
        reasons.append("三個條件都過")
    return CheckResult(
        hs, cs, diff, ci, by_line, "有用（開發期支持）" if passed else "沒有用", tuple(reasons)
    )


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:+.2f}%"


@dataclass(frozen=True)
class MarketLift:
    """次要：全市場每天「資金流入族群的股票 − 其他股票」之後 horizon 日收盤報酬的平均差。"""

    days: int
    mean: float | None
    positive: float | None  # 差 > 0 的天數比例
    sampled: int  # 每隔 step 天取一天（不重疊）
    t: float | None  # 取樣那些天的 t 值


def market_lift(
    prices: pl.DataFrame,
    hot_members: Callable[[dt.date], frozenset[str] | None],
    days: Sequence[dt.date],
    universe: str,
    horizon: int,
    step: int,
) -> MarketLift:
    """prices（date、stock_id、close）；hot_members(day) 回那天資金流入族群的成員（那天沒算回 None，跳過）。

    之後 horizon 個交易日不夠的日子不算。報酬是收盤到收盤，不含成本與股利——只看方向，不決定結論。
    """
    px = prices.filter(pl.col("stock_id").str.contains(universe)).select(
        "date", "stock_id", "close"
    )
    sessions = sorted(px.get_column("date").unique().to_list())
    pos = {d: i for i, d in enumerate(sessions)}
    wide = px.pivot(on="date", index="stock_id", values="close")
    diffs: list[tuple[dt.date, float]] = []
    for day in days:
        i = pos.get(day)
        members = hot_members(day)
        if i is None or i + horizon >= len(sessions) or members is None:
            continue
        a, b = str(sessions[i]), str(sessions[i + horizon])
        fwd = wide.select("stock_id", (pl.col(b) / pl.col(a) - 1.0).alias("fwd")).filter(
            pl.col("fwd").is_not_null() & pl.col("fwd").is_finite()
        )
        is_hot = fwd.get_column("stock_id").is_in(list(members))
        hot, cold = fwd.filter(is_hot).get_column("fwd"), fwd.filter(~is_hot).get_column("fwd")
        if hot.len() == 0 or cold.len() == 0:
            continue
        diffs.append((day, float(hot.mean()) - float(cold.mean())))  # type: ignore[arg-type]
    if not diffs:
        return MarketLift(0, None, None, 0, None)
    vals = np.array([d for _, d in diffs])
    sample = vals[::step]
    sd = float(sample.std(ddof=1)) if len(sample) >= 3 else 0.0
    # 每天的差幾乎一樣（標準差只剩浮點誤差）時 t 沒有意義
    t = float(sample.mean() / (sd / math.sqrt(len(sample)))) if sd > _TINY else None
    return MarketLift(len(vals), float(vals.mean()), float((vals > 0).mean()), len(sample), t)


def render(
    result: CheckResult, market: MarketLift, cfg: GroupFlowCheckConfig, as_of: dt.date
) -> str:
    """結果的 markdown（印在工作流程頁、存進 docs/reports）。"""

    def row(name: str, s: GroupStats) -> str:
        win = "—" if s.win is None else f"{s.win:.0%}"
        return f"| {name} | {s.n} | {s.dates} | {_pct(s.mean)} | {_pct(s.median)} | {win} |"

    lines = [
        f"# 族群資金流入有沒有用（{cfg.registration}）",
        "",
        f"資料到 {as_of}；逐筆＝名單績效（D-176）{cfg.start} 起已賣出的，三條線合併。",
        "",
        f"## 結論：**{result.verdict}**",
        "",
        *[f"- {r}" for r in result.reasons],
        "",
        "## 主要：名單逐筆，進名單那天族群資金流入 vs 沒有",
        "",
        "| 組 | 筆數 | 天數 | 平均報酬 | 中位數 | 賺錢比例 |",
        "|---|---:|---:|---:|---:|---:|",
        row("資金流入", result.hot),
        row("其他", result.cold),
        "",
        f"平均報酬差 **{_pct(result.diff)}**（門檻 {_pct(cfg.min_diff)}）；"
        + (
            f"{cfg.ci_level:.0%} 信賴區間 {_pct(result.ci[0])} ～ {_pct(result.ci[1])}"
            if result.ci
            else "信賴區間：樣本不足未算"
        )
        + f"（整天一起抽的 bootstrap，{cfg.bootstrap_reps:,} 次，seed {cfg.seed}）。",
        "",
        "### 各線",
        "",
        "| 線 | 資金流入筆數 | 平均 | 其他筆數 | 平均 | 差 |",
        "|---|---:|---:|---:|---:|---:|",
        *[
            f"| {line} | {h.n} | {_pct(h.mean)} | {c.n} | {_pct(c.mean)} | {_pct(d)} |"
            for line, (h, c, d) in result.by_line.items()
        ],
        "",
        "## 次要（只當參考）：全市場",
        "",
        f"每天資金流入族群的股票 − 其他股票，之後 {cfg.market_horizon} 個交易日收盤報酬（不含成本、股利）：",
        f"{market.days} 天，平均差 {_pct(market.mean)}，差 > 0 的天數 "
        + ("—" if market.positive is None else f"{market.positive:.0%}")
        + f"；每 {cfg.market_step} 天取一天（{market.sampled} 天）t ＝ "
        + ("—" if market.t is None else f"{market.t:.2f}")
        + "。",
        "",
        "## 已知限制",
        "",
        "- 族群用的是現在（族群表抓的那天）的分類，套回過去；下市的公司不在平台上。",
        "- 5/4～10/7 的名單是事後補的（模型在那之前就訓練好了，仍是樣本外），一併計入。",
        "- 同一天進名單的幾筆會一起漲跌，所以信賴區間整天一起抽；筆數看起來多，獨立的資訊沒那麼多。",
    ]
    return "\n".join(lines) + "\n"
