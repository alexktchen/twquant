"""回測結果包：把一次 run 的完整輸出落地成單一 JSON（T-131／SPEC-013 §3.3）。

為什麼需要它（D-061／R8）：`backtest` 只印指標並把純量寫進 `research_log`，
`BacktestResult` 的 `equity`／`ledger`／`unfilled` 全部丟棄。SPEC-013 §3.6 要求
「把既有曲線切成子區間、不得重跑」，但那份曲線從來沒有被存下來過。

落地放在 `reporting/` 而非 `backtest/`：回測層禁止寫檔（黃金規則 10）。
`bundle_backtest` 本身是純函數，只有 `write_bundle`／`read_bundle` 碰檔案。
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from twquant.backtest.costs import CostConfig
from twquant.backtest.engine import BacktestResult
from twquant.backtest.metrics import Metrics, compute_metrics
from twquant.errors import DataError
from twquant.strategy.config import StrategyConfig

SCHEMA_VERSION = 1


def data_fingerprint(tables: dict[str, tuple[int, str]]) -> str:
    """由「每張輸入表的 (列數, max(available_at))」算出資料指紋。

    偵測得到：回補、重抓、修正——任何改變列數或推進可得時間的動作。
    **偵測不到**：不改列數也不動 available_at 的就地竄改。內容級的驗證是
    物化與重訓那一條線的責任（T-112），這裡刻意只做便宜的檢查，
    並在此註明限制，而不是宣稱它是完整的內容雜湊。

    表的列舉順序不影響結果（排序後才雜湊）——同一份資料必須只有一個指紋。
    """
    if not tables:
        raise DataError(
            "result_bundle",
            None,
            "算資料指紋時沒有收到任何表。回傳空輸入的雜湊等於假裝有防護，"
            "呼叫端必須至少提供 prices_daily。",
        )
    payload = json.dumps(
        {k: list(v) for k, v in sorted(tables.items())}, ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def bundle_backtest(
    result: BacktestResult,
    *,
    strategy: StrategyConfig,
    cost: CostConfig,
    start: dt.date,
    end: dt.date,
    initial_capital: float,
    max_participation: float,
    source: str,
    data_fingerprint: str,
    data_tables: dict[str, tuple[int, str]] | None = None,
) -> dict[str, Any]:
    """把一次回測的完整輸出與身分序列化成可 JSON 化的 dict（純函數）。

    日期一律在此轉成 ISO 字串：留給呼叫端轉，每個報表腳本會各寫各的格式，
    結果包就不再是逐位元可比的了。
    """
    m = result.metrics
    return {
        "schema_version": SCHEMA_VERSION,
        "strategy": strategy.model_dump(mode="json"),
        "cost": cost.model_dump(mode="json"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "initial_capital": initial_capital,
        "max_participation": max_participation,
        "source": source,
        "data_fingerprint": data_fingerprint,
        # 逐表明細：跨策略比雜湊無意義（不同策略讀不同的表，雜湊必然不同），
        # 要比就得比**重疊的表**。純追加欄位，舊結果包缺此鍵仍可讀。
        "data_tables": {k: list(v) for k, v in sorted((data_tables or {}).items())},
        "initial_equity": result.initial_capital,
        "final_equity": result.final_equity,
        "metrics": {
            "cagr": m.cagr,
            "sharpe": m.sharpe,
            "sortino": m.sortino,
            "mdd": m.mdd,
            "calmar": m.calmar,
            "turnover": m.turnover,
            "win_rate": m.win_rate,
            "n_days": m.n_days,
        },
        # 曲線用陣列而非物件：逐日 4 個數字 × 上千日，鍵名會讓檔案大三倍
        "equity": [
            [e.date.isoformat(), e.equity, e.cash, e.positions_value] for e in result.equity
        ],
        "ledger": [
            {
                "date": t.date.isoformat(),
                "stock_id": t.stock_id,
                "side": t.side,
                "shares": t.shares,
                "price": t.price,
                "commission": t.commission,
                "tax": t.tax,
                "slippage": t.slippage,
            }
            for t in result.ledger
        ],
        "unfilled": [
            {
                "date": u.date.isoformat(),
                "stock_id": u.stock_id,
                "side": u.side,
                "shares": u.shares,
                "reason": u.reason,
            }
            for u in result.unfilled
        ],
        "final_holdings": [
            {
                "stock_id": h.stock_id,
                "shares": h.shares,
                "last_close": h.last_close,
                "value": h.value,
                "weight": h.weight,
                "entry_date": h.entry_date.isoformat() if h.entry_date else None,
                "avg_cost": h.avg_cost,
                "ret": h.ret,
                "max_loss": h.max_loss,
            }
            for h in result.final_holdings
        ],
    }


def metrics_from_bundle(bundle: dict[str, Any]) -> Metrics:
    """由結果包的權益曲線重算指標，供與引擎當初的輸出對帳（AC-3）。

    報表若自己重算卻與回測不符，兩邊必有一邊錯了；這個函數讓那件事可被測到，
    而不是等到報表上的數字被人發現對不上。
    """
    equity = [float(row[1]) for row in bundle["equity"]]
    turnover = float(bundle["metrics"]["turnover"])
    return compute_metrics(equity, turnover_annualized=turnover)


def write_bundle(path: str | Path, bundle: dict[str, Any]) -> None:
    """落地成 JSON。`sort_keys` 讓同一輸入產出逐位元相同的檔案。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )


def read_bundle(path: str | Path) -> dict[str, Any]:
    """讀回結果包，並擋掉版本不符——舊包的欄位語意可能已改。"""
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    got = data.get("schema_version")
    if got != SCHEMA_VERSION:
        raise DataError(
            "result_bundle",
            None,
            f"結果包 schema_version={got}，本版預期 {SCHEMA_VERSION}。"
            "舊包的欄位語意可能已改，請重跑而非強行讀取。",
        )
    return data
