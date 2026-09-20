"""每日出場規則的判斷（T-231／D-114 §四）。

引擎每個交易日**收盤後**呼叫 `exit_reasons`，回傳「哪些持股該在隔日開盤出清、為什麼」。
只用當日收盤（含）以前的資訊：價格規則吃引擎已載入的當日行情列與本輪成本，
分數規則透過 `wh.view(day)`（PitView）讀因子——與 `build_targets` 同一條路，故沒有前視。
純運算：不寫檔、不連網。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from twquant.data.warehouse import TableSource
    from twquant.strategy.config import ExitRuleConfig


@dataclass(frozen=True)
class HeldPosition:
    """出場判斷需要的部位狀態（由引擎的 `_Lot` 攤出來）。"""

    shares: int
    avg_cost: float  # 本輪加權平均買進價（不含費用）
    dividends_per_share: float  # D-085：已收股利攤回單價，否則除息會被當成虧損
    peak_close: float = 0.0  # 本輪持有期間最高收盤（trailing_ma 用）
    sessions_held: int = 0  # 買進後經過幾個交易日（買進日＝0；time_stop 用）


def ma_column(window: int) -> str:
    """價格面板裡 N 日均線欄位的名字（引擎與本模組共用，避免兩邊各寫一次字串）。"""
    return f"_ma{window}"


def rule_label(rule: ExitRuleConfig) -> str:
    """寫進成交紀錄的出場原因。"""
    if rule.kind in ("below_ma", "trailing_ma"):
        return f"{rule.kind}_{rule.window}"
    if rule.kind == "score_below":
        return f"score_below:{rule.factor}"
    return rule.kind


def exit_reasons(
    rules: Sequence[ExitRuleConfig],
    held: Mapping[str, HeldPosition],
    day_px: Mapping[str, Mapping[str, Any]],
    wh: TableSource,
    day: dt.date,
) -> dict[str, str]:
    """{stock_id: 原因}。任一規則觸發即出場；多條同時觸發時記**排在前面**的那一條。

    當日沒有行情列（停牌）的持股不判斷——沒有收盤價可比，隔日也賣不掉。
    分數規則查不到該股當日分數時**不觸發**：缺分數不等於模型說賣，
    而分數整天缺席是資料問題，由 `assert_inference_features_present` 那一層把關。
    """
    out: dict[str, str] = {}
    scores: dict[str, float] = {}
    for rule in rules:
        if rule.kind == "score_below":
            scores = _scores(rule, wh, day, sorted(held))
        for sid in sorted(held):
            if sid in out:
                continue
            close = day_px.get(sid, {}).get("close")
            if close is None:
                continue
            if rule.kind == "stop_loss":
                pos = held[sid]
                if pos.avg_cost <= 0.0:
                    continue
                hit = (float(close) + pos.dividends_per_share) / pos.avg_cost - 1.0 <= -_req(
                    rule.pct
                )
            elif rule.kind == "take_profit":
                pos = held[sid]
                if pos.avg_cost <= 0.0:
                    continue
                gain = (float(close) + pos.dividends_per_share) / pos.avg_cost - 1.0
                hit = gain >= _req(rule.pct)
            elif rule.kind == "time_stop":
                hit = held[sid].sessions_held >= int(_req(rule.sessions))
            elif rule.kind == "below_ma":
                ma = day_px[sid].get(ma_column(int(_req(rule.window))))
                hit = ma is not None and float(close) < float(ma)
            elif rule.kind == "trailing_ma":
                pos = held[sid]
                ma = day_px[sid].get(ma_column(int(_req(rule.window))))
                peak = (
                    (pos.peak_close + pos.dividends_per_share) / pos.avg_cost - 1.0
                    if pos.avg_cost > 0.0
                    else 0.0
                )
                armed = peak >= _req(rule.min_peak_gain)  # 曾經漲到過門檻才上膛
                hit = armed and ma is not None and float(close) < float(ma)
            else:
                score = scores.get(sid)
                hit = score is not None and score < _req(rule.threshold)
            if hit:
                out[sid] = rule_label(rule)
    return out


def _req(value: float | int | None) -> float:
    if value is None:  # ExitRuleConfig 的驗證器已保證，這裡只是給型別檢查器看
        raise ValueError("出場規則缺必要參數")
    return float(value)


def _scores(
    rule: ExitRuleConfig, wh: TableSource, day: dt.date, ids: list[str]
) -> dict[str, float]:
    import twquant.factors.builtins  # noqa: F401  # 匯入觸發全因子註冊
    from twquant.factors.compute import compute

    factor = str(rule.factor)
    df = compute([factor], wh.view(day), dates=[day], params={factor: dict(rule.params)})
    df = df.filter(pl.col("stock_id").is_in(ids) & pl.col("value").is_not_null())
    return {str(r["stock_id"]): float(r["value"]) for r in df.to_dicts()}


def entry_blocked(
    rules: Sequence[ExitRuleConfig],
    ids: Sequence[str],
    day_px: Mapping[str, Mapping[str, Any]],
    wh: TableSource,
    day: dt.date,
) -> set[str]:
    """候選裡「買了隔天就會被自己的賣出規則賣掉」的那些（T-247）。

    只看**不需要部位**就能判斷的規則（below_ma／score_below）。停損與 trailing_ma 相對於
    買進價與持有期高點，對還沒買的股票沒有定義——新部位的未實現報酬是 0，兩者都不會觸發。
    """
    free = [r for r in rules if r.kind in ("below_ma", "score_below")]
    if not free or not ids:
        return set()
    fresh = {sid: HeldPosition(shares=0, avg_cost=0.0, dividends_per_share=0.0) for sid in ids}
    return set(exit_reasons(free, fresh, day_px, wh, day))
