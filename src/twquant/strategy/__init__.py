"""策略層（SPEC-003）：配置 → 宇宙過濾 → 因子組合 → 選股 → 權重 → overlay → 目標權重。

策略只產出「目標權重」(date, stock_id, target_weight)，不產出訂單（訂單在 SPEC-006）。
同一份 YAML 是回測與生產的唯一輸入。本層為純運算（無網路、無寫檔，黃金規則 10）。
"""

from twquant.strategy.config import StrategyConfig, load_strategy

__all__ = ["StrategyConfig", "load_strategy"]
