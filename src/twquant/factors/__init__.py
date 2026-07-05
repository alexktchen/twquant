"""因子層（SPEC-002）：純函數因子 + 註冊表 + 標準化 + 反前視護欄。

因子是純函數（view, params）→ (date, stock_id, value)，無 IO、無全域狀態。
金融內容（要做哪些因子）見 docs/STRATEGY.md。
"""

from twquant.factors.registry import FactorDef, factor, get, list_factors

__all__ = ["FactorDef", "factor", "get", "list_factors"]
