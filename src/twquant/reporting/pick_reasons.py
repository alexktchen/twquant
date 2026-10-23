"""「為什麼選它」（D-180）：名單上每一檔，模型最看重的理由翻成白話。只供顯示，不影響名單。

- 潛伏飆股／高分爆量：A20 模型對這一檔的特徵貢獻（LightGBM pred_contrib，對數勝算單位），加分最多的 top_n 個
  ＋扣分最多的一個。大盤狀態特徵（ctx.）同一天每檔都一樣，不當理由。
- 大盤精選：名單由規則因子排名產生，理由就是那幾個規則因子，依「權重 × 全市場百分位」排序。
- 句子用因子的**原始值**（factor_values.value）與全市場百分位（pct，由低到高 0～1）寫，句型在 configs/pick_reasons.yaml。

純函數：貢獻、原始值由呼叫端算好／讀好傳進來。
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field

from twquant.config import BaseConfig
from twquant.errors import DataError
from twquant.ml.explain import Contributions

Kind = Literal[
    "pct",
    "pct1",
    "pp",
    "lnpct",
    "times",
    "sd",
    "int",
    "count",
    "inv",
    "exp_yi",
    "exp_abs_times",
    "gap",
    "rank",
    "rank_self",
    "none",
]


class Phrase(BaseConfig):
    """一個因子的句型：值 > at 用 pos、< at 用 neg、＝ at 用 zero（沒給 neg／zero 就退回）。"""

    kind: Kind
    pos: str = Field(min_length=1)
    neg: str | None = None
    zero: str | None = None
    at: float = 0.0
    scale: float | None = Field(default=None, gt=0.0)  # kind=count：比例 × scale＝天數


class PickReasonsConfig(BaseConfig):
    """configs/pick_reasons.yaml（D-180）。"""

    top_n: int = Field(ge=1)
    model_line_exclude_prefix: str = Field(min_length=1)
    core_signals: dict[str, float]
    # D-215：其他規則線各自的因子與權重（鍵＝線別代碼）；沒列的規則線用 core_signals
    line_signals: dict[str, dict[str, float]] = Field(default_factory=dict)
    factors: dict[str, Phrase]

    def rule_signals(self, line: str) -> dict[str, float]:
        """規則線的因子與權重：`line_signals` 有這條線就用它，否則是大盤精選的 `core_signals`。"""
        return self.line_signals.get(line, self.core_signals)

    def rule_factors(self) -> list[str]:
        """所有規則線會用到的因子（讀原始值用），去重、排序。"""
        names = set(self.core_signals)
        for sig in self.line_signals.values():
            names |= set(sig)
        return sorted(names)


@dataclass(frozen=True)
class Reason:
    """一條理由。sign：+1 加分、−1 扣分；weight：排序用的量（模型＝貢獻、規則＝權重×百分位）。"""

    factor: str
    text: str
    sign: int
    weight: float


def _num(kind: Kind, v: float, pct: float | None, scale: float | None) -> str | None:
    """依 kind 把值寫成字；寫不出來（缺百分位、除以 0）回 None。"""
    a = abs(v)
    if kind == "pct":
        return f"{a * 100:.0f}%"
    if kind == "pct1":
        return f"{a * 100:.1f}%"
    if kind == "pp":
        return f"{a * 100:.0f}"
    if kind == "lnpct":
        return f"{abs(math.exp(v) - 1.0) * 100:.0f}%"
    if kind == "times":
        return f"{v:.1f}"
    if kind == "sd":
        return f"{a:.1f}"
    if kind == "int":
        return f"{round(v):d}"
    if kind == "count":
        return f"{round(v * (scale or 1.0)):d}"
    if kind == "inv":
        return None if v <= 0 else f"{1.0 / v:.1f}"
    if kind == "exp_yi":
        return f"{math.exp(v) / 1e8:,.0f}"
    if kind == "exp_abs_times":
        return f"{math.exp(a):.1f}"
    if kind == "gap":
        return f"{max(0.0, 1.0 - v) * 100:.0f}%"
    if kind == "rank":
        if pct is None:
            return None
        return (
            f"前 {max(1, round((1.0 - pct) * 100))}%"
            if pct >= 0.5
            else f"後 {max(1, round(pct * 100))}%"
        )
    if kind == "rank_self":
        return f"{v * 100:.0f}%"
    return ""


def phrase(p: Phrase, value: float | None, pct: float | None) -> str | None:
    """因子的一句白話；值缺、非有限數或寫不出數字 → None（呼叫端跳過這個因子，不編字）。"""
    if value is None or not math.isfinite(value):
        return None
    if value > p.at:
        tpl = p.pos
    elif value < p.at:
        tpl = p.neg or p.pos
    else:
        tpl = p.zero or p.neg or p.pos
    if "{v}" not in tpl:
        return tpl
    num = _num(p.kind, value, pct, p.scale)
    return None if num is None else tpl.replace("{v}", num)


def model_reasons(
    contribs: Mapping[str, float],
    values: Mapping[str, tuple[float | None, float | None]],
    cfg: PickReasonsConfig,
) -> list[Reason]:
    """模型線的理由：加分最多的 top_n 個（貢獻 > 0）＋扣分最多的一個（貢獻 < 0）。

    contribs：特徵 → 貢獻（不含常數項）；values：特徵 → (原始值, 全市場百分位)。
    排除 ctx.；寫不出句子的特徵跳過、往下遞補。設定裡沒有句型的特徵 → raise（新特徵要先寫句型）。
    """
    cands = [
        (f, c)
        for f, c in contribs.items()
        if not f.startswith(cfg.model_line_exclude_prefix) and math.isfinite(c) and c != 0.0
    ]
    missing = sorted(f for f, _ in cands if f not in cfg.factors)
    if missing:
        raise ValueError(f"這些特徵沒有句型（configs/pick_reasons.yaml）：{missing}")
    out: list[Reason] = []
    for f, c in sorted((x for x in cands if x[1] > 0), key=lambda x: (-x[1], x[0])):
        text = phrase(cfg.factors[f], *values.get(f, (None, None)))
        if text:
            out.append(Reason(f, text, 1, c))
        if len(out) == cfg.top_n:
            break
    for f, c in sorted((x for x in cands if x[1] < 0), key=lambda x: (x[1], x[0])):
        text = phrase(cfg.factors[f], *values.get(f, (None, None)))
        if text:
            out.append(Reason(f, text, -1, c))
            break
    return out


def rule_reasons(
    values: Mapping[str, tuple[float | None, float | None]],
    cfg: PickReasonsConfig,
    signals: Mapping[str, float] | None = None,
) -> list[Reason]:
    """規則線的理由：規則因子（預設大盤精選的 core_signals）依「權重 × 全市場百分位」由高到低，取 top_n 個。缺值的跳過。"""
    rows: list[Reason] = []
    for f, w in (cfg.core_signals if signals is None else signals).items():
        value, pct = values.get(f, (None, None))
        text = phrase(cfg.factors[f], value, pct)
        if text is None or pct is None:
            continue
        rows.append(Reason(f, f"{text}，全市場前 {max(1, round((1.0 - pct) * 100))}%", 1, w * pct))
    rows.sort(key=lambda r: (-r.weight, r.factor))
    return rows[: cfg.top_n]


def reasons_json(reasons: Sequence[Reason]) -> list[dict[str, object]]:
    """存進資料庫／給網站的形狀：[{f, t, s}]（排序用的 weight 不外流）。"""
    return [{"f": r.factor, "t": r.text, "s": r.sign} for r in reasons]


class PickLike(Protocol):
    """名單上的一檔（`pick_reasons_store.DayPick`）。"""

    @property
    def line(self) -> str: ...
    @property
    def stock_id(self) -> str: ...
    @property
    def score(self) -> float | None: ...
    @property
    def model_id(self) -> str | None: ...


Row = tuple[str, str, str | None, list[dict[str, object]]]


def model_rows(
    day: dt.date,
    picks: Sequence[PickLike],
    contribs: Mapping[str, Contributions],
    raw: Mapping[tuple[str, str], tuple[float | None, float | None]],
    model_id: str,
    cfg: PickReasonsConfig,
    tol: float,
) -> list[Row]:
    """模型線每一檔的理由列。重算的分數要等於名單上的分數（差 ≤ tol），對不上或不在推論池裡就 raise。"""
    out: list[Row] = []
    for p in picks:
        c = contribs.get(p.stock_id)
        if c is None:
            raise DataError("pick_reasons", day, f"{p.line} {p.stock_id} 不在當天的推論池裡")
        if p.score is None or abs(c.prediction - p.score) > tol:
            raise DataError(
                "pick_reasons",
                day,
                f"{p.stock_id} 重算的分數 {c.prediction:.6f} 與名單上的 {p.score} 不同——輸入變了，不寫理由",
            )
        vals = {f: raw.get((p.stock_id, f), (None, None)) for f in c.contribs}
        out.append(
            (p.line, p.stock_id, model_id, reasons_json(model_reasons(c.contribs, vals, cfg)))
        )
    return out


def rule_rows(
    picks: Sequence[PickLike],
    raw: Mapping[tuple[str, str], tuple[float | None, float | None]],
    gates: Mapping[str, Mapping[str, float]],
    cfg: PickReasonsConfig,
) -> list[Row]:
    """規則線每一檔的理由列：規則因子＋排序模型（串接閘門）的全市場百分位（有的話，當「前提」s=0）。"""
    out: list[Row] = []
    for p in picks:
        signals = cfg.rule_signals(p.line)
        vals = {f: raw.get((p.stock_id, f), (None, None)) for f in signals}
        reasons = rule_reasons(vals, cfg, signals)
        pct = gates.get(p.model_id or "", {}).get(p.stock_id)
        if pct is not None:
            top = max(1, round((1.0 - pct) * 100))
            reasons.append(Reason("ml.rank", f"排序模型分數在全市場前 {top}%", 0, pct))
        out.append((p.line, p.stock_id, p.model_id, reasons_json(reasons)))
    return out
