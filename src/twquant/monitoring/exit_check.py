"""持股出場檢查（D-116／T-237）：把 E 系列的每日出場規則套到「今天的」持股上。

回測引擎的出場掛鉤（T-231）只活在回測裡；平台沒有部位簿，不知道使用者買了什麼、
買在哪個價位。本模組補上中間那一段：給一串股票代號，回傳每一條規則**今天**的狀態。

三條界線：
- **買進價不進 API。** 那是使用者自己的資料；停損由前端用本機保存的買進價去算，
  伺服器只回收盤與門檻。
- **不知道就說不知道。** 均線歷史不足、模型分數缺席或落後於行情日——一律回
  `triggered: null` 並附原因，**絕不**回 `false`。「沒有賣出訊號」與「沒辦法判斷」
  在畫面上必須長得不一樣（黃金規則 5）。
- **門檻只活在一個地方。** 規則由 D-114 登記的 E 系列策略配置讀出，這裡不另寫數字。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from twquant.config import BaseConfig, ServingConfig
from twquant.strategy.config import ExitRuleConfig, load_strategy

Rule = tuple[str, str, ExitRuleConfig]  # (代號, 顯示名稱, 規則)


class ExitCheckRuleRef(BaseConfig):
    """一條要顯示的規則：代號、顯示名稱、門檻所在的策略配置。"""

    code: str
    label: str
    strategy: str


class ExitCheckConfig(BaseConfig):
    """configs/exit_check.yaml。"""

    rules: list[ExitCheckRuleRef]


def load_exit_rules(cfg: ExitCheckConfig, strategies_dir: Path) -> list[Rule]:
    """由策略配置讀出每一條規則的門檻。策略必須**恰好一條** `exit_rules`。"""
    out: list[Rule] = []
    for ref in cfg.rules:
        rules = load_strategy(strategies_dir / f"{ref.strategy}.yaml").exit_rules
        if len(rules) != 1:
            raise ValueError(
                f"exit_check 的 {ref.code} 指向 {ref.strategy}，其 exit_rules 有 {len(rules)} 條"
                "——須恰好一條，否則說不清是哪一條觸發"
            )
        out.append((ref.code, ref.label, rules[0]))
    return out


def derive_exit_check(
    prices: Sequence[Mapping[str, Any]],
    scores: Mapping[str, Mapping[str, Any]],
    rules: Sequence[tuple[str, ExitRuleConfig] | Rule],
) -> dict[str, Any]:
    """純函數。`prices` 為 date 遞增的 (date, close)；`scores` 為 {model_id: {date, rank_pct}}。"""
    closes = [(r["date"], float(r["close"])) for r in prices if r.get("close") is not None]
    last_date, close = closes[-1] if closes else (None, None)
    evaluated = []
    for item in rules:
        code, label, rule = (item[0], item[0], item[1]) if len(item) == 2 else item
        evaluated.append(
            {"code": code, "label": label, **_evaluate(rule, closes, scores, last_date)}
        )
    return {"date": last_date, "close": close, "rules": evaluated}


def _evaluate(
    rule: ExitRuleConfig,
    closes: list[tuple[dt.date, float]],
    scores: Mapping[str, Mapping[str, Any]],
    last_date: dt.date | None,
) -> dict[str, Any]:
    base: dict[str, Any] = {"kind": rule.kind, "triggered": None, "note": ""}
    if rule.kind == "stop_loss":
        return base | {"pct": rule.pct, "note": "由買進價計算（買進價只存在你的瀏覽器）"}
    if rule.kind == "below_ma":
        window = int(rule.window or 0)
        if len(closes) < window:
            return base | {"window": window, "note": f"歷史不足 {window} 日，算不出均線"}
        ma = sum(c for _, c in closes[-window:]) / window
        return base | {"window": window, "reference": ma, "triggered": closes[-1][1] < ma}
    model_id = str(rule.params.get("model_id", ""))
    row = scores.get(model_id)
    out = base | {"threshold": rule.threshold, "model_id": model_id}
    if row is None:
        return out | {"note": "查不到這檔的模型分數"}
    out |= {"value": float(row["rank_pct"]), "score_date": row["date"]}
    if last_date is not None and row["date"] < last_date:
        return out | {"note": f"分數停在 {row['date']}，落後於行情日 {last_date}，不予判斷"}
    return out | {"triggered": float(row["rank_pct"]) < float(rule.threshold or 0.0)}


def exit_check(
    cfg: ServingConfig, check_cfg: ExitCheckConfig, strategies_dir: Path, ids: Sequence[str]
) -> dict[str, Any]:
    """讀 PG 後逐檔套用 `derive_exit_check`。PG 連不上會 raise（由端點轉 503）。"""
    from psycopg.rows import dict_row

    from twquant.data.serving import store

    rules = load_exit_rules(check_cfg, strategies_dir)
    longest = max([int(r.window) for _, _, r in rules if r.window] or [1])
    models = sorted({str(r.params["model_id"]) for _, _, r in rules if r.kind == "score_below"})
    schema = cfg.pg_schema
    with store.connect(cfg) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f'''SELECT stock_id, date, close FROM (
                  SELECT stock_id, date, close,
                         row_number() OVER (PARTITION BY stock_id ORDER BY date DESC) rn
                  FROM "{schema}"."prices_daily" WHERE stock_id = ANY(%s)) t
                WHERE rn <= %s ORDER BY stock_id, date''',
            (list(ids), longest),
        )
        prices: dict[str, list[dict[str, Any]]] = {}
        for row in cur.fetchall():
            prices.setdefault(str(row["stock_id"]), []).append(row)
        cur.execute(
            f'''SELECT DISTINCT ON (stock_id, model_id) stock_id, model_id, date, rank_pct
                FROM "{schema}"."ml_scores" WHERE stock_id = ANY(%s) AND model_id = ANY(%s)
                ORDER BY stock_id, model_id, date DESC''',
            (list(ids), models),
        )
        scores: dict[str, dict[str, dict[str, Any]]] = {}
        for row in cur.fetchall():
            scores.setdefault(str(row["stock_id"]), {})[str(row["model_id"])] = row
        cur.execute(
            f'SELECT stock_id, name FROM "{schema}"."stock_meta" WHERE stock_id = ANY(%s)',
            (list(ids),),
        )
        names = {str(r["stock_id"]): r["name"] for r in cur.fetchall()}
    return {
        "mode": "research",
        "stocks": [
            {"stock_id": sid, "name": names.get(sid)}
            | derive_exit_check(prices.get(sid, []), scores.get(sid, {}), rules)
            for sid in ids
        ],
    }
