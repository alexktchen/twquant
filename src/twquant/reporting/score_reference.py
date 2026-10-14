"""歷史同區間參考（D-179）：名單上每一檔，過去「分數落在同一個區間」的股票照名單規則買進抱 20 天的報酬分布。

- 一筆＝網站績效的逐筆（D-176 §二）：公布隔天開盤買、抱 hold_sessions、再隔天開盤賣、扣成本含股利；
  每個區間當成一份每日名單（`new_entries`），同一檔還抱著時不重複算（`drop_repeats`）。
- 潛伏兩條線依模型分數分區間；大盤精選沒有分數，整條線一個區間（`ALL`），筆來自結果包換股清單的新買進。
- 期間以公布日分：dev（開發期）／unseen（模型沒看過）。賣出日一律 ≤ dev_end，之後的不算；行情由呼叫端只讀到 dev_end。

純函數：分數、行情面板、結果包由呼叫端讀好傳進來。給的是分布，不是預測；只供顯示，不進模型。
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import polars as pl
from pydantic import Field, model_validator

from twquant.backtest.costs import CostConfig
from twquant.backtest.list_trades import EntryTrade, drop_repeats, new_entries, simulate_entries
from twquant.config import BaseConfig
from twquant.errors import DataError
from twquant.reporting.scorecard import trade_stats

ALL = "ALL"  # 不分區間的線（大盤精選）用的區間鍵
Period = Literal["dev", "unseen"]


class Bucket(BaseConfig):
    """分數區間 [lo, hi)。"""

    key: str = Field(min_length=1)
    lo: float = Field(ge=0.0)
    hi: float = Field(gt=0.0)


class LineRef(BaseConfig):
    """一條線怎麼找「同區間」：score＝依模型分數分區間；bundle＝結果包的新買進、不分區間。"""

    kind: Literal["score", "bundle"]
    model_id: str | None = None
    oos_start: dt.date | None = None
    train_end: dt.date | None = None  # 公布日 ≤ 這天＝dev，之後＝unseen
    volume_window: int | None = Field(default=None, ge=1)
    volume_ratio_min: float | None = Field(default=None, gt=0.0)
    bundles: dict[str, str] | None = None  # 期間 → 結果包路徑（相對 data_dir）

    @model_validator(mode="after")
    def _complete(self) -> LineRef:
        if self.kind == "score" and not (self.model_id and self.oos_start and self.train_end):
            raise ValueError("score 線要有 model_id、oos_start、train_end")
        if self.kind == "bundle" and not self.bundles:
            raise ValueError("bundle 線要有 bundles")
        if (self.volume_window is None) != (self.volume_ratio_min is None):
            raise ValueError("volume_window 與 volume_ratio_min 要一起給")
        return self


class ScoreReferenceConfig(BaseConfig):
    """configs/score_reference.yaml（D-179 事前登記）。"""

    registration: str = Field(min_length=1)
    dev_end: dt.date
    warmup_days: int = Field(ge=1)
    hold_sessions: int = Field(ge=1)
    notional: float = Field(gt=0.0)
    lot_size: int = Field(ge=1)
    max_participation: float = Field(gt=0.0, le=1.0)
    big_loss: float = Field(lt=0.0)
    min_n: int = Field(ge=1)
    buckets: list[Bucket] = Field(min_length=1)
    periods: dict[str, str]
    lines: dict[str, LineRef]

    @model_validator(mode="after")
    def _buckets_tile(self) -> ScoreReferenceConfig:
        prev = 0.0
        for b in self.buckets:
            if b.lo != prev or b.hi <= b.lo:
                raise ValueError(f"區間要從 0 起、連續不重疊：{b.key} [{b.lo}, {b.hi})")
            prev = b.hi
        if set(self.periods) != {"dev", "unseen"}:
            raise ValueError("periods 要剛好是 dev 與 unseen")
        return self


@dataclass(frozen=True)
class RefRow:
    """一條線 × 期間 × 區間的分布。筆數 0 時數字都是 None。"""

    line: str
    period: str
    bucket: str
    lo: float | None
    hi: float | None
    n: int
    mean: float | None
    median: float | None
    win_rate: float | None
    big_loss_rate: float | None
    p10: float | None
    p90: float | None
    best: float | None
    worst: float | None
    signals_from: dt.date | None
    signals_to: dt.date | None


def bucket_of(score: float, buckets: Sequence[Bucket]) -> str | None:
    """分數落在哪個區間；不在任何區間（負的、超過上界、NaN）→ None。"""
    for b in buckets:
        if b.lo <= score < b.hi:
            return b.key
    return None


def bucket_lists(
    scores: pl.DataFrame, buckets: Sequence[Bucket], keep: pl.DataFrame | None = None
) -> dict[str, dict[dt.date, list[str]]]:
    """每個區間每天的名單（分數高的在前）。`scores`：date、stock_id、score；`keep`：date、stock_id，給了就只留這些列。

    分數有空值或不在任何區間即 raise（模型分數是機率，出界代表資料壞了）。
    """
    df = scores.select("date", "stock_id", "score")
    if df.filter(pl.col("score").is_null() | ~pl.col("score").is_finite()).height:
        raise DataError("ml_scores", None, "分數有空值或非有限數")
    # 有打分的日子都算「有公布」——爆量條件篩掉整天也是空名單，不是沒公布
    days = sorted(df.get_column("date").unique().to_list())
    if keep is not None:
        df = df.join(keep.select("date", "stock_id").unique(), on=["date", "stock_id"], how="inner")
    out: dict[str, dict[dt.date, list[str]]] = {b.key: {} for b in buckets}
    for r in df.sort(["date", "score", "stock_id"], descending=[False, True, False]).iter_rows():
        day, sid, score = r
        key = bucket_of(float(score), buckets)
        if key is None:
            raise DataError("ml_scores", day, f"{sid} 的分數 {score} 不在任何區間")
        out[key].setdefault(day, []).append(str(sid))
    # 那天有打分、但這個區間沒有股票：空名單也算「有公布」（前一天在區間裡的今天就不在了）
    for lists in out.values():
        for d in days:
            lists.setdefault(d, [])
    return out


def volume_surge(prices: pl.DataFrame, window: int, ratio_min: float) -> pl.DataFrame:
    """當天成交量 ≥ 前 window 個交易日平均（不含當天）× ratio_min 的 (date, stock_id)。同 `volume.vol_ratio_5d`。"""
    px = prices.select("date", "stock_id", "volume").sort(["stock_id", "date"])
    prev = pl.col("volume").shift(1).rolling_mean(window).over("stock_id")
    return (
        px.with_columns(_pa=prev)
        .filter((pl.col("_pa") > 0) & (pl.col("volume") / pl.col("_pa") >= ratio_min))
        .select("date", "stock_id")
    )


def bundle_entries(
    bundle: Mapping[str, Any], sessions: Sequence[dt.date]
) -> list[tuple[dt.date, str]]:
    """結果包換股清單裡「部位 0 → >0」的買進 → (公布日, 代號)：公布日＝成交日前一個交易日。"""
    from twquant.reporting.trade_journal import ENTER, build_journal

    index = {d: i for i, d in enumerate(sessions)}
    out: list[tuple[dt.date, str]] = []
    for day in build_journal(dict(bundle)):
        fill = dt.date.fromisoformat(day.date)
        if fill not in index or index[fill] == 0:
            raise DataError("score_reference", fill, "結果包的成交日不在交易日序列裡（或是第一天）")
        signal = sessions[index[fill] - 1]
        out.extend((signal, m.stock_id) for m in day.moves if m.action == ENTER)
    return out


def _closed_before(trades: Sequence[EntryTrade], dev_end: dt.date) -> list[EntryTrade]:
    """只留已賣出、而且賣出日 ≤ dev_end 的筆（之後才賣的不算，不是 0）。"""
    out = []
    for t in trades:
        if t.status != "closed":
            continue
        if t.exit_date is None or t.exit_date > dev_end:
            raise DataError(
                "score_reference", t.exit_date, f"{t.stock_id} 在開發期上界之後才賣出，不得計入"
            )
        out.append(t)
    return out


def distribution(rets: Sequence[float], big_loss: float) -> dict[str, Any]:
    """筆數、平均、中位數、上漲比例、大虧比例、p10／p90、最好最差。少於 2 筆 p10／p90 為 None。"""
    base = trade_stats(rets, big_loss=big_loss)
    if len(rets) >= 2:
        q = statistics.quantiles(rets, n=10, method="inclusive")
        base["p10"], base["p90"] = q[0], q[-1]
    else:
        base["p10"] = base["p90"] = None
    return base


def _simulate(
    entries: Sequence[tuple[dt.date, str]],
    sessions: Sequence[dt.date],
    panel: Mapping[dt.date, Mapping[str, Mapping[str, Any]]],
    dividends: Mapping[dt.date, Sequence[tuple[str, float, str]]],
    splits: Mapping[dt.date, Sequence[tuple[str, float]]],
    cost: CostConfig,
    cfg: ScoreReferenceConfig,
) -> list[EntryTrade]:
    trades, _ = drop_repeats(
        simulate_entries(
            entries,
            sessions,
            panel,
            dividends,
            splits,
            cost,
            hold_sessions=cfg.hold_sessions,
            notional=cfg.notional,
            max_participation=cfg.max_participation,
            lot=cfg.lot_size,
        )
    )
    return _closed_before(trades, cfg.dev_end)


def _row(
    line: str,
    period: str,
    bucket: Bucket | None,
    trades: Sequence[EntryTrade],
    cfg: ScoreReferenceConfig,
) -> RefRow:
    rets = [t.ret for t in trades if t.ret is not None]
    d = distribution(rets, cfg.big_loss)
    signals = [t.signal_date for t in trades]
    return RefRow(
        line=line,
        period=period,
        bucket=bucket.key if bucket else ALL,
        lo=bucket.lo if bucket else None,
        hi=bucket.hi if bucket else None,
        n=int(d["n"]),
        mean=d["mean"],
        median=d["median"],
        win_rate=d["win_rate"],
        big_loss_rate=d["big_loss_rate"],
        p10=d["p10"],
        p90=d["p90"],
        best=d["best"],
        worst=d["worst"],
        signals_from=min(signals) if signals else None,
        signals_to=max(signals) if signals else None,
    )


def score_line_rows(
    line: str,
    ref: LineRef,
    scores: pl.DataFrame,
    sessions: Sequence[dt.date],
    panel: Mapping[dt.date, Mapping[str, Mapping[str, Any]]],
    dividends: Mapping[dt.date, Sequence[tuple[str, float, str]]],
    splits: Mapping[dt.date, Sequence[tuple[str, float]]],
    cost: CostConfig,
    cfg: ScoreReferenceConfig,
    prices: pl.DataFrame | None = None,
) -> list[RefRow]:
    """依分數分區間的線：每個區間 × 期間一列。`prices`（date、stock_id、volume）只有要爆量條件的線才要。

    sessions 要是 [oos_start, dev_end] 的交易日；公布日在 oos_start 之前或 dev_end 之後的分數 raise（不得偷看）。
    """
    assert ref.kind == "score" and ref.oos_start and ref.train_end
    if not sessions or sessions[-1] > cfg.dev_end or sessions[0] < ref.oos_start:
        raise DataError("score_reference", None, f"交易日序列要落在 {ref.oos_start}～{cfg.dev_end}")
    days = scores.get_column("date")
    if days.min() < ref.oos_start or days.max() > cfg.dev_end:  # type: ignore[operator]
        raise DataError(
            "ml_scores",
            None,
            f"分數日期超出 {ref.oos_start}～{cfg.dev_end}：{days.min()!s}～{days.max()!s}",
        )
    keep = None
    if ref.volume_window is not None and ref.volume_ratio_min is not None:
        if prices is None:
            raise ValueError(f"{line} 要爆量條件，得給 prices")
        keep = volume_surge(prices, ref.volume_window, ref.volume_ratio_min)
    lists_by_bucket = bucket_lists(scores, cfg.buckets, keep)
    out: list[RefRow] = []
    for b in cfg.buckets:
        lists = lists_by_bucket[b.key]
        trades = _simulate(
            new_entries(lists, sessions), sessions, panel, dividends, splits, cost, cfg
        )
        for period in ("dev", "unseen"):
            mine = [t for t in trades if (t.signal_date <= ref.train_end) == (period == "dev")]
            out.append(_row(line, period, b, mine, cfg))
    return out


def bundle_line_rows(
    line: str,
    bundles: Mapping[str, Mapping[str, Any]],
    sessions: Sequence[dt.date],
    panel: Mapping[dt.date, Mapping[str, Mapping[str, Any]]],
    dividends: Mapping[dt.date, Sequence[tuple[str, float, str]]],
    splits: Mapping[dt.date, Sequence[tuple[str, float]]],
    cost: CostConfig,
    cfg: ScoreReferenceConfig,
) -> list[RefRow]:
    """不分區間的線：每個期間一列，筆來自那個期間的結果包。"""
    if not sessions or sessions[-1] > cfg.dev_end:
        raise DataError("score_reference", None, f"交易日序列要在 {cfg.dev_end} 以前")
    out: list[RefRow] = []
    for period in ("dev", "unseen"):
        b = bundles[period]
        if dt.date.fromisoformat(str(b["end"])) > cfg.dev_end:
            raise DataError(
                "score_reference", None, f"{line} {period} 結果包結束於 {b['end']}，越過開發期上界"
            )
        trades = _simulate(
            bundle_entries(b, sessions), sessions, panel, dividends, splits, cost, cfg
        )
        out.append(_row(line, period, None, trades, cfg))
    return out


def render(rows: Sequence[RefRow], cfg: ScoreReferenceConfig, names: Mapping[str, str]) -> str:
    """markdown 報告：每條線一張表（期間 × 區間）。"""

    def pct(x: float | None) -> str:
        return "—" if x is None else f"{x:+.1%}"

    def rate(x: float | None) -> str:
        return "—" if x is None else f"{x:.0%}"

    lines = [
        f"# {cfg.registration} 歷史同區間參考",
        "",
        "每筆＝公布隔天開盤買、抱 20 個交易日、再隔天開盤賣、扣成本含股利；同一檔還抱著不重複算。賣出日 ≤ "
        f"{cfg.dev_end}。開發期是規則試出來的那段，只當參考；不保證未來。",
        "",
    ]
    for line in cfg.lines:
        lines += [
            f"## {names.get(line, line)}（{line}）",
            "",
            "| 期間 | 區間 | 筆數 | 中位數 | 平均 | 上漲 | 虧>15% | 最差一成 | 最好一成 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for r in rows:
            if r.line != line:
                continue
            few = "（樣本少）" if 0 < r.n < cfg.min_n else ""
            lines.append(
                f"| {cfg.periods[r.period]} | {r.bucket} | {r.n}{few} | {pct(r.median)} | {pct(r.mean)} | "
                f"{rate(r.win_rate)} | {rate(r.big_loss_rate)} | {pct(r.p10)} | {pct(r.p90)} |"
            )
        lines.append("")
    return "\n".join(lines)
