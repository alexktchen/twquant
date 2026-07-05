"""回測層（SPEC-004）：日頻回測、台股交易限制、成本模型、驗證工具。

核心原則：回測成交假設與 PaperBroker（SPEC-006）共用同一套程式碼。純運算層
（無網路、無寫檔）；落地與 research_log 由 reporting 負責。
"""

from twquant.backtest.engine import BacktestResult, run_backtest

__all__ = ["BacktestResult", "run_backtest"]
