"""執行層（SPEC-006）：券商協定、下單、盤前檢查、對帳。

唯一可對真實世界發指令的層，安全優先。PaperBroker 成交直接用回測的成交原語
（backtest.fill.decide_fill），兩邊不分家（漂移偵測前提）。
"""

from twquant.execution.broker import Fill, Order, OrderAck, PaperBroker, Position, make_order

__all__ = ["Fill", "Order", "OrderAck", "PaperBroker", "Position", "make_order"]
